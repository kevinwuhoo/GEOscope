---
title: Prefect, Embedding, and Local Elasticsearch Coworker Prompts
tags: [handoff, prompts, prefect, soft, embeddings, elasticsearch]
status: ready-to-send
created: 2026-07-11
updated: 2026-07-11
---

# 55 · Prefect and Local Elasticsearch Coworker Prompts

← [[Home]] · replaces the active ETL/embedding/search handoffs in
[[50-Coworker-Handoff-Prompts]] · plans:
[[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]] and
[[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]]

These three owners can work in parallel after agreeing on two small interfaces:
the canonical record schema and the embedding builder/store API. The Prefect
owner parses and orchestrates, the embedding owner encodes and persists vectors,
and the Elasticsearch owner only loads/searches canonical outputs.

## Prompt A — Prefect SOFT ETL and canonical records

```text
Please own the Prefect SOFT-to-canonical-record ETL pipeline for this repository.

Read these files in order:
1. wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md
2. wiki/21-Ingestion-Pipeline.md
3. wiki/52-Embedding-Bakeoff-Runbook.md only for the embedding integration API
4. wiki/54-Incremental-Corpus-Future-State.md only to understand what is
   explicitly deferred

Treat wiki/53 as the implementation source of truth. Work on your own branch,
use tests before implementation, and make small reviewable commits.

The current inputs are metadata-only family SOFT files under
data/processed/soft_meta. Build one canonical JSON output per GSE at:

data/processed/series_records/<GSE bucket>/<GSE>.json

Preserve complete structured series, platform, and per-sample metadata attribute
maps in each canonical record, plus deterministic GSE-level aggregates. Apply
the existing organism, sex, and assay normalizers during materialization, but
keep embed_text based on raw narrative fields.

Existence is the prototype state machine. If a record exists, skip it without
opening either the output or its source SOFT file. Missing records are parsed
in bounded Prefect batches and published atomically. A malformed source must
leave no final record. Deleting a canonical record is the explicit way to force
recomputation; do not add mtime/hash/update detection.

Use Prefect 3 locally with a bounded ThreadPoolTaskRunner. Submit parse batches
at flow level and resolve every future. Do not create one Prefect task per GSE,
use Prefect Cloud, add production scheduling, or make the local Prefect server a
hard requirement. The flow must also run directly as a normal CLI. The local
server/UI is optional observability.

The completed flow calls this exact embedding-owner interface after all parse
futures resolve:

build_missing_embeddings(
    records_root: Path,
    store_path: Path,
    model_key: str,
    *,
    replace_gses: AbstractSet[str],
    allow_paid_gemini: bool,
) -> EmbeddingBuildResult

Pass the GSEs created during this run as replace_gses so explicitly deleted and
rebuilt records receive replacement vectors. While the embedding branch is not
landed, test this call with a fake and keep orchestration independent of encoder
internals. Do not implement the real embedding store or model adapters yourself.

Do not implement Elasticsearch code, snapshots, content hashes, vector delta
shards, source update detection, or versioned artifacts. Those belong to other
or future work.

Before handoff report:
- commits and files changed
- exact focused/full test results
- record schema and SQLite schema
- discovered/skipped/created/failed record counts
- proof that created GSEs are passed to the embedding integration as replace_gses
- second-run proof showing zero source parsing for completed work
- one-record deletion/rebuild proof
- remaining blockers or deviations
```

## Prompt B — canonical embedding store and model execution

```text
Please own canonical embedding persistence and model execution for this
repository.

Read these files in order:
1. wiki/52-Embedding-Bakeoff-Runbook.md
2. wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md, especially Tasks
   3, 4, and 6
3. wiki/46-Retrieval-Evaluation-Plan.md
4. wiki/54-Incremental-Corpus-Future-State.md only to understand what is
   explicitly deferred

Work on your own branch, use tests before implementation, and make small
reviewable commits. Coordinate ownership of pyproject.toml and uv.lock with the
Prefect owner; do not independently overwrite their dependency changes.

Own these modules and their tests:
- src/geo_index/embedding_registry.py
- src/geo_index/embedding_store.py
- src/geo_index/embedding_local.py
- src/geo_index/embedding_gemini.py
- src/geo_index/embed_missing.py
- src/geo_index/adopt_embeddings.py

Persist all new vectors in one canonical local file:

data/processed/series_embeddings.sqlite

Implement the exact schema and validation in wiki/53. There is one canonical
model configuration per model_key and one row per (gse, model_key). Existing
rows are skipped. GSEs passed in replace_gses are recomputed and replaced even
when a row already exists. Registry configuration mismatches fail explicitly;
do not retain multiple revisions or silently mix them under one key.

Expose this exact Prefect-neutral function:

build_missing_embeddings(
    records_root: Path,
    store_path: Path,
    model_key: str,
    *,
    replace_gses: AbstractSet[str],
    allow_paid_gemini: bool,
) -> EmbeddingBuildResult

Do not import Prefect or Elasticsearch in embedding modules. Read canonical GSE
JSON records, keep one local encoder instance alive for all batches of a model,
write little-endian finite float32 vectors in bounded SQLite transactions, and
leave successfully completed rows reusable after a partial failure.

Implement the fixed BGE, MedCPT, Qwen, and Gemini variants from wiki/52. Gemini
uses full 3,072 dimensions and must require both allow_paid_gemini=True and
GEMINI_API_KEY before any network request. Print and record a missing-row token
and cost estimate before submission. Preserve provider job IDs, retry only
failed/missing GSEs, and validate response identity before writing rows.

Preserve the existing BGE and PubMedBERT NPY/ID files. Import the aligned BGE
matrix into SQLite without recomputation or deletion. Do not call the old
PubMedBERT artifact MedCPT; if imported at all, use an honest excluded legacy
key.

Unit tests must use fake encoders and a temporary SQLite file. They must prove:
- primary-key idempotence and missing-row discovery
- replace_gses replacement behavior
- registry mismatch rejection
- dimension, BLOB-length, and finite-value validation
- partial failure/resume behavior
- paid Gemini guard before network I/O
- legacy BGE alignment/import without modifying source files

Do not implement Prefect orchestration, SOFT parsing, Elasticsearch loading,
snapshot directories, versioned matrices, or content-hash delta storage.

Before handoff report:
- commits and files changed
- exact focused/full test results
- registry and SQLite schemas
- inserted/skipped/replaced counts by model
- BGE adoption status
- MedCPT/Qwen/Gemini build status and measured runtime/storage
- Gemini job IDs, usage, and estimated charge if paid work ran
- proof that a second call performs zero encoding for completed rows
- remaining blockers or deviations
```

## Prompt C — local Elasticsearch loader and search adapter

```text
Please own the local Elasticsearch foundation for this repository.

Read these files in order:
1. wiki/51-Search-Database-Bakeoff-and-Elasticsearch-Plan.md
2. wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md only for its output
   contracts
3. wiki/45-Normalized-Filters-and-Facets-Plan.md
4. wiki/46-Retrieval-Evaluation-Plan.md
5. wiki/54-Incremental-Corpus-Future-State.md only to understand what is
   explicitly deferred

Treat the 2026-07-11 prototype update in wiki/51 as the implementation source
of truth. Work on your own branch, use tests before implementation, and make
small reviewable commits.

Set up one local single-node Elasticsearch 9.4.2 container for the demo. Bind
9200 to localhost, persist its data in a Docker volume, keep credentials in an
ignored environment file, and add a health check. Do not provision Elastic
Cloud, Terraform, networking, DNS, or production authentication. Every client
must connect only through ELASTICSEARCH_URL plus credentials so the same scripts
can later point at a managed deployment.

Use one fixed canonical index named geo-series. Do not implement dated index
names, daily snapshots, aliases, or rollback releases. Use the GSE accession as
the Elasticsearch document _id and bulk `index` actions so repeated loads are
safe upserts and never create duplicates.

The loader consumes only:
- data/processed/series_records/<bucket>/<GSE>.json
- data/processed/series_embeddings.sqlite
- the fixed embedding model registry

It must not parse SOFT, import Prefect, download models, call Gemini, mutate the
SQLite store, or depend on the old NPY matrices directly.

Implement explicit mappings for BM25 text, exact GSE lookup, normalized keyword
arrays, dates/numeric fields, and the four dense-vector dimensions. Implement
the backend-neutral SearchService behavior for exact lookup, BM25, filtered
dense retrieval, native RRF hybrid retrieval, and disjunctive facets.

Preserve these contracts:
- OR within one facet and AND across different facets
- omit a facet's own filter when computing its counts
- blank-query counts cover all matching documents
- search-query counts use the bounded retrieval pool
- stable GSE secondary ordering
- one deployment-selected active model, never a public model selector

Use fake-client unit tests for mappings, bulk retry/error accounting, GSE _id,
wrong dimensions, nonfinite vectors, a no-duplicate second load, filters,
facets, ordering, and provenance. Put real container tests behind
GEO_TEST_ELASTIC=1.

Do not delete the PostgreSQL implementation. Use it as a parity baseline. Do
not implement managed hosting, remote MCP deployment, snapshots, vector
generation, or versioned alias releases.

Before handoff report:
- commits and files changed
- exact focused/full/live test results
- Docker/container version and health
- mapping/settings and vector dimensions
- loaded document/vector coverage counts
- second-load no-duplicate proof
- BM25/dense/hybrid/filter/facet smoke results
- Postgres parity observations
- the exact URL/credential configuration later used for managed Elasticsearch
- remaining blockers or deviations
```
