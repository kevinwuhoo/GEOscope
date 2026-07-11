from __future__ import annotations

from typing import Any, Iterable

import pytest

from geo_index.elasticsearch_config import INDEX_NAME, VECTOR_FIELDS
from geo_index.elasticsearch_loader import (
    LoadFailedError,
    bulk_upsert,
    load_index,
)
from geo_index.elasticsearch_sources import IndexDocument


class _BulkClient:
    def __init__(self, statuses: dict[str, list[int]] | None = None) -> None:
        self.statuses = {key: list(values) for key, values in (statuses or {}).items()}
        self.operations: list[dict[str, object]] = []
        self.bulk_calls = 0
        self.documents: dict[str, dict[str, object]] = {}

    def bulk(
        self, *, operations: list[dict[str, object]], refresh: bool
    ) -> dict[str, object]:
        assert refresh is False
        self.bulk_calls += 1
        self.operations.extend(operations)
        items: list[dict[str, object]] = []
        for offset in range(0, len(operations), 2):
            action = operations[offset]
            source = operations[offset + 1]
            metadata = action["index"]
            assert isinstance(metadata, dict)
            gse = str(metadata["_id"])
            scripted = self.statuses.get(gse, [])
            status = scripted.pop(0) if scripted else 201
            result: dict[str, object] = {
                "_index": metadata["_index"],
                "_id": gse,
                "status": status,
            }
            if status in {200, 201}:
                assert isinstance(source, dict)
                self.documents[gse] = dict(source)
                result["result"] = "created" if status == 201 else "updated"
            else:
                result["error"] = {
                    "type": "es_rejected_execution_exception"
                    if status == 429
                    else "mapper_parsing_exception",
                    "reason": f"scripted status {status}",
                }
            items.append({"index": result})
        return {"errors": any(item["index"]["status"] >= 300 for item in items), "items": items}


def _documents() -> list[IndexDocument]:
    return [
        IndexDocument("GSE2", {"gse": "GSE2", "title": "two"}),
        IndexDocument("GSE10", {"gse": "GSE10", "title": "ten"}),
    ]


def test_bulk_actions_use_gse_id_and_index_operation() -> None:
    client = _BulkClient()
    report = bulk_upsert(client, [_documents()[0]])
    assert client.operations == [
        {"index": {"_index": "geo-series", "_id": "GSE2"}},
        {"gse": "GSE2", "title": "two"},
    ]
    assert client.documents == {"GSE2": {"gse": "GSE2", "title": "two"}}
    assert report.attempted == 1
    assert report.succeeded == 1
    assert report.retried == 0
    assert report.failures == ()


def test_second_load_replaces_without_duplicate_documents() -> None:
    client = _BulkClient()
    bulk_upsert(client, [IndexDocument("GSE2", {"gse": "GSE2", "title": "first"})])
    bulk_upsert(client, [IndexDocument("GSE2", {"gse": "GSE2", "title": "second"})])
    assert len(client.documents) == 1
    assert client.documents["GSE2"]["title"] == "second"


def test_only_retryable_item_failures_are_retried_and_accounted() -> None:
    client = _BulkClient(statuses={"GSE2": [429, 201], "GSE10": [400]})
    report = bulk_upsert(client, _documents(), max_item_retries=2)
    assert report.attempted == 2
    assert report.succeeded == 1
    assert report.retried == 1
    assert [(failure.gse, failure.status, failure.error_type) for failure in report.failures] == [
        ("GSE10", 400, "mapper_parsing_exception")
    ]
    assert client.bulk_calls == 2


def test_exhausted_retry_is_reported_once() -> None:
    client = _BulkClient(statuses={"GSE2": [503, 503, 503]})
    report = bulk_upsert(client, [_documents()[0]], max_item_retries=2)
    assert report.succeeded == 0
    assert report.retried == 2
    assert [(failure.gse, failure.status) for failure in report.failures] == [
        ("GSE2", 503)
    ]


@pytest.mark.parametrize(
    ("batch_size", "max_item_retries", "message"),
    [(0, 1, "batch size"), (10, -1, "item retries")],
)
def test_bulk_upsert_rejects_invalid_bounds(
    batch_size: int, max_item_retries: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        bulk_upsert(
            _BulkClient(),
            _documents(),
            batch_size=batch_size,
            max_item_retries=max_item_retries,
        )


class _Indices:
    def __init__(self) -> None:
        self.refresh_calls: list[str] = []

    def refresh(self, *, index: str) -> None:
        self.refresh_calls.append(index)


class _LoadClient(_BulkClient):
    def __init__(self, statuses: dict[str, list[int]] | None = None) -> None:
        super().__init__(statuses)
        self.indices = _Indices()

    def info(self) -> dict[str, object]:
        return {"version": {"number": "9.4.2"}}

    def count(
        self, *, index: str, query: dict[str, object] | None = None
    ) -> dict[str, int]:
        assert index == INDEX_NAME
        if query is None:
            return {"count": len(self.documents)}
        field = query["exists"]["field"]  # type: ignore[index]
        return {
            "count": sum(field in source for source in self.documents.values())
        }


def test_load_index_refreshes_once_and_reports_document_vector_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _LoadClient()
    documents = [
        IndexDocument(
            "GSE2",
            {
                "gse": "GSE2",
                VECTOR_FIELDS["bge_small_v15"].field: [0.0] * 384,
            },
        ),
        IndexDocument("GSE10", {"gse": "GSE10"}),
    ]
    monkeypatch.setattr(
        "geo_index.elasticsearch_loader.ensure_index", lambda _client: True
    )
    monkeypatch.setattr(
        "geo_index.elasticsearch_loader.iter_index_documents",
        lambda *_args, **_kwargs: iter(documents),
    )
    report = load_index(
        client,
        records_root=object(),  # type: ignore[arg-type]
        artifacts_root=object(),  # type: ignore[arg-type]
        model_keys=("bge_small_v15",),
    )
    assert client.indices.refresh_calls == [INDEX_NAME]
    assert report.server_version == "9.4.2"
    assert report.discovered_records == 2
    assert report.document_count == 2
    assert report.vector_coverage["embedding_bge_384"] == 1
    assert report.vector_coverage["embedding_medcpt_768"] == 0


def test_load_index_raises_with_complete_report_after_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _LoadClient(statuses={"GSE10": [400]})
    monkeypatch.setattr(
        "geo_index.elasticsearch_loader.ensure_index", lambda _client: False
    )
    monkeypatch.setattr(
        "geo_index.elasticsearch_loader.iter_index_documents",
        lambda *_args, **_kwargs: iter(_documents()),
    )
    with pytest.raises(LoadFailedError) as captured:
        load_index(
            client,
            records_root=object(),  # type: ignore[arg-type]
            artifacts_root=object(),  # type: ignore[arg-type]
            model_keys=("bge_small_v15",),
        )
    report = captured.value.report
    assert report.succeeded == 1
    assert [(failure.gse, failure.status) for failure in report.failures] == [
        ("GSE10", 400)
    ]
    assert client.indices.refresh_calls == [INDEX_NAME]
