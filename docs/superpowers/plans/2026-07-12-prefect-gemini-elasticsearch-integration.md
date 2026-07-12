# Prefect Gemini and Elasticsearch Completion Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the approved primary Gemini-to-Elasticsearch Prefect cutover, then finish, upload, and verify the live 249,736-record artifact.

**Architecture:** Build on the in-progress primary-cutover implementation instead of replacing it. Keep Gemini and Elasticsearch required, add explicit concurrency propagation, validate Gemini before any Elasticsearch write, load all available registered artifacts to preserve vector fields, and fail unless Gemini coverage equals document count.

**Tech Stack:** Python 3.11+, Prefect 3, `google-genai`, NumPy, Elasticsearch 9.4, argparse, pytest

## Global Constraints

- The approved Elasticsearch Primary Cutover Design is authoritative.
- `geo-soft-etl` requires Gemini and Elasticsearch for success.
- Paid Gemini requires explicit `--allow-paid-gemini`.
- Gemini concurrency defaults to `1`; production uses `4`.
- Never start a second Gemini writer against the same temporary state.
- Preserve request/model/dimension/shard/pricing and canonical artifact contracts.
- Bulk `index` replaces whole sources; load every available registered vector artifact.
- Validate the Gemini artifact before Elasticsearch writes.
- Require `embedding_gemini_3072` coverage equal to document count.
- Credentials remain environment-only and never enter reports or logs.
- Offline tests use fakes only and make no provider or Elasticsearch calls.
- Preserve all unrelated and concurrent working-tree changes.

---

### Task 1: Add Prefect concurrency and fail-closed validation

**Files:**
- Modify: `src/geo_index/prefect_etl.py`
- Modify: `tests/test_prefect_etl.py`

**Interfaces:**
- Consumes: the in-progress primary cutover's required Gemini/Elasticsearch flow.
- Produces: `geo_soft_etl(..., gemini_concurrency: int = 1)` and CLI `--gemini-concurrency`.

- [ ] **Step 1: Write failing early-validation test**

```python
def test_paid_authorization_and_concurrency_fail_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda *_args: (_ for _ in ()).throw(AssertionError("discovered")),
    )
    with pytest.raises(ValueError, match="allow-paid-gemini"):
        geo_soft_etl.fn(allow_paid_gemini=False)
    with pytest.raises(ValueError, match="concurrency"):
        geo_soft_etl.fn(allow_paid_gemini=True, gemini_concurrency=0)
```

- [ ] **Step 2: Write failing forwarding test**

Update the existing flow test so the fake embedding builder records
`gemini_concurrency`, invoke the flow with `allow_paid_gemini=True` and
`gemini_concurrency=4`, and require:

```python
assert embedding_calls == [{
    "model_key": "gemini_embedding_2_3072_v1",
    "replace_gses": frozenset({"GSE2", "GSE20"}),
    "allow_paid_gemini": True,
    "gemini_concurrency": 4,
}]
```

Every other direct flow test that reaches discovery must pass
`allow_paid_gemini=True`; provider execution remains monkeypatched.

- [ ] **Step 3: Run focused tests to verify RED**

Run:

```bash
uv run pytest -q tests/test_prefect_etl.py -k 'authorization_and_concurrency or partial_failures'
```

Expected: failures for missing `gemini_concurrency` and missing early validation.

- [ ] **Step 4: Implement validation and propagation**

At the start of `geo_soft_etl`:

```python
if not allow_paid_gemini:
    raise ValueError("--allow-paid-gemini is required for the primary Gemini ETL")
if gemini_concurrency < 1:
    raise ValueError("Gemini concurrency must be at least 1")
```

Add `gemini_concurrency: int = 1` to the flow and pass it to
`build_missing_embeddings(...)`.

Add to `_parser()`:

```python
parser.add_argument("--gemini-concurrency", type=int, default=1)
```

Validate the same bounds in `main()` before configuring the flow and pass
`gemini_concurrency=args.gemini_concurrency`.

- [ ] **Step 5: Run all Prefect tests to verify GREEN**

Run:

```bash
uv run pytest -q tests/test_prefect_etl.py
```

Expected: all Prefect tests pass offline.

- [ ] **Step 6: Commit Task 1**

Stage only the Prefect files after the concurrent primary-cutover task has
committed its own changes:

```bash
git add src/geo_index/prefect_etl.py tests/test_prefect_etl.py
git commit -m "feat: configure primary Gemini ETL concurrency"
```

---

### Task 2: Preserve all vectors and require complete Gemini coverage

**Files:**
- Modify: `src/geo_index/prefect_etl.py`
- Modify: `tests/test_prefect_etl.py`

**Interfaces:**
- Consumes: `VECTOR_FIELDS`, `load_artifact(...)`, `load_index(...)`, and the existing Elasticsearch load counters.
- Produces: all-model replacement documents and fail-closed Gemini coverage validation.

- [ ] **Step 1: Write failing preservation test**

Update the existing successful load test to fake Gemini artifact validation and
require all registered keys:

```python
validated: list[tuple[Path, str]] = []
monkeypatch.setattr(
    prefect_etl,
    "load_artifact",
    lambda path, spec: validated.append((path, spec.model_key)) or object(),
)

assert load_calls == [{
    "records_root": records_root,
    "artifacts_root": artifacts_root,
    "model_keys": tuple(VECTOR_FIELDS),
    "batch_size": 17,
    "max_item_retries": 4,
}]
assert validated == [(
    artifacts_root / "gemini_embedding_2_3072_v1",
    "gemini_embedding_2_3072_v1",
)]
```

- [ ] **Step 2: Write failing incomplete-coverage test**

```python
def test_incomplete_gemini_coverage_fails_and_closes_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_elasticsearch_stage,
) -> None:
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda _soft_root, _records_root: DiscoveryResult(0, 0, ()),
    )
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *_args, **_kwargs: _fake_embedding_result("skipped"),
    )
    monkeypatch.setattr(prefect_etl, "load_artifact", lambda *_args: object())
    incomplete = _fake_load_report()
    incomplete = dataclasses.replace(
        incomplete,
        document_count=2,
        vector_coverage={"embedding_gemini_3072": 1},
    )
    monkeypatch.setattr(prefect_etl, "load_index", lambda *_args, **_kwargs: incomplete)

    report = geo_soft_etl.fn(
        soft_root=tmp_path,
        records_root=tmp_path / "records",
        allow_paid_gemini=True,
    )

    assert report.elasticsearch_status == "failed"
    assert report.elasticsearch_error == (
        "ValueError: incomplete Gemini vector coverage: 1/2"
    )
    assert report.succeeded is False
    assert fake_elasticsearch_stage[0].closed is True
```

Import `dataclasses` and `VECTOR_FIELDS` in the test module.

- [ ] **Step 3: Write failing missing-artifact/no-client test**

```python
def test_missing_gemini_artifact_prevents_elasticsearch_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda _soft_root, _records_root: DiscoveryResult(0, 0, ()),
    )
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *_args, **_kwargs: _fake_embedding_result("skipped"),
    )
    monkeypatch.setattr(
        prefect_etl,
        "load_artifact",
        lambda *_args: (_ for _ in ()).throw(ValueError("missing artifact")),
    )
    created: list[object] = []
    monkeypatch.setattr(prefect_etl, "create_client", lambda settings: created.append(settings))

    report = geo_soft_etl.fn(
        soft_root=tmp_path,
        records_root=tmp_path / "records",
        allow_paid_gemini=True,
    )

    assert report.elasticsearch_error == "ValueError: missing artifact"
    assert created == []
```

- [ ] **Step 4: Run focused tests to verify RED**

Run:

```bash
uv run pytest -q tests/test_prefect_etl.py -k 'loads_only_gemini or incomplete_gemini or missing_gemini'
```

Expected: the loader still receives only Gemini, no artifact preflight occurs,
and incomplete coverage is accepted.

- [ ] **Step 5: Implement artifact, preservation, and coverage gates**

Import `load_artifact` from `elasticsearch_sources`. Before settings/client
creation:

```python
gemini_spec = VECTOR_FIELDS[DEFAULT_EMBEDDING_MODEL_KEY]
load_artifact(
    resolved_artifacts_root / DEFAULT_EMBEDDING_MODEL_KEY,
    gemini_spec,
)
```

Change the loader call to:

```python
load_report = load_index(
    client,
    records_root=records_root,
    artifacts_root=resolved_artifacts_root,
    model_keys=tuple(VECTOR_FIELDS),
    batch_size=elasticsearch_batch_size,
    max_item_retries=elasticsearch_max_item_retries,
)
```

Before setting status to indexed:

```python
vector_count = load_report.vector_coverage.get(gemini_spec.field, 0)
if vector_count != load_report.document_count:
    raise ValueError(
        "incomplete Gemini vector coverage: "
        f"{vector_count}/{load_report.document_count}"
    )
```

Keep report counters and client closure behavior unchanged.

- [ ] **Step 6: Run focused and cross-module tests to verify GREEN**

Run:

```bash
uv run pytest -q tests/test_prefect_etl.py tests/test_elasticsearch_loader.py tests/test_elasticsearch_sources.py tests/test_elasticsearch_index.py tests/test_elasticsearch_config.py
```

Expected: all selected tests pass offline.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/geo_index/prefect_etl.py tests/test_prefect_etl.py
git commit -m "fix: require complete Gemini Elasticsearch coverage"
```

---

### Task 3: Document and verify the primary command

**Files:**
- Modify: `README.md`
- Modify only if already part of the cutover: `wiki/21-Ingestion-Pipeline.md`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: the explicit production command and multi-vector preservation explanation.

- [ ] **Step 1: Update the production command**

Require:

```bash
set -a
source .env
source .env.elasticsearch
set +a
uv run geo-soft-etl \
  --allow-paid-gemini \
  --gemini-concurrency 4
```

Document that Gemini and Elasticsearch are required primary stages, the loader
includes all available registered artifacts, and success requires full Gemini
coverage.

- [ ] **Step 2: Run static and full verification**

Run:

```bash
uv run ruff check src/geo_index/prefect_etl.py tests/test_prefect_etl.py
uv run pytest -q
git diff --check
```

Expected: lint and whitespace checks pass; all offline tests pass; guarded live
tests may skip.

- [ ] **Step 3: Commit Task 3**

Stage only the documentation lines changed for this hardening after the primary
cutover commit exists:

```bash
git add README.md wiki/21-Ingestion-Pipeline.md
git commit -m "docs: verify primary Gemini Elasticsearch pipeline"
```

---

### Task 4: Finish, upload, and prove the live result

**Files:**
- Verify: `data/processed/embedding_artifacts/.gemini_embedding_2_3072_v1.tmp/gemini_state.json`
- Verify: `data/processed/embedding_artifacts/gemini_embedding_2_3072_v1/{vectors.npy,ids.json,metadata.json}`
- Produce: `data/processed/elasticsearch_load_report.json`
- Produce: `data/processed/soft_etl_report.json`

**Interfaces:**
- Consumes: the merged primary cutover plus Tasks 1–3, active Gemini coordinator, and healthy local Elasticsearch.
- Produces: validated artifact, full live Gemini coverage, and idempotent loader/Prefect evidence.

- [ ] **Step 1: Monitor the existing single writer to completion**

Verify `active <= 4`, no terminal failures, and monotonically increasing local
results. Do not launch another builder while the current process is alive.

- [ ] **Step 2: Validate the final artifact**

```bash
uv run python -c '
from pathlib import Path
from geo_index.embedding_artifacts import validate_artifact
from geo_index.embedding_registry import get_variant
p = Path("data/processed/embedding_artifacts/gemini_embedding_2_3072_v1")
m = validate_artifact(p, get_variant("gemini_embedding_2_3072_v1"))
print({"record_count": m.record_count, "dimensions": m.dimensions, "model_key": m.model_key})
'
```

Require 249,736 records, 3,072 dimensions, and the Gemini model key.

- [ ] **Step 3: Prove builder idempotence**

After the coordinator exits, source `.env` and run the identical builder with
concurrency four. Require JSON `status=skipped` and no provider work.

- [ ] **Step 4: Load all artifacts into Elasticsearch twice**

Source `.worktrees/elasticsearch-foundation/.env.elasticsearch` without
printing it and run `geo-elasticsearch-load` with default all-model behavior.
Require zero failures, document count 249,736, and Gemini coverage 249,736.
Run the identical load again and require unchanged counts.

- [ ] **Step 5: Prove the patched Prefect path**

With both credential files sourced, run:

```bash
uv run geo-soft-etl --allow-paid-gemini --gemini-concurrency 4
```

Require skipped complete Gemini work, successful Elasticsearch reload, full
coverage, terminal report evidence, and exit zero.

- [ ] **Step 6: Completion audit**

Re-run the full offline suite and inspect the artifact, load report, Prefect
report, and live Elasticsearch counts against every approved requirement. Mark
the goal complete only if all evidence agrees.
