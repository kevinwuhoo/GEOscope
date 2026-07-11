---
title: Datastore — One Postgres
tags: [postgres, pgvector, paradedb, schema]
---

# 26 · Datastore — One Postgres

← [[Home]] · serves [[23-Search-and-Retrieval]], [[24-Faceted-Search]]

> **Status update (2026-07-10):** This page documents the implemented and
> measured Postgres baseline. After a single-service database bakeoff, the
> prototype deployment direction moved to managed Elasticsearch; see
> [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]]. Keep this baseline for
> parity measurements and reproducibility, but do not extend its temporary
> per-model columns as the active architecture.

## Decision

> **One Postgres does dense vector + BM25 + structured filters + facets**, via `pgvector` + **ParadeDB `pg_search`**. No separate vector DB or Elasticsearch to keep in sync.

### Temporary v1 model-bake-off columns

The implemented baseline remains `series.embedding vector(384)`. The approved
model comparison adds two typed columns and independent cosine HNSW indexes:

```sql
ALTER TABLE series
  ADD COLUMN embedding_medcpt_768 vector(768),
  ADD COLUMN embedding_qwen3_06b_1024 vector(1024);

CREATE INDEX series_hnsw_medcpt_768
  ON series USING hnsw (embedding_medcpt_768 vector_cosine_ops);
CREATE INDEX series_hnsw_qwen3_06b_1024
  ON series USING hnsw (embedding_qwen3_06b_1024 vector_cosine_ops);
```

These columns compare three **single whole-document** representations; they are
not per-field/multi-vector retrieval. Code selects only registry-whitelisted
columns, and the MCP server exposes only one deployment-selected active variant.
→ [[48-Alternate-Embedding-Bakeoff]],
[[49-Alternate-Embedding-Bakeoff-Implementation-Plan]]

Extensions:
- **[`pgvector`](https://github.com/pgvector/pgvector)** — dense cosine kNN
  with HNSW and iterative scans for filtered retrieval.
- **[`pg_search`](https://www.paradedb.com/blog/introducing-search)**
  (ParadeDB/Tantivy) — the committed BM25 engine. The current v1 facets use
  explicit disjunctive SQL `GROUP BY`; `pdb.agg` is a later optimization to
  benchmark, not the implemented contract. → [[24-Faceted-Search]]
- **`pg_trgm`** — fuzzy / gene-symbol / accession matching.
- Plain columns + `text[]` arrays — current flat filters/facets; ancestor arrays
  are v2+.

### Why `pg_search` (over native FTS / `pg_textsearch`)

Faceting is make-or-break for this product—*"clean ontology + advanced search +
strict enums the LLM or human can query"* ([[00-Overview#North star|north
star]]). The committed v1 split is `pg_search` for BM25 plus portable,
disjunctive-correct SQL counts over the four normalized arrays.

The measured 222,961-row implementation uses `COUNT(DISTINCT series.id)` over
unnested values: 0.612 s for all four exact facets and 0.308 s for a BM25-scoped
1,000-candidate run on the development database
([[42-Build-Log#Status — 2026-07-10 (normalized filters and facets)]]). These
are observations, not latency SLOs. Benchmark `pdb.agg` only if this becomes a
real bottleneck or the project moves to sample-level indexing.

| Option | Maturity | Deps | Faceting | Verdict |
|---|---|---|---|---|
| **ParadeDB `pg_search`** | deployed in the current prototype | Rust / `pgrx` | supports `pdb.agg`; v1 uses SQL counts | **Chosen for BM25.** Self-host via ParadeDB Docker. |
| Native `tsvector` | core Postgres, ~15 yrs | none | via `GROUP BY` only | fallback if `pg_search` ever unavailable |
| Timescale `pg_textsearch` | alternative BM25 extension | C | re-check current capabilities if reconsidered | not the implemented stack |

> ⚠️ **Deployment watch-out:** extension availability must be verified for any
> eventual managed Postgres target. The spike stays on the working ParadeDB
> image. If a future host lacks `pg_search`, native FTS is the graceful
> degradation; SQL facet counts still work, but ranking behavior must be
> re-evaluated.

## Target schema sketch

The following `geo_series`/`geo_series_raw` names are the broader target model
from the original architecture. The implemented prototype table is `series`,
whose current and bake-off columns are documented above; this sketch is not
copy-paste migration DDL.

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;   -- ParadeDB BM25 (pdb.score; optional pdb.agg)
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- fuzzy / symbol / accession matching

-- raw landing (21-Ingestion)
CREATE TABLE geo_series_raw (
  gse           text PRIMARY KEY,
  esummary      jsonb,
  miniml        jsonb,
  samples       jsonb,
  sra           jsonb,
  update_date   date,
  fetched_at    timestamptz DEFAULT now()
);

-- normalized + indexed (22, 25)
CREATE TABLE geo_series (
  gse               text PRIMARY KEY,
  title             text,
  summary           text,
  overall_design    text,
  -- normalized controlled IDs (arrays: a study can span values)
  organism_id       text[],      -- NCBITaxon
  assay_id          text[],      -- EFO
  tissue_id         text[],      -- UBERON
  cell_type_id      text[],      -- CL
  disease_id        text[],      -- MONDO
  sex_id            text[],      -- PATO
  -- transitive-ancestor closure for hierarchical facets (24)
  ancestors         text[],
  -- scalar facets
  n_samples         int,
  submission_year   int,
  platform_id       text[],      -- raw GPL — exact lookup/provenance ONLY, not a facet
  instrument_model  text[],      -- derived from GPL, organism stripped (the facet)
  platform_technology text[],    -- GPL 'technology' attr: seq / array / … (coarse facet)
  is_single_cell    boolean,
  -- search
  embedding         vector(384),           -- implemented BGE baseline; candidate columns are described above. See 28/48.
  bm25_doc          text,                 -- concatenated narrative (normalized-label injection is a later ablation)
  confidence        jsonb,                -- per-field mapping confidence
  display           jsonb                 -- everything the UI/LLM shows
);

-- indexes
CREATE INDEX ON geo_series USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON geo_series USING gin (ancestors);
CREATE INDEX ON geo_series USING gin (organism_id);
CREATE INDEX ON geo_series USING bm25 (gse, bm25_doc)          -- pg_search: BM25
  WITH (key_field='gse');
CREATE INDEX ON geo_series USING gin (bm25_doc gin_trgm_ops);  -- fuzzy/symbol fallback
```

## Hybrid query (RRF in plain SQL)

No built-in fusion function — RRF is a windowed-`UNION` idiom ([ParadeDB's recipe](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)):

```sql
WITH dense_ann AS MATERIALIZED (
  SELECT gse, embedding <=> :qvec AS distance
  FROM geo_series
  WHERE organism_id @> ARRAY['NCBITaxon:9606']          -- filters apply here
  ORDER BY embedding <=> :qvec                          -- keep raw kNN order
  LIMIT 100
),
dense AS (
  SELECT gse,
         ROW_NUMBER() OVER (ORDER BY distance + 0, gse ASC) AS r
  FROM dense_ann                                        -- strict outer re-sort
),
lexical AS (   -- pg_search BM25
  SELECT gse, ROW_NUMBER() OVER (ORDER BY pdb.score(gse) DESC, gse ASC) AS r
  FROM geo_series
  WHERE bm25_doc @@@ :qtext          -- and structured filters as needed
  ORDER BY pdb.score(gse) DESC, gse ASC
  LIMIT 100
)
SELECT gse, SUM(1.0/(60+r)) AS rrf
FROM (SELECT * FROM dense UNION ALL SELECT * FROM lexical) u
GROUP BY gse ORDER BY rrf DESC, gse ASC LIMIT 20;
```

The frozen retrieval profile sets `hnsw.ef_search=100` and
`hnsw.iterative_scan=relaxed_order`. The materialized outer sort follows
pgvector's relaxed-scan guidance and makes ordering within the approximate ANN
candidate set stable without disabling its raw-operator index path; it does not
claim exact membership at the inner cutoff. →
[[49-Alternate-Embedding-Bakeoff-Implementation-Plan]]

## Facet queries

- **Implemented v1:** unnest one whitelisted array, count distinct series/value
  pairs with `GROUP BY`, omit the facet's own selected filter, sort by count then
  value, and cap buckets. Text-query facets count over a bounded ranked pool;
  blank-query facets count all matching rows. →
  [[45-Normalized-Filters-and-Facets-Plan]]
- **Possible optimization:** benchmark `pdb.agg` against the same disjunctive
  semantics before replacing anything.
- **Hierarchical roll-up (v2+):** `GROUP BY unnest(ancestors)` only after an
  ontology-backed field and ancestor materialization pass the tissue gate.

## Scale headroom

The current 222,961-row series corpus works with HNSW, BM25, and SQL facets at
the measurements above. Evaluate a scale extension such as
[`pgvectorscale`](https://github.com/timescale/pgvectorscale) only if the project
moves to **sample-level (8.6M)** indexing. → [[40-Roadmap]]

## Alternatives considered (and why not, for the spike)

| Option | Verdict |
|---|---|
| **OpenSearch/Elasticsearch** | Excellent hybrid + faceting, but a second system to run/sync; overkill at 289k. Revisit only if Postgres faceting stalls at sample scale. |
| **Qdrant / Weaviate / Milvus** | Strong vector + now-native facets, but add a datastore next to Postgres; you'd still keep Postgres for raw/relational. One-store wins for a spike. |
| **Typesense** | Great facets + keyword, weaker as the semantic core; another service. |
| **LanceDB** | Nice embedded/S3-native, but **no native facets** (hand-rolled GROUP BY) — facets are central here. |

Full engine comparison notes live in [[99-Sources]].

## Sources

- pgvector 0.8 (HNSW, halfvec, iterative scans) — https://www.postgresql.org/about/news/pgvector-080-released-2952/ · GitHub — https://github.com/pgvector/pgvector · filtered kNN on Aurora — https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/
- ParadeDB `pg_search` — https://www.paradedb.com/blog/introducing-search · hybrid RRF recipe — https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual · faceting — https://www.paradedb.com/blog/faceting
- Timescale `pg_textsearch` — https://github.com/timescale/pg_textsearch
- pgvectorscale (StreamingDiskANN + SBQ) — https://github.com/timescale/pgvectorscale
- Alternatives — OpenSearch RRF — https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/ · Elasticsearch retrievers — https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever · Qdrant — https://qdrant.tech/documentation/search/hybrid-queries/ · Weaviate — https://docs.weaviate.io/weaviate/concepts/search/hybrid-search · Milvus — https://milvus.io/blog/introduce-milvus-2-5-full-text-search-powerful-metadata-filtering-and-more.md · Vespa — https://docs.vespa.ai/en/querying/grouping.html · Typesense — https://typesense.org/docs/30.2/api/search.html · LanceDB (no native facets) — https://github.com/lancedb/lancedb/issues/1348
