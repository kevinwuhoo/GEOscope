# Prefect SOFT Canonical Records and Embedding Artifacts Design

**Status:** approved for implementation on 2026-07-11

## Goal

Convert every metadata-only GEO family SOFT file under
`data/processed/soft_meta/` into one deterministic canonical JSON record, then
build complete canonical BGE, MedCPT, and Qwen matrix artifacts from that record
tree. Implement Gemini Embedding 2 batch support, but do not submit Gemini work
until the operator supplies a key and explicitly authorizes paid execution.

## Scope

This tranche implements Tasks 1–7 from
`wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md`, with Tasks 1, 2, 5,
and 7 defining the ETL and Tasks 3, 4, and 6 defining embeddings. It preserves
the model, dimension, prompt, and evaluation-facing contracts from wiki 52.

It does not implement Elasticsearch, source/content hashes, mtime comparison,
automatic source-update detection, snapshots, versioned records, delta vector
stores, SQLite storage, or paid Gemini execution.

## Architecture

The implementation has two independent layers joined by a narrow Python API:

1. `soft_records.py` is a Prefect-free streaming parser and atomic record
   materializer. It owns SOFT structure, deterministic aggregates, normalization,
   discovery, and existence-based idempotence.
2. `prefect_etl.py` inventories once, submits bounded batches to a Prefect 3
   `ThreadPoolTaskRunner`, resolves every future, publishes the run report, and
   calls the embedding-owner integration with the exact set of GSEs created by
   that run.
3. The embedding modules load completed canonical JSON records in stable numeric
   GSE order and publish one validated `vectors.npy`/`ids.json`/`metadata.json`
   directory per model. They do not import Prefect or Elasticsearch.

The parser and artifact builders remain pure enough to test with tiny fixtures
and fake encoders. Heavy libraries and SDK clients are imported only inside the
provider adapters.

## Canonical record contract

Each record is written to
`data/processed/series_records/<GSE bucket>/<GSE>.json`. The bucket is computed
from the accession by replacing its last three digits with `nnn` (for example,
`GSE271800` becomes `GSE271nnn`).

The top-level schema is:

```text
schema_version: 1
gse: string
source_soft: string relative to soft_root
title: string
summary: string
overall_design: string
type: sorted distinct string[]
pubmed_ids: sorted distinct string[]
submission_date: ISO date string or null
last_update_date: ISO date string or null
platform_ids: numerically sorted distinct GPL[]
n_samples: integer
organisms: sorted distinct string[]
molecules: sorted distinct string[]
source_names: sorted distinct string[]
characteristics: [{name, values[]}]
library_strategies: sorted distinct string[]
library_sources: sorted distinct string[]
library_selections: sorted distinct string[]
organism_ids: sorted ontology ID[]
organism_status: string
sex_ids: sorted ontology ID[]
sex_status: string
assay_categories: sorted label[]
assay_labels: sorted label[]
assay_status: string
sample_titles: sorted distinct string[]
sample_accessions: numerically sorted distinct GSM[]
series_attributes: {SOFT attribute key: source-ordered value[]}
platforms: [{gpl, attributes}]
samples: [{gsm, title, source_name, organism, molecule,
           characteristics: [{name, value, raw}], attributes}]
embed_text: string
```

Attribute maps preserve every `!Series_*`, `!Platform_*`, and `!Sample_*`
attribute, including fields projected to top-level values. Keys omit only the
leading `!`, matching the locked schema examples (`Series_relation`,
`Platform_title`, `Sample_treatment_protocol_ch1`). Repeated values remain in
source order with exact decoded text. This resolves the prose ambiguity about
prefix stripping in favor of the locked schema examples and maximum fidelity.

The parser uses explicit `SERIES`, `PLATFORM`, and `SAMPLE` state while reading
gzip text line by line with UTF-8 replacement decoding. Characteristics split
on the first colon only; `raw` retains the complete original value. A
characteristic without a colon has an empty name and its whole raw string as the
value. Series aggregates are distinct and sorted; per-record maps and sample
lists retain source association and source order.

The existing `map_organisms`, `map_sex_field`, and `normalize_assay_fields`
functions produce normalization fields. `embed_text` composes raw title, type,
organism, summary, overall-design, molecule, source-name, and characteristic
values; it never injects normalized labels.

The parser rejects a filename/accession mismatch, a missing or repeated
conflicting series accession, malformed record headers, missing sample or
platform accessions, duplicate sample accessions, and any mismatch between
declared series sample IDs and parsed sample blocks. Rejection returns no
record.

## Existence state machine and atomic records

Discovery enumerates `*_family.soft.gz` exactly once, derives the GSE from the
filename, computes the destination, and tests only destination existence.
Existing destinations are counted as skipped without opening the source or the
destination. No source metadata, hash, or mtime is inspected.

Missing records are sorted by numeric GSE and divided into bounded batches.
Each successful materialization writes deterministic UTF-8 JSON to
`<destination>.tmp`, flushes and `fsync`s it, closes it, and calls `os.replace`.
An exception removes the temporary file and never exposes a final JSON file.
Deleting the final JSON is the only record recomputation mechanism.

## Prefect flow and CLI

`geo_soft_etl()` uses Prefect 3 with a bounded `ThreadPoolTaskRunner` and one
retryable task per batch, never one task per GSE. The flow submits all batches at
flow level and resolves every returned future. A batch returns individual
successes and failures so one malformed input does not discard successful
siblings.

The latest report is atomically written to
`data/processed/soft_etl_report.json` and includes discovered, skipped, created,
failed, parse-batch, duration, and per-failure details. The CLI accepts SOFT
root, records root, batch size, and worker count. It runs without Prefect Cloud
or a local server and returns nonzero when parsing or embedding fails.

After parsing, the flow calls:

```python
build_missing_embeddings(
    records_root: Path,
    store_path: Path,
    model_key: str,
    *,
    replace_gses: AbstractSet[str],
    allow_paid_gemini: bool,
) -> EmbeddingBuildResult
```

`replace_gses` is exactly the set of records created in that run. An injectable
fake proves this contract until the concrete embedding module is present. The
real integration facade rebuilds the selected complete model artifact when the
set is nonempty; an empty set preserves valid-artifact skip behavior. This is a
whole-artifact rebuild, not a per-GSE vector store.

## Embedding registry and inventory

The lightweight registry fixes these variants:

| Key | Provider/model | Dimensions | Document policy |
|---|---|---:|---|
| `bge_small_v15` | `BAAI/bge-small-en-v1.5` | 384 | raw `embed_text`, normalized output |
| `medcpt_v1` | `ncbi/MedCPT-Article-Encoder` | 768 | title/body pair, CLS pooling |
| `qwen3_06b_1024_v1` | `Qwen/Qwen3-Embedding-0.6B` | 1024 | raw `embed_text`, no document prompt |
| `gemini_embedding_2_3072_v1` | `gemini-embedding-2` | 3072 | `document: title: {title} | text: {content}` |

The inventory reads only `*.json` canonical records, validates GSE identity,
rejects duplicates, and sorts numerically by accession. The builder reads only
`gse`, `title`, and `embed_text` for encoding.

## Canonical artifact contract

Each model publishes exactly:

```text
data/processed/embedding_artifacts/<model_key>/
  vectors.npy
  ids.json
  metadata.json
```

`vectors.npy` is a two-dimensional, finite, C-contiguous float32 matrix with the
registry dimension. `ids.json` is a unique numeric-GSE-sorted list whose length
equals the matrix row count. `metadata.json` records schema/model/provider IDs,
resolved revision when available, dimension, document/query formatting,
normalization, record count, runtime, SDK/library versions, truncation policy
and counts, and provider usage/job/cost data where applicable.

The builder writes into a deterministic sibling temporary directory, validates
all three files, and only then publishes the final directory. A valid final
directory skips all encoder/provider work. A provider failure leaves no final
directory. Resumable provider state may remain only in the sibling temporary
directory.

Local builders keep one model/tokenizer instance alive for all batches. BGE and
Qwen use the sentence-transformers encode contract with explicit batch size,
normalization, and truncation settings. MedCPT uses its article encoder's
document pair, tokenization, CLS-pooling, and normalization contract.

## Gemini batch design

Gemini code generates deterministic request JSONL and a cost estimate before
any credential or network use. Submission requires both
`allow_paid_gemini=True` and `GEMINI_API_KEY`. It uses the official Google GenAI
batch/file APIs only; no corpus path calls synchronous `embed_content`.

Temporary state records request shards, provider file IDs, batch job IDs,
terminal states, and downloaded response paths. Resume loads this state and
does not resubmit successful jobs. Result assembly validates each custom GSE ID,
rejects missing/duplicate/unexpected responses, verifies 3,072 finite values,
and records usage, truncation, job IDs, SDK/API version, and estimated charge.
The current authorized run stops before submission.

## Legacy BGE adoption

Adoption validates source matrix dtype/shape/finiteness, source ID order and
uniqueness, expected BGE dimension, and exact row alignment before copying. It
never modifies or deletes the legacy BGE matrix or IDs. If its document
composition or coverage does not match the canonical record tree, the new BGE
artifact is rebuilt instead. The legacy PubMedBERT artifact is never labeled
MedCPT.

## Verification and real-data gate

Tests prove parsing fidelity, deterministic aggregation, normalizer reuse,
atomic cleanup, no-read existence skips, bounded batching, complete future
resolution, report counts, `replace_gses`, stable inventory order, artifact
validation, zero-call skips, paid guards, Gemini batch-only behavior, resume,
and legacy-source preservation.

The real-data gate processes a 500-record slice twice, deletes one record and
rebuilds it, validates 5,000 stripped files independently, then processes the
complete current SOFT inventory. After canonical-record completion, it adopts
or rebuilds BGE and builds MedCPT and Qwen. It records counts, failures,
throughput, runtime, storage, matrix shape, and validation status in
`wiki/42-Build-Log.md`.

Gemini is code-complete and dry-run verified but remains unsubmitted until a key
and explicit paid authorization are available.
