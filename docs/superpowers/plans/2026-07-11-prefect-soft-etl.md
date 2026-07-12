# Prefect SOFT Canonical Record ETL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse every metadata-only GEO family SOFT file into one deterministic canonical record and orchestrate missing-record materialization with bounded Prefect 3 batches.

**Architecture:** A pure streaming parser owns SOFT fidelity, normalization, discovery, and atomic writes. A thin Prefect flow inventories once, submits batches to a bounded thread runner, resolves all futures, publishes a report, and passes exactly the newly created GSEs to the embedding owner.

**Tech Stack:** Python 3.11+, stdlib gzip/json/pathlib, Prefect `>=3,<4`, pytest, uv.

## Global Constraints

- Inputs come only from `data/processed/soft_meta/`.
- Existing canonical records skip without opening either source or output.
- Existence is the only record state; do not add hashes, mtime checks, versions, snapshots, or update detection.
- Publish records and the latest report atomically.
- Use one Prefect task per bounded batch, not per GSE.
- Use `ThreadPoolTaskRunner(max_workers=8)` by default and resolve every future.
- Run without Prefect Cloud or a mandatory local server.
- Apply existing organism, sex, and assay normalizers, but keep `embed_text` raw-field based.

---

### Task 1: Define canonical SOFT parsing behavior

**Files:**
- Create: `tests/fixtures/soft/minimal_family.soft.gz`
- Create: `tests/fixtures/soft/repeated_characteristics_family.soft.gz`
- Create: `tests/test_soft_records.py`
- Create: `src/geo_index/soft_records.py`

**Interfaces:**
- Produces: `record_path(records_root: Path, gse: str) -> Path`
- Produces: `parse_soft_record(source: Path, *, soft_root: Path) -> dict[str, object]`
- Produces: `normalize_soft_record(record: Mapping[str, object]) -> dict[str, object]`
- Produces: `compose_soft_embed_text(record: Mapping[str, object]) -> str`

- [ ] **Step 1: Add synthetic family SOFT fixtures**

Include one series, platform, and two samples. Cover repeated series values,
unknown attributes, repeated sample attributes, a characteristic containing a
second colon, human organism, female sex, and RNA-Seq assay text.

- [ ] **Step 2: Write parser contract tests**

```python
def test_record_path_uses_geo_bucket(tmp_path: Path) -> None:
    assert record_path(tmp_path, "GSE271800") == (
        tmp_path / "GSE271nnn" / "GSE271800.json"
    )


def test_parser_preserves_repeated_attributes_and_associations(fixtures: Path) -> None:
    record = parse_soft_record(
        fixtures / "repeated_characteristics_family.soft.gz",
        soft_root=fixtures,
    )
    assert record["series_attributes"]["Series_relation"] == [
        "BioProject: https://example/one",
        "SRA: https://example/two",
    ]
    assert record["samples"][0]["characteristics"][0] == {
        "name": "disease",
        "value": "status: active",
        "raw": "disease: status: active",
    }
```

Also assert every locked top-level key, stable distinct aggregates, sample/GPL
numeric ordering, ISO dates, complete attribute maps, normalizer outputs, raw
`embed_text`, filename mismatch rejection, missing series accession rejection,
and declared-sample/block mismatch rejection.

- [ ] **Step 3: Run the focused tests and confirm RED**

Run: `uv run pytest -q tests/test_soft_records.py`

Expected: collection fails because `geo_index.soft_records` does not exist.

- [ ] **Step 4: Implement the streaming parser**

Use explicit state and ordered maps:

```python
for raw_line in gzip.open(source, "rt", encoding="utf-8", errors="replace"):
    line = raw_line.rstrip("\r\n")
    if line.startswith("^"):
        record_type, separator, accession = line[1:].partition(" = ")
        if not separator or record_type not in {"SERIES", "PLATFORM", "SAMPLE"}:
            raise SoftParseError(f"malformed record header: {line}")
        start_record(record_type, accession)
    elif line.startswith("!"):
        key, separator, value = line[1:].partition(" = ")
        if not separator:
            raise SoftParseError(f"malformed attribute: {line}")
        current_attributes.setdefault(key, []).append(value)
```

Project scalar fields with first-or-empty helpers, aggregate distinct values
through a stable sorter, normalize GEO dates with `%b %d %Y`, and validate the
declared/parsed sample identity lists before returning.

- [ ] **Step 5: Run parser tests and confirm GREEN**

Run: `uv run pytest -q tests/test_soft_records.py`

Expected: all tests pass.

- [ ] **Step 6: Commit the parser**

```bash
git add tests/fixtures/soft tests/test_soft_records.py src/geo_index/soft_records.py
git commit -m "feat: parse canonical records from stripped SOFT"
```

### Task 2: Materialize missing records atomically

**Files:**
- Create: `tests/test_soft_record_materialization.py`
- Modify: `src/geo_index/soft_records.py`

**Interfaces:**
- Consumes: `record_path`, `parse_soft_record`
- Produces: `RecordJob`, `MaterializeResult`, `BatchResult`
- Produces: `discover_missing(soft_root: Path, records_root: Path) -> DiscoveryResult`
- Produces: `materialize_record(job: RecordJob) -> MaterializeResult`
- Produces: `materialize_batch(jobs: Sequence[RecordJob]) -> BatchResult`

- [ ] **Step 1: Write no-read and atomicity tests**

```python
def test_discovery_does_not_open_completed_source(tmp_path: Path, monkeypatch) -> None:
    completed = make_soft(tmp_path, "GSE1")
    missing = make_soft(tmp_path, "GSE2")
    write_completed_record(tmp_path, "GSE1")
    real_open = Path.open
    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *a, **kw: (_ for _ in ()).throw(AssertionError(self))
        if self == completed else real_open(self, *a, **kw),
    )
    discovery = discover_missing(soft_root(tmp_path), records_root(tmp_path))
    assert [job.gse for job in discovery.jobs] == ["GSE2"]
```

Add tests for deterministic JSON bytes, temp cleanup after parser failure,
successful `os.replace`, numeric job order, and delete-then-rebuild.

- [ ] **Step 2: Run materialization tests and confirm RED**

Run: `uv run pytest -q tests/test_soft_record_materialization.py`

Expected: imports or assertions fail because discovery/materialization is absent.

- [ ] **Step 3: Implement existence-only discovery and atomic writes**

```python
destination.parent.mkdir(parents=True, exist_ok=True)
temporary = destination.with_suffix(destination.suffix + ".tmp")
try:
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
finally:
    temporary.unlink(missing_ok=True)
```

Catch per-record exceptions in `materialize_batch`, returning structured
failures while retaining successful siblings.

- [ ] **Step 4: Run materialization and parser tests**

Run: `uv run pytest -q tests/test_soft_records.py tests/test_soft_record_materialization.py`

Expected: all tests pass.

- [ ] **Step 5: Commit materialization**

```bash
git add src/geo_index/soft_records.py tests/test_soft_record_materialization.py
git commit -m "feat: materialize missing canonical series records"
```

### Task 3: Add the bounded Prefect flow and CLI

**Files:**
- Create: `tests/test_prefect_etl.py`
- Create: `src/geo_index/prefect_etl.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: `discover_missing`, `materialize_batch`
- Consumes: `build_missing_embeddings(records_root, store_path, model_key, *, replace_gses, allow_paid_gemini)`
- Produces: `EtlReport`
- Produces: `geo_soft_etl(...) -> EtlReport`
- Produces CLI: `geo-soft-etl = "geo_index.prefect_etl:main"`

- [ ] **Step 1: Add Prefect and lock once**

Run: `uv add 'prefect>=3,<4'`

Expected: `pyproject.toml` and `uv.lock` contain Prefect 3.

- [ ] **Step 2: Write flow tests with fake batches and fake embeddings**

```python
def test_created_gses_are_replace_gses(tmp_path: Path, monkeypatch) -> None:
    calls: list[set[str]] = []
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *a, replace_gses, **kw: calls.append(set(replace_gses)) or fake_result(),
    )
    report = prefect_etl.geo_soft_etl.fn(soft_root=soft, records_root=records)
    assert calls == [{"GSE1", "GSE2"}]
    assert report.created == 2
```

Test batch sizes `250, 250, 1`, partial failure reporting, every future's
`.result()` call, a no-work second run, explicit record rebuild, atomic report
publication, no server requirement, and CLI nonzero on failures.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `uv run pytest -q tests/test_prefect_etl.py`

Expected: collection fails because `geo_index.prefect_etl` does not exist.

- [ ] **Step 4: Implement the batch task and flow**

```python
@task(retries=2, retry_delay_seconds=5)
def parse_batch(jobs: tuple[RecordJob, ...]) -> BatchResult:
    return materialize_batch(jobs)


@flow(
    name="geo-soft-etl",
    task_runner=ThreadPoolTaskRunner(max_workers=8),
    log_prints=True,
)
def geo_soft_etl(...):
    discovery = discover_missing(soft_root, records_root)
    futures = [parse_batch.submit(tuple(batch)) for batch in chunked(discovery.jobs, parse_batch_size)]
    results = [future.result() for future in futures]
```

Aggregate created GSEs and failures, call `build_missing_embeddings` with
`replace_gses=frozenset(created)`, atomically write the report, and expose CLI
flags for the SOFT root, records root, batch size, and worker count. Keep the
embedding store, model key, and paid-work policy fixed inside the locked flow
signature for this prototype.
Use `geo_soft_etl.with_options(task_runner=ThreadPoolTaskRunner(max_workers=n))`
for the CLI worker override so the flow signature remains stable.

- [ ] **Step 5: Run focused and full tests**

Run: `uv run pytest -q tests/test_prefect_etl.py`

Run: `uv run pytest -q`

Expected: focused tests pass; full offline suite passes with only documented
Postgres skips.

- [ ] **Step 6: Commit Prefect orchestration**

```bash
git add pyproject.toml uv.lock src/geo_index/prefect_etl.py tests/test_prefect_etl.py
git commit -m "feat: orchestrate idempotent SOFT record ETL with Prefect"
```

### Task 4: Prove real-data resumability and run the complete corpus

**Files:**
- Modify: `wiki/42-Build-Log.md`

**Interfaces:**
- Consumes CLI: `uv run geo-soft-etl`
- Consumes CLI: `uv run geo-validate-soft --limit 5000`
- Produces: measured ETL evidence and complete `series_records/` tree

- [ ] **Step 1: Run a 500-input slice in an isolated data root**

Create a stable 500-file symlink/copy inventory in temporary storage, run the
CLI with a fake embedding hook, and record elapsed time, failures, record bytes,
and peak RSS.

- [ ] **Step 2: Run the identical slice again**

Expected report: `created=0`, `failed=0`, and zero parser/source-open calls.

- [ ] **Step 3: Delete one known slice record and rerun**

Expected report: exactly one created GSE, and the fake embedding hook receives
exactly that singleton as `replace_gses`.

- [ ] **Step 4: Validate source metadata independently**

Run: `uv run geo-validate-soft --limit 5000`

Expected: zero validation failures.

- [ ] **Step 5: Run the complete current SOFT inventory**

Run the direct CLI against `data/processed/soft_meta` and
`data/processed/series_records`, resolving every Prefect batch. Preserve
successful outputs if malformed inputs exist and rerun to prove all completed
records skip.

- [ ] **Step 6: Record and commit measurements**

Record discovered/skipped/created/failed counts, timings, storage, second-run
zero-parse evidence, and deletion/rebuild evidence.

```bash
git add wiki/42-Build-Log.md
git commit -m "docs: record Prefect SOFT ETL validation"
```
