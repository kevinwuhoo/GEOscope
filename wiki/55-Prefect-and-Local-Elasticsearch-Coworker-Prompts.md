---
title: Prefect and Local Elasticsearch Coworker Prompts
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

These two owners can work in parallel after agreeing on the canonical record
schema and SQLite embedding-store read interface. The Prefect owner never writes
to Elasticsearch. The Elasticsearch owner never parses SOFT or calls an
embedding model.

## Prompt A — Prefect SOFT ETL and canonical embeddings

```text
Please own the Prefect SOFT ETL and embedding pipeline for this repository.

Read these files in order:
1. wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md
2. wiki/21-Ingestion-Pipeline.md
3. wiki/52-Embedding-Bakeoff-Runbook.md
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

After parsing, fill the one canonical local SQLite embedding store:

data/processed/series_embeddings.sqlite

It contains one canonical row per (gse, model_key). Existing rows are skipped.
If the current flow rebuilt a deleted record, replace that GSE's configured
embedding rows. Adding a model fills its missing rows. A model configuration
mismatch must fail and require an explicit model delete/rebuild; do not retain
multiple revisions.

Use Prefect 3 locally with a bounded ThreadPoolTaskRunner. Submit parse batches
at flow level and resolve every future. Do not create one Prefect task per GSE,
use Prefect Cloud, add production scheduling, or make the local Prefect server a
hard requirement. The flow must also run directly as a normal CLI. The local
server/UI is optional observability.

Preserve the existing BGE and PubMedBERT NPY/ID files. Import the aligned BGE
matrix into SQLite without recomputation or deletion. Do not call the old
PubMedBERT artifact MedCPT.

Implement model-neutral missing-row generation for BGE, MedCPT, Qwen, and
Gemini as specified in wiki/52. Gemini requires the explicit paid-run flag and
GEMINI_API_KEY before any network call. Unit tests use fake encoders and must
not download models or call Google.

Do not implement Elasticsearch code, snapshots, content hashes, vector delta
shards, source update detection, or versioned artifacts. Those belong to other
or future work.

Before handoff report:
- commits and files changed
- exact focused/full test results
- record schema and SQLite schema
- discovered/skipped/created/failed record counts
- embedding inserted/skipped/replaced counts by model
- BGE adoption status
- paid Gemini work actually submitted, if any, including job IDs and usage
- second-run proof showing zero parsing/embedding for completed work
- one-record deletion/rebuild proof
- remaining blockers or deviations
```

## Prompt B — local Elasticsearch loader and search adapter

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
