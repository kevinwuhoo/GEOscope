# Gemini Batch Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resume the paused Gemini embedding build with one crash-safe coordinator maintaining up to four concurrent provider jobs and assembling results in canonical order.

**Architecture:** Keep one process, one SDK client, and one atomic state writer. A cooperative scheduler round-robin polls persisted jobs, downloads completions, and fills open slots in deterministic shard order. Definitive HTTP 429 creation failures receive reconciled, bounded backoff; every other ambiguous submission remains fail-closed.

**Tech Stack:** Python 3.11+, `google-genai`, NumPy, JSON/JSONL, argparse, pytest

## Global Constraints

- Production concurrency is explicitly `4`; concurrency `1` remains the default.
- Do not change request text, model ID, 3,072 dimensions, shard boundaries, or price estimation.
- Do not call synchronous per-document token counting.
- Do not add threads, processes, state locks, or multiple writers for `gemini_state.json`.
- Persist every upload ID, submission identity, job ID, state, and output ID before advancing.
- Never blindly resubmit completed, submitted, or ambiguous paid work.
- Assemble in deterministic shard-index and GSE order, never provider completion order.
- Tests use fake clients only; they must not source `.env` or make provider calls.
- Preserve the paused state: 12 downloaded shards and shard index 12 succeeded provider-side awaiting download.
- Leave unrelated working-tree changes untouched.

## File structure

- `src/geo_index/build_embedding_artifact.py`: CLI parsing and propagation of `gemini_concurrency`.
- `src/geo_index/embedding_gemini.py`: serialized provider scheduler, retry metadata, downloads, and ordered assembly.
- `tests/test_build_embedding_artifact.py`: CLI/default/propagation coverage.
- `tests/test_embedding_gemini.py`: concurrency, ordering, resume, failure, and quota coverage.
- `README.md`: safe concurrency-four resume command.

---

### Task 1: Add and validate the concurrency interface

**Files:**
- Modify: `src/geo_index/build_embedding_artifact.py:48-65,155-162,193-200,250-315`
- Modify: `src/geo_index/embedding_gemini.py:337-360`
- Test: `tests/test_build_embedding_artifact.py`
- Test: `tests/test_embedding_gemini.py`

**Interfaces:**
- Produces: `build_gemini_vectors(records: Sequence[RecordRef], variant: EmbeddingVariant, temp_dir: Path, *, allow_paid: bool, concurrency: int = 1) -> LocalProviderResult`
- Produces: `build_embedding_artifact(records_root: Path, output_root: Path, model_key: str, *, allow_paid_gemini: bool, gemini_concurrency: int = 1) -> EmbeddingBuildResult`
- Produces: CLI `--gemini-concurrency N`, default `1`

- [ ] **Step 1: Write failing propagation and parser tests**

Add these tests to `tests/test_build_embedding_artifact.py`, reusing existing imports and adding `geo_index.embedding_gemini as gemini` and `get_variant` if missing:

~~~python
def test_encode_forwards_explicit_gemini_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build(records, variant, temp_dir, *, allow_paid, concurrency):
        captured["allow_paid"] = allow_paid
        captured["concurrency"] = concurrency
        return SimpleNamespace(vectors=np.empty((0, 3072), dtype=np.float32))

    monkeypatch.setattr(gemini, "build_gemini_vectors", fake_build)
    builder._encode(
        get_variant("gemini_embedding_2_3072_v1"),
        (),
        tmp_path,
        allow_paid_gemini=True,
        gemini_concurrency=4,
    )

    assert captured == {"allow_paid": True, "concurrency": 4}


def test_parser_defaults_to_sequential_gemini_batches() -> None:
    args = builder._parser().parse_args(
        ["--model-key", "gemini_embedding_2_3072_v1"]
    )
    assert args.gemini_concurrency == 1


def test_parser_accepts_explicit_gemini_concurrency() -> None:
    args = builder._parser().parse_args(
        [
            "--model-key",
            "gemini_embedding_2_3072_v1",
            "--gemini-concurrency",
            "4",
        ]
    )
    assert args.gemini_concurrency == 4
~~~

- [ ] **Step 2: Write the failing direct-provider validation test**

Add to `tests/test_embedding_gemini.py`:

~~~python
def test_invalid_concurrency_is_rejected_before_client_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        gemini,
        "_create_client",
        lambda key: (_ for _ in ()).throw(AssertionError("client constructed")),
    )

    with pytest.raises(ValueError, match="concurrency must be at least 1"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=0,
        )
~~~

- [ ] **Step 3: Run the tests and verify RED**

~~~bash
uv run pytest -q   tests/test_build_embedding_artifact.py::test_encode_forwards_explicit_gemini_concurrency   tests/test_build_embedding_artifact.py::test_parser_defaults_to_sequential_gemini_batches   tests/test_build_embedding_artifact.py::test_parser_accepts_explicit_gemini_concurrency   tests/test_embedding_gemini.py::test_invalid_concurrency_is_rejected_before_client_construction
~~~

Expected: missing keyword parameters/options and validation cause failures.

- [ ] **Step 4: Implement minimal plumbing**

Change the provider signature and validate before request preparation/client construction:

~~~python
def build_gemini_vectors(
    records: Sequence[RecordRef],
    variant: EmbeddingVariant,
    temp_dir: Path,
    *,
    allow_paid: bool,
    concurrency: int = 1,
) -> LocalProviderResult:
    if concurrency < 1:
        raise ValueError("Gemini concurrency must be at least 1")
    estimate = prepare_gemini_requests(records, variant, temp_dir)
~~~

Add `gemini_concurrency: int = 1` to `_encode`, `_build`, `build_embedding_artifact`, and `build_missing_embeddings`. Forward it only to Gemini:

~~~python
return build_gemini_vectors(
    records,
    variant,
    temp_dir,
    allow_paid=allow_paid_gemini,
    concurrency=gemini_concurrency,
)
~~~

Add and forward the CLI option:

~~~python
parser.add_argument("--gemini-concurrency", type=int, default=1)
~~~

~~~python
result = build_embedding_artifact(
    args.records_root,
    args.output_root,
    args.model_key,
    allow_paid_gemini=args.allow_paid_gemini,
    gemini_concurrency=args.gemini_concurrency,
)
~~~

- [ ] **Step 5: Run focused suites**

~~~bash
uv run pytest -q tests/test_build_embedding_artifact.py tests/test_embedding_gemini.py
~~~

Expected: all tests pass and no real client is constructed.

- [ ] **Step 6: Commit**

~~~bash
git add src/geo_index/build_embedding_artifact.py   src/geo_index/embedding_gemini.py   tests/test_build_embedding_artifact.py   tests/test_embedding_gemini.py
git commit -m "feat: configure Gemini batch concurrency"
~~~

---

### Task 2: Implement the single-writer cooperative scheduler

**Files:**
- Modify: `src/geo_index/embedding_gemini.py:318-502`
- Modify: `tests/test_embedding_gemini.py:203-624`

**Interfaces:**
- Consumes: `build_gemini_vectors(records: Sequence[RecordRef], variant: EmbeddingVariant, temp_dir: Path, *, allow_paid: bool, concurrency: int = 1) -> LocalProviderResult`
- Produces: `_run_batch_lifecycle(client, estimate: GeminiRequestEstimate, state: dict[str, object], state_path: Path, temp_dir: Path, variant: EmbeddingVariant, *, concurrency: int, sleep_fn=time.sleep, now_fn=time.time) -> None`
- Produces: deterministic local result path helper

- [ ] **Step 1: Add deterministic multi-job fakes**

Add to `tests/test_embedding_gemini.py`:

~~~python
def _many_records(count: int) -> tuple[RecordRef, ...]:
    return tuple(
        RecordRef(
            f"GSE{index + 1}",
            f"Title {index + 1}",
            f"document {index + 1}",
            Path(f"GSE{index + 1}.json"),
        )
        for index in range(count)
    )


class CooperativeClient:
    def __init__(self, *, running_polls: int = 1) -> None:
        self.events: list[tuple[str, str]] = []
        self.polls: dict[str, int] = {}
        self.running_polls = running_polls
        self.files = SimpleNamespace(
            upload=self.upload,
            download=self.download,
        )
        self.batches = SimpleNamespace(
            create_embeddings=self.create_embeddings,
            get=self.get,
            list=lambda: (),
        )

    def upload(self, *, file, config):
        index = Path(file).stem.rsplit("-", 1)[1]
        self.events.append(("upload", index))
        return SimpleNamespace(name=f"files/input-{index}")

    def create_embeddings(self, *, model, src, config):
        index = src["file_name"].rsplit("-", 1)[1]
        self.events.append(("create", index))
        return SimpleNamespace(name=f"batches/job-{index}")

    def get(self, *, name):
        index = name.rsplit("-", 1)[1]
        count = self.polls.get(index, 0)
        self.polls[index] = count + 1
        self.events.append(("get", index))
        state = (
            "JOB_STATE_RUNNING"
            if count < self.running_polls
            else "JOB_STATE_SUCCEEDED"
        )
        return SimpleNamespace(
            name=name,
            state=SimpleNamespace(name=state),
            dest=SimpleNamespace(
                file_name=f"files/output-{index}"
                if state == "JOB_STATE_SUCCEEDED"
                else None
            ),
            error=None,
        )

    def download(self, *, file):
        index = file.rsplit("-", 1)[1]
        self.events.append(("download", index))
        gse = f"GSE{int(index) + 1}"
        return (json.dumps(_response(gse, float(int(index) + 1))) + "\n").encode()

    @property
    def models(self):
        raise AssertionError("synchronous models API must not be used")
~~~

- [ ] **Step 2: Write failing four-slot and sequential-compatibility tests**

~~~python
def test_coordinator_fills_four_slots_before_polling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)
    monkeypatch.setattr(gemini.time, "sleep", lambda seconds: None)
    client = CooperativeClient(running_polls=1)
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    result = build_gemini_vectors(
        _many_records(5),
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=4,
    )

    creates = [
        position for position, event in enumerate(client.events)
        if event[0] == "create"
    ]
    first_get = next(
        position for position, event in enumerate(client.events)
        if event[0] == "get"
    )
    assert all(position < first_get for position in creates[:4])
    assert creates[4] > first_get
    assert result.vectors.shape == (5, 3072)
    assert result.vectors[:, 0].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_concurrency_one_preserves_sequential_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)
    client = CooperativeClient(running_polls=0)
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    build_gemini_vectors(
        _many_records(2),
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=1,
    )

    assert client.events == [
        ("upload", "00000"),
        ("create", "00000"),
        ("get", "00000"),
        ("download", "00000"),
        ("upload", "00001"),
        ("create", "00001"),
        ("get", "00001"),
        ("download", "00001"),
    ]
~~~

- [ ] **Step 3: Write the mixed-state resume test**

Prepare four one-record shards. Write shard 0's result locally; give shards 1
and 2 persisted upload/job IDs; leave shard 3 pending. Use `CooperativeClient`
with immediate success, call concurrency four, and assert:

~~~python
assert ("upload", "00000") not in client.events
assert ("create", "00000") not in client.events
assert ("upload", "00001") not in client.events
assert ("create", "00001") not in client.events
assert ("upload", "00002") not in client.events
assert ("create", "00002") not in client.events
assert ("download", "00001") in client.events
assert ("download", "00002") in client.events
assert result.vectors[:, 0].tolist() == [1.0, 2.0, 3.0, 4.0]
~~~

Construct the state with `prepare_gemini_requests`, `_load_state`, and
`_atomic_json` exactly as existing resume tests do. This test models the paused
production boundary: a provider-succeeded persisted job must download without
upload/create.

Add the over-limit resume test with three persisted jobs, one pending shard,
and configured concurrency two:

~~~python
def test_resumed_jobs_above_limit_are_polled_before_new_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)
    records = _many_records(4)
    estimate = prepare_gemini_requests(records, VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    for index in range(3):
        state["shards"][index].update(
            uploaded_file_name=f"files/input-{index:05d}",
            submission_display_name=f"display-{index:05d}",
            job_name=f"batches/job-{index:05d}",
            job_state="JOB_STATE_RUNNING",
        )
    gemini._atomic_json(state_path, state)
    client = CooperativeClient(running_polls=0)
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    build_gemini_vectors(
        records,
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=2,
    )

    assert client.events[:3] == [
        ("get", "00000"),
        ("get", "00001"),
        ("get", "00002"),
    ]
    assert client.events.index(("upload", "00003")) > 2
~~~

Add the terminal-failure test:

~~~python
def test_terminal_failure_stops_new_submissions_and_preserves_active_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)

    class FailingFirstClient(CooperativeClient):
        def get(self, *, name):
            index = name.rsplit("-", 1)[1]
            self.events.append(("get", index))
            state = "JOB_STATE_FAILED" if index == "00000" else "JOB_STATE_RUNNING"
            return SimpleNamespace(
                name=name,
                state=SimpleNamespace(name=state),
                dest=SimpleNamespace(file_name=None),
                error="provider failure" if state == "JOB_STATE_FAILED" else None,
            )

    client = FailingFirstClient()
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(RuntimeError, match="batches/job-00000.*JOB_STATE_FAILED"):
        build_gemini_vectors(
            _many_records(5),
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=4,
        )

    creates = [event for event in client.events if event[0] == "create"]
    assert creates == [
        ("create", "00000"),
        ("create", "00001"),
        ("create", "00002"),
        ("create", "00003"),
    ]
    state = json.loads((tmp_path / "gemini_state.json").read_text())
    assert [state["shards"][index]["job_name"] for index in range(1, 4)] == [
        "batches/job-00001",
        "batches/job-00002",
        "batches/job-00003",
    ]
~~~

- [ ] **Step 4: Run scheduler tests and verify RED**

~~~bash
uv run pytest -q tests/test_embedding_gemini.py   -k 'coordinator or concurrency_one or mixed_state'
~~~

Expected: current code polls shard 0 before creating four jobs and cannot satisfy
the mixed-state concurrent assertions.

- [ ] **Step 5: Extract lifecycle helpers**

Add:

~~~python
def _result_path(temp_dir: Path, shard: GeminiRequestShard) -> Path:
    return temp_dir / f"gemini_results-{shard.index:05d}.jsonl"


def _download_succeeded_job(
    client,
    job,
    result_path: Path,
    state_path: Path,
    state: dict[str, object],
    raw_shard_state: dict[str, object],
) -> None:
    output_file_name = job.dest.file_name
    if not output_file_name:
        raise RuntimeError(f"Gemini batch {job.name} has no output file")
    raw_shard_state["output_file_name"] = output_file_name
    _atomic_json(state_path, state)
    content = client.files.download(file=output_file_name)
    temporary = result_path.with_suffix(".jsonl.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, result_path)
~~~

Extract the existing upload, legacy-upload check, intent reconciliation,
submission, and job persistence into:

~~~python
def _submit_or_resume_shard(
    client,
    shard: GeminiRequestShard,
    raw_shard_state: dict[str, object],
    state: dict[str, object],
    state_path: Path,
    variant: EmbeddingVariant,
    *,
    shard_count: int,
    now_fn=time.time,
) -> bool:
~~~

Return `True` only when `job_name` is durably stored. Task 3 adds the `False`
quota-backoff path.

- [ ] **Step 6: Implement the cooperative scheduler**

Implement this loop in `_run_batch_lifecycle`:

~~~python
while True:
    incomplete = [
        index for index, shard in enumerate(estimate.shards)
        if not _result_path(temp_dir, shard).exists()
    ]
    if not incomplete:
        return

    made_progress = False
    active = [
        index for index in incomplete
        if state_shards[index].get("job_name")
    ]

    for index in tuple(active):
        raw = state_shards[index]
        job_name = str(raw["job_name"])
        job = client.batches.get(name=job_name)
        previous = raw.get("job_state")
        job_state = _state_name(job)
        raw["job_state"] = job_state
        _atomic_json(state_path, state)
        made_progress = made_progress or previous != job_state
        if job_state == "JOB_STATE_SUCCEEDED":
            _download_succeeded_job(
                client,
                job,
                _result_path(temp_dir, estimate.shards[index]),
                state_path,
                state,
                raw,
            )
            made_progress = True
        elif job_state in TERMINAL_STATES:
            raise RuntimeError(
                f"Gemini batch {job_name} ended as {job_state}: {job.error}"
            )

    active_count = sum(
        1 for index in incomplete
        if state_shards[index].get("job_name")
        and not _result_path(temp_dir, estimate.shards[index]).exists()
    )
    for index in incomplete:
        if active_count >= concurrency:
            break
        shard = estimate.shards[index]
        if _result_path(temp_dir, shard).exists():
            continue
        raw = state_shards[index]
        if raw.get("job_name"):
            continue
        if _submit_or_resume_shard(
            client,
            shard,
            raw,
            state,
            state_path,
            variant,
            shard_count=len(estimate.shards),
            now_fn=now_fn,
        ):
            active_count += 1
            made_progress = True
        else:
            break

    if not made_progress:
        sleep_fn(POLL_SECONDS)
~~~

Validate every `state_shards[index]` is a dictionary before `.get`. If resumed
active jobs exceed concurrency, poll them all and skip fills until the count
drops.

- [ ] **Step 7: Separate provider lifecycle from ordered assembly**

Call `_run_batch_lifecycle` once, then iterate
`zip(estimate.shards, state_shards, strict=True)` in shard order and call
`_assemble_results` for each local result. Append vectors, failures, usage, and
provider IDs only in this second pass. Keep the final
`np.concatenate(vector_batches, axis=0)` unchanged.

- [ ] **Step 8: Run Gemini tests and commit**

~~~bash
uv run pytest -q tests/test_embedding_gemini.py
git add src/geo_index/embedding_gemini.py tests/test_embedding_gemini.py
git commit -m "feat: coordinate concurrent Gemini batch jobs"
~~~

Expected: every existing and new Gemini test passes.

---

### Task 3: Add quota-safe reconciliation and bounded backoff

**Files:**
- Modify: `src/geo_index/embedding_gemini.py:218-259,318-502`
- Modify: `tests/test_embedding_gemini.py`

**Interfaces:**
- Produces optional shard fields `last_create_status`, `submission_retry_count`, and `submission_retry_not_before`.
- Produces: `_matching_submissions(client, display_name: str) -> list[object]`
- Produces: `_quota_backoff_seconds(retry_count: int) -> int`

- [ ] **Step 1: Add a fake clock and failing zero-match retry test**

~~~python
class QuotaError(RuntimeError):
    status_code = 429


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
~~~

Add the complete zero-match retry test:

~~~python
def test_definitive_429_with_zero_matches_backs_off_then_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    clock = FakeClock()
    create_display_names: list[str] = []

    class QuotaBatches:
        def create_embeddings(self, *, model, src, config):
            create_display_names.append(config["display_name"])
            if len(create_display_names) == 1:
                raise QuotaError("queue full")
            return SimpleNamespace(name="batches/job-after-backoff")

        def list(self):
            return ()

        def get(self, *, name):
            return SimpleNamespace(
                name=name,
                state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                dest=SimpleNamespace(file_name="files/output-1"),
                error=None,
            )

    client = FakeClient([_response("GSE2", 2), _response("GSE10", 10)])
    client.batches = QuotaBatches()
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)
    monkeypatch.setattr(gemini.time, "time", clock.time)
    monkeypatch.setattr(gemini.time, "sleep", clock.sleep)

    result = build_gemini_vectors(
        _records(),
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=4,
    )

    assert len(create_display_names) == 2
    assert create_display_names[0] != create_display_names[1]
    assert clock.sleeps[0] == 30
    assert result.vectors.shape == (2, 3072)
~~~

Add the active-job progress test:

~~~python
def test_429_backoff_keeps_polling_existing_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)
    clock = FakeClock()

    class QuotaSecondClient(CooperativeClient):
        def __init__(self) -> None:
            self.second_attempts = 0
            super().__init__(running_polls=0)

        def create_embeddings(self, *, model, src, config):
            index = src["file_name"].rsplit("-", 1)[1]
            self.events.append(("create", index))
            if index == "00001":
                self.second_attempts += 1
                if self.second_attempts == 1:
                    raise QuotaError("queue full")
            return SimpleNamespace(name=f"batches/job-{index}")

    client = QuotaSecondClient()
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)
    monkeypatch.setattr(gemini.time, "time", clock.time)
    monkeypatch.setattr(gemini.time, "sleep", clock.sleep)

    result = build_gemini_vectors(
        _many_records(2),
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=2,
    )

    first_job_poll = client.events.index(("get", "00000"))
    second_retry = max(
        index for index, event in enumerate(client.events)
        if event == ("create", "00001")
    )
    assert first_job_poll < second_retry
    assert ("download", "00000") in client.events
    assert result.vectors.shape == (2, 3072)
~~~

- [ ] **Step 2: Add failing one-match, multiple-match, and non-429 cases**

Add these complete tests:

~~~python
def test_429_reconciliation_accepts_exactly_one_created_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    create_calls: list[str] = []

    class ReconciledBatches:
        def create_embeddings(self, *, model, src, config):
            create_calls.append(config["display_name"])
            raise QuotaError("quota response after create")

        def list(self):
            return (
                SimpleNamespace(
                    name="batches/reconciled",
                    display_name=create_calls[0],
                ),
            )

        def get(self, *, name):
            return SimpleNamespace(
                name=name,
                state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                dest=SimpleNamespace(file_name="files/output-1"),
                error=None,
            )

    client = FakeClient([_response("GSE2", 2), _response("GSE10", 10)])
    client.batches = ReconciledBatches()
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    result = build_gemini_vectors(
        _records(),
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=4,
    )

    assert len(create_calls) == 1
    assert result.usage["provider_job_ids"] == ["batches/reconciled"]


def test_429_reconciliation_with_multiple_jobs_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    create_calls: list[str] = []

    class DuplicateBatches:
        def create_embeddings(self, *, model, src, config):
            create_calls.append(config["display_name"])
            raise QuotaError("quota response after duplicate creates")

        def list(self):
            return tuple(
                SimpleNamespace(
                    name=f"batches/job-{index}",
                    display_name=create_calls[0],
                )
                for index in (1, 2)
            )

    client = SimpleNamespace(
        files=FakeFiles([_response("GSE2", 2), _response("GSE10", 10)]),
        batches=DuplicateBatches(),
    )
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(RuntimeError, match=r"found 2.*refusing to resubmit"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=4,
        )

    assert len(create_calls) == 1


def test_non_429_create_failure_retains_intent_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class FailingCreateBatches:
        def create_embeddings(self, *, model, src, config):
            raise RuntimeError("connection lost")

    first = SimpleNamespace(
        files=FakeFiles([_response("GSE2", 2), _response("GSE10", 10)]),
        batches=FailingCreateBatches(),
    )
    monkeypatch.setattr(gemini, "_create_client", lambda key: first)

    with pytest.raises(RuntimeError, match="connection lost"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=4,
        )

    state = json.loads((tmp_path / "gemini_state.json").read_text())
    persisted = state["shards"][0]
    assert persisted["submission_display_name"]
    assert persisted.get("job_name") is None

    resumed_create_calls: list[dict[str, object]] = []

    class NoMatchBatches:
        def list(self):
            return ()

        def create_embeddings(self, **kwargs):
            resumed_create_calls.append(kwargs)
            raise AssertionError("ambiguous work was resubmitted")

    resumed = SimpleNamespace(files=SimpleNamespace(), batches=NoMatchBatches())
    monkeypatch.setattr(gemini, "_create_client", lambda key: resumed)

    with pytest.raises(RuntimeError, match="cannot safely reconcile"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=4,
        )

    assert resumed_create_calls == []
~~~

- [ ] **Step 3: Run quota tests and verify RED**

~~~bash
uv run pytest -q tests/test_embedding_gemini.py -k '429 or non_429'
~~~

Expected: zero-match retry fails closed and retry metadata/backoff is absent.

- [ ] **Step 4: Add compatible retry metadata and helpers**

Extend new shard state:

~~~python
"last_create_status": None,
"submission_retry_count": 0,
"submission_retry_not_before": None,
~~~

Existing schema-version-2 files remain valid because every new read uses
`.get` with a conservative default.

Add:

~~~python
MAX_QUOTA_BACKOFF_SECONDS = 300


def _matching_submissions(client, display_name: str) -> list[object]:
    return [
        job for job in client.batches.list()
        if getattr(job, "display_name", None) == display_name
    ]


def _quota_backoff_seconds(retry_count: int) -> int:
    return min(
        POLL_SECONDS * (2 ** max(0, retry_count - 1)),
        MAX_QUOTA_BACKOFF_SECONDS,
    )


def _error_status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    return value if isinstance(value, int) else None
~~~

Refactor `_reconcile_submission` to call `_matching_submissions` while keeping
its ordinary exact-one contract unchanged.

- [ ] **Step 5: Implement the definitive-429 state transition**

Wrap create in `_submit_or_resume_shard`:

~~~python
try:
    job = client.batches.create_embeddings(
        model=variant.document_model_id,
        src={"file_name": uploaded_file_name},
        config={"display_name": submission_display_name},
    )
except BaseException as exc:
    if _error_status_code(exc) != 429:
        raise
    raw_shard_state["last_create_status"] = 429
    _atomic_json(state_path, state)
    matches = _matching_submissions(client, str(submission_display_name))
    if len(matches) == 1 and getattr(matches[0], "name", None):
        job = matches[0]
    elif len(matches) > 1:
        raise RuntimeError(
            f"Gemini submission {submission_display_name!r} cannot safely "
            f"reconcile after 429 (found {len(matches)}); refusing to resubmit"
        ) from exc
    else:
        retry_count = int(raw_shard_state.get("submission_retry_count") or 0) + 1
        raw_shard_state["submission_retry_count"] = retry_count
        raw_shard_state["submission_retry_not_before"] = (
            now_fn() + _quota_backoff_seconds(retry_count)
        )
        raw_shard_state["submission_display_name"] = None
        _atomic_json(state_path, state)
        return False

raw_shard_state["job_name"] = job.name
raw_shard_state["last_create_status"] = None
raw_shard_state["submission_retry_not_before"] = None
_atomic_json(state_path, state)
return True
~~~

Before creating a new intent, return `False` while
`submission_retry_not_before > now_fn()`. A `False` result stops fills for that
cycle but does not stop polling current jobs.

When `submission_retry_count > 0` and the retry deadline has passed, reuse the
persisted upload and create a new display name. Apply the legacy
uploaded-without-intent failure only when `submission_retry_count == 0`; this
keeps old ambiguous states fail-closed without blocking a proven zero-match
429 retry.

On restart, an intent with `last_create_status == 429` repeats exact-name
reconciliation: accept one, fail on multiple, or clear and schedule on zero.
All non-429 intent-only states still require exactly one match and fail closed
on zero or multiple matches.

- [ ] **Step 6: Run focused and complete suites**

~~~bash
uv run pytest -q tests/test_embedding_gemini.py -k '429 or non_429'
uv run pytest -q tests/test_embedding_gemini.py tests/test_build_embedding_artifact.py
~~~

Expected: all tests pass and ambiguous non-429 behavior is unchanged.

- [ ] **Step 7: Commit**

~~~bash
git add src/geo_index/embedding_gemini.py tests/test_embedding_gemini.py
git commit -m "fix: back off concurrent Gemini submissions"
~~~

---

### Task 4: Document, verify, and resume production

**Files:**
- Modify: `README.md:122-141`
- Verify: `data/processed/embedding_artifacts/.gemini_embedding_2_3072_v1.tmp/gemini_state.json`
- Produces: `data/processed/embedding_artifacts/gemini_embedding_2_3072_v1/{vectors.npy,ids.json,metadata.json}`

**Interfaces:**
- Consumes: Tasks 1–3
- Produces: documented concurrency-four resume and validated artifact

- [ ] **Step 1: Update README**

Change the command to:

~~~bash
set -a
source .env
set +a
uv run python -m geo_index.build_embedding_artifact   --model-key gemini_embedding_2_3072_v1   --gemini-concurrency 4   --allow-paid-gemini
~~~

Add:

~~~markdown
`--gemini-concurrency` controls provider-side active batch jobs while one local
coordinator remains the sole state writer. Its default is `1`. Do not launch
multiple builder processes against the same temporary state directory. A rerun
resumes persisted uploads and jobs and assembles results in canonical order.
~~~

- [ ] **Step 2: Run static and full verification**

~~~bash
uv run ruff check src/geo_index/embedding_gemini.py   src/geo_index/build_embedding_artifact.py   tests/test_embedding_gemini.py   tests/test_build_embedding_artifact.py
uv run pytest -q
~~~

Expected: both commands exit 0 with zero test failures.

- [ ] **Step 3: Commit documentation**

~~~bash
git add README.md
git commit -m "docs: document concurrent Gemini resume"
~~~

- [ ] **Step 4: Audit paused state before paid resume**

Run:

~~~bash
uv run python -c '
import json
from pathlib import Path
p = Path("data/processed/embedding_artifacts/.gemini_embedding_2_3072_v1.tmp")
s = json.loads((p / "gemini_state.json").read_text())
results = {x.name for x in p.glob("gemini_results-*.jsonl")}
pending = [
    x["index"] for x in s["shards"]
    if x.get("job_name")
    and f"gemini_results-{x['index']:05d}.jsonl" not in results
]
print({
    "results": len(results),
    "persisted_jobs": sum(bool(x.get("job_name")) for x in s["shards"]),
    "pending_downloads": pending,
})
'
~~~

Expected before resume: 12 results, 13 persisted jobs, and pending shard index
12. If counts changed, proceed only when every submitted shard still has a
persisted job ID.

- [ ] **Step 5: Resume the already-authorized paid run**

~~~bash
set -a
source .env
set +a
uv run python -m geo_index.build_embedding_artifact   --model-key gemini_embedding_2_3072_v1   --gemini-concurrency 4   --allow-paid-gemini
~~~

Expected initial behavior: shard index 12 downloads without upload/create;
pending shards fill to four active jobs; no existing job ID changes.

- [ ] **Step 6: Verify live concurrency**

Run during processing:

~~~bash
uv run python -c '
import json
from pathlib import Path
p = Path("data/processed/embedding_artifacts/.gemini_embedding_2_3072_v1.tmp")
s = json.loads((p / "gemini_state.json").read_text())
active = [
    x for x in s["shards"]
    if x.get("job_name")
    and not (p / f"gemini_results-{x['index']:05d}.jsonl").exists()
]
print({
    "active": len(active),
    "indices": [x["index"] for x in active],
    "results": len(list(p.glob("gemini_results-*.jsonl"))),
})
'
~~~

Expected: `active <= 4` and the result count increases.

- [ ] **Step 7: Validate final artifact and idempotence**

After the builder exits 0:

~~~bash
uv run python -c '
from pathlib import Path
from geo_index.embedding_artifacts import validate_artifact
from geo_index.embedding_registry import get_variant
p = Path("data/processed/embedding_artifacts/gemini_embedding_2_3072_v1")
m = validate_artifact(p, get_variant("gemini_embedding_2_3072_v1"))
print({
    "record_count": m.record_count,
    "dimensions": m.dimensions,
    "model_key": m.model_key,
})
'
~~~

Expected:

~~~text
{'record_count': 249736, 'dimensions': 3072, 'model_key': 'gemini_embedding_2_3072_v1'}
~~~

Run the same builder command once more. Expected JSON includes
`"status": "skipped"` and makes no provider call.
