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
the canonical record schema and the embedding matrix-artifact contract. The Prefect
owner parses and orchestrates, the embedding owner encodes and persists vectors,
and the Elasticsearch owner only loads/searches canonical outputs.

## Prompt A — Prefect SOFT ETL and canonical records

```text
Please own the Prefect SOFT-to-canonical-record ETL pipeline for this repository.

Read these files in order:
1. wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md
2. wiki/21-Ingestion-Pipeline.md
3. wiki/52-Embedding-Bakeoff-Runbook.md only for the downstream JSON-record handoff
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

The ETL flow ends after atomically writing canonical JSON records and its latest
run report. Do not invoke embedding code from Prefect in this prototype. The
embedding coworker will independently read the completed JSON record tree after
this flow has produced the desired corpus.

Do not implement Elasticsearch code, snapshots, content hashes, vector delta
shards, source update detection, or versioned artifacts. Those belong to other
or future work.

Before handoff report:
- commits and files changed
- exact focused/full test results
- canonical record schema
- discovered/skipped/created/failed record counts
- second-run proof showing zero source parsing for completed work
- one-record deletion/rebuild proof
- remaining blockers or deviations
```

## Prompt B — canonical embedding artifacts and model execution

```text
Please own the code that builds canonical embedding artifacts from the JSON
records produced by coworker #1.

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
- src/geo_index/embedding_artifacts.py
- src/geo_index/embedding_local.py
- src/geo_index/embedding_gemini.py
- src/geo_index/build_embedding_artifact.py
- src/geo_index/adopt_embeddings.py

Write the code now, using synthetic JSON fixtures and fake encoders. Do not wait
for the real canonical JSON record tree to finish before implementing and
testing it. Run full real-model builds only after coworker #1 has produced the
desired JSON record set under:

data/processed/series_records/<GSE bucket>/<GSE>.json

Produce one canonical directory per model:

data/processed/embedding_artifacts/<model_key>/
  vectors.npy
  ids.json
  metadata.json

Expose this exact Prefect-neutral function:

build_embedding_artifact(
    records_root: Path,
    output_root: Path,
    model_key: str,
    *,
    allow_paid_gemini: bool,
) -> EmbeddingBuildResult

Do not import Prefect or Elasticsearch. Enumerate canonical GSE JSON records in
stable numeric-GSE order, keep one local encoder instance alive for all batches,
and write a finite float32 matrix with an exactly aligned ordered ID list.

Build in a temporary sibling directory. Publish the final model directory only
after matrix shape, dimension, finite-value, ID alignment, and metadata
validation succeed. If a valid final artifact already exists, skip the entire
model and perform zero encoder/API calls. Do not append new GSEs to an existing
matrix in this prototype; explicitly delete and rebuild that model artifact if
the canonical record inventory changes.

Implement the fixed BGE, MedCPT, Qwen, and Gemini variants from wiki/52. Gemini
uses full 3,072 dimensions and must require both allow_paid_gemini=True and
GEMINI_API_KEY before any network request.

Gemini document embeddings MUST use the Google batch API to receive batch
pricing. Do not send one synchronous embedding request per GSE. The Gemini
workflow must:

1. enumerate the sorted canonical JSON records;
2. generate deterministic batch-request JSONL using the frozen document wrapper;
3. print and record the estimated token count and batch cost;
4. require explicit paid-run approval before submission;
5. submit batch jobs and persist all provider file/job IDs in temporary state;
6. poll and resume existing jobs without resubmitting successful work;
7. download results and validate every response-to-GSE identity;
8. assemble the full 3,072-dimensional vectors.npy in ids.json order;
9. record actual usage, truncation, job IDs, SDK/API version, and cost estimate
   in metadata.json;
10. atomically publish the final artifact only after complete validation.

Synchronous Gemini embedding calls are reserved for the small live query set
after model selection, not corpus document generation.

Preserve the existing BGE and PubMedBERT NPY/ID files. Adopt the aligned BGE
matrix by copying it into the canonical BGE artifact directory without
recomputation or deletion. Do not call the old PubMedBERT artifact MedCPT; if
adopted at all, use an honest excluded legacy key.

Unit tests must use synthetic canonical JSON and fake encoders. They must prove:
- stable numeric-GSE ordering and exact ID/matrix alignment
- valid-existing-artifact skip behavior
- dimension, row-count, and finite-value validation
- partial failure leaves no published final directory
- temporary Gemini state resumes without duplicate submission
- paid Gemini guard before network I/O
- Gemini uses batch submission rather than per-document synchronous calls
- legacy BGE alignment/import without modifying source files

Do not implement Prefect orchestration, SOFT parsing, Elasticsearch loading,
SQLite storage, snapshot directories, versioned matrices, or content-hash delta
storage.

Before handoff report:
- commits and files changed
- exact focused/full test results
- registry and canonical artifact schemas
- matrix shape, ID count, and validation result by model
- BGE adoption status
- MedCPT/Qwen/Gemini build status and measured runtime/storage
- Gemini job IDs, usage, and estimated charge if paid work ran
- proof that a second call performs zero encoding for a completed artifact
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
- data/processed/embedding_artifacts/<model_key>/vectors.npy
- data/processed/embedding_artifacts/<model_key>/ids.json
- data/processed/embedding_artifacts/<model_key>/metadata.json
- the fixed embedding model registry

It must not parse SOFT, import Prefect, download models, call Gemini, mutate the
embedding artifacts, or depend on the old legacy NPY matrices directly.

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
