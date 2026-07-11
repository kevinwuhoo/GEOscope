---
title: Search Database Bake-off and Elasticsearch Plan
tags: [elasticsearch, qdrant, postgres, search, facets, vectors, plan, v1]
status: approved-design
created: 2026-07-10
updated: 2026-07-10
---

# 51 · Search Database Bake-off and Elasticsearch Plan

← [[Home]] · supersedes the deployment choice in [[26-Datastore-Postgres]] ·
coordinates with [[52-Embedding-Bakeoff-Runbook]] and [[47-MCP-Server-Plan]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:writing-plans` before turning this design into implementation
> tasks, then `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` to execute them. Preserve the public search and
> MCP contracts unless a reviewed plan explicitly changes them.

## Decision

Use **one managed Elasticsearch deployment** as the only running search/database
service for the prototype. Store the rebuildable source of truth as versioned
JSONL, embedding matrices, and manifests; build an immutable Elasticsearch index;
validate it; and atomically move the `geo-series-current` alias.

Use an Elastic Cloud tier that includes Elasticsearch's native reciprocal-rank
fusion (RRF). The paid tier costs more than hand-implementing fusion, but the
explicit priority for this prototype is technical simplicity rather than the
lowest infrastructure bill.

This replaces the current deployment direction of PostgreSQL + ParadeDB +
pgvector. The PostgreSQL implementation remains a valuable working baseline and
benchmark, not the target architecture to extend.

## What prompted the bake-off

The prototype now needs all of these in one understandable system:

- BM25 lexical retrieval;
- multiple dense embedding variants, including a 3,072-dimensional hosted model;
- hybrid fusion and filtered kNN;
- exact-match lookup by GSE accession;
- multi-valued normalized filters and useful facet counts;
- versioned, reproducible rebuilds without in-place partial state;
- a small read-only search boundary suitable for the remote MCP server.

PostgreSQL can implement the complete feature set, but 3,072-dimensional HNSW
needs a `halfvec` candidate-index/fp32-rerank workaround because pgvector's
`vector` HNSW indexes are limited to 2,000 dimensions. Qdrant can also implement
the feature set, but a Qdrant-only application would own more lexical and facet
orchestration. Elasticsearch provides the most direct single-service prototype.

## Options considered

| Candidate | What fit well | Main complication for this project | Verdict |
|---|---|---|---|
| PostgreSQL + ParadeDB + pgvector | Existing implementation, transactions, exact SQL facets, one familiar relational store | Extension/SQL complexity; 3,072-dimension HNSW workaround; managed-extension availability | Keep as measured baseline; do not extend as the deployment target |
| Qdrant only | Named dense vectors, BM25 sparse search, RRF, multi-stage retrieval, payload filters, facets, quantization/rescoring | The application must generate/version sparse lexical vectors and do more work to preserve current disjunctive facet semantics | Strong runner-up; reject for this simplicity-first prototype |
| **Elasticsearch only** | Native BM25/BM25F, dense vectors through 4,096 dimensions, filtered kNN, RRF, array keywords, aggregations, atomic aliases | Higher managed cost; not relational; native RRF is a paid feature | **Chosen** |
| Weaviate | BM25F, hybrid search, named vectors, filters, aggregations | Vector-scoped faceting has more caveats and less direct control over exact current semantics | Do not choose |
| Vespa | Excellent retrieval/ranking flexibility and grouping | More schema/operations learning; documented NN grouping-count limitations | Too complex for the spike |
| Milvus | Strong dense + BM25 hybrid retrieval | Less natural fit for exact faceting and a broader infrastructure surface | Do not choose |

### Why not Qdrant only?

Qdrant is technically viable and would be the alternative if vector-first
retrieval became the dominant product. It supports named vectors, sparse BM25
vectors, RRF, filters, exact facet counts, and quantization with rescoring.

For this application, however, BM25 and faceting are first-class rather than
secondary vector-database features. Elasticsearch owns text analysis, BM25,
vector search, fusion, and aggregations in one query system. That removes a
custom sparse-vector generation/versioning layer. The remaining facet adapter is
needed to preserve our particular API semantics, not to compensate for missing
database functionality.

### Why not keep PostgreSQL?

The working Postgres system proves the product concept and is still useful for
comparative evaluation. It is not wrong. The change is a prototype optimization:

- Elastic accepts all planned vector dimensions directly;
- managed Elastic removes extension packaging and upgrade questions;
- BM25, kNN, filters, aggregations, fusion, and alias swaps share one supported
  search abstraction;
- immutable snapshot rebuilds match the corpus better than transactional row
  mutation.

If the product later adds user annotations, live relational writes, or other
transactional state, revisit a relational system of record. Do not force those
future requirements into this metadata-search spike.

## Target architecture

```text
GEOmetadb / raw source artifacts
             |
             v
data/processed/geo_series.jsonl
             |
             v
pure normalization + enrichment
             |
             +--> normalized JSONL + normalization report
             |
             v
provider-neutral embedding builders
             |
             +--> vectors + manifests + ordered-GSE hashes
             |
             v
versioned Elasticsearch index (geo-series-YYYYMMDD-buildNN)
             |
             +--> validation and retrieval smoke tests
             |
             v
atomic alias swap: geo-series-current
             |
             v
SearchService --> FastMCP --> invited users
```

Only Elasticsearch is a live database/search dependency. JSONL, NumPy matrices,
and manifests are durable build artifacts, not a second online database.

## Canonical artifact contract

Every index build must be reproducible from immutable inputs. A build directory
contains:

- normalized series JSONL in stable GSE order;
- normalization report and normalizer version;
- one manifest per embedding variant;
- ordered-GSE SHA-256 and document-input SHA-256;
- vector dimension, dtype, row count, and matrix SHA-256;
- source snapshot identity and build timestamp;
- Elasticsearch mapping/settings hash;
- final index name and validation report.

The loader rejects mismatched row counts, ordered-ID hashes, nonfinite vectors,
wrong dimensions, incomplete manifests, or unknown model keys. A failed build
never changes the live alias.

## Elasticsearch index design

Use one document per GSE. Initial mapping:

| Field | Mapping | Purpose |
|---|---|---|
| `gse` | `keyword` | exact lookup and stable tie-break |
| `title` | `text` plus `keyword` subfield | BM25 and display |
| `summary`, `overall_design` | `text` | display and optional lexical fields |
| `embed_text` | `text` | frozen lexical/document-composition input |
| `organism_ids`, `sex_ids` | multi-valued `keyword` | filters and facets |
| `assay_categories`, `assay_labels` | multi-valued `keyword` | filters and facets |
| future tissue/disease/cell-type IDs | multi-valued `keyword` | added only after their normalization gates |
| `*_status` | `keyword` | mapped/ambiguous/unmapped reporting |
| `n_samples` | `integer` | filters/display |
| source/update dates | `date` | filters/display |
| `embedding_bge_384` | `dense_vector`, 384 dims | current baseline |
| `embedding_medcpt_768` | `dense_vector`, 768 dims | biomedical candidate |
| `embedding_qwen3_06b_1024` | `dense_vector`, 1,024 dims | local general candidate |
| `embedding_gemini_3072` | `dense_vector`, 3,072 dims | hosted candidate |

Use a single primary shard for the 222,961-series prototype unless measurement
proves it inadequate. This keeps aggregation and ranking behavior easy to reason
about. Replica count is a deployment availability choice.

Configure vector index options explicitly rather than accepting a default that
may change with Elasticsearch versions. For Gemini, start with quality-first
`int8_hnsw`; Elasticsearch retains original float vectors for rescoring. Tune
`num_candidates` and rescore oversampling against measured exact-search recall.
Do not choose these values from model dimension alone.

## Retrieval contract

Keep the current public modes and Pydantic response types:

- `bm25`: native Elastic lexical query over the frozen text fields;
- `dense`: the active variant's query encoder plus filtered kNN;
- `hybrid`: native RRF over BM25 and dense retrievers;
- exact GSE lookup: term query on `gse`;
- deterministic final tie-break: ascending `gse` after the primary score/order.

The active model remains deployment configuration. Search and MCP callers do not
select arbitrary vector fields or models. Responses include a stable retrieval
version containing the index build and active embedding manifest identity.

For the bakeoff, use the same candidate depths and filters for comparable runs.
Measure native RRF against the existing application RRF once, then freeze the
Elastic profile used by the official evaluation.

## Facet semantics that must not regress

Current filters use **OR within a facet and AND across facets**. Facet counts are
disjunctive: when counting a facet, apply all filters except that facet's own
filter. Keyword arrays count a GSE once per value.

The current API has two important modes:

1. With a blank query, counts cover all documents matching the remaining
   filters.
2. With a text/vector query, counts cover a bounded retrieval pool (currently
   1,000 candidates), not the entire corpus.

Do not replace this silently with one global aggregation over the final top-k.
The initial Elastic adapter should issue one bounded retrieval per requested
facet while omitting that facet's own filter, then count values from returned
keyword arrays. Native `filter`/`terms` aggregations may replace the client-side
count only where tests prove identical semantics. Keep a frozen alphabetical
secondary order for equal counts.

## Rebuild and alias lifecycle

1. Create a new immutable index name; never bulk-load into
   `geo-series-current`.
2. Apply reviewed settings and explicit mappings.
3. Stream normalized documents and all ready vector variants with the bulk API.
4. Refresh once after the load, then validate document count, required-field
   coverage, vector coverage, representative filters, exact lookup, and fixed
   retrieval smoke queries.
5. Write the validation report into the build artifacts.
6. Atomically remove the alias from the old index and add it to the new index in
   one aliases request.
7. Retain at least the immediately previous index for rollback during the spike.

Rollback is an alias swap, not an in-place data repair.

## Security and operations

- Use managed Elastic Cloud for the prototype.
- Give the loader a write-capable credential scoped to build indices and alias
  management; do not give it to the MCP service.
- Give the MCP/search service a read-only credential scoped to
  `geo-series-current`.
- Keep credentials in environment/secret management and never in artifacts.
- Pin the Elasticsearch client compatibility range and record the server version
  in every build report.
- Set bounded timeouts and result sizes; do not expose raw Elasticsearch queries
  through MCP.
- Do not log study text, bearer tokens, credentials, or full user queries.

## Implementation plan

### Task 1 — Split normalization from database mutation

- [ ] Add `src/geo_index/normalize_artifacts.py` to stream the canonical input,
  call the existing pure `normalize_row`, and atomically write normalized JSONL
  plus a report.
- [ ] Add unit tests for stable ordering, deterministic output, atomic failure,
  and normalization counts.
- [ ] Keep the Postgres normalization commands during migration; label them
  baseline-only rather than deleting them early.
- [ ] Verification: `pytest -q tests/test_normalize_artifacts.py tests/test_normalize.py`.

### Task 2 — Define and load versioned Elastic indices

- [ ] Add `src/geo_index/elasticsearch_index.py` containing reviewed settings,
  mappings, index-name validation, bulk serialization, validation, and alias swap.
- [ ] Reject dynamic field names and unknown embedding variants.
- [ ] Add fake-client unit tests for mapping dimensions, partial bulk failures,
  manifest/hash mismatches, validation failure, and atomic alias requests.
- [ ] Add an opt-in live test behind `GEO_TEST_ELASTIC=1`.

### Task 3 — Implement the backend-neutral search adapter

- [ ] Add `src/geo_index/elasticsearch_search.py` implementing the existing
  `SearchService` behavior for exact, BM25, dense, hybrid, filters, and facets.
- [ ] Retain the current response models and validation bounds.
- [ ] Encode only the deployment-selected query variant.
- [ ] Test OR-within/AND-across filters and own-filter omission for every facet.
- [ ] Test deterministic ordering and retrieval-version provenance.

### Task 4 — Migrate the web and remote MCP composition roots

- [ ] Move backend construction behind environment configuration; imports must
  not perform network/model I/O.
- [ ] Reuse the FastMCP authentication, bounded models, and transport design from
  the `codex/remote-mcp-first-draft` branch, but replace its PostgreSQL search
  service with the Elastic adapter.
- [ ] Keep exactly `search_datasets`, `get_dataset`, and `facet_values` in v1.
- [ ] Add startup readiness checks for the current alias, active vector field,
  manifest identity, and query encoder.

### Task 5 — Cut over only after comparative verification

- [ ] Load the same frozen corpus into a candidate Elastic index.
- [ ] Run exact lookup, filter/facet parity tests, and the fixed retrieval eval
  against both Postgres and Elasticsearch.
- [ ] Record relevance, ANN recall, p50/p95 latency, index size, and build time.
- [ ] Resolve material parity failures before switching the application default.
- [ ] After cutover, keep the Postgres path long enough to reproduce the recorded
  baseline; remove dependencies only in a separately reviewed cleanup.

## Acceptance criteria

- One managed Elasticsearch deployment is the only online datastore/search
  dependency.
- A clean build from canonical artifacts creates a new versioned index without
  mutating the live alias.
- Validation failure cannot change `geo-series-current`.
- All four planned embedding dimensions load and can be selected by fixed code
  registry.
- BM25, dense, hybrid, exact lookup, normalized filters, and disjunctive facets
  satisfy contract tests.
- Retrieval and MCP responses identify the index and embedding build used.
- The fixed evaluation records Postgres-vs-Elastic parity before cutover.

## Revisit triggers

Reopen the datastore decision if any of these become real requirements:

- transactional user data or annotations;
- frequent partial updates where immutable rebuilds are too expensive;
- sample-level indexing at a scale that changes latency/cost materially;
- managed Elastic cost dominates the prototype budget;
- native RRF licensing or hosted constraints become unacceptable;
- measured Qdrant-only operation is materially simpler for the actual workload.

## Primary references

- [pgvector supported types and index limits](https://github.com/pgvector/pgvector#hnsw)
- [Elasticsearch dense vectors](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/dense-vector)
- [Elasticsearch kNN and vector rescoring](https://www.elastic.co/docs/solutions/search/vector/knn)
- [Elasticsearch RRF retriever](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever)
- [Elastic subscription features](https://www.elastic.co/subscriptions)
- [Elasticsearch arrays](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/array)
- [Elasticsearch terms aggregation](https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-terms-aggregation)
- [Elasticsearch filter aggregation](https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-filter-aggregation)
- [Elasticsearch aliases](https://www.elastic.co/guide/en/elasticsearch/reference/current/aliases.html)
- [Qdrant hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Qdrant full-text search](https://qdrant.tech/documentation/search/text-search/full-text-search/)
- [Qdrant payload and facets](https://qdrant.tech/documentation/concepts/payload/)
- [Qdrant quantization](https://qdrant.tech/documentation/guides/quantization/)
- [Weaviate hybrid search](https://docs.weaviate.io/weaviate/search/hybrid)
- [Weaviate aggregate API](https://docs.weaviate.io/weaviate/api/graphql/aggregate)
- [Vespa nearest-neighbor guide](https://docs.vespa.ai/en/querying/nearest-neighbor-search-guide.html)
- [Vespa grouping](https://docs.vespa.ai/en/grouping/)
- [Milvus overview](https://milvus.io/docs/overview.md)
