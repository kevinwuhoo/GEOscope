---
title: Prefect SOFT ETL and Embedding Prototype Plan
tags: [prefect, soft, etl, embeddings, json, numpy, plan, v1]
status: approved-plan
created: 2026-07-11
updated: 2026-07-11
---

# 53 · Prefect SOFT ETL and Embedding Prototype Plan

> **Superseded pipeline boundary (2026-07-12):** This plan stopped at canonical
> records and embedding artifacts. The implemented primary Prefect flow now
> requires `gemini_embedding_2_3072_v1`, loads it into Elasticsearch, audits
> document/vector coverage, and fails the run if indexing is unavailable.

← [[Home]] · implements the current path in [[21-Ingestion-Pipeline]] · feeds
[[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]] · model details in
[[52-Embedding-Bakeoff-Runbook]] · future endpoint in
[[54-Incremental-Corpus-Future-State]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn every available stripped GEO family SOFT file into one canonical
GSE JSON record, and provide separate code that later builds one complete
on-disk matrix artifact per embedding model from those JSON records.

**Architecture:** A Prefect 3 flow inventories stripped SOFT files and derived
record files. A derived file's existence means that GSE is complete and must not
be opened or parsed again. Missing records are built in bounded batches with
atomic writes. Separate model-specific builders later enumerate the completed
JSON records in stable GSE order and publish one canonical NumPy matrix/ID/
metadata directory per model. There are no dated snapshots, SQLite vector
stores, content-addressed versions, or daily matrix deltas in the prototype.

**Tech Stack:** Python 3.11+, Prefect 3 (`prefect>=3,<4`), stdlib gzip/JSON/XML-
free SOFT parsing, NumPy float32 matrices, existing local/Hugging Face encoders,
Google GenAI batch API for Gemini document embeddings, pytest.

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
- Read embeddings only from completed canonical JSON records; do not couple model
  code to Prefect, SOFT parsing, SQLite, PostgreSQL, or Elasticsearch.
- Persist one canonical directory per model under
  `data/processed/embedding_artifacts/<model_key>/`, containing `vectors.npy`,
  `ids.json`, and `metadata.json`.
- If a model artifact directory exists and validates, skip that whole model.
  Delete it explicitly to rebuild after adding records or changing a model.
- Preserve the existing BGE and PubMedBERT `.npy`/ID files; importing them must
  not overwrite or delete the originals.
- Prefect coordinates SOFT parsing; filesystem existence, not Prefect result
  caching, defines record idempotence.
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
| Record tree exists, model artifact missing | Model has not been built | Build the complete model artifact from sorted JSON records |
| Model artifact exists and validates | Model build complete | Skip without encoding |
| Record added/deleted after model build | Artifact no longer matches canonical IDs | Explicitly delete and rebuild that model artifact |

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
    embedding_artifacts/               # one complete canonical directory/model
      bge_small_v15/
        vectors.npy
        ids.json
        metadata.json
      medcpt_v1/
      qwen3_06b_1024_v1/
      gemini_embedding_2_3072_v1/
    soft_etl_report.json                # overwritten summary of the latest run

  processed/embeddings.npy             # existing BGE legacy artifact; preserved
  processed/embeddings.ids.json
  processed/embeddings_pubmedbert.npy   # existing non-MedCPT artifact; preserved
  processed/embeddings_pubmedbert.ids.json
```

The record is the shared metadata representation for embeddings and
Elasticsearch. The embedder reads `gse`, `title`, and `embed_text`; the local ES
loader reads the entire record and joins vectors through each artifact's ordered
`ids.json`. There is no separate embedding JSONL or Elasticsearch JSONL.

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

## Canonical embedding artifact

Each model writes exactly one final directory:

```text
data/processed/embedding_artifacts/<model_key>/
  vectors.npy
  ids.json
  metadata.json
```

`vectors.npy` is a finite C-contiguous float32 matrix. `ids.json` is the ordered
GSE list matching its rows. `metadata.json` records the fixed model/provider ID,
resolved revision where available, wrapper/prompt, dimensions, normalization,
record count, build time, SDK/API version, and Gemini usage/job IDs when
applicable.

Build in a sibling temporary directory and publish the final directory only
after shape, dimension, finite-value, and ID-count validation. If the final
directory already exists and validates, skip the whole model. The prototype does
not append rows: when the canonical record inventory changes, delete and rebuild
the affected model artifact explicitly.

Gemini document embeddings must use the Google batch API, not synchronous
per-record requests. Persist request JSONL and provider job state only inside the
temporary build directory; resume existing jobs rather than resubmitting them.
After all results arrive, validate GSE identity and assemble the canonical
3,072-dimensional matrix.

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
    parse_batch_size: int = 250,
) -> EtlReport:
    ...
```

Flow-level orchestration:

1. Enumerate and sort `*_family.soft.gz` paths once.
2. Convert each path to a GSE and canonical destination.
3. Remove every item whose destination already exists without opening it.
4. Submit missing paths in 250-file batches to eight thread workers.
5. Resolve every future and collect successfully created GSEs and failures.
6. Atomically overwrite `soft_etl_report.json` with counts, failures, and timings.
7. Return nonzero from the CLI if parse failures occurred, while preserving every
   successfully completed output.

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

For parallel ownership, the Prefect/SOFT owner implements Tasks 1, 2, 5, and 7;
the embedding owner implements Tasks 3, 4, and 6. The embedding code consumes
only the completed canonical JSON tree after the ETL run. The third owner
implements local Elasticsearch from
[[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]] and consumes the finished
record/matrix formats read-only. Copy-ready prompts are in
[[55-Prefect-and-Local-Elasticsearch-Coworker-Prompts]].

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

### Task 3 — Canonical matrix artifact contract

**Files:**

- Create: `src/geo_index/embedding_artifacts.py`
- Create: `tests/test_embedding_artifacts.py`

**Interfaces:**

- `artifact_dir(output_root: Path, model_key: str) -> Path`
- `load_record_inventory(records_root: Path) -> RecordInventory`
- `validate_artifact(path: Path, variant: EmbeddingVariant) -> ArtifactMetadata`
- `publish_artifact(temp_dir: Path, final_dir: Path) -> None`

- [ ] Write failing tests for stable numeric-GSE inventory, matrix/ID alignment,
  wrong dimensions, nonfinite values, malformed metadata, incomplete temporary
  output, and existing-valid-artifact detection.
- [ ] Implement one canonical `vectors.npy`/`ids.json`/`metadata.json` directory
  per fixed model key. Write only to a sibling temporary directory until every
  validation passes, then publish with one directory rename.
- [ ] Reject a final artifact whose IDs do not match its vector rows. The ES
  loader may later join partial model coverage by ID, but the artifact itself
  must be internally complete.
- [ ] Run `pytest -q tests/test_embedding_artifacts.py`; expect pass.
- [ ] Commit `feat: define canonical embedding matrix artifacts`.

### Task 4 — Provider-neutral complete-artifact builder

**Files:**

- Create: `src/geo_index/embedding_registry.py`
- Create: `src/geo_index/embedding_local.py`
- Create: `src/geo_index/embedding_gemini.py`
- Create: `src/geo_index/build_embedding_artifact.py`
- Create: `tests/test_build_embedding_artifact.py`

**Interfaces:**

- `get_variant(model_key: str) -> EmbeddingVariant`
- `build_embedding_artifact(records_root: Path, output_root: Path, model_key: str, *, allow_paid_gemini: bool) -> EmbeddingBuildResult`

- [ ] Port only provider-neutral registry/adapter ideas from
  `codex/embedding-bakeoff-first-draft`; do not port PostgreSQL storage.
- [ ] Write fake-encoder tests proving a valid existing artifact skips all
  encoding, records are encoded in stable GSE order, final output is unpublished
  after failure, and a temporary build can resume without duplicating completed
  provider work.
- [ ] Keep one local encoder instance alive for all batches of a model in a run.
- [ ] Require `allow_paid_gemini=True` plus `GEMINI_API_KEY` before any Gemini
  submission; otherwise fail before network I/O.
- [ ] For Gemini documents, generate deterministic batch-request JSONL, submit
  through the Google batch API, persist provider job IDs/state in the temporary
  directory, poll/resume those jobs, and assemble full 3,072-dimensional results.
  Do not send one synchronous API request per GSE.
- [ ] Preserve model-specific formatting and dimensions from
  [[52-Embedding-Bakeoff-Runbook]].
- [ ] Run `pytest -q tests/test_build_embedding_artifact.py`; expect pass.
- [ ] Commit `feat: build canonical embedding artifacts from JSON records`.

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
- [ ] Write tests with temporary roots and fake parser functions proving
  existing-record skip, 250-job batching, partial-failure reporting, explicit
  record rebuild, and a no-work second run.
- [ ] Implement `@task(retries=2, retry_delay_seconds=5)` batch parsing and a
  `ThreadPoolTaskRunner(max_workers=8)` flow. Resolve all submitted futures.
- [ ] Add CLI flags for SOFT root, records root, batch size, and worker count;
  defaults must match the plan.
- [ ] Atomically write the latest-run report. Include discovered, skipped,
  created, failed, and duration counts.
- [ ] Run `pytest -q tests/test_prefect_etl.py`; expect pass.
- [ ] Run `pytest -q`; expect the entire offline suite to pass.
- [ ] Commit `feat: orchestrate idempotent SOFT record ETL with Prefect`.

### Task 6 — Adopt the existing BGE artifact without recomputation

**Files:**

- Create: `src/geo_index/adopt_embeddings.py`
- Create: `tests/test_adopt_embeddings.py`
- Modify: `README.md`

**Interfaces:**

- `adopt_legacy_matrix(matrix_path: Path, ids_path: Path, output_root: Path, model_key: str) -> AdoptionReport`
- CLI: `python -m geo_index.adopt_embeddings --model-key bge_small_v15 ...`

- [ ] Write tests proving aligned IDs/vectors adoption, existing artifact skip,
  metadata model/dimension validation, source preservation, and no published
  output on mismatched counts.
- [ ] Copy the validated `data/processed/embeddings.npy` matrix and ordered IDs
  into the canonical `bge_small_v15` artifact directory. Keep the legacy files
  untouched.
- [ ] Do not adopt `embeddings_pubmedbert.npy` as MedCPT. If retained as an
  artifact, register it under an honest legacy key such as
  `pubmedbert_neuml_legacy_768`, excluded from the nine-system bakeoff.
- [ ] Run the importer first with a small fixture, then on the real BGE artifact;
  record count and artifact size in [[42-Build-Log]].
- [ ] Commit `feat: adopt legacy BGE as canonical matrix artifact`.

### Task 7 — Real-data slice and resumability gate

**Files:**

- Modify: `wiki/42-Build-Log.md`

- [ ] Start the optional local Prefect UI with `prefect server start` and set
  `PREFECT_API_URL=http://127.0.0.1:4200/api`, or run directly without the UI.
- [ ] Run the flow against 500 missing SOFT inputs and record parse throughput,
  failures, record size, and memory.
- [ ] Run the identical ETL command again. Acceptance is zero source parses for
  completed GSEs.
- [ ] Delete one canonical record, rerun, and verify exactly that record is
  rebuilt.
- [ ] Run the BGE artifact builder against the completed test record tree, then
  rerun it and verify the existing valid artifact causes zero encoder calls.
- [ ] Run `geo-validate-soft --limit 5000` independently; the ETL flow does not
  replace source validation.
- [ ] Run the full offline suite and record exact results.
- [ ] Commit `docs: record Prefect SOFT ETL validation`.

## Acceptance criteria

- Already-materialized records cause no source read or parser invocation.
- A missing/deleted record is rebuilt atomically on the next run.
- A no-work second ETL run performs zero parsing.
- A valid existing model artifact causes zero encoder/API calls.
- Each model publishes one complete matrix/ID/metadata directory from canonical
  JSON records; no per-GSE vector store is introduced.
- One canonical record tree and one canonical artifact directory per model exist; no
  snapshot/version/delta directories are created.
- Existing BGE artifacts are adopted without recomputation or deletion.
- Parse failures are visible in Prefect and the latest-run report. Embedding
  failures leave only resumable temporary state and never publish a partial
  canonical artifact.
- The output contract is sufficient for the separate local Elasticsearch loader.

## Prototype operations

Manual run:

```bash
uv run geo-soft-etl
```

Explicitly rebuild one GSE:

```bash
rm data/processed/series_records/GSE271nnn/GSE271800.json
uv run geo-soft-etl
```

The deletion is intentionally manual and destructive; operators must verify the
GSE path before running it. No wildcard rebuild command belongs in v1.

After the manual full run is stable, a machine-local cron or Prefect `serve`
schedule may invoke the same flow daily. Scheduling does not change idempotence:
new source files create new records, existing records remain untouched.

After the desired canonical JSON record set exists, build models separately:

```bash
uv run python -m geo_index.build_embedding_artifact --model-key bge_small_v15
uv run python -m geo_index.build_embedding_artifact --model-key medcpt_v1
uv run python -m geo_index.build_embedding_artifact --model-key qwen3_06b_1024_v1
uv run python -m geo_index.build_embedding_artifact \
  --model-key gemini_embedding_2_3072_v1 \
  --allow-paid-gemini
```

The Gemini command prepares and submits batch jobs. It must never fall back to
one synchronous document request per GSE.

## Current official Prefect references

- [Prefect flows](https://docs.prefect.io/v3/concepts/flows)
- [Prefect tasks](https://docs.prefect.io/v3/concepts/tasks)
- [Prefect task runners and bounded concurrency](https://docs.prefect.io/v3/concepts/task-runners)
- [Run a local Prefect server](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli)
- [Install Prefect](https://docs.prefect.io/v3/get-started/install)
