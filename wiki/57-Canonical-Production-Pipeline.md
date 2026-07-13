---
title: Canonical Production Pipeline
tags: [production, prefect, gemini, elasticsearch, runbook]
status: current
updated: 2026-07-12
---

# 57 · Canonical production pipeline

← [[Home]] · architecture: [[20-Architecture-Overview]] · ingestion details:
[[21-Ingestion-Pipeline]]

## Production contract

There is one canonical production pipeline:

```text
data/processed/soft_meta
  -> geo-soft-etl / Prefect
  -> data/processed/series_records
  -> Gemini gemini-embedding-2 Batch API
  -> gemini_embedding_2_3072_v1 artifact
  -> Elasticsearch geo-series
  -> document-count and embedding_gemini_3072 coverage audit
```

Production uses only `gemini_embedding_2_3072_v1`, with 3,072-dimensional
vectors stored in Elasticsearch as `embedding_gemini_3072`. BGE, MedCPT, and
Qwen are development/evaluation only. They are retained for comparison and
regression testing, but they are not production inputs, dependencies, active
models, or success criteria.

`geo-soft-etl` is the canonical orchestration command. A run succeeds only if
canonical record materialization, the Gemini artifact, Elasticsearch loading,
the final document count, and complete Gemini vector coverage all succeed.

## Files and durable state

Paths are relative to the repository root unless otherwise noted.

| Purpose | Location | Operator contract |
|---|---|---|
| Metadata-only stripped family SOFT | `data/processed/soft_meta/` | Prefect input; no expression tables |
| Canonical series records | `data/processed/series_records/<bucket>/<GSE>.json` | One atomic JSON document per GSE; existing files are skipped |
| Production artifact root | `data/processed/embedding_artifacts/` | Keep Gemini-only in a production deployment |
| Published Gemini artifact | `data/processed/embedding_artifacts/gemini_embedding_2_3072_v1/` | Complete aligned artifact and provider provenance |
| Canonical matrix | `.../vectors.npy` | C-contiguous finite `float32`, one 3,072-dimensional row per ID |
| Matrix row IDs | `.../ids.json` | Numeric-GSE-sorted row alignment for `vectors.npy` |
| Artifact metadata | `.../metadata.json` | Model, dimensions, usage bounds, encoded/reused counts, runtime |
| Batch request/result provenance | `.../gemini_requests-*.jsonl`, `.../gemini_results-*.jsonl`, `.../gemini_state.json` | Resumable provider job and result history; do not edit |
| Active/interrupted build workspace | `data/processed/embedding_artifacts/.gemini_embedding_2_3072_v1.tmp/` | Durable sync plan and Batch resume state; rerun the same command |
| Prefect terminal report | `data/processed/soft_etl_report.json` | Materialization, embedding, Elasticsearch, and coverage outcome |
| Standalone loader report | `data/processed/elasticsearch_load_report.json` | Bulk counts, failures, document count, and vector coverage |
| Local Elasticsearch data | Docker volume `geo_elasticsearch_data` | Persistent `geo-series` index; do not edit volume files directly |

Directories such as `.gemini_embedding_2_3072_v1.cancelled-*` or `.smoke*`
are quarantined development history, not resumable production state.

## Credentials and local Elasticsearch

The Google AI Studio Gemini key belongs in the ignored repository `.env` as
`GEMINI_API_KEY`. Elasticsearch settings belong in the ignored
`.env.elasticsearch`, created from the safe template:

```bash
cp .env.elasticsearch.example .env.elasticsearch
# Set ELASTICSEARCH_PASSWORD in .env.elasticsearch.
docker compose --env-file .env.elasticsearch \
  -f docker-compose.elasticsearch.yml up -d
docker compose --env-file .env.elasticsearch \
  -f docker-compose.elasticsearch.yml ps
```

The local service is Elasticsearch 9.4.2 on `127.0.0.1:9200`. A managed
deployment uses the same `ELASTICSEARCH_URL` plus username/password or API-key
settings; it does not depend on Docker.

## Run the canonical pipeline

The completed 2026-07-12 delta had a conservative upper bound of `$9.5460`, so
`$9.55` was sufficient for that frozen request inventory. The ceiling is an
authorization limit, not an expected invoice. If future new records make the
printed estimate larger, review it and deliberately choose a new ceiling; an
absent, non-finite, or insufficient value fails before client construction or
paid submission.

```bash
set -a
source .env
source .env.elasticsearch
set +a

uv run geo-soft-etl \
  --allow-paid-gemini \
  --gemini-max-cost-usd 9.55 \
  --gemini-concurrency 4
```

Gemini corpus documents use only the asynchronous Batch/file API. There is no
synchronous corpus-embedding fallback. Concurrency `4` means at most four
provider jobs are active while one local coordinator remains the only state
writer. Do not run two coordinators against the same artifact root.

The builder compares canonical IDs with the published IDs on every run. It
encodes only new or explicitly replaced rows, reuses unchanged matrix rows,
and removes deleted rows. If interrupted, rerun the exact command: the durable
sync plan, uploaded file IDs, job IDs, and downloaded result files prevent a
silent full rebuild or duplicate paid submission. If IDs already match, Gemini
returns `skipped` without provider work.

## Production artifact isolation

The Elasticsearch loader discovers every registered artifact present under
its configured artifact root. A production deployment must therefore copy or
mount only this directory:

```text
data/processed/embedding_artifacts/gemini_embedding_2_3072_v1/
```

Do not place BGE, MedCPT, or Qwen directories in the production artifact root.
The fixed production active model remains:

```bash
ELASTICSEARCH_ACTIVE_MODEL=gemini_embedding_2_3072_v1
```

## Validate the outputs

Inspect the two reports without exposing credentials:

```bash
uv run python -c \
  'import json; from pathlib import Path; print(json.loads(Path("data/processed/soft_etl_report.json").read_text()))'

uv run python -c \
  'import json; from pathlib import Path; print(json.loads(Path("data/processed/elasticsearch_load_report.json").read_text()))'
```

The required invariant is:

```text
canonical record count
  == Gemini artifact record_count
  == Elasticsearch document_count
  == embedding_gemini_3072 coverage
```

At the completed 2026-07-12 checkpoint:

| Check | Count |
|---|---:|
| Canonical GSE records | 288,904 |
| Gemini artifact rows | 288,904 |
| Elasticsearch documents | 288,904 |
| `embedding_gemini_3072` coverage | 288,904 |

The 39,168 new rows were submitted in 40 Batch shards; all 40 succeeded. The
published artifact reused the earlier 249,736 Gemini rows. Google omitted
per-row token counts, so actual cost remains a billing-console fact rather than
being falsely recorded as zero.

## Standalone repair commands

The canonical flow normally owns both operations. These commands are for an
explicit repair or diagnosis:

```bash
# Build/resume only the production Gemini artifact.
set -a; source .env; set +a
uv run python -m geo_index.build_embedding_artifact \
  --model-key gemini_embedding_2_3072_v1 \
  --allow-paid-gemini \
  --gemini-max-cost-usd 9.55 \
  --gemini-concurrency 4

# Reload all artifacts present in the production artifact root and write audit.
set -a; source .env.elasticsearch; set +a
uv run geo-elasticsearch-load \
  --records-root data/processed/series_records \
  --artifacts-root data/processed/embedding_artifacts \
  --report data/processed/elasticsearch_load_report.json
```

Do not pass a single development `--model-key` against the production
`geo-series` index: loader operations replace whole documents, so omitted vector
fields would be removed.

## Development embedding options

Build alternate models into a separate development/evaluation only root:

```bash
uv run python -m geo_index.build_embedding_artifact \
  --model-key bge_small_v15 \
  --output-root data/processed/embedding_artifacts-dev

uv run python -m geo_index.build_embedding_artifact \
  --model-key medcpt_v1 \
  --output-root data/processed/embedding_artifacts-dev

uv run python -m geo_index.build_embedding_artifact \
  --model-key qwen3_06b_1024_v1 \
  --output-root data/processed/embedding_artifacts-dev
```

Use those artifacts only with a disposable local development index/evaluation
workflow. They must not be copied into the production artifact root or selected
through `ELASTICSEARCH_ACTIVE_MODEL` in production. Historical model results
and comparison procedures remain in [[52-Embedding-Bakeoff-Runbook]].
