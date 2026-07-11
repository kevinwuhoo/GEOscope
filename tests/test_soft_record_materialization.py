from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import pytest

import geo_index.soft_records as soft_records
from geo_index.soft_records import (
    RecordJob,
    SoftParseError,
    discover_records,
    discover_missing,
    materialize_batch,
    materialize_record,
    record_path,
)


FIXTURE = Path(__file__).parent / "fixtures" / "soft" / "minimal_family.soft.gz"


def _copy_fixture(soft_root: Path, gse: str) -> Path:
    digits = gse[3:]
    bucket = f"GSE{digits[:-3]}nnn" if len(digits) > 3 else "GSEnnn"
    destination = soft_root / bucket / f"{gse}_family.soft.gz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, destination)
    if gse != "GSE1001":
        with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
            text = handle.read().replace("GSE1001", gse)
        with gzip.open(destination, "wt", encoding="utf-8") as handle:
            handle.write(text)
    return destination


def _job(soft_root: Path, records_root: Path, gse: str = "GSE1001") -> RecordJob:
    source = _copy_fixture(soft_root, gse)
    return RecordJob(
        gse=gse,
        source=source,
        destination=record_path(records_root, gse),
        soft_root=soft_root,
    )


def test_discovery_skips_completed_record_without_reading_source(
    tmp_path: Path,
) -> None:
    soft_root = tmp_path / "soft"
    records_root = tmp_path / "records"
    completed_source = _copy_fixture(soft_root, "GSE1001")
    _copy_fixture(soft_root, "GSE2")
    completed_destination = record_path(records_root, "GSE1001")
    completed_destination.parent.mkdir(parents=True)
    completed_destination.write_text("completed but deliberately invalid JSON")
    completed_source.chmod(0)
    try:
        discovery = discover_records(soft_root, records_root)
    finally:
        completed_source.chmod(0o644)

    assert discovery.discovered == 2
    assert discovery.skipped == 1
    assert [job.gse for job in discovery.jobs] == ["GSE2"]
    assert discover_missing(soft_root, records_root)[0].gse == "GSE2"


def test_discovery_sorts_jobs_by_numeric_gse(tmp_path: Path) -> None:
    soft_root = tmp_path / "soft"
    records_root = tmp_path / "records"
    for gse in ("GSE1001", "GSE2", "GSE10"):
        _copy_fixture(soft_root, gse)

    assert [job.gse for job in discover_missing(soft_root, records_root)] == [
        "GSE2",
        "GSE10",
        "GSE1001",
    ]


def test_materialize_record_publishes_deterministic_json_atomically(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path / "soft", tmp_path / "records")

    first = materialize_record(job)
    first_bytes = job.destination.read_bytes()
    job.destination.unlink()
    second = materialize_record(job)

    assert first.created is True
    assert second.created is True
    assert first.gse == second.gse == "GSE1001"
    assert first_bytes == job.destination.read_bytes()
    assert first_bytes.endswith(b"\n")
    assert not job.destination.with_suffix(".json.tmp").exists()


def test_materialize_record_rechecks_existence_without_parser_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job(tmp_path / "soft", tmp_path / "records")
    job.destination.parent.mkdir(parents=True)
    job.destination.write_text("complete")
    monkeypatch.setattr(
        soft_records,
        "parse_soft_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("parsed")),
    )

    result = materialize_record(job)

    assert result.created is False
    assert result.gse == "GSE1001"


def test_parse_failure_leaves_no_final_or_temporary_record(tmp_path: Path) -> None:
    soft_root = tmp_path / "soft"
    records_root = tmp_path / "records"
    source = soft_root / "GSEnnn" / "GSE1_family.soft.gz"
    source.parent.mkdir(parents=True)
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write("^SERIES = GSE1\n!Series_title = missing accession\n")
    job = RecordJob("GSE1", source, record_path(records_root, "GSE1"), soft_root)

    with pytest.raises(SoftParseError, match="missing !Series_geo_accession"):
        materialize_record(job)

    assert not job.destination.exists()
    assert not job.destination.with_suffix(".json.tmp").exists()


def test_materialize_batch_keeps_successes_and_reports_failures(tmp_path: Path) -> None:
    soft_root = tmp_path / "soft"
    records_root = tmp_path / "records"
    good = _job(soft_root, records_root)
    bad_source = soft_root / "GSEnnn" / "GSE2_family.soft.gz"
    bad_source.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(bad_source, "wt", encoding="utf-8") as handle:
        handle.write("not a SOFT file\n")
    bad = RecordJob("GSE2", bad_source, record_path(records_root, "GSE2"), soft_root)

    result = materialize_batch([good, bad])

    assert result.created_gses == ("GSE1001",)
    assert result.skipped_gses == ()
    assert len(result.failures) == 1
    assert result.failures[0].gse == "GSE2"
    assert "missing ^SERIES block" in result.failures[0].error
    assert good.destination.exists()
    assert not bad.destination.exists()


def test_deleting_output_explicitly_forces_rebuild(tmp_path: Path) -> None:
    job = _job(tmp_path / "soft", tmp_path / "records")
    assert materialize_record(job).created is True
    assert materialize_record(job).created is False

    job.destination.unlink()

    assert materialize_record(job).created is True
    assert job.destination.exists()
