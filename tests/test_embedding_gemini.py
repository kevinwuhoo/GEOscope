from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from google.genai.errors import ClientError

import geo_index.embedding_gemini as gemini
from geo_index.embedding_artifacts import RecordRef
from geo_index.embedding_gemini import (
    GeminiAuthorizationError,
    build_gemini_vectors,
    prepare_gemini_requests,
)
from geo_index.embedding_registry import get_variant


VARIANT = get_variant("gemini_embedding_2_3072_v1")


@pytest.fixture(autouse=True)
def _fake_sdk_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini, "version", lambda package: "fake-sdk-1")


def _records() -> tuple[RecordRef, ...]:
    return (
        RecordRef("GSE2", "Two", "document two", Path("GSE2.json")),
        RecordRef("GSE10", "Ten", "document ten", Path("GSE10.json")),
    )


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


def _response(gse: str, value: float, *, tokens: int = 5) -> dict[str, object]:
    return {
        "key": gse,
        "response": {
            "tokenCount": str(tokens),
            "embedding": {"values": [value] * 3072},
        },
    }


class FakeFiles:
    def __init__(self, result_rows: list[dict[str, object]]) -> None:
        self.result_rows = result_rows
        self.upload_calls: list[dict[str, object]] = []
        self.download_calls: list[str] = []

    def upload(self, *, file, config):
        self.upload_calls.append({"file": file, "config": config})
        return SimpleNamespace(name="files/input-1")

    def download(self, *, file):
        self.download_calls.append(file)
        return (
            "\n".join(json.dumps(row) for row in self.result_rows) + "\n"
        ).encode()


class FakeBatches:
    def __init__(self, *, fail_get_once: bool = False) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.get_calls: list[str] = []
        self.fail_get_once = fail_get_once

    def create_embeddings(self, *, model, src, config):
        self.create_calls.append({"model": model, "src": src, "config": config})
        return SimpleNamespace(name="batches/job-1")

    def get(self, *, name):
        self.get_calls.append(name)
        if self.fail_get_once:
            self.fail_get_once = False
            raise RuntimeError("poll interrupted")
        return SimpleNamespace(
            name=name,
            state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
            dest=SimpleNamespace(file_name="files/output-1"),
            error=None,
        )


class FakeClient:
    def __init__(
        self,
        result_rows: list[dict[str, object]],
        *,
        fail_get_once: bool = False,
    ) -> None:
        self.files = FakeFiles(result_rows)
        self.batches = FakeBatches(fail_get_once=fail_get_once)

    @property
    def models(self):
        raise AssertionError("synchronous models API must not be used")


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
                file_name=(
                    f"files/output-{index}"
                    if state == "JOB_STATE_SUCCEEDED"
                    else None
                )
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


def test_request_jsonl_is_deterministic_keyed_and_full_dimension(tmp_path: Path) -> None:
    first = prepare_gemini_requests(_records(), VARIANT, tmp_path)
    first_bytes = first.request_path.read_bytes()
    second = prepare_gemini_requests(_records(), VARIANT, tmp_path)

    assert second.request_path.read_bytes() == first_bytes
    rows = [json.loads(line) for line in first_bytes.decode().splitlines()]
    assert [row["key"] for row in rows] == ["GSE2", "GSE10"]
    assert rows[0]["request"] == {
        "content": {
            "parts": [
                {"text": "document: title: Two | text: document two"}
            ]
        },
        "output_dimensionality": 3072,
    }
    assert first.estimated_tokens > 0
    assert first.estimated_cost_usd > 0
    assert first.truncation_count == 0


def test_requests_are_deterministically_sharded_by_bounded_record_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)

    first = prepare_gemini_requests(_records(), VARIANT, tmp_path)
    first_bytes = [shard.request_path.read_bytes() for shard in first.shards]
    second = prepare_gemini_requests(_records(), VARIANT, tmp_path)

    assert [shard.gses for shard in first.shards] == [("GSE2",), ("GSE10",)]
    assert [shard.request_path.name for shard in first.shards] == [
        "gemini_requests-00000.jsonl",
        "gemini_requests-00001.jsonl",
    ]
    assert [shard.request_path.read_bytes() for shard in second.shards] == first_bytes


def test_request_preflight_preserves_full_input_with_multibyte_unicode(
    tmp_path: Path,
) -> None:
    title = "Crème brûlée 🧬"
    embed_text = "é漢🙂" * 1_000
    records = (
        RecordRef("GSE2", title, embed_text, Path("GSE2.json")),
    )

    estimate = prepare_gemini_requests(records, VARIANT, tmp_path)
    row = json.loads(estimate.request_path.read_text())
    text = row["request"]["content"]["parts"][0]["text"]

    assert len(embed_text.encode("utf-8")) > 8_000
    assert text == VARIANT.document_format.format(
        title=title,
        embed_text=embed_text,
    )
    assert estimate.truncation_count == 0


def test_paid_flag_guard_happens_before_key_or_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        gemini,
        "_create_client",
        lambda key: (_ for _ in ()).throw(AssertionError("client constructed")),
    )

    with pytest.raises(GeminiAuthorizationError, match="allow_paid_gemini=True"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=False,
        )

    assert "estimated Gemini batch" in capsys.readouterr().out


def test_api_key_guard_happens_before_client_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        gemini,
        "_create_client",
        lambda key: (_ for _ in ()).throw(AssertionError("client constructed")),
    )

    with pytest.raises(GeminiAuthorizationError, match="GEMINI_API_KEY"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
        )


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
        position
        for position, event in enumerate(client.events)
        if event[0] == "create"
    ]
    first_get = next(
        position for position, event in enumerate(client.events) if event[0] == "get"
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


def test_mixed_state_resume_never_resubmits_persisted_paid_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)
    records = _many_records(4)
    estimate = prepare_gemini_requests(records, VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    state_shards = state["shards"]
    assert isinstance(state_shards, list)
    (tmp_path / "gemini_results-00000.jsonl").write_text(
        json.dumps(_response("GSE1", 1.0)) + "\n",
        encoding="utf-8",
    )
    for index in (1, 2):
        raw = state_shards[index]
        assert isinstance(raw, dict)
        raw.update(
            uploaded_file_name=f"files/input-{index:05d}",
            submission_display_name=f"display-{index:05d}",
            job_name=f"batches/job-{index:05d}",
            job_state="JOB_STATE_RUNNING",
        )
    gemini._atomic_json(state_path, state)
    client = CooperativeClient(running_polls=0)
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    result = build_gemini_vectors(
        records,
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=4,
    )

    assert ("upload", "00000") not in client.events
    assert ("create", "00000") not in client.events
    assert ("upload", "00001") not in client.events
    assert ("create", "00001") not in client.events
    assert ("upload", "00002") not in client.events
    assert ("create", "00002") not in client.events
    assert ("download", "00001") in client.events
    assert ("download", "00002") in client.events
    assert result.vectors[:, 0].tolist() == [1.0, 2.0, 3.0, 4.0]


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
        state["shards"][index].update(  # type: ignore[index,union-attr]
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


def test_terminal_failure_harvests_later_active_success_before_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)
    records = _many_records(3)
    estimate = prepare_gemini_requests(records, VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    for index in range(2):
        state["shards"][index].update(  # type: ignore[index,union-attr]
            uploaded_file_name=f"files/input-{index:05d}",
            submission_display_name=f"display-{index:05d}",
            job_name=f"batches/job-{index:05d}",
            job_state="JOB_STATE_RUNNING",
        )
    gemini._atomic_json(state_path, state)

    class FailedThenSucceededClient(CooperativeClient):
        def get(self, *, name):
            index = name.rsplit("-", 1)[1]
            if index == "00000":
                self.events.append(("get", index))
                return SimpleNamespace(
                    name=name,
                    state=SimpleNamespace(name="JOB_STATE_FAILED"),
                    dest=SimpleNamespace(file_name=None),
                    error="provider failure",
                )
            return super().get(name=name)

    client = FailedThenSucceededClient(running_polls=0)
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(RuntimeError, match="batches/job-00000.*JOB_STATE_FAILED"):
        build_gemini_vectors(
            records,
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=3,
        )

    assert client.events == [
        ("get", "00000"),
        ("get", "00001"),
        ("download", "00001"),
    ]
    persisted = json.loads(state_path.read_text())["shards"]
    assert persisted[1]["job_state"] == "JOB_STATE_SUCCEEDED"
    assert persisted[1]["output_file_name"] == "files/output-00001"
    assert (tmp_path / "gemini_results-00001.jsonl").exists()
    assert persisted[2]["uploaded_file_name"] is None
    assert persisted[2]["job_name"] is None


def test_successful_output_id_is_durable_before_polling_later_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)

    class LaterPollFailsClient(CooperativeClient):
        def get(self, *, name):
            index = name.rsplit("-", 1)[1]
            if index == "00001":
                self.events.append(("get", index))
                raise RuntimeError("later poll interrupted")
            return super().get(name=name)

    client = LaterPollFailsClient(running_polls=0)
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(RuntimeError, match="later poll interrupted"):
        build_gemini_vectors(
            _many_records(2),
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=2,
        )

    state = json.loads((tmp_path / "gemini_state.json").read_text())
    assert state["shards"][0]["job_state"] == "JOB_STATE_SUCCEEDED"
    assert state["shards"][0]["output_file_name"] == "files/output-00000"
    assert ("download", "00000") not in client.events


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
                raise ClientError(
                    429,
                    {
                        "error": {
                            "code": 429,
                            "message": "queue full",
                            "status": "RESOURCE_EXHAUSTED",
                        }
                    },
                )
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


def test_quota_backoff_is_exponential_and_capped() -> None:
    assert [gemini._quota_backoff_seconds(count) for count in range(1, 7)] == [
        30,
        60,
        120,
        240,
        300,
        300,
    ]


def test_restart_of_zero_match_429_intent_schedules_a_new_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    estimate = prepare_gemini_requests(_records(), VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    original_display_name = "geo-gemini-embedding-2-original"
    state["shards"][0].update(  # type: ignore[index,union-attr]
        uploaded_file_name="files/input-1",
        submission_display_name=original_display_name,
        last_create_status=429,
        submission_retry_count=0,
        submission_retry_not_before=None,
    )
    gemini._atomic_json(state_path, state)
    clock = FakeClock()
    create_display_names: list[str] = []

    class RestartBatches:
        def list(self):
            return ()

        def create_embeddings(self, *, model, src, config):
            create_display_names.append(config["display_name"])
            return SimpleNamespace(name="batches/restarted")

        def get(self, *, name):
            return SimpleNamespace(
                name=name,
                state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                dest=SimpleNamespace(file_name="files/output-1"),
                error=None,
            )

    client = FakeClient([_response("GSE2", 2), _response("GSE10", 10)])
    client.batches = RestartBatches()
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

    assert len(create_display_names) == 1
    assert create_display_names[0] != original_display_name
    assert clock.sleeps[0] == 30
    persisted = json.loads(state_path.read_text())["shards"][0]
    assert persisted["submission_retry_count"] == 1
    assert persisted["job_name"] == "batches/restarted"
    assert result.vectors.shape == (2, 3072)


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
        index
        for index, event in enumerate(client.events)
        if event == ("create", "00001")
    )
    assert first_job_poll < second_retry
    assert ("download", "00000") in client.events
    assert result.vectors.shape == (2, 3072)


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


def test_429_status_is_persisted_before_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class DurableStatusBatches:
        display_name: str | None = None

        def create_embeddings(self, *, model, src, config):
            self.display_name = config["display_name"]
            raise QuotaError("quota response after create")

        def list(self):
            state = json.loads((tmp_path / "gemini_state.json").read_text())
            assert state["shards"][0]["last_create_status"] == 429
            return (
                SimpleNamespace(
                    name="batches/durable-status",
                    display_name=self.display_name,
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
    client.batches = DurableStatusBatches()
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    result = build_gemini_vectors(
        _records(),
        VARIANT,
        tmp_path,
        allow_paid=True,
        concurrency=4,
    )

    assert result.usage["provider_job_ids"] == ["batches/durable-status"]


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


def test_429_reconciliation_without_provider_identity_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    clock = FakeClock()
    create_calls: list[str] = []

    class MissingIdentityBatches:
        def create_embeddings(self, *, model, src, config):
            create_calls.append(config["display_name"])
            if len(create_calls) == 1:
                raise QuotaError("quota response after create")
            raise AssertionError("matching provider work was resubmitted")

        def list(self):
            return (SimpleNamespace(display_name=create_calls[0]),)

    client = SimpleNamespace(
        files=FakeFiles([_response("GSE2", 2), _response("GSE10", 10)]),
        batches=MissingIdentityBatches(),
    )
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)
    monkeypatch.setattr(gemini.time, "time", clock.time)
    monkeypatch.setattr(gemini.time, "sleep", clock.sleep)

    with pytest.raises(RuntimeError, match=r"no provider identity.*refusing"):
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


def test_non_429_failure_after_quota_retry_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    estimate = prepare_gemini_requests(_records(), VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    state["shards"][0].update(  # type: ignore[index,union-attr]
        uploaded_file_name="files/input-1",
        submission_display_name=None,
        last_create_status=429,
        submission_retry_count=1,
        submission_retry_not_before=900.0,
    )
    gemini._atomic_json(state_path, state)
    clock = FakeClock()

    class FailedRetryBatches:
        def create_embeddings(self, *, model, src, config):
            raise RuntimeError("connection lost after retry")

    first = SimpleNamespace(files=SimpleNamespace(), batches=FailedRetryBatches())
    monkeypatch.setattr(gemini, "_create_client", lambda key: first)
    monkeypatch.setattr(gemini.time, "time", clock.time)
    monkeypatch.setattr(gemini.time, "sleep", clock.sleep)

    with pytest.raises(RuntimeError, match="connection lost after retry"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
            concurrency=4,
        )

    persisted = json.loads(state_path.read_text())["shards"][0]
    assert persisted["submission_display_name"]
    assert persisted["last_create_status"] is None

    resumed_create_calls: list[dict[str, object]] = []

    class NoMatchBatches:
        def list(self):
            return ()

        def create_embeddings(self, **kwargs):
            resumed_create_calls.append(kwargs)
            raise AssertionError("ambiguous retried work was resubmitted")

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


def test_batch_submission_uses_file_api_and_aligns_results_by_gse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    client = FakeClient([_response("GSE10", 10), _response("GSE2", 2)])
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    result = build_gemini_vectors(
        _records(),
        VARIANT,
        tmp_path,
        allow_paid=True,
    )

    assert len(client.files.upload_calls) == 1
    assert len(client.batches.create_calls) == 1
    create_call = client.batches.create_calls[0]
    assert create_call["model"] == "gemini-embedding-2"
    assert create_call["src"] == {"file_name": "files/input-1"}
    display_name = create_call["config"]["display_name"]  # type: ignore[index]
    assert display_name.startswith("geo-gemini-embedding-2-")
    state = json.loads((tmp_path / "gemini_state.json").read_text())
    assert state["shards"][0]["submission_display_name"] == display_name
    assert client.files.download_calls == ["files/output-1"]
    assert result.vectors.shape == (2, 3072)
    assert result.vectors.dtype == np.float32
    assert np.all(result.vectors[0] == 2)
    assert np.all(result.vectors[1] == 10)
    assert result.usage["provider_job_ids"] == ["batches/job-1"]
    assert result.usage["actual_tokens"] == 10


def test_row_errors_are_aggregated_without_writing_an_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    first_error = {
        "code": 400,
        "message": "input rejected",
        "details": [{"reason": "document too long"}],
    }
    second_error = {"code": 429, "message": "quota exhausted"}
    client = FakeClient(
        [
            {"key": "GSE10", "error": first_error},
            {"key": "GSE2", "error": second_error},
        ]
    )
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(gemini.GeminiBatchRowError) as exc_info:
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
        )

    assert exc_info.value.failures == (
        {"gse": "GSE2", "error": second_error},
        {"gse": "GSE10", "error": first_error},
    )
    assert "GSE10" in str(exc_info.value)
    assert "GSE2" in str(exc_info.value)
    assert not (tmp_path / "vectors.npy").exists()
    assert (tmp_path / "gemini_requests-00000.jsonl").exists()
    assert (tmp_path / "gemini_state.json").exists()
    assert (tmp_path / "gemini_results-00000.jsonl").exists()


def test_row_errors_are_aggregated_across_all_terminal_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)
    first_error = {"code": 400, "message": "first shard rejected"}
    second_error = {"code": 429, "message": "second shard rejected"}

    class ShardFiles:
        def __init__(self) -> None:
            self.upload_count = 0

        def upload(self, *, file, config):
            self.upload_count += 1
            return SimpleNamespace(name=f"files/input-{self.upload_count}")

        def download(self, *, file):
            rows = {
                "files/output-1": [{"key": "GSE2", "error": first_error}],
                "files/output-2": [{"key": "GSE10", "error": second_error}],
            }[file]
            return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()

    class ShardBatches:
        def __init__(self) -> None:
            self.create_count = 0

        def create_embeddings(self, *, model, src, config):
            self.create_count += 1
            return SimpleNamespace(name=f"batches/job-{self.create_count}")

        def get(self, *, name):
            suffix = name.rsplit("-", 1)[1]
            return SimpleNamespace(
                name=name,
                state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                dest=SimpleNamespace(file_name=f"files/output-{suffix}"),
                error=None,
            )

    client = SimpleNamespace(files=ShardFiles(), batches=ShardBatches())
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(gemini.GeminiBatchRowError) as exc_info:
        build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)

    assert exc_info.value.failures == (
        {"gse": "GSE2", "error": first_error},
        {"gse": "GSE10", "error": second_error},
    )
    assert (tmp_path / "gemini_results-00000.jsonl").exists()
    assert (tmp_path / "gemini_results-00001.jsonl").exists()


def test_resume_uses_persisted_job_without_duplicate_upload_or_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    interrupted = FakeClient(
        [_response("GSE2", 2), _response("GSE10", 10)],
        fail_get_once=True,
    )
    monkeypatch.setattr(gemini, "_create_client", lambda key: interrupted)
    with pytest.raises(RuntimeError, match="poll interrupted"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
        )
    state = json.loads((tmp_path / "gemini_state.json").read_text())
    assert state["shards"][0]["job_name"] == "batches/job-1"

    resumed = FakeClient([_response("GSE2", 2), _response("GSE10", 10)])
    monkeypatch.setattr(gemini, "_create_client", lambda key: resumed)
    result = build_gemini_vectors(
        _records(),
        VARIANT,
        tmp_path,
        allow_paid=True,
    )

    assert resumed.files.upload_calls == []
    assert resumed.batches.create_calls == []
    assert resumed.batches.get_calls == ["batches/job-1"]
    assert result.vectors.shape == (2, 3072)


def test_restart_reconciles_job_created_before_state_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    created_jobs: list[SimpleNamespace] = []
    create_calls: list[dict[str, object]] = []
    list_calls: list[None] = []
    upload_calls: list[str] = []

    class ReconcileFiles:
        def upload(self, *, file, config):
            upload_calls.append(file)
            return SimpleNamespace(name="files/input-1")

        def download(self, *, file):
            rows = [_response("GSE2", 2), _response("GSE10", 10)]
            return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()

    class ReconcileBatches:
        def create_embeddings(self, *, model, src, config):
            create_calls.append({"model": model, "src": src, "config": config})
            job = SimpleNamespace(
                name=f"batches/job-{len(create_calls)}",
                display_name=config["display_name"],
            )
            created_jobs.append(job)
            return job

        def list(self):
            list_calls.append(None)
            return tuple(created_jobs)

        def get(self, *, name):
            return SimpleNamespace(
                name=name,
                state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                dest=SimpleNamespace(file_name="files/output-1"),
                error=None,
            )

    first_client = SimpleNamespace(files=ReconcileFiles(), batches=ReconcileBatches())
    monkeypatch.setattr(gemini, "_create_client", lambda key: first_client)
    real_atomic_json = gemini._atomic_json

    def crash_after_provider_create(path: Path, value: object) -> None:
        shards = value.get("shards") if isinstance(value, dict) else None
        if (
            created_jobs
            and isinstance(shards, list)
            and shards
            and isinstance(shards[0], dict)
            and shards[0].get("job_name")
        ):
            raise RuntimeError("state persistence interrupted")
        real_atomic_json(path, value)

    monkeypatch.setattr(gemini, "_atomic_json", crash_after_provider_create)
    with pytest.raises(RuntimeError, match="state persistence interrupted"):
        build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)

    monkeypatch.setattr(gemini, "_atomic_json", real_atomic_json)
    resumed_client = SimpleNamespace(files=ReconcileFiles(), batches=ReconcileBatches())
    monkeypatch.setattr(gemini, "_create_client", lambda key: resumed_client)

    result = build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)

    assert len(create_calls) == 1
    assert len(list_calls) == 1
    assert len(upload_calls) == 1
    assert result.usage["provider_job_ids"] == ["batches/job-1"]


def test_ambiguous_submission_intent_fails_closed_without_resubmitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    estimate = prepare_gemini_requests(_records(), VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    state["shards"][0].update(  # type: ignore[index,union-attr]
        {
            "uploaded_file_name": "files/input-1",
            "submission_display_name": "geo-gemini-embedding-2-ambiguous",
        }
    )
    gemini._atomic_json(state_path, state)

    class NoMatchBatches:
        def list(self):
            return ()

        def create_embeddings(self, **kwargs):
            raise AssertionError("ambiguous provider work was resubmitted")

    client = SimpleNamespace(
        files=SimpleNamespace(),
        batches=NoMatchBatches(),
    )
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(RuntimeError, match="cannot safely reconcile"):
        build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)


def test_legacy_uploaded_state_without_submission_identity_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    estimate = prepare_gemini_requests(_records(), VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    state["shards"][0]["uploaded_file_name"] = "files/input-1"  # type: ignore[index]
    gemini._atomic_json(state_path, state)

    class LegacyBatches:
        def create_embeddings(self, **kwargs):
            raise AssertionError("legacy ambiguous state was resubmitted")

    client = SimpleNamespace(files=SimpleNamespace(), batches=LegacyBatches())
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(RuntimeError, match="legacy Gemini submission state"):
        build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)


def test_multiple_matching_provider_jobs_fail_closed_without_resubmitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    estimate = prepare_gemini_requests(_records(), VARIANT, tmp_path)
    state_path = tmp_path / "gemini_state.json"
    state = gemini._load_state(state_path, estimate)
    display_name = "geo-gemini-embedding-2-duplicate"
    state["shards"][0].update(  # type: ignore[index,union-attr]
        {
            "uploaded_file_name": "files/input-1",
            "submission_display_name": display_name,
        }
    )
    gemini._atomic_json(state_path, state)
    create_calls: list[dict[str, object]] = []

    class MultipleMatchBatches:
        def list(self):
            return (
                SimpleNamespace(name="batches/job-1", display_name=display_name),
                SimpleNamespace(name="batches/job-2", display_name=display_name),
            )

        def create_embeddings(self, **kwargs):
            create_calls.append(kwargs)
            raise AssertionError("duplicate provider matches were resubmitted")

    client = SimpleNamespace(files=SimpleNamespace(), batches=MultipleMatchBatches())
    monkeypatch.setattr(gemini, "_create_client", lambda key: client)

    with pytest.raises(RuntimeError, match=r"found 2.*refusing to resubmit"):
        build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)

    assert create_calls == []


def test_resume_skips_completed_shards_and_continues_only_missing_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "MAX_REQUESTS_PER_SHARD", 1)

    class ShardFiles:
        def __init__(self, rows_by_output: dict[str, list[dict[str, object]]]) -> None:
            self.rows_by_output = rows_by_output
            self.upload_calls: list[str] = []
            self.download_calls: list[str] = []

        def upload(self, *, file, config):
            self.upload_calls.append(file)
            return SimpleNamespace(name=f"files/input-{len(self.upload_calls)}")

        def download(self, *, file):
            self.download_calls.append(file)
            return (
                "\n".join(json.dumps(row) for row in self.rows_by_output[file]) + "\n"
            ).encode()

    class ShardBatches:
        def __init__(self, *, fail_job: str | None) -> None:
            self.fail_job = fail_job
            self.create_calls: list[dict[str, object]] = []
            self.get_calls: list[str] = []

        def create_embeddings(self, *, model, src, config):
            self.create_calls.append({"model": model, "src": src, "config": config})
            return SimpleNamespace(name=f"batches/job-{len(self.create_calls)}")

        def get(self, *, name):
            self.get_calls.append(name)
            if name == self.fail_job:
                raise RuntimeError("poll interrupted on shard two")
            suffix = name.rsplit("-", 1)[1]
            return SimpleNamespace(
                name=name,
                state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                dest=SimpleNamespace(file_name=f"files/output-{suffix}"),
                error=None,
            )

    class ShardClient:
        def __init__(self, *, fail_job: str | None) -> None:
            self.files = ShardFiles(
                {
                    "files/output-1": [_response("GSE2", 2)],
                    "files/output-2": [_response("GSE10", 10)],
                }
            )
            self.batches = ShardBatches(fail_job=fail_job)

        @property
        def models(self):
            raise AssertionError("synchronous models API must not be used")

    interrupted = ShardClient(fail_job="batches/job-2")
    monkeypatch.setattr(gemini, "_create_client", lambda key: interrupted)
    with pytest.raises(RuntimeError, match="shard two"):
        build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)

    state = json.loads((tmp_path / "gemini_state.json").read_text())
    assert state["shards"][0]["output_file_name"] == "files/output-1"
    assert state["shards"][1]["job_name"] == "batches/job-2"

    resumed = ShardClient(fail_job=None)
    monkeypatch.setattr(gemini, "_create_client", lambda key: resumed)
    result = build_gemini_vectors(_records(), VARIANT, tmp_path, allow_paid=True)

    assert resumed.files.upload_calls == []
    assert resumed.batches.create_calls == []
    assert resumed.batches.get_calls == ["batches/job-2"]
    assert resumed.files.download_calls == ["files/output-2"]
    assert np.all(result.vectors[0] == 2)
    assert np.all(result.vectors[1] == 10)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([_response("GSE2", 2)], "missing Gemini responses.*GSE10"),
        (
            [_response("GSE2", 2), _response("GSE2", 3), _response("GSE10", 10)],
            "duplicate Gemini response GSE2",
        ),
        (
            [_response("GSE2", 2), _response("GSE10", 10), _response("GSE99", 99)],
            "unexpected Gemini response GSE99",
        ),
    ],
)
def test_response_identity_must_exactly_match_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
    message: str,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini, "_create_client", lambda key: FakeClient(rows))

    with pytest.raises(ValueError, match=message):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
        )


def test_wrong_response_dimension_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    bad = _response("GSE2", 2)
    bad["response"]["embedding"]["values"] = [2.0] * 3071  # type: ignore[index]
    monkeypatch.setattr(
        gemini,
        "_create_client",
        lambda key: FakeClient([bad, _response("GSE10", 10)]),
    )

    with pytest.raises(ValueError, match="GSE2.*3072 dimensions"):
        build_gemini_vectors(
            _records(),
            VARIANT,
            tmp_path,
            allow_paid=True,
        )
