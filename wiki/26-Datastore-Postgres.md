---
title: Datastore — One Postgres
tags: [postgres, pgvector, paradedb, schema]
---

# 26 · Datastore — One Postgres

← [[Home]] · serves [[23-Search-and-Retrieval]], [[24-Faceted-Search]]

## Decision

> **One Postgres does dense vector + BM25 + structured filters + facets**, via `pgvector` + **ParadeDB `pg_search`**. No separate vector DB or Elasticsearch to keep in sync.

Extensions:
- **`pgvector` 0.8.x** — dense kNN (HNSW), `halfvec`, binary quant, **iterative index scans** (fixes over-filtering). `<=>` cosine.
- **`pg_search`** (ParadeDB, Tantivy) — **committed choice**: real **BM25** *and* first-class, fast **faceting** (`pdb.agg`) in one extension; transactional/auto-updating. → [[24-Faceted-Search]]
- **`pg_trgm`** — fuzzy / gene-symbol / accession matching.
- Plain columns + `text[]` arrays — filters + ontology ancestor facets.

### Why `pg_search` (over native FTS / `pg_textsearch`)

Faceting is make-or-break for this product — *"clean ontology + advanced search + strict enums the LLM or human can query"* ([[00-Overview#North star|north star]]) — and we want it **fast and first-class alongside real BM25**, without rewriting the query layer as we scale.

> Nuance: facet *counts* don't strictly *require* `pg_search` — native `GROUP BY` computes them fine at 289k rows. But `pg_search`'s columnar `pdb.agg` keeps facets sub-100ms toward sample-level, and bundles true BM25 in the same extension. Committing now avoids a later rewrite.

| Option | Maturity | Deps | Faceting | Verdict |
|---|---|---|---|---|
| **ParadeDB `pg_search`** | most established BM25 extension; large deploy base | Rust / `pgrx` | ✅ first-class (`pdb.agg`) | **Chosen.** Self-host via ParadeDB Docker. |
| Native `tsvector` | core Postgres, ~15 yrs | none | via `GROUP BY` only | fallback if `pg_search` ever unavailable |
| Timescale `pg_textsearch` | v1.0 (Mar 2026), newer | C, no pgrx | ❌ none yet | not a swap-in — no faceting at v1.0 |

> ⚠️ **Deployment watch-out:** `pg_search` is often **not available on managed RDS/Aurora**. Self-host (Docker) for the spike; use **ParadeDB Cloud** or self-managed Postgres for production. If that ever becomes a hard blocker, native FTS is the graceful degradation (facets still work via `GROUP BY`, you just lose real BM25 ranking + `pdb.agg` speed).

## Schema (sketch)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;   -- ParadeDB: BM25 + faceting (pdb.agg / pdb.score)
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
  embedding         halfvec(1536),        -- one doc embedding; or vector(768) for MedCPT. See 28.
  bm25_doc          text,                 -- concatenated narrative + normalized labels (BM25)
  confidence        jsonb,                -- per-field mapping confidence
  display           jsonb                 -- everything the UI/LLM shows
);

-- indexes
CREATE INDEX ON geo_series USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX ON geo_series USING gin (ancestors);
CREATE INDEX ON geo_series USING gin (organism_id);
CREATE INDEX ON geo_series USING bm25 (gse, bm25_doc)          -- pg_search: BM25 + facets
  WITH (key_field='gse');
CREATE INDEX ON geo_series USING gin (bm25_doc gin_trgm_ops);  -- fuzzy/symbol fallback
```

## Hybrid query (RRF in plain SQL)

No built-in fusion function — RRF is a windowed-`UNION` idiom ([ParadeDB's recipe](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)):

```sql
WITH dense AS (
  SELECT gse, ROW_NUMBER() OVER (ORDER BY embedding <=> :qvec) AS r
  FROM geo_series
  WHERE organism_id @> ARRAY['NCBITaxon:9606']          -- filters apply here
  ORDER BY embedding <=> :qvec LIMIT 100
),
lexical AS (   -- pg_search BM25
  SELECT gse, ROW_NUMBER() OVER (ORDER BY pdb.score(gse) DESC) AS r
  FROM geo_series
  WHERE bm25_doc @@@ :qtext          -- and structured filters as needed
  LIMIT 100
)
SELECT gse, SUM(1.0/(60+r)) AS rrf
FROM (SELECT * FROM dense UNION ALL SELECT * FROM lexical) u
GROUP BY gse ORDER BY rrf DESC LIMIT 20;
```

Set `SET hnsw.iterative_scan = relaxed_order;` so filtered kNN doesn't under-return.

## Facet queries

- **Fast path (`pg_search`):** `pdb.agg` returns value→count buckets over the columnar index in a single pass — the primary facet mechanism.
- **Simple facet (portable):** `SELECT unnest(organism_id) v, COUNT(*) FROM (<result set>) GROUP BY v` — the `GROUP BY` fallback if `pg_search` is unavailable.
- **Disjunctive** ([[24-Faceted-Search#A. Disjunctive]]): compute each facet where its *own* predicate is omitted — `pdb.agg` per facet, or a small `GROUP BY` per facet.
- **Hierarchical roll-up** ([[24-Faceted-Search#B. Ontology-hierarchy]]): `GROUP BY unnest(ancestors)` — every record counts toward all its ancestors.

## Scale headroom

289k rows is small — HNSW and `pg_search` faceting are comfortable out of the box. `pgvectorscale` (StreamingDiskANN + SBQ) is the lever to pull only if/when you go **sample-level (8.6M)**. → [[40-Roadmap]]

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
- Timescale `pg_textsearch` (v1.0 GA; C; no faceting yet) — https://github.com/timescale/pg_textsearch · https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres
- pgvectorscale (StreamingDiskANN + SBQ) — https://github.com/timescale/pgvectorscale
- Alternatives — OpenSearch RRF — https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/ · Elasticsearch retrievers — https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever · Qdrant — https://qdrant.tech/documentation/search/hybrid-queries/ · Weaviate — https://docs.weaviate.io/weaviate/concepts/search/hybrid-search · Milvus — https://milvus.io/blog/introduce-milvus-2-5-full-text-search-powerful-metadata-filtering-and-more.md · Vespa — https://docs.vespa.ai/en/querying/grouping.html · Typesense — https://typesense.org/docs/30.2/api/search.html · LanceDB (no native facets) — https://github.com/lancedb/lancedb/issues/1348
