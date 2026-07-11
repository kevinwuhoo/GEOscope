"""Idempotent bulk loader for canonical GEO records and embedding artifacts."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .elasticsearch_config import (
    INDEX_NAME,
    VECTOR_FIELDS,
    ElasticsearchSettings,
    create_client,
    response_body,
)
from .elasticsearch_index import MAPPING_REVISION, ensure_index
from .elasticsearch_sources import IndexDocument, iter_index_documents


_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class BulkFailure:
    gse: str
    status: int
    error_type: str
    reason: str


@dataclass(frozen=True)
class BulkReport:
    attempted: int
    succeeded: int
    retried: int
    failures: tuple[BulkFailure, ...]


def _batches(
    documents: Iterable[IndexDocument], batch_size: int
) -> Iterator[list[IndexDocument]]:
    batch: list[IndexDocument] = []
    for document in documents:
        batch.append(document)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _operations(documents: Sequence[IndexDocument]) -> list[dict[str, object]]:
    operations: list[dict[str, object]] = []
    for document in documents:
        operations.append(
            {"index": {"_index": INDEX_NAME, "_id": document.gse}}
        )
        operations.append(document.source)
    return operations


def _failure(document: IndexDocument, item: object) -> BulkFailure:
    if not isinstance(item, dict):
        return BulkFailure(document.gse, 500, "malformed_bulk_response", "item is not an object")
    status = item.get("status")
    status_number = status if type(status) is int else 500
    error = item.get("error")
    if isinstance(error, dict):
        error_type = str(error.get("type") or "unknown_error")
        reason = str(error.get("reason") or "bulk item failed")
    else:
        error_type = "unknown_error"
        reason = str(error or "bulk item failed")
    return BulkFailure(
        gse=document.gse,
        status=status_number,
        error_type=error_type,
        reason=reason[:500],
    )


def _bulk_items(response: object, expected: int) -> list[object]:
    body = response_body(response)
    if not isinstance(body.get("items"), list):
        raise ValueError("Elasticsearch bulk response has no items array")
    raw_items = body["items"]
    if len(raw_items) != expected:
        raise ValueError(
            f"Elasticsearch bulk response returned {len(raw_items)} items "
            f"for {expected} documents"
        )
    items: list[object] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            items.append(raw.get("index"))
        else:
            items.append(raw)
    return items


def bulk_upsert(
    client: Any,
    documents: Iterable[IndexDocument],
    *,
    batch_size: int = 500,
    max_item_retries: int = 3,
) -> BulkReport:
    """Bulk-index documents with bounded retries for transient item failures."""

    if batch_size < 1:
        raise ValueError("batch size must be positive")
    if max_item_retries < 0:
        raise ValueError("max item retries must be nonnegative")
    attempted = 0
    succeeded = 0
    retried = 0
    failures: list[BulkFailure] = []
    for original_batch in _batches(documents, batch_size):
        attempted += len(original_batch)
        pending = original_batch
        for attempt in range(max_item_retries + 1):
            response = client.bulk(
                operations=_operations(pending),
                refresh=False,
            )
            items = _bulk_items(response, len(pending))
            retry_documents: list[IndexDocument] = []
            for document, item in zip(pending, items, strict=True):
                failure = _failure(document, item)
                if failure.status in {200, 201}:
                    succeeded += 1
                elif (
                    failure.status in _RETRYABLE_STATUSES
                    and attempt < max_item_retries
                ):
                    retry_documents.append(document)
                    retried += 1
                else:
                    failures.append(failure)
            if not retry_documents:
                break
            pending = retry_documents
    return BulkReport(
        attempted=attempted,
        succeeded=succeeded,
        retried=retried,
        failures=tuple(failures),
    )


@dataclass(frozen=True)
class LoadReport:
    server_version: str
    index_name: str
    mapping_revision: str
    discovered_records: int
    attempted: int
    succeeded: int
    retried: int
    failures: tuple[BulkFailure, ...]
    document_count: int
    vector_coverage: dict[str, int]


class LoadFailedError(RuntimeError):
    def __init__(self, report: LoadReport) -> None:
        super().__init__(f"{len(report.failures)} Elasticsearch bulk items failed")
        self.report = report


def _server_version(client: Any) -> str:
    response = response_body(client.info())
    try:
        return str(response["version"]["number"])
    except (KeyError, TypeError) as exc:
        raise ValueError("cannot read Elasticsearch server version") from exc


def load_index(
    client: Any,
    *,
    records_root: Path,
    artifacts_root: Path,
    model_keys: Sequence[str] = tuple(VECTOR_FIELDS),
    batch_size: int = 500,
    max_item_retries: int = 3,
) -> LoadReport:
    """Validate inputs, upsert the canonical index, refresh once, and audit it."""

    ensure_index(client)
    documents = iter_index_documents(records_root, artifacts_root, model_keys)
    bulk = bulk_upsert(
        client,
        documents,
        batch_size=batch_size,
        max_item_retries=max_item_retries,
    )
    client.indices.refresh(index=INDEX_NAME)
    document_count = int(client.count(index=INDEX_NAME)["count"])
    vector_coverage = {
        spec.field: int(
            client.count(
                index=INDEX_NAME,
                query={"exists": {"field": spec.field}},
            )["count"]
        )
        for spec in VECTOR_FIELDS.values()
    }
    report = LoadReport(
        server_version=_server_version(client),
        index_name=INDEX_NAME,
        mapping_revision=MAPPING_REVISION,
        discovered_records=bulk.attempted,
        attempted=bulk.attempted,
        succeeded=bulk.succeeded,
        retried=bulk.retried,
        failures=bulk.failures,
        document_count=document_count,
        vector_coverage=vector_coverage,
    )
    if report.failures:
        raise LoadFailedError(report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load canonical GEO records into Elasticsearch"
    )
    parser.add_argument(
        "--records-root",
        type=Path,
        default=Path("data/processed/series_records"),
    )
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=Path("data/processed/embedding_artifacts"),
    )
    parser.add_argument("--model-key", action="append", choices=tuple(VECTOR_FIELDS))
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-item-retries", type=int, default=3)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/processed/elasticsearch_load_report.json"),
    )
    return parser


def _write_report(path: Path, report: LoadReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = ElasticsearchSettings.from_env()
    client = create_client(settings)
    try:
        try:
            report = load_index(
                client,
                records_root=args.records_root,
                artifacts_root=args.artifacts_root,
                model_keys=tuple(args.model_key or VECTOR_FIELDS),
                batch_size=args.batch_size,
                max_item_retries=args.max_item_retries,
            )
        except LoadFailedError as exc:
            _write_report(args.report, exc.report)
            print(
                f"indexed {exc.report.succeeded}/{exc.report.attempted}; "
                f"{len(exc.report.failures)} failed",
                flush=True,
            )
            return 2
        _write_report(args.report, report)
        print(
            f"indexed {report.succeeded}/{report.attempted}; "
            f"documents={report.document_count}",
            flush=True,
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
