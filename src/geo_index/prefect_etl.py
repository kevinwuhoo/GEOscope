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
from .elasticsearch_config import (
    VECTOR_FIELDS,
    ElasticsearchSettings,
    create_client,
)
from .elasticsearch_loader import LoadFailedError, LoadReport, load_index
from .elasticsearch_sources import load_artifact
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
DEFAULT_EMBEDDING_MODEL_KEY = "gemini_embedding_2_3072_v1"
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
    elasticsearch_status: str = "not_run"
    elasticsearch_error: str | None = None
    elasticsearch_attempted: int = 0
    elasticsearch_succeeded: int = 0
    elasticsearch_retried: int = 0
    elasticsearch_document_count: int = 0
    elasticsearch_vector_count: int = 0

    @property
    def succeeded(self) -> bool:
        return (
            self.failed == 0
            and self.embedding_error is None
            and self.elasticsearch_error is None
            and self.elasticsearch_status == "indexed"
        )


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


def _elasticsearch_metrics(report: LoadReport) -> tuple[int, int, int, int, int]:
    vector_field = VECTOR_FIELDS[DEFAULT_EMBEDDING_MODEL_KEY].field
    return (
        report.attempted,
        report.succeeded,
        report.retried,
        report.document_count,
        report.vector_coverage.get(vector_field, 0),
    )


def _validate_elasticsearch_audit(report: LoadReport) -> None:
    expected = report.attempted
    if report.succeeded != expected:
        raise ValueError(
            f"Elasticsearch indexed {report.succeeded} of {expected} records"
        )
    if report.document_count != expected:
        raise ValueError(
            f"Elasticsearch document count {report.document_count} != {expected}"
        )
    vector_field = VECTOR_FIELDS[DEFAULT_EMBEDDING_MODEL_KEY].field
    vector_count = report.vector_coverage.get(vector_field, 0)
    if vector_count != expected:
        raise ValueError(f"Gemini vector coverage {vector_count} != {expected}")


@flow(
    name="geo-soft-etl",
    task_runner=ThreadPoolTaskRunner(max_workers=DEFAULT_WORKERS),
    log_prints=True,
)
def geo_soft_etl(
    soft_root: Path = DEFAULT_SOFT_ROOT,
    records_root: Path = DEFAULT_RECORDS_ROOT,
    artifacts_root: Path | None = None,
    parse_batch_size: int = DEFAULT_BATCH_SIZE,
    allow_paid_gemini: bool = False,
    gemini_concurrency: int = 1,
    elasticsearch_batch_size: int = 500,
    elasticsearch_max_item_retries: int = 3,
) -> EtlReport:
    """Materialize, embed with Gemini, and index canonical records in Elastic."""
    if not allow_paid_gemini:
        raise ValueError("--allow-paid-gemini is required for the primary Gemini ETL")
    if gemini_concurrency < 1:
        raise ValueError("Gemini concurrency must be at least 1")
    if parse_batch_size < 1:
        raise ValueError("parse_batch_size must be positive")
    if elasticsearch_batch_size < 1:
        raise ValueError("elasticsearch_batch_size must be positive")
    if elasticsearch_max_item_retries < 0:
        raise ValueError("elasticsearch_max_item_retries must be nonnegative")
    resolved_artifacts_root = (
        artifacts_root or records_root.parent / "embedding_artifacts"
    )
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
            resolved_artifacts_root,
            DEFAULT_EMBEDDING_MODEL_KEY,
            replace_gses=frozenset(created_gses),
            allow_paid_gemini=allow_paid_gemini,
            gemini_concurrency=gemini_concurrency,
        )
        embedding_status = embedding_result.status
    except Exception as exc:  # noqa: BLE001 - report before returning nonzero
        embedding_error = f"{type(exc).__name__}: {exc}"

    elasticsearch_status = "not_run"
    elasticsearch_error: str | None = None
    elasticsearch_attempted = 0
    elasticsearch_succeeded = 0
    elasticsearch_retried = 0
    elasticsearch_document_count = 0
    elasticsearch_vector_count = 0
    if embedding_error is None:
        client = None
        try:
            gemini_spec = VECTOR_FIELDS[DEFAULT_EMBEDDING_MODEL_KEY]
            load_artifact(
                resolved_artifacts_root / DEFAULT_EMBEDDING_MODEL_KEY,
                gemini_spec,
            )
            settings = ElasticsearchSettings.from_env()
            client = create_client(settings)
            load_report = load_index(
                client,
                records_root=records_root,
                artifacts_root=resolved_artifacts_root,
                model_keys=tuple(VECTOR_FIELDS),
                batch_size=elasticsearch_batch_size,
                max_item_retries=elasticsearch_max_item_retries,
            )
            (
                elasticsearch_attempted,
                elasticsearch_succeeded,
                elasticsearch_retried,
                elasticsearch_document_count,
                elasticsearch_vector_count,
            ) = _elasticsearch_metrics(load_report)
            vector_count = load_report.vector_coverage.get(gemini_spec.field, 0)
            if vector_count != load_report.document_count:
                raise ValueError(
                    "incomplete Gemini vector coverage: "
                    f"{vector_count}/{load_report.document_count}"
                )
            _validate_elasticsearch_audit(load_report)
            elasticsearch_status = "indexed"
        except LoadFailedError as exc:
            (
                elasticsearch_attempted,
                elasticsearch_succeeded,
                elasticsearch_retried,
                elasticsearch_document_count,
                elasticsearch_vector_count,
            ) = _elasticsearch_metrics(exc.report)
            elasticsearch_status = "failed"
            elasticsearch_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - terminal report owns failure
            elasticsearch_status = "failed"
            elasticsearch_error = f"{type(exc).__name__}: {exc}"
        finally:
            if client is not None:
                client.close()

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
        elasticsearch_status=elasticsearch_status,
        elasticsearch_error=elasticsearch_error,
        elasticsearch_attempted=elasticsearch_attempted,
        elasticsearch_succeeded=elasticsearch_succeeded,
        elasticsearch_retried=elasticsearch_retried,
        elasticsearch_document_count=elasticsearch_document_count,
        elasticsearch_vector_count=elasticsearch_vector_count,
    )
    _write_report(records_root.parent / "soft_etl_report.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize, embed, and index canonical GEO records"
    )
    parser.add_argument("--soft-root", type=Path, default=DEFAULT_SOFT_ROOT)
    parser.add_argument("--records-root", type=Path, default=DEFAULT_RECORDS_ROOT)
    parser.add_argument("--artifacts-root", type=Path, default=DEFAULT_EMBEDDING_STORE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--allow-paid-gemini", action="store_true")
    parser.add_argument("--gemini-concurrency", type=int, default=1)
    parser.add_argument("--elasticsearch-batch-size", type=int, default=500)
    parser.add_argument("--elasticsearch-max-item-retries", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paid_gemini:
        raise ValueError("--allow-paid-gemini is required for the primary Gemini ETL")
    if args.gemini_concurrency < 1:
        raise ValueError("Gemini concurrency must be at least 1")
    if args.batch_size < 1:
        raise ValueError("batch size must be positive")
    if args.workers < 1:
        raise ValueError("workers must be positive")
    if args.elasticsearch_batch_size < 1:
        raise ValueError("Elasticsearch batch size must be positive")
    if args.elasticsearch_max_item_retries < 0:
        raise ValueError("Elasticsearch max item retries must be nonnegative")
    configured_flow = geo_soft_etl.with_options(
        task_runner=ThreadPoolTaskRunner(max_workers=args.workers)
    )
    report = configured_flow(
        soft_root=args.soft_root,
        records_root=args.records_root,
        artifacts_root=args.artifacts_root,
        parse_batch_size=args.batch_size,
        allow_paid_gemini=args.allow_paid_gemini,
        gemini_concurrency=args.gemini_concurrency,
        elasticsearch_batch_size=args.elasticsearch_batch_size,
        elasticsearch_max_item_retries=args.elasticsearch_max_item_retries,
    )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
