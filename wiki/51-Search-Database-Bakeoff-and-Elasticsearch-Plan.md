---
title: Search Database Bake-off and Elasticsearch Plan
tags: [elasticsearch, qdrant, postgres, search, facets, vectors, plan, v1]
status: approved-design
created: 2026-07-10
updated: 2026-07-11
---

# 51 · Search Database Bake-off and Elasticsearch Plan

> **Implemented primary cutover (2026-07-12):** Elasticsearch now owns the
> primary loader, search CLI, web path, filters, facets, and native RRF.
> Prefect builds `gemini_embedding_2_3072_v1` and audits its 3,072-dimensional
> `embedding_gemini_3072` field before success. PostgreSQL code is retained only
> for historical comparison.

← [[Home]] · supersedes the deployment choice in [[26-Datastore-Postgres]] ·
coordinates with [[52-Embedding-Bakeoff-Runbook]] and [[47-MCP-Server-Plan]]

> **Prototype scope update (2026-07-11):** Elasticsearch remains selected, but
> the first implementation is one **local** single-node Elasticsearch 9.4.2
> container and one canonical `geo-series` index. SOFT ETL and embedding
> persistence are the separate Prefect plan in
> [[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]]. Do not implement cloud
> provisioning, daily snapshots, versioned build directories, or alias rollback
> in this tranche; those are preserved in [[54-Incremental-Corpus-Future-State]].

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:writing-plans` before turning this design into implementation
> tasks, then `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` to execute them. Preserve the public search and
> MCP contracts unless a reviewed plan explicitly changes them.

## Decision

Use **one local Elasticsearch 9.4.2 container** as the only running
search/database service for the prototype. Load canonical per-GSE JSON records
and vectors from canonical model matrix artifacts into a fixed `geo-series`
index, using GSE as the Elasticsearch document `_id` so repeated loads are safe
upserts.

Keep clients configured only by `ELASTICSEARCH_URL` and credentials. Later, buy
a managed Elastic deployment and point the same scripts at it. Cloud
provisioning, networking, and production access control are outside the current
task. A local trial license may be used to exercise native RRF.

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
- restartable/idempotent loading into one canonical local index;
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
- the same standard client works against local Docker and a later managed host;
- BM25, kNN, filters, aggregations, and fusion share one supported
  search abstraction;
- GSE-keyed upserts match the prototype's append-mostly corpus.

If the product later adds user annotations, live relational writes, or other
transactional state, revisit a relational system of record. Do not force those
future requirements into this metadata-search spike.

## Target architecture

```text
stripped metadata SOFT files
             |
             v
Prefect ETL (existence-based, atomic per-GSE writes)
             |
             +--> data/processed/series_records/<bucket>/<GSE>.json
             |
             +--> data/processed/embedding_artifacts/<model_key>/
             |
             v
local Elasticsearch 9.4.2 index: geo-series
             |
             +--> idempotent GSE-keyed bulk upserts + validation
             |
             v
SearchService --> FastMCP --> invited users
```

Only Elasticsearch is a live search dependency. The record tree and NumPy
embedding artifacts are local files, not another online service.

## Prototype input contract

The ES loader consumes only:

- canonical JSON files under `data/processed/series_records/`;
- canonical `vectors.npy`/`ids.json`/`metadata.json` directories under
  `data/processed/embedding_artifacts/`;
- a fixed code registry mapping safe model keys to dimensions and ES fields.

It rejects malformed records, unknown model keys, nonfinite vectors, wrong
dimensions, and matrix/ID count mismatches. It does not own
SOFT parsing or embedding calls. Re-running the loader uses `gse` as `_id` and
therefore replaces the same document rather than creating a duplicate.

## Elasticsearch index design

Use one document per GSE. Initial mapping:

| Field | Mapping | Purpose |
|---|---|---|
| `gse` | `keyword` | exact lookup and stable tie-break |
| `title` | `text` plus `keyword` subfield | BM25 and display |
| `summary`, `overall_design` | `text` | display and optional lexical fields |
| `embed_text` | `text` | canonical lexical/document-composition input |
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
version containing the fixed mapping revision and active model configuration.

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

## Local index lifecycle

1. Start the pinned local single-node container bound to `127.0.0.1:9200` with
   persistent Docker storage.
2. Create `geo-series` with reviewed explicit settings/mappings if absent.
3. Stream canonical records and available vectors with the bulk API, using
   `gse` as `_id` and `index` actions for safe replacement.
4. Refresh once after the batch, then validate document count, required-field
   coverage, vector coverage, representative filters, exact lookup, and fixed
   retrieval smoke queries.
5. Re-running the command upserts records and leaves unrelated documents intact.
6. A full destructive local rebuild is a separate explicit command used only for
   mapping changes or test recovery.

Versioned indices and alias rollback are the future endpoint in
[[54-Incremental-Corpus-Future-State]], not prototype acceptance criteria.

## Security and operations

- Pin local Docker to Elasticsearch 9.4.2; never use `latest`.
- Bind port 9200 to localhost only and persist data in a named Docker volume.
- Keep the local password/API key in an ignored `.env`, never in artifacts.
- Give the loader write access to `geo-series`; use a read-only credential for
  the search/MCP service when that integration is exercised.
- Pin the Elasticsearch client compatibility range and record the server version
  in the loader report.
- Set bounded timeouts and result sizes; do not expose raw Elasticsearch queries
  through MCP.
- Do not log study text, bearer tokens, credentials, or full user queries.
- Do not add Elastic Cloud provisioning. Later managed deployment changes only
  URL/credentials and may add an alias/release policy.

## Implementation plan

### Task 1 — Consume the Prefect ETL boundary

- [ ] Treat [[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]] as a separate
  prerequisite and do not add SOFT parsing, Prefect, or encoder code to the ES
  modules.
- [ ] Add read-only iterators for canonical JSON records and model matrix/ID
  artifacts.
- [ ] Test malformed JSON, missing GSE, unknown model keys, wrong dimensions,
  nonfinite values, and missing embeddings without requiring Elasticsearch.
- [ ] Keep the Postgres path during parity testing; do not delete it early.

### Task 2 — Define and load the one local Elastic index

- [ ] Add `docker-compose.elasticsearch.yml` pinned to Elasticsearch 9.4.2 with
  localhost-only port binding, a persistent volume, and a health check.
- [ ] Add `src/geo_index/elasticsearch_index.py` containing reviewed settings,
  mappings, bulk-upsert serialization, validation, and an explicit local-reset
  command.
- [ ] Reject dynamic field names and unknown embedding variants.
- [ ] Add fake-client unit tests for mapping dimensions, GSE `_id`, partial bulk
  failures, retries, validation failure, and a no-duplicate second load.
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
- [ ] Add startup readiness checks for the `geo-series` index, active vector
  field, fixed registry configuration, and query encoder.

### Task 5 — Cut over only after comparative verification

- [ ] Load the canonical record tree and available embedding rows into local
  `geo-series`.
- [ ] Run exact lookup, filter/facet parity tests, and the fixed retrieval eval
  against both Postgres and Elasticsearch.
- [ ] Record relevance, ANN recall, p50/p95 latency, index size, and build time.
- [ ] Resolve material parity failures before switching the application default.
- [ ] After cutover, keep the Postgres path long enough to reproduce the recorded
  baseline; remove dependencies only in a separately reviewed cleanup.

## Acceptance criteria

- One local Elasticsearch 9.4.2 container is the only online datastore/search
  dependency.
- Re-running the loader upserts by GSE and creates no duplicate documents.
- ETL/embedding generation remains outside the Elasticsearch modules.
- All four planned embedding dimensions load and can be selected by fixed code
  registry.
- BM25, dense, hybrid, exact lookup, normalized filters, and disjunctive facets
  satisfy contract tests.
- Retrieval and MCP responses identify the fixed mapping revision and active
  model configuration.
- The fixed evaluation records Postgres-vs-Elastic parity before cutover.

## Revisit triggers

Reopen the datastore decision if any of these become real requirements:

- transactional user data or annotations;
- frequent partial updates where immutable rebuilds are too expensive;
- sample-level indexing at a scale that changes latency/cost materially;
- later managed Elastic cost dominates the prototype budget;
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
- [Local single-node Elasticsearch Docker](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-docker-basic)
- [Qdrant hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Qdrant full-text search](https://qdrant.tech/documentation/search/text-search/full-text-search/)
- [Qdrant payload and facets](https://qdrant.tech/documentation/concepts/payload/)
- [Qdrant quantization](https://qdrant.tech/documentation/guides/quantization/)
- [Weaviate hybrid search](https://docs.weaviate.io/weaviate/search/hybrid)
- [Weaviate aggregate API](https://docs.weaviate.io/weaviate/api/graphql/aggregate)
- [Vespa nearest-neighbor guide](https://docs.vespa.ai/en/querying/nearest-neighbor-search-guide.html)
- [Vespa grouping](https://docs.vespa.ai/en/grouping/)
- [Milvus overview](https://milvus.io/docs/overview.md)
