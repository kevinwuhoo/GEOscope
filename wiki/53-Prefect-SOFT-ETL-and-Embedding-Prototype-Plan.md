---
title: Prefect SOFT ETL and Embedding Prototype Plan
tags: [prefect, soft, etl, embeddings, json, sqlite, plan, v1]
status: approved-plan
created: 2026-07-11
updated: 2026-07-11
---

# 53 · Prefect SOFT ETL and Embedding Prototype Plan

← [[Home]] · implements the current path in [[21-Ingestion-Pipeline]] · feeds
[[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]] · model details in
[[52-Embedding-Bakeoff-Runbook]] · future endpoint in
[[54-Incremental-Corpus-Future-State]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn every available stripped GEO family SOFT file into one canonical
GSE JSON record, then compute each configured missing embedding exactly once and
persist it locally for later Elasticsearch loading.

**Architecture:** A Prefect 3 flow inventories stripped SOFT files and derived
record files. A derived file's existence means that GSE is complete and must not
be opened or parsed again. Missing records are built in bounded batches with
atomic writes; the flow then fills missing rows in one local SQLite embedding
store. There are no dated snapshots, content-addressed versions, daily matrix
deltas, or automatic source-update detection in the prototype.

**Tech Stack:** Python 3.11+, Prefect 3 (`prefect>=3,<4`), stdlib gzip/JSON/XML-
free SOFT parsing, NumPy float32 vectors, SQLite, existing local/Hugging Face
encoders, Google GenAI for the explicitly approved Gemini batch run, pytest.

## Global constraints

- The input root is `data/processed/soft_meta/`; never parse expression or
  platform data tables from `data/raw/soft/` for this workflow.
- The canonical record path is
  `data/processed/series_records/<GSE bucket>/<GSE>.json`.
- If the canonical record exists, the flow skips it without opening either the
  output or its source SOFT file.
- A record is complete only after an atomic temporary-file + `os.replace` write.
- Delete the canonical record to force a rebuild. The next flow run rebuilds it
  and replaces that GSE's configured embeddings.
- Do not compare source mtimes, compute source/content hashes, or detect upstream
  changes in v1.
- Do not create snapshot directories or retain multiple record versions.
- Persist embeddings in the one canonical local file
  `data/processed/series_embeddings.sqlite`; do not put float arrays in JSON.
- One row per `(gse, model_key)` is canonical. Adding a new model fills missing
  rows; changing an existing model definition requires an explicit delete/rebuild.
- Preserve the existing BGE and PubMedBERT `.npy`/ID files; importing them must
  not overwrite or delete the originals.
- Prefect coordinates work and records failures; filesystem/SQLite existence,
  not Prefect result caching, defines idempotence.
- Do not submit paid Gemini work unless the explicit paid-run flag is present.
- Unit tests must use tiny synthetic SOFT fixtures and fake encoders. They must
  not download models, call Google, require Prefect Cloud, or start Elasticsearch.
- Run the flow manually first. Daily scheduling is optional after the manual
  full run succeeds and is not required for this prototype tranche.

---

## Why this is intentionally simpler

The longer-term design uses record hashes, embedding-text hashes, immutable
daily manifests, reusable vector deltas, and versioned Elasticsearch indices.
That is the correct endpoint for update-aware production operation, and it is
recorded in [[54-Incremental-Corpus-Future-State]]. It is not necessary to prove
the prototype.

For v1, existence is the state machine:

| State | Meaning | Next run |
|---|---|---|
| SOFT exists, record missing | Never processed or explicitly invalidated | Parse and atomically write record |
| SOFT exists, record exists | Complete | Skip without reading source or output |
| Record exists, embedding row missing | Model not run for this GSE | Compute and insert vector |
| Record rebuilt during this run | Explicit invalidation | Replace configured embeddings for this GSE |
| Record and embedding rows exist | Complete | Do nothing |

This deliberately does **not** notice that NCBI replaced a SOFT file after the
record was built. The operator must delete the derived record when a refresh is
desired. That limitation is visible, predictable, and acceptable for the demo.

## Current inputs

At the plan checkpoint on 2026-07-11, the shared workspace contained 244,186
stripped metadata SOFT files (about 1.6 GB) and 51,435 retained raw family files
(about 146 GB). The crawler may continue adding stripped files while this plan
is implemented. Each run inventories the input root once at its start; files
that arrive later wait for the next run.

The existing `geo-build-series-docs` command is not this pipeline: it reads
GEOmetadb SQLite. Reuse its neutral `compose_embed_text` behavior where useful,
but do not route SOFT parsing through GEOmetadb.

## Locked data layout

```text
data/
  processed/
    soft_meta/                         # existing metadata-only family SOFT
      GSE271nnn/GSE271800_family.soft.gz
    series_records/                    # one canonical parsed record per GSE
      GSE271nnn/GSE271800.json
    series_embeddings.sqlite           # all canonical model/GSE vectors
    soft_etl_report.json                # overwritten summary of the latest run

  processed/embeddings.npy             # existing BGE legacy artifact; preserved
  processed/embeddings.ids.json
  processed/embeddings_pubmedbert.npy   # existing non-MedCPT artifact; preserved
  processed/embeddings_pubmedbert.ids.json
```

The record is the shared metadata representation for embeddings and
Elasticsearch. The embedder reads `gse`, `title`, and `embed_text`; the local ES
loader reads the entire record and looks up vectors by `(gse, model_key)`. There
is no separate embedding JSONL or Elasticsearch JSONL.

## Canonical record schema

Each JSON object has these top-level keys. Arrays are distinct and sorted so
the output is deterministic.

```json
{
  "schema_version": 1,
  "gse": "GSE271800",
  "source_soft": "GSE271nnn/GSE271800_family.soft.gz",
  "title": "...",
  "summary": "...",
  "overall_design": "...",
  "type": ["Expression profiling by high throughput sequencing"],
  "pubmed_ids": ["12345678"],
  "submission_date": "2024-01-01",
  "last_update_date": "2024-01-02",
  "platform_ids": ["GPL24676"],
  "n_samples": 12,
  "organisms": ["Homo sapiens"],
  "molecules": ["total RNA"],
  "source_names": ["peripheral blood"],
  "characteristics": [
    {"name": "disease", "values": ["control", "asthma"]}
  ],
  "library_strategies": ["RNA-Seq"],
  "library_sources": ["TRANSCRIPTOMIC"],
  "library_selections": ["cDNA"],
  "organism_ids": ["NCBITaxon:9606"],
  "organism_status": "mapped",
  "sex_ids": ["PATO:0000383"],
  "sex_status": "mapped",
  "assay_categories": ["transcriptomic"],
  "assay_labels": ["RNA-seq"],
  "assay_status": "mapped",
  "sample_titles": ["..."],
  "sample_accessions": ["GSM123"],
  "series_attributes": {
    "Series_relation": ["BioProject: https://..."],
    "Series_supplementary_file": ["ftp://..."]
  },
  "platforms": [
    {
      "gpl": "GPL24676",
      "attributes": {"Platform_title": ["..."]}
    }
  ],
  "samples": [
    {
      "gsm": "GSM123",
      "title": "...",
      "source_name": "peripheral blood",
      "organism": "Homo sapiens",
      "molecule": "total RNA",
      "characteristics": [
        {"name": "disease", "value": "asthma", "raw": "disease: asthma"}
      ],
      "attributes": {
        "Sample_treatment_protocol_ch1": ["..."]
      }
    }
  ],
  "embed_text": "Title: ...\nSummary: ..."
}
```

Because existence makes the canonical record terminal, preserve metadata that
is not currently indexed in the `series_attributes`, platform `attributes`, and
sample `attributes` maps. Strip the leading `!` and record-type prefix from map
keys, and retain every repeated value in source order with exact decoded text.
Do not include the already-removed expression/platform data tables.

Top-level fields are deterministic GSE-level aggregates derived from those
records. Repeated SOFT attributes accumulate rather than overwrite.
Characteristics split only on the first colon; a value containing additional
colons remains intact, and `raw` preserves the original string. A malformed file
produces no output.

Run the existing deterministic organism, sex, and assay normalizers while
materializing the canonical record so Elasticsearch can consume those arrays
directly. Normalizer changes do not rewrite existing records automatically in
v1; delete the affected record(s) explicitly before rerunning. Keep
`embed_text` based on the raw narrative fields so normalized-label injection
remains a later controlled ablation rather than silently changing this bakeoff.

## Canonical embedding store

Use SQLite because it gives simple incremental writes and primary-key
idempotence without creating roughly one million small vector files.

```sql
CREATE TABLE IF NOT EXISTS embedding_models (
    model_key       TEXT PRIMARY KEY,
    model_id        TEXT NOT NULL,
    dimensions      INTEGER NOT NULL,
    config_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS series_embeddings (
    gse             TEXT NOT NULL,
    model_key       TEXT NOT NULL,
    dimensions      INTEGER NOT NULL,
    vector_f32      BLOB NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (gse, model_key),
    FOREIGN KEY (model_key) REFERENCES embedding_models(model_key)
);

CREATE INDEX IF NOT EXISTS series_embeddings_model_gse
    ON series_embeddings(model_key, gse);
```

Vectors are C-contiguous, little-endian float32 bytes. A read validates the BLOB
length as `dimensions * 4` and rejects nonfinite values. Store the exact fixed
model configuration from [[52-Embedding-Bakeoff-Runbook]] in canonicalized
`config_json`. If a registered key already exists with different configuration,
fail with instructions to explicitly delete that model's rows and registry entry;
do not silently mix revisions under one key.

## Prefect flow

Use one flow with bounded batch tasks:

```python
@flow(
    name="geo-soft-etl",
    task_runner=ThreadPoolTaskRunner(max_workers=8),
    log_prints=True,
)
def geo_soft_etl(
    soft_root: Path = Path("data/processed/soft_meta"),
    records_root: Path = Path("data/processed/series_records"),
    embedding_db: Path = Path("data/processed/series_embeddings.sqlite"),
    model_keys: tuple[str, ...] = ("bge_small_v15",),
    parse_batch_size: int = 250,
    allow_paid_gemini: bool = False,
) -> EtlReport:
    ...
```

Flow-level orchestration:

1. Enumerate and sort `*_family.soft.gz` paths once.
2. Convert each path to a GSE and canonical destination.
3. Remove every item whose destination already exists without opening it.
4. Submit missing paths in 250-file batches to eight thread workers.
5. Resolve every future and collect successfully created GSEs and failures.
6. For each configured model, compute the union of:
   - records missing a `(gse, model_key)` row; and
   - GSEs rebuilt during this run, whose existing rows must be replaced.
7. Run the appropriate provider adapter in model-efficient batches and commit
   SQLite rows in bounded transactions.
8. Atomically overwrite `soft_etl_report.json` with counts, failures, timings,
   embedding coverage, and the selected model keys.
9. Return nonzero from the CLI if any parse/embedding failures occurred, while
   preserving every successfully completed output.

Prefect tasks are retryable and observable units, and `.submit()` uses the
configured task runner for concurrency. Keep task submission at flow level and
resolve all terminal futures. Do not submit child tasks from within a bounded
worker and synchronously wait on them.

The flow can run directly without a deployment. For local UI/history, start the
open-source server with `prefect server start`; its default SQLite backend is
adequate for this single-machine prototype. Do not add Prefect Cloud, work pools,
Docker agents, or production orchestration.

---

## Implementation plan

### Task 1 — Pure SOFT record parser

**Files:**

- Create: `src/geo_index/soft_records.py`
- Create: `tests/fixtures/soft/minimal_family.soft.gz`
- Create: `tests/fixtures/soft/repeated_characteristics_family.soft.gz`
- Create: `tests/test_soft_records.py`

**Interfaces:**

- `record_path(records_root: Path, gse: str) -> Path`
- `parse_soft_record(source: Path, *, soft_root: Path) -> dict[str, object]`
- `normalize_soft_record(record: Mapping[str, object]) -> dict[str, object]`
- `compose_soft_embed_text(record: Mapping[str, object]) -> str`

- [ ] Write failing tests for bucket paths, required series/sample fields,
  repeated and unknown metadata attributes, per-sample associations, first-colon
  characteristics, sorted top-level deduplication, current organism/sex/assay
  normalization, and deterministic raw-field `embed_text`.
- [ ] Run `pytest -q tests/test_soft_records.py`; expect failures because the
  module does not exist.
- [ ] Implement a streaming line parser with explicit `^SERIES`, `^SAMPLE`, and
  `^PLATFORM` record state. Do not introduce GEOparse unless fixtures prove a
  stdlib parser cannot preserve the locked schema.
- [ ] Reject filename/accession mismatch, missing `!Series_geo_accession`, and
  declared-sample/block-count mismatch before returning a record.
- [ ] Run `pytest -q tests/test_soft_records.py`; expect all tests to pass.
- [ ] Commit `test: define canonical SOFT record parsing` followed by
  `feat: parse canonical records from stripped SOFT`.

### Task 2 — Existence-based atomic record materialization

**Files:**

- Modify: `src/geo_index/soft_records.py`
- Create: `tests/test_soft_record_materialization.py`

**Interfaces:**

- `discover_missing(soft_root: Path, records_root: Path) -> list[RecordJob]`
- `materialize_record(job: RecordJob) -> MaterializeResult`
- `materialize_batch(jobs: Sequence[RecordJob]) -> BatchResult`

- [ ] Write a failing test that creates two SOFT files and one pre-existing
  canonical record, removes read permission from the completed source, and
  proves discovery skips it without opening it.
- [ ] Write failing tests for atomic success, temporary-file cleanup after parse
  failure, deterministic JSON serialization, and rebuilding after output deletion.
- [ ] Implement discovery using filename-derived GSEs and destination existence
  only. Sort jobs by GSE numeric order before batching.
- [ ] Write to `<destination>.tmp`, flush and close, then `os.replace`; never
  expose a partial `.json` file.
- [ ] Run `pytest -q tests/test_soft_record_materialization.py`; expect pass.
- [ ] Commit `feat: materialize missing canonical series records`.

### Task 3 — One canonical SQLite embedding store

**Files:**

- Create: `src/geo_index/embedding_store.py`
- Create: `tests/test_embedding_store.py`

**Interfaces:**

- `initialize_store(path: Path) -> None`
- `register_model(path: Path, variant: EmbeddingVariant) -> None`
- `missing_gses(path: Path, model_key: str, gses: Sequence[str]) -> list[str]`
- `write_vectors(path: Path, model_key: str, rows: Sequence[VectorRow], *, replace: bool) -> int`
- `read_vector(path: Path, model_key: str, gse: str) -> np.ndarray`

- [ ] Write failing tests for schema creation, missing-row discovery, insert,
  replacement, registry-config mismatch, wrong dimension, nonfinite values, and
  truncated BLOB rejection.
- [ ] Implement canonical JSON serialization for `config_json` and little-endian
  float32 conversion.
- [ ] Use bounded `executemany` transactions; do not share one SQLite connection
  across Prefect worker threads.
- [ ] Run `pytest -q tests/test_embedding_store.py`; expect pass.
- [ ] Commit `feat: add canonical incremental embedding store`.

### Task 4 — Provider-neutral missing-embedding builder

**Files:**

- Create: `src/geo_index/embedding_registry.py`
- Create: `src/geo_index/embedding_local.py`
- Create: `src/geo_index/embedding_gemini.py`
- Create: `src/geo_index/embed_missing.py`
- Create: `tests/test_embed_missing.py`

**Interfaces:**

- `get_variant(model_key: str) -> EmbeddingVariant`
- `build_missing_embeddings(records_root: Path, store_path: Path, model_key: str, *, replace_gses: AbstractSet[str], allow_paid_gemini: bool) -> EmbeddingBuildResult`

- [ ] Port only provider-neutral registry/adapter ideas from
  `codex/embedding-bakeoff-first-draft`; do not port PostgreSQL storage.
- [ ] Write fake-encoder tests proving existing rows are skipped, missing rows
  are inserted, rebuilt GSEs are replaced, model order is stable, and provider
  failures leave successful batches committed and failed rows missing.
- [ ] Keep one local encoder instance alive for all batches of a model in a run.
- [ ] Require `allow_paid_gemini=True` plus `GEMINI_API_KEY` before any Gemini
  submission; otherwise fail before network I/O.
- [ ] Preserve model-specific formatting and dimensions from
  [[52-Embedding-Bakeoff-Runbook]].
- [ ] Run `pytest -q tests/test_embed_missing.py`; expect pass.
- [ ] Commit `feat: build only missing canonical embeddings`.

### Task 5 — Prefect flow and CLI

**Files:**

- Create: `src/geo_index/prefect_etl.py`
- Create: `tests/test_prefect_etl.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**

- `geo_soft_etl(...) -> EtlReport` with the exact signature above
- CLI entry point: `geo-soft-etl = "geo_index.prefect_etl:main"`

- [ ] Add `prefect>=3,<4`, regenerate the lock once, and coordinate this shared
  file with any concurrent branch.
- [ ] Write tests with temporary roots and fake parser/encoder functions proving
  existing-record skip, 250-job batching, partial-failure reporting, rebuilt-GSE
  embedding replacement, and a no-work second run.
- [ ] Implement `@task(retries=2, retry_delay_seconds=5)` batch parsing and a
  `ThreadPoolTaskRunner(max_workers=8)` flow. Resolve all submitted futures.
- [ ] Add CLI flags for roots, model keys, batch size, worker count, and
  `--allow-paid-gemini`; defaults must match the plan.
- [ ] Atomically write the latest-run report. Include discovered, skipped,
  created, failed, embedded, embedding-skipped, and duration counts.
- [ ] Run `pytest -q tests/test_prefect_etl.py`; expect pass.
- [ ] Run `pytest -q`; expect the entire offline suite to pass.
- [ ] Commit `feat: orchestrate SOFT ETL and embeddings with Prefect`.

### Task 6 — Adopt the existing BGE artifact without recomputation

**Files:**

- Create: `src/geo_index/adopt_embeddings.py`
- Create: `tests/test_adopt_embeddings.py`
- Modify: `README.md`

**Interfaces:**

- `adopt_legacy_matrix(matrix_path: Path, ids_path: Path, store_path: Path, model_key: str) -> AdoptionReport`
- CLI: `python -m geo_index.adopt_embeddings --model-key bge_small_v15 ...`

- [ ] Write tests proving aligned IDs/vectors import, existing rows skip,
  metadata model/dimension validation, source preservation, and rollback on
  mismatched counts.
- [ ] Import `data/processed/embeddings.npy` and its ID sidecar into the canonical
  SQLite store. Keep the legacy files untouched.
- [ ] Do not import `embeddings_pubmedbert.npy` as MedCPT. If retained in the
  store, register it under an honest legacy key such as
  `pubmedbert_neuml_legacy_768`, excluded from the nine-system bakeoff.
- [ ] Run the importer first with a small fixture, then on the real BGE artifact;
  record inserted/skipped counts and database size in [[42-Build-Log]].
- [ ] Commit `feat: adopt legacy BGE vectors into canonical store`.

### Task 7 — Real-data slice and resumability gate

**Files:**

- Modify: `wiki/42-Build-Log.md`

- [ ] Start the optional local Prefect UI with `prefect server start` and set
  `PREFECT_API_URL=http://127.0.0.1:4200/api`, or run directly without the UI.
- [ ] Run the flow against 500 missing SOFT inputs with BGE only and record
  parse throughput, failures, record size, embedding skips/inserts, and memory.
- [ ] Run the identical command again. Acceptance is zero source parses and zero
  embedding computations for completed GSEs.
- [ ] Delete one canonical record, rerun, and verify exactly that record is
  rebuilt and its BGE row replaced.
- [ ] Run `geo-validate-soft --limit 5000` independently; the ETL flow does not
  replace source validation.
- [ ] Run the full offline suite and record exact results.
- [ ] Commit `docs: record Prefect SOFT ETL validation`.

## Acceptance criteria

- Already-materialized records cause no source read or parser invocation.
- A missing/deleted record is rebuilt atomically on the next run.
- A no-work second run performs zero parsing and zero embedding calls.
- Rebuilt GSEs replace their configured embedding rows; untouched GSEs do not.
- Adding a model key fills only its missing rows.
- One canonical record tree and one canonical embedding SQLite file exist; no
  snapshot/version/delta directories are created.
- Existing BGE artifacts are adopted without recomputation or deletion.
- Parse and embedding failures are visible in Prefect and the latest-run report,
  while successful outputs remain reusable.
- The output contract is sufficient for the separate local Elasticsearch loader.

## Prototype operations

Manual run:

```bash
uv run geo-soft-etl --model-key bge_small_v15
```

Explicitly rebuild one GSE:

```bash
rm data/processed/series_records/GSE271nnn/GSE271800.json
uv run geo-soft-etl --model-key bge_small_v15
```

The deletion is intentionally manual and destructive; operators must verify the
GSE path before running it. No wildcard rebuild command belongs in v1.

After the manual full run is stable, a machine-local cron or Prefect `serve`
schedule may invoke the same flow daily. Scheduling does not change idempotence:
new source files create new records, existing records remain untouched.

## Current official Prefect references

- [Prefect flows](https://docs.prefect.io/v3/concepts/flows)
- [Prefect tasks](https://docs.prefect.io/v3/concepts/tasks)
- [Prefect task runners and bounded concurrency](https://docs.prefect.io/v3/concepts/task-runners)
- [Run a local Prefect server](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli)
- [Install Prefect](https://docs.prefect.io/v3/get-started/install)
