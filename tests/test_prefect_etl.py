from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import geo_index.prefect_etl as prefect_etl
from geo_index.prefect_etl import EtlReport, geo_soft_etl, main
from geo_index.soft_records import (
    BatchResult,
    DiscoveryResult,
    MaterializeFailure,
    RecordJob,
    materialize_batch,
    record_path,
)


FIXTURE = Path(__file__).parent / "fixtures" / "soft" / "minimal_family.soft.gz"


class FakeFuture:
    def __init__(self, result: BatchResult) -> None:
        self.value = result
        self.result_calls = 0

    def result(self) -> BatchResult:
        self.result_calls += 1
        return self.value


def _job(root: Path, number: int) -> RecordJob:
    gse = f"GSE{number}"
    return RecordJob(
        gse=gse,
        source=root / f"{gse}_family.soft.gz",
        destination=record_path(root / "records", gse),
        soft_root=root,
    )


def _copy_fixture(soft_root: Path, gse: str) -> Path:
    digits = gse[3:]
    bucket = f"GSE{digits[:-3]}nnn" if len(digits) > 3 else "GSEnnn"
    path = soft_root / bucket / f"{gse}_family.soft.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        text = handle.read().replace("GSE1001", gse)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _fake_embedding_result(status: str = "created"):
    return SimpleNamespace(status=status, record_count=1, duration_seconds=0.01)


def test_parse_task_logs_each_record_failure_for_prefect_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job(tmp_path, 10)
    failure = MaterializeFailure(job.gse, job.source, "SoftParseError: bad")
    monkeypatch.setattr(
        prefect_etl,
        "materialize_batch",
        lambda jobs: BatchResult((), (), (failure,)),
    )
    errors: list[tuple[object, ...]] = []
    logger = SimpleNamespace(error=lambda *args: errors.append(args))
    monkeypatch.setattr(prefect_etl, "get_run_logger", lambda: logger)

    result = prefect_etl.parse_record_batch.fn((job,))

    assert result.failures == (failure,)
    assert len(errors) == 1
    assert errors[0][1:] == (job.gse, str(job.source), failure.error)


def test_flow_batches_501_jobs_as_250_250_1_and_resolves_every_future(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = tuple(_job(tmp_path, number) for number in range(1, 502))
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda soft_root, records_root: DiscoveryResult(501, 0, jobs),
    )
    submitted: list[tuple[RecordJob, ...]] = []
    futures: list[FakeFuture] = []

    def submit(batch: tuple[RecordJob, ...]) -> FakeFuture:
        submitted.append(batch)
        future = FakeFuture(
            BatchResult(tuple(job.gse for job in batch), (), ())
        )
        futures.append(future)
        return future

    monkeypatch.setattr(prefect_etl.parse_record_batch, "submit", submit)
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *args, **kwargs: _fake_embedding_result(),
    )

    report = geo_soft_etl.fn(
        soft_root=tmp_path,
        records_root=tmp_path / "records",
        parse_batch_size=250,
    )

    assert [len(batch) for batch in submitted] == [250, 250, 1]
    assert [future.result_calls for future in futures] == [1, 1, 1]
    assert report.discovered == 501
    assert report.created == 501
    assert report.parse_batches == 3


def test_flow_reports_partial_failures_and_passes_created_gses_as_replace_gses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = tuple(_job(tmp_path, number) for number in (2, 10, 20))
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda soft_root, records_root: DiscoveryResult(4, 1, jobs),
    )
    failure = MaterializeFailure("GSE10", jobs[1].source, "SoftParseError: bad")
    monkeypatch.setattr(
        prefect_etl.parse_record_batch,
        "submit",
        lambda batch: FakeFuture(BatchResult(("GSE2", "GSE20"), (), (failure,))),
    )
    embedding_calls: list[dict[str, object]] = []

    def fake_embeddings(records_root, store_path, model_key, **kwargs):
        embedding_calls.append(
            {
                "records_root": records_root,
                "store_path": store_path,
                "model_key": model_key,
                **kwargs,
            }
        )
        return _fake_embedding_result()

    monkeypatch.setattr(prefect_etl, "build_missing_embeddings", fake_embeddings)

    report = geo_soft_etl.fn(
        soft_root=tmp_path,
        records_root=tmp_path / "records",
        parse_batch_size=250,
    )

    assert report.discovered == 4
    assert report.skipped == 1
    assert report.created == 2
    assert report.failed == 1
    assert report.failures[0]["gse"] == "GSE10"
    assert embedding_calls == [
        {
            "records_root": tmp_path / "records",
            "store_path": tmp_path / "embedding_artifacts",
            "model_key": "bge_small_v15",
            "replace_gses": frozenset({"GSE2", "GSE20"}),
            "allow_paid_gemini": False,
        }
    ]


def test_flow_reconciles_retry_commits_from_original_submitted_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job(tmp_path, 2)
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda soft_root, records_root: DiscoveryResult(1, 0, (job,)),
    )

    class RetryFuture:
        def result(self) -> BatchResult:
            job.destination.parent.mkdir(parents=True, exist_ok=True)
            job.destination.write_text("committed by prior attempt\n")
            return BatchResult((), (job.gse,), ())

    monkeypatch.setattr(
        prefect_etl.parse_record_batch,
        "submit",
        lambda batch: RetryFuture(),
    )
    replace_sets: list[frozenset[str]] = []
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *args, replace_gses, **kwargs: (
            replace_sets.append(replace_gses) or _fake_embedding_result()
        ),
    )

    report = geo_soft_etl.fn(
        soft_root=tmp_path,
        records_root=tmp_path / "records",
        parse_batch_size=250,
    )

    assert report.created == 1
    assert report.skipped == 0
    assert report.created_gses == ("GSE2",)
    assert replace_sets == [frozenset({"GSE2"})]


def test_completed_record_causes_no_source_read_in_full_flow_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soft_root = tmp_path / "soft"
    records_root = tmp_path / "records"
    completed_source = _copy_fixture(soft_root, "GSE1001")
    _copy_fixture(soft_root, "GSE2")
    destination = record_path(records_root, "GSE1001")
    destination.parent.mkdir(parents=True)
    destination.write_text("completed")
    parsed: list[str] = []

    def submit(batch: tuple[RecordJob, ...]) -> FakeFuture:
        parsed.extend(job.gse for job in batch)
        return FakeFuture(materialize_batch(batch))

    monkeypatch.setattr(prefect_etl.parse_record_batch, "submit", submit)
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *args, **kwargs: _fake_embedding_result(),
    )
    completed_source.chmod(0)
    try:
        report = geo_soft_etl.fn(soft_root=soft_root, records_root=records_root)
    finally:
        completed_source.chmod(0o644)

    assert parsed == ["GSE2"]
    assert report.skipped == 1
    assert report.created == 1


def test_second_run_submits_no_parse_batches_for_completed_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soft_root = tmp_path / "soft"
    records_root = tmp_path / "records"
    _copy_fixture(soft_root, "GSE1001")
    _copy_fixture(soft_root, "GSE2")
    parsed: list[str] = []

    def submit(batch: tuple[RecordJob, ...]) -> FakeFuture:
        parsed.extend(job.gse for job in batch)
        return FakeFuture(materialize_batch(batch))

    monkeypatch.setattr(prefect_etl.parse_record_batch, "submit", submit)
    replace_sets: list[frozenset[str]] = []

    def fake_embeddings(*args, replace_gses, **kwargs):
        replace_sets.append(replace_gses)
        return _fake_embedding_result("skipped" if not replace_gses else "created")

    monkeypatch.setattr(prefect_etl, "build_missing_embeddings", fake_embeddings)

    first = geo_soft_etl.fn(soft_root=soft_root, records_root=records_root)
    second = geo_soft_etl.fn(soft_root=soft_root, records_root=records_root)

    assert first.created == 2
    assert second.created == 0
    assert second.skipped == 2
    assert second.parse_batches == 0
    assert parsed == ["GSE2", "GSE1001"]
    assert replace_sets == [frozenset({"GSE2", "GSE1001"}), frozenset()]


def test_deleting_one_record_rebuilds_exactly_that_gse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soft_root = tmp_path / "soft"
    records_root = tmp_path / "records"
    _copy_fixture(soft_root, "GSE1001")
    _copy_fixture(soft_root, "GSE2")

    monkeypatch.setattr(
        prefect_etl.parse_record_batch,
        "submit",
        lambda batch: FakeFuture(materialize_batch(batch)),
    )
    replace_sets: list[frozenset[str]] = []
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *args, replace_gses, **kwargs: (
            replace_sets.append(replace_gses) or _fake_embedding_result()
        ),
    )
    geo_soft_etl.fn(soft_root=soft_root, records_root=records_root)
    record_path(records_root, "GSE2").unlink()

    report = geo_soft_etl.fn(soft_root=soft_root, records_root=records_root)

    assert report.created == 1
    assert report.skipped == 1
    assert replace_sets[-1] == frozenset({"GSE2"})


def test_report_is_atomically_overwritten_with_required_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda soft_root, records_root: DiscoveryResult(0, 0, ()),
    )
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *args, **kwargs: _fake_embedding_result("skipped"),
    )
    records_root = tmp_path / "processed" / "series_records"

    report = geo_soft_etl.fn(soft_root=tmp_path, records_root=records_root)

    report_path = tmp_path / "processed" / "soft_etl_report.json"
    payload = json.loads(report_path.read_text())
    assert payload["discovered"] == 0
    assert payload["skipped"] == 0
    assert payload["created"] == 0
    assert payload["failed"] == 0
    assert not report_path.with_suffix(".json.tmp").exists()


def test_embedding_failure_is_reported_and_makes_cli_result_unsuccessful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prefect_etl,
        "discover_records",
        lambda soft_root, records_root: DiscoveryResult(0, 0, ()),
    )
    monkeypatch.setattr(
        prefect_etl,
        "build_missing_embeddings",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("embed failed")),
    )

    report = geo_soft_etl.fn(
        soft_root=tmp_path,
        records_root=tmp_path / "records",
    )

    assert report.embedding_error == "RuntimeError: embed failed"
    assert report.succeeded is False


def test_cli_uses_requested_worker_bound_and_returns_nonzero_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def with_options(*, task_runner):
        captured["max_workers"] = task_runner._max_workers

        def run(**kwargs):
            captured.update(kwargs)
            return EtlReport(
                discovered=1,
                skipped=0,
                created=0,
                failed=1,
                parse_batches=1,
                duration_seconds=0.1,
                failures=({"gse": "GSE1", "error": "bad"},),
                created_gses=(),
                embedding_status="skipped",
                embedding_error=None,
            )

        return run

    monkeypatch.setattr(prefect_etl.geo_soft_etl, "with_options", with_options)

    exit_code = main(
        [
            "--soft-root",
            str(tmp_path / "soft"),
            "--records-root",
            str(tmp_path / "records"),
            "--batch-size",
            "17",
            "--workers",
            "3",
        ]
    )

    assert exit_code == 1
    assert captured["max_workers"] == 3
    assert captured["parse_batch_size"] == 17


def test_etl_report_is_frozen_and_success_requires_no_failures() -> None:
    report = EtlReport(
        discovered=1,
        skipped=1,
        created=0,
        failed=0,
        parse_batches=0,
        duration_seconds=0.1,
        failures=(),
        created_gses=(),
        embedding_status="skipped",
        embedding_error=None,
    )
    assert report.succeeded is True
    with pytest.raises((AttributeError, TypeError)):
        report.created = 1  # type: ignore[misc]
