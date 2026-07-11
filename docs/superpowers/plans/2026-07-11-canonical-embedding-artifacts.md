# Canonical Embedding Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build validated complete BGE, MedCPT, Qwen, and Gemini embedding artifact pipelines from canonical GSE JSON records, execute all authorized local models, and leave Gemini ready but unsubmitted.

**Architecture:** A lightweight fixed registry describes model contracts; a provider-neutral artifact layer owns inventory, validation, and atomic publication; separate lazy provider adapters own local models and Gemini batch state. A command-line builder connects these boundaries and a separate adoption command preserves legacy BGE sources.

**Tech Stack:** Python 3.11+, NumPy, sentence-transformers/transformers/PyTorch, Google GenAI SDK, pytest, uv.

## Global Constraints

- Embedding modules do not import Prefect, Elasticsearch, SQLite, or SOFT parsing.
- Read only `gse`, `title`, and `embed_text` from canonical records.
- Sort unique GSEs numerically and align `ids.json` exactly to matrix rows.
- Publish finite C-contiguous float32 matrices at 384, 768, 1,024, or 3,072 dimensions.
- A valid existing final artifact performs zero encoder or API calls.
- Do not append rows, create snapshots/versions/deltas, or execute paid Gemini work.
- Keep one local model instance alive for the complete model build.
- Preserve legacy BGE and PubMedBERT source files unchanged.

---

### Task 1: Define the registry and canonical artifact contract

**Files:**
- Create: `tests/test_embedding_artifacts.py`
- Create: `src/geo_index/embedding_registry.py`
- Create: `src/geo_index/embedding_artifacts.py`

**Interfaces:**
- Produces: `EmbeddingVariant`, `get_variant(model_key: str) -> EmbeddingVariant`
- Produces: `RecordRef`, `RecordInventory`, `ArtifactMetadata`
- Produces: `artifact_dir`, `load_record_inventory`, `validate_artifact`, `publish_artifact`

- [ ] **Step 1: Write registry and inventory tests**

```python
def test_inventory_uses_numeric_gse_order(records_root: Path) -> None:
    write_record(records_root, "GSE10")
    write_record(records_root, "GSE2")
    assert load_record_inventory(records_root).ids == ("GSE2", "GSE10")


def test_registry_dimensions() -> None:
    assert get_variant("bge_small_v15").dimensions == 384
    assert get_variant("medcpt_v1").dimensions == 768
    assert get_variant("qwen3_06b_1024_v1").dimensions == 1024
    assert get_variant("gemini_embedding_2_3072_v1").dimensions == 3072
```

Test duplicate record IDs, path/payload mismatch, malformed records, unsafe
model keys, and lightweight imports.

- [ ] **Step 2: Write artifact validation tests**

Assert rejection of wrong dimensions, non-float32 dtype, nonfinite values,
non-contiguous matrices, duplicate/unsorted IDs, row-count mismatch, missing
metadata fields, incomplete directories, and an existing destination during
publication.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `uv run pytest -q tests/test_embedding_artifacts.py`

Expected: collection fails because the modules do not exist.

- [ ] **Step 4: Implement the fixed registry and artifact layer**

Use frozen dataclasses and a fixed mapping. Load record payloads lazily from
`RecordRef.path`, validate IDs against their JSON and filename, and use
`np.load(..., mmap_mode="r")` for validation. Publish only after
`validate_artifact(temp_dir, variant)` succeeds.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest -q tests/test_embedding_artifacts.py`

```bash
git add src/geo_index/embedding_registry.py src/geo_index/embedding_artifacts.py tests/test_embedding_artifacts.py
git commit -m "feat: define canonical embedding matrix artifacts"
```

### Task 2: Build complete local artifacts with fake encoders

**Files:**
- Create: `tests/test_build_embedding_artifact.py`
- Create: `src/geo_index/embedding_local.py`
- Create: `src/geo_index/build_embedding_artifact.py`

**Interfaces:**
- Consumes: registry and artifact interfaces from Task 1
- Produces: `LocalEncoder` protocol and `create_local_encoder(variant)`
- Produces: `EmbeddingBuildResult`
- Produces: `build_embedding_artifact(records_root, output_root, model_key, *, allow_paid_gemini) -> EmbeddingBuildResult`
- Produces: `build_missing_embeddings(records_root, store_path, model_key, *, replace_gses, allow_paid_gemini) -> EmbeddingBuildResult`

- [ ] **Step 1: Write fake-encoder orchestration tests**

```python
def test_valid_existing_artifact_skips_encoder(tmp_path: Path, monkeypatch) -> None:
    make_valid_artifact(tmp_path, "bge_small_v15")
    monkeypatch.setattr(local, "create_local_encoder", fail_if_called)
    result = build_embedding_artifact(records, tmp_path, "bge_small_v15", allow_paid_gemini=False)
    assert result.status == "skipped"
```

Also prove stable batch order, exact row/ID alignment, one encoder construction,
float32 conversion, provider failure leaving no final directory, metadata
completeness, and `build_missing_embeddings` replacement semantics.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest -q tests/test_build_embedding_artifact.py -k 'not gemini'`

Expected: collection or calls fail because the builder is absent.

- [ ] **Step 3: Implement local adapters lazily**

Provide a common `encode(records, batch_size, temp_dir)` contract. Instantiate
sentence-transformers once for BGE/Qwen. Instantiate one tokenizer and one
transformers model for MedCPT, tokenize `[title, embed_text]` pairs, use CLS
pooling, and L2-normalize. Resolve device in the order CUDA, MPS, CPU and record
it in metadata.

- [ ] **Step 4: Implement provider-neutral building**

Create the deterministic sibling temp directory, encode all rows, save
`vectors.npy`, `ids.json`, and complete `metadata.json`, validate, then publish.
On a valid final artifact return before constructing providers. On failure keep
only provider-resumable temp state.

- [ ] **Step 5: Run focused tests and commit**

Run: `uv run pytest -q tests/test_build_embedding_artifact.py -k 'not gemini'`

```bash
git add src/geo_index/embedding_local.py src/geo_index/build_embedding_artifact.py tests/test_build_embedding_artifact.py
git commit -m "feat: build local embedding artifacts from canonical records"
```

### Task 3: Implement resumable Gemini batch embeddings without executing them

**Files:**
- Create: `tests/test_embedding_gemini.py`
- Create: `src/geo_index/embedding_gemini.py`
- Modify: `src/geo_index/build_embedding_artifact.py`

**Interfaces:**
- Produces: deterministic bounded request JSONL shards and schema-v2 per-shard state
- Produces: token/cost estimate
- Produces: `build_gemini_vectors(inventory, variant, temp_dir, *, allow_paid) -> ProviderResult`

- [ ] **Step 1: Confirm the current official SDK batch API**

Use only official Google GenAI documentation and the installed SDK's public
types. Freeze adapter calls behind a narrow client protocol so tests do not
depend on network or mutable SDK internals.

- [ ] **Step 2: Write batch-only, paid-guard, and resume tests**

Use a fake client that records file uploads, batch submissions, polls, and
downloads. Assert the guard rejects before client construction, each request
has a stable GSE custom ID and 3,072 output dimension, resume does not resubmit
a stored successful shard, response identity is exact, missing/duplicate rows
fail, the conservative UTF-8 preflight stays below the model token limit, and
no adapter path exposes or calls synchronous `embed_content` or `countTokens`.

- [ ] **Step 3: Run Gemini tests and confirm RED**

Run: `uv run pytest -q tests/test_embedding_gemini.py`

Expected: collection fails because the adapter does not exist.

- [ ] **Step 4: Implement deterministic requests, state, resume, and assembly**

Write request JSONL and state JSON atomically. Print estimated tokens and batch
charge before checking authorization. Require the paid flag and
`GEMINI_API_KEY`, submit/upload only missing shards, poll stored job IDs, download
terminal results, validate custom IDs, assemble numeric-GSE order, and return
usage/job/truncation metadata.

- [ ] **Step 5: Dry-run without credentials and commit**

Run: `env -u GEMINI_API_KEY uv run python -m geo_index.build_embedding_artifact --model-key gemini_embedding_2_3072_v1`

Expected: a printed estimate followed by an authorization error and zero network
submission.

Run: `uv run pytest -q tests/test_embedding_gemini.py tests/test_build_embedding_artifact.py`

```bash
git add src/geo_index/embedding_gemini.py src/geo_index/build_embedding_artifact.py tests/test_embedding_gemini.py tests/test_build_embedding_artifact.py
git commit -m "feat: add resumable Gemini batch embedding pipeline"
```

### Task 4: Adopt legacy BGE without changing source files

**Files:**
- Create: `tests/test_adopt_embeddings.py`
- Create: `src/geo_index/adopt_embeddings.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `AdoptionReport`
- Produces: `adopt_legacy_matrix(matrix_path, ids_path, output_root, model_key) -> AdoptionReport`

- [ ] **Step 1: Write adoption tests**

Hash source files before and after adoption. Prove aligned copy, valid-existing
skip, model/dimension validation, count mismatch rejection, nonfinite rejection,
source preservation, and no final directory after failure.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest -q tests/test_adopt_embeddings.py`

Expected: collection fails because the adoption module is absent.

- [ ] **Step 3: Implement validation, copy, metadata, and CLI**

Copy the matrix using `shutil.copyfile`, write canonical IDs and evidence-based
metadata in a sibling temp directory, validate it with the registry, and publish.
Never delete or rename source files. Reject a request to label the PubMedBERT
source as `medcpt_v1`.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q tests/test_adopt_embeddings.py`

```bash
git add src/geo_index/adopt_embeddings.py tests/test_adopt_embeddings.py README.md
git commit -m "feat: adopt legacy BGE as canonical matrix artifact"
```

### Task 5: Install model dependencies and build all authorized artifacts

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `wiki/42-Build-Log.md`

**Interfaces:**
- Consumes complete canonical record tree from the ETL plan
- Produces canonical BGE, MedCPT, and Qwen artifact directories
- Produces Gemini dry-run evidence only

- [ ] **Step 1: Add only required provider dependencies and lock once**

Add compatible NumPy, transformers/sentence-transformers/PyTorch, and
`google-genai` dependencies required by the adapters. Keep heavyweight imports
lazy so unit tests remain fast.

- [ ] **Step 2: Verify whether legacy BGE aligns to the canonical inventory**

Validate matrix shape, dtype, finiteness, IDs, and exact inventory coverage. If
coverage or document provenance differs, preserve it and build BGE anew.

- [ ] **Step 3: Download and smoke-test each local model**

Build a small canonical record slice with BGE, MedCPT, and Qwen. Verify expected
dimensions, finite normalized rows, query/document compatibility, runtime,
device, and memory before full execution.

- [ ] **Step 4: Build BGE across the complete canonical tree**

Run the builder, validate its artifact, call it a second time, and prove zero
encoder construction on the completed artifact.

- [ ] **Step 5: Build MedCPT across the complete canonical tree**

Run the builder and validate shape `record_count × 768`, exact ID alignment,
finiteness, runtime, and storage. Call it again to prove zero encoding.

- [ ] **Step 6: Build Qwen across the complete canonical tree**

Run the builder and validate shape `record_count × 1024`, exact ID alignment,
finiteness, runtime, peak memory, and storage. Call it again to prove zero
encoding.

- [ ] **Step 7: Verify Gemini code without paid execution**

Generate deterministic request shards and the token/cost estimate. Confirm the
paid/key guard before client construction. Do not upload, submit, or poll a real
job.

- [ ] **Step 8: Run focused and full verification**

Run: `uv run pytest -q tests/test_embedding_artifacts.py tests/test_build_embedding_artifact.py tests/test_embedding_gemini.py tests/test_adopt_embeddings.py`

Run: `uv run pytest -q`

Expected: all offline tests pass with only documented Postgres skips.

- [ ] **Step 9: Record and commit real-build evidence**

Record per-model revision, shape, ID count, validation, runtime, storage,
truncation, device, skip proof, BGE adoption decision, and Gemini no-submit
status.

```bash
git add pyproject.toml uv.lock wiki/42-Build-Log.md
git commit -m "docs: record canonical embedding artifact builds"
```
