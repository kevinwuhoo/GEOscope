"""Bounded Prefect 3 orchestration for existence-based canonical SOFT ETL."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from prefect import flow, get_run_logger, task
from prefect.task_runners import ThreadPoolTaskRunner

from .build_embedding_artifact import build_missing_embeddings
from .soft_records import (
    BatchResult,
    MaterializeFailure,
    RecordJob,
    discover_records,
    materialize_batch,
)


DEFAULT_SOFT_ROOT = Path("data/processed/soft_meta")
DEFAULT_RECORDS_ROOT = Path("data/processed/series_records")
DEFAULT_EMBEDDING_STORE = Path("data/processed/embedding_artifacts")
DEFAULT_EMBEDDING_MODEL_KEY = "bge_small_v15"
DEFAULT_BATCH_SIZE = 250
DEFAULT_WORKERS = 8


@dataclass(frozen=True)
class EtlReport:
    discovered: int
    skipped: int
    created: int
    failed: int
    parse_batches: int
    duration_seconds: float
    failures: tuple[dict[str, str], ...]
    created_gses: tuple[str, ...]
    embedding_status: str | None
    embedding_error: str | None

    @property
    def succeeded(self) -> bool:
        return self.failed == 0 and self.embedding_error is None


@task(name="materialize-canonical-record-batch", retries=2, retry_delay_seconds=5)
def parse_record_batch(jobs: tuple[RecordJob, ...]) -> BatchResult:
    """Materialize one bounded, retryable group of canonical records."""
    result = materialize_batch(jobs)
    logger = get_run_logger()
    for failure in result.failures:
        logger.error(
            "canonical record parse failed: gse=%s source=%s error=%s",
            failure.gse,
            str(failure.source),
            failure.error,
        )
    return result


def _chunks(values: Sequence[RecordJob], size: int):
    for start in range(0, len(values), size):
        yield tuple(values[start : start + size])


def _failure_dict(failure: MaterializeFailure) -> dict[str, str]:
    return {
        "gse": failure.gse,
        "source": str(failure.source),
        "error": failure.error,
    }


def _write_report(path: Path, report: EtlReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(report), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@flow(
    name="geo-soft-etl",
    task_runner=ThreadPoolTaskRunner(max_workers=DEFAULT_WORKERS),
    log_prints=True,
)
def geo_soft_etl(
    soft_root: Path = DEFAULT_SOFT_ROOT,
    records_root: Path = DEFAULT_RECORDS_ROOT,
    parse_batch_size: int = DEFAULT_BATCH_SIZE,
) -> EtlReport:
    """Inventory once, parse missing records in bounded batches, and embed them."""
    if parse_batch_size < 1:
        raise ValueError("parse_batch_size must be positive")
    started = time.perf_counter()
    discovery = discover_records(soft_root, records_root)
    submitted: list[tuple[object, tuple[RecordJob, ...]]] = []
    for batch in _chunks(discovery.jobs, parse_batch_size):
        submitted.append((parse_record_batch.submit(batch), batch))

    created_gses: list[str] = []
    race_skipped = 0
    failures: list[dict[str, str]] = []
    for future, batch in submitted:
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - all futures must be resolved
            failures.extend(
                {
                    "gse": job.gse,
                    "source": str(job.source),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                for job in batch
            )
            continue
        reported_created = set(result.created_gses)
        reported_skipped = set(result.skipped_gses)
        committed_gses = [
            job.gse
            for job in batch
            if job.gse in reported_created
            or (job.gse in reported_skipped and job.destination.exists())
        ]
        created_gses.extend(committed_gses)
        race_skipped += len(reported_skipped - set(committed_gses))
        failures.extend(_failure_dict(failure) for failure in result.failures)

    embedding_status: str | None = None
    embedding_error: str | None = None
    try:
        embedding_result = build_missing_embeddings(
            records_root,
            records_root.parent / "embedding_artifacts",
            DEFAULT_EMBEDDING_MODEL_KEY,
            replace_gses=frozenset(created_gses),
            allow_paid_gemini=False,
        )
        embedding_status = embedding_result.status
    except Exception as exc:  # noqa: BLE001 - report before returning nonzero
        embedding_error = f"{type(exc).__name__}: {exc}"

    report = EtlReport(
        discovered=discovery.discovered,
        skipped=discovery.skipped + race_skipped,
        created=len(created_gses),
        failed=len(failures),
        parse_batches=len(submitted),
        duration_seconds=time.perf_counter() - started,
        failures=tuple(failures),
        created_gses=tuple(created_gses),
        embedding_status=embedding_status,
        embedding_error=embedding_error,
    )
    _write_report(records_root.parent / "soft_etl_report.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Materialize canonical GSE records")
    parser.add_argument("--soft-root", type=Path, default=DEFAULT_SOFT_ROOT)
    parser.add_argument("--records-root", type=Path, default=DEFAULT_RECORDS_ROOT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.batch_size < 1:
        raise ValueError("batch size must be positive")
    if args.workers < 1:
        raise ValueError("workers must be positive")
    configured_flow = geo_soft_etl.with_options(
        task_runner=ThreadPoolTaskRunner(max_workers=args.workers)
    )
    report = configured_flow(
        soft_root=args.soft_root,
        records_root=args.records_root,
        parse_batch_size=args.batch_size,
    )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
