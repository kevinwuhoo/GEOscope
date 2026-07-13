from __future__ import annotations

import numpy as np
import pytest

import geo_index.mcp_search_service as mcp_search
from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_search_service import (
    McpSearchService,
    UnknownFilterValueError,
)
from geo_index.search_models import (
    FACET_FIELDS,
    FacetBucket,
    FacetResult,
    SearchFilters,
    SearchProvenance,
    SearchResponse,
)


SETTINGS = ElasticsearchSettings(
    url="https://elastic.internal:9200",
    api_key="secret",
    active_model_key="gemini_embedding_2_3072_v1",
)


def _document(gse: str = "GSE123", **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "gse": gse,
        "title": "Study title",
        "summary": "Study summary",
        "overall_design": "Study design",
        "type": "Expression profiling by high throughput sequencing",
        "n_samples": 12,
        "pubmed_ids": ["12345678"],
        "organism_ids": ["NCBITaxon:9606"],
        "organism_status": "mapped",
        "sex_ids": ["PATO:0000383"],
        "sex_status": "mapped",
        "assay_categories": ["transcriptomics"],
        "assay_labels": ["scRNA-seq"],
        "assay_status": "mapped",
    }
    value.update(overrides)
    return value


def _facets(scope: str = "candidate_pool") -> dict[str, FacetResult]:
    return {
        field: FacetResult(
            field=field,
            buckets=(FacetBucket(value="value", label="Value", count=3),),
            scope=scope,  # type: ignore[arg-type]
            candidate_count=4 if scope == "candidate_pool" else None,
        )
        for field in FACET_FIELDS
    }


def _response(*, mode: str = "bm25", hits: tuple[dict[str, object], ...] | None = None):
    return SearchResponse(
        hits=hits or ({"gse": "GSE123", "score": 0.75},),
        facets=_facets(),
        provenance=SearchProvenance(
            backend="elasticsearch",
            mapping_revision="geo-series-v1",
            active_model_key="gemini_embedding_2_3072_v1",
            vector_field="embedding_gemini_3072",
            dimensions=3072,
            mode=mode,  # type: ignore[arg-type]
        ),
    )


class _Client:
    def __init__(self, documents: dict[str, dict[str, object]] | None = None) -> None:
        self.documents = documents or {"GSE123": _document()}
        self.closed = False
        self.search_calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> dict[str, object]:
        self.search_calls.append(kwargs)
        aggregations = {
            field: {"buckets": [{"key": value, "doc_count": 1}]}
            for field, value in {
                "organism_ids": "NCBITaxon:9606",
                "sex_ids": "PATO:0000383",
                "assay_categories": "transcriptomics",
                "assay_labels": "scRNA-seq",
            }.items()
        }
        return {"aggregations": aggregations}

    def mget(self, **kwargs: object) -> dict[str, object]:
        return {
            "docs": [
                {"_id": gse, "found": gse in self.documents,
                 "_source": self.documents.get(gse)}
                for gse in kwargs["ids"]  # type: ignore[union-attr]
            ]
        }

    def close(self) -> None:
        self.closed = True


class _DomainSearch:
    def __init__(self, encode_query, responses: list[SearchResponse] | None = None):
        self.encode_query = encode_query
        self.responses = list(responses or [_response()])
        self.calls: list[dict[str, object]] = []

    def search(self, query: str, **kwargs: object) -> SearchResponse:
        self.calls.append({"query": query, **kwargs})
        if kwargs["mode"] != "bm25":
            self.encode_query(query)
        return self.responses.pop(0)

    def get_dataset(self, gse: str) -> dict[str, object] | None:
        return _document(gse) if gse == "GSE123" else None


class _Encoder:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = False

    def encode(self, query: str) -> np.ndarray:
        self.queries.append(query)
        return np.ones(3072, dtype=np.float32)

    def close(self) -> None:
        self.closed = True


def _service(
    *,
    client: _Client | None = None,
    encoder: _Encoder | None = None,
    responses: list[SearchResponse] | None = None,
):
    active_client = client or _Client()
    active_encoder = encoder or _Encoder()
    readiness_calls: list[tuple[object, str]] = []
    domain: _DomainSearch | None = None

    def search_factory(client, *, active_model_key, encode_query):
        nonlocal domain
        domain = _DomainSearch(encode_query, responses)
        return domain

    service = McpSearchService(
        elasticsearch=SETTINGS,
        client_factory=lambda settings: active_client,
        query_encoder_factory=lambda key: active_encoder,
        search_service_factory=search_factory,
        readiness_check=lambda client, key: readiness_calls.append((client, key)),
    )
    return service, active_client, active_encoder, readiness_calls, lambda: domain


def test_open_validates_readiness_loads_fixed_vocabulary_and_close_is_idempotent() -> None:
    service, client, encoder, readiness_calls, _ = _service()
    service.open()

    assert service.is_open
    assert readiness_calls == [(client, "gemini_embedding_2_3072_v1")]
    assert set(client.search_calls[0]["aggs"]) == set(FACET_FIELDS)  # type: ignore[arg-type]
    assert service.facet_vocabulary["organism_ids"] == frozenset({"NCBITaxon:9606"})

    service.close()
    service.close()
    assert client.closed
    assert not encoder.closed
    assert not service.is_open


def test_startup_failure_closes_client_and_leaves_service_closed() -> None:
    client = _Client()
    service = McpSearchService(
        elasticsearch=SETTINGS,
        client_factory=lambda settings: client,
        readiness_check=lambda client, key: (_ for _ in ()).throw(RuntimeError("bad index")),
    )

    with pytest.raises(RuntimeError, match="bad index"):
        service.open()
    assert client.closed
    assert not service.is_open


def test_truncated_facet_vocabulary_fails_closed() -> None:
    class TruncatedClient(_Client):
        def search(self, **kwargs: object) -> dict[str, object]:
            response = super().search(**kwargs)
            response["aggregations"]["assay_labels"]["sum_other_doc_count"] = 1
            return response

    client = TruncatedClient()
    service = McpSearchService(
        elasticsearch=SETTINGS,
        client_factory=lambda settings: client,
        readiness_check=lambda client, key: None,
    )

    with pytest.raises(RuntimeError, match="assay_labels vocabulary exceeds"):
        service.open()
    assert client.closed
    assert not service.is_open


def test_public_search_always_uses_hybrid_retrieval() -> None:
    encoder = _Encoder()
    responses = [_response(mode="hybrid")]
    service, _, _, _, domain = _service(encoder=encoder, responses=responses)
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=5
    )

    assert domain().calls[0]["mode"] == "hybrid"
    assert domain().calls[0]["topk"] == 5
    assert encoder.queries == ["immune"]
    assert output.embedding_variant == "gemini_embedding_2_3072_v1"
    service.close()
    assert encoder.closed


def test_search_hydrates_ranked_hits_maps_provenance_and_bounds_output() -> None:
    client = _Client(
        {
            "GSE123": _document(
                title="t" * 600,
                summary="s" * 1200,
                assay_labels=["x" * 300] + [f"label-{i}" for i in range(110)],
            )
        }
    )
    service, _, _, _, _ = _service(
        client=client, responses=[_response(mode="hybrid")]
    )
    service.open()
    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=5
    )

    assert output.results[0].gse == "GSE123"
    assert output.results[0].rank == 1
    assert output.results[0].score == 0.75
    assert len(output.results[0].title or "") == 500
    assert len(output.results[0].snippet or "") == 1000
    assert len(output.results[0].assay_labels) == 100
    assert output.results[0].truncated_fields == ["assay_labels", "snippet", "title"]
    assert output.retrieval_version == (
        "geo-series-v1:gemini_embedding_2_3072_v1:"
        "embedding_gemini_3072:hybrid"
    )
    assert output.embedding_variant == "gemini_embedding_2_3072_v1"
    assert set(output.facets) == set(FACET_FIELDS)


def test_exact_lookup_maps_urls_pubmed_and_missing() -> None:
    service, _, _, _, _ = _service()
    service.open()

    found = service.get_dataset("GSE123")
    assert found.found
    assert str(found.dataset.geo_url).endswith("acc=GSE123")
    assert str(found.dataset.pubmed_url) == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert service.get_dataset("GSE999").model_dump() == {"found": False, "dataset": None}


def test_unknown_filter_is_rejected_before_search() -> None:
    service, _, _, _, domain = _service()
    service.open()
    with pytest.raises(UnknownFilterValueError, match="unknown organism_ids"):
        service.search_datasets(
            query="immune",
            filters=SearchFilters(organism_ids=("NCBITaxon:10090",)),
            limit=5,
        )
    assert domain().calls == []


def test_facet_values_use_filter_only_for_blank_and_hybrid_for_query() -> None:
    encoder = _Encoder()
    blank = SearchResponse(hits=(), facets=_facets("all_matches"), provenance=None)
    service, _, _, _, domain = _service(
        encoder=encoder,
        responses=[blank, _response(mode="hybrid")],
    )
    service.open()

    all_values = service.facet_values(
        field="organism_ids", query=None, filters=SearchFilters(), limit=10
    )
    assert all_values.scope == "all_matches"
    assert all_values.retrieval_version == "facet-all-matches-v1"
    assert all_values.embedding_variant is None

    candidates = service.facet_values(
        field="organism_ids", query="immune", filters=SearchFilters(), limit=10
    )
    assert candidates.scope == "candidate_pool"
    assert candidates.embedding_variant == "gemini_embedding_2_3072_v1"
    assert domain().calls[0]["query"] == ""
    assert domain().calls[0]["mode"] == "bm25"
    assert domain().calls[1]["mode"] == "hybrid"
    assert encoder.queries == ["immune"]


def test_ping_requires_open_service_and_rechecks_readiness() -> None:
    service, client, _, readiness_calls, _ = _service()
    with pytest.raises(RuntimeError, match="not open"):
        service.ping()
    service.open()
    service.ping()
    assert readiness_calls == [
        (client, "gemini_embedding_2_3072_v1"),
        (client, "gemini_embedding_2_3072_v1"),
    ]


def test_default_encoder_factory_delegates_to_shared_elasticsearch_factory(
    monkeypatch,
) -> None:
    sentinel = _Encoder()
    calls: list[str] = []

    def shared_factory(model_key: str):
        calls.append(model_key)
        return sentinel

    monkeypatch.setattr(mcp_search, "create_query_encoder", shared_factory)
    service = McpSearchService(elasticsearch=SETTINGS)

    assert service._default_query_encoder_factory(
        "gemini_embedding_2_3072_v1"
    ) is sentinel
    assert calls == ["gemini_embedding_2_3072_v1"]
