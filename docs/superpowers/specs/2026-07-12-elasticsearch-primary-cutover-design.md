# Elasticsearch Primary Cutover Design

**Status:** Approved 2026-07-12

## Goal

Make Elasticsearch the only primary indexing and retrieval backend. The canonical Prefect pipeline must produce Gemini `gemini-embedding-2` 3,072-dimensional artifacts and load them into Elasticsearch before a run is successful. Retain the existing PostgreSQL implementation as historical comparison code, but remove it from every default runtime path and current-state runbook.

## Architecture

The required primary flow is:

```text
stripped SOFT -> canonical GSE records -> Gemini embedding artifact
              -> Elasticsearch bulk upsert -> index audit -> search/web clients
```

Canonical JSON records and embedding artifacts remain durable handoff boundaries. Elasticsearch is the only online datastore and search dependency. PostgreSQL modules and tests remain available for historical evaluation, but primary commands do not import or connect to them.

Gemini is required because the selected 3,072-dimensional vectors exceed PostgreSQL pgvector's 2,000-dimension `vector` limit. The fixed primary model key is `gemini_embedding_2_3072_v1`, mapped to Elasticsearch field `embedding_gemini_3072`. `ELASTICSEARCH_ACTIVE_MODEL` defaults to this key while retaining an explicit override for evaluation.

## Prefect pipeline

`geo-soft-etl` remains the canonical orchestration entry point. After bounded record materialization it builds or resumes the Gemini artifact with paid-provider use explicitly authorized by an operator flag. It then creates an Elasticsearch client from environment-only settings, calls the existing idempotent `load_index()` boundary, audits document and vector coverage, closes the client, and writes one terminal ETL report.

The flow is fail-closed:

- record failures, embedding failures, Elasticsearch connection failures, bulk item failures, or audit failures make `EtlReport.succeeded` false and the CLI exit nonzero;
- completed canonical records and valid embedding artifacts remain in place for safe retry;
- Elasticsearch uses stable GSE document IDs, so a retry is an idempotent upsert;
- credentials are never accepted as CLI flags or written to reports;
- report fields include embedding status/error plus Elasticsearch status/error, attempted/succeeded/retried counts, document count, and Gemini vector coverage.

The flow accepts explicit records/artifacts roots, Elasticsearch batch size, item retry count, and `--allow-paid-gemini`. Paid Gemini submission is never enabled implicitly.

## Primary retrieval paths

The default search CLI and HTTP demo construct `ElasticsearchSettings`, an official Elasticsearch client, and a Gemini query encoder. They pass the encoder to `ElasticsearchSearchService`, whose closed API continues to provide BM25, dense, hybrid RRF, normalized filters, disjunctive facets, and get-by-accession.

BM25 does not need to initialize the query encoder. Dense and hybrid requests lazily initialize the Gemini encoder. Long-lived server resources are closed on shutdown, and one-shot CLI resources are closed in `finally` blocks.

The existing brute-force `search_test.py` and `pg_hybrid.py` are retained but renamed or described as explicit legacy/evaluation commands. `geo-search` becomes the Elasticsearch primary CLI. The web UI copy and module documentation no longer describe PostgreSQL as the local backend.

## Documentation cutover

README setup and runbooks describe the primary sequence: configure Elasticsearch, run the Prefect flow with paid Gemini authorization, then run Elasticsearch search/web commands. PostgreSQL rebuild instructions move under a clearly marked historical baseline section.

Current-state wiki pages—Home, overview, architecture, ingestion, search, facets, roadmap, glossary, and build log—describe Elasticsearch plus Gemini as primary. Historical PostgreSQL design and implementation-plan pages remain intact but receive a prominent superseded/historical marker where needed. Documentation must not claim that PostgreSQL is the current primary datastore, that a primary flow ends before Elasticsearch loading, or that BGE is the default active model.

## Testing

Offline tests use fakes and prove:

- the Prefect flow requests Gemini artifacts, requires explicit paid authorization, indexes only after embedding succeeds, propagates Elasticsearch failures, records load metrics, and closes clients;
- Elasticsearch settings default to Gemini 3,072 dimensions;
- the primary CLI and web path route BM25/dense/hybrid search through `ElasticsearchSearchService` without importing PostgreSQL;
- resource cleanup and nonzero exit behavior are deterministic;
- documentation consistency checks reject current-state PostgreSQL-primary or BGE-default claims.

The full offline suite must pass without Elasticsearch, PostgreSQL, model downloads, or provider calls. Existing opt-in Elasticsearch integration tests remain the live verification path. PostgreSQL tests remain supported but are not part of primary-path acceptance.

## Scope

This cutover does not delete PostgreSQL code, migrate historical evaluation reports, add managed-host provisioning, implement versioned Elasticsearch aliases, or change the canonical record schema. Those are independent follow-ups.
