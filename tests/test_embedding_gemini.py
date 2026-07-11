from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

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


def test_request_preflight_uses_token_safe_utf8_byte_bound(tmp_path: Path) -> None:
    records = (
        RecordRef("GSE2", "Two", "é" * 10_000, Path("GSE2.json")),
    )

    estimate = prepare_gemini_requests(records, VARIANT, tmp_path)
    row = json.loads(estimate.request_path.read_text())
    text = row["request"]["content"]["parts"][0]["text"]

    assert len(text.encode("utf-8")) <= gemini.SAFE_INPUT_UTF8_BYTES
    assert estimate.truncation_count == 1


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
    assert client.batches.create_calls == [
        {
            "model": "gemini-embedding-2",
            "src": {"file_name": "files/input-1"},
            "config": {"display_name": "geo-gemini-embedding-2"},
        }
    ]
    assert client.files.download_calls == ["files/output-1"]
    assert result.vectors.shape == (2, 3072)
    assert result.vectors.dtype == np.float32
    assert np.all(result.vectors[0] == 2)
    assert np.all(result.vectors[1] == 10)
    assert result.usage["provider_job_ids"] == ["batches/job-1"]
    assert result.usage["actual_tokens"] == 10


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
