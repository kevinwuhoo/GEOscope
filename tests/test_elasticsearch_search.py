from __future__ import annotations

import inspect
from typing import Any

import pytest

from geo_index.elasticsearch_search import (
    ElasticsearchSearchService,
    build_filter_query,
)
from geo_index.search_models import FACET_FIELDS, SearchFilters


def _response(*hits: tuple[str, float, dict[str, object]]) -> dict[str, object]:
    return {
        "hits": {
            "hits": [
                {"_id": gse, "_score": score, "_source": {"gse": gse, **source}}
                for gse, score, source in hits
            ]
        }
    }


class _Client:
    def __init__(
        self,
        *,
        search_responses: list[dict[str, object]] | None = None,
        documents: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.search_responses = list(search_responses or [])
        self.documents = documents or {}
        self.search_calls: list[dict[str, object]] = []
        self.exists_calls: list[dict[str, str]] = []
        self.get_calls: list[dict[str, str]] = []

    def search(self, **kwargs: object) -> dict[str, object]:
        self.search_calls.append(kwargs)
        if not self.search_responses:
            raise AssertionError("unexpected search call")
        return self.search_responses.pop(0)

    def exists(self, *, index: str, id: str) -> bool:
        self.exists_calls.append({"index": index, "id": id})
        return id in self.documents

    def get(self, *, index: str, id: str) -> dict[str, object]:
        self.get_calls.append({"index": index, "id": id})
        return {"_id": id, "_source": self.documents[id], "found": True}


class _WrappedResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body


class _WrappedClient(_Client):
    def search(self, **kwargs: object) -> _WrappedResponse:
        return _WrappedResponse(super().search(**kwargs))

    def get(self, *, index: str, id: str) -> _WrappedResponse:
        return _WrappedResponse(super().get(index=index, id=id))


def _service(
    client: _Client, *, encode_calls: list[str] | None = None
) -> ElasticsearchSearchService:
    calls = encode_calls if encode_calls is not None else []

    def encode(query: str) -> list[float]:
        calls.append(query)
        return [0.0] * 383 + [1.0]

    return ElasticsearchSearchService(
        client,
        active_model_key="bge_small_v15",
        encode_query=encode,
    )


def test_exact_lookup_uses_gse_document_id_and_handles_missing() -> None:
    client = _Client(documents={"GSE2": {"gse": "GSE2", "title": "two"}})
    service = _service(client)
    assert service.get_dataset("gse2") == {"gse": "GSE2", "title": "two"}
    assert service.get_dataset("GSE10") is None
    assert client.exists_calls == [
        {"index": "geo-series", "id": "GSE2"},
        {"index": "geo-series", "id": "GSE10"},
    ]
    assert client.get_calls == [{"index": "geo-series", "id": "GSE2"}]


def test_search_accepts_official_client_style_object_responses() -> None:
    client = _WrappedClient(
        documents={"GSE2": {"gse": "GSE2", "title": "two"}},
        search_responses=[
            _response(("GSE2", 1.0, {"title": "two"})),
            *[_response() for _ in FACET_FIELDS],
        ],
    )
    service = _service(client)
    assert service.get_dataset("GSE2") == {"gse": "GSE2", "title": "two"}
    assert service.search("immune", mode="bm25").hits[0]["gse"] == "GSE2"


def test_filter_query_ors_within_and_ands_across() -> None:
    query = build_filter_query(
        SearchFilters(
            organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
            sex_ids=("PATO:0000383",),
        )
    )
    assert query == [
        {
            "terms": {
                "organism_ids": ["NCBITaxon:9606", "NCBITaxon:10090"]
            }
        },
        {"terms": {"sex_ids": ["PATO:0000383"]}},
    ]


def test_bm25_uses_frozen_fields_and_filters_without_encoding() -> None:
    client = _Client(search_responses=[_response(("GSE2", 2.0, {"title": "two"}))] + [
        {"aggregations": {"values": {"buckets": []}}} for _ in FACET_FIELDS
    ])
    encode_calls: list[str] = []
    service = _service(client, encode_calls=encode_calls)
    service.search(
        "immune cells",
        mode="bm25",
        filters=SearchFilters(organism_ids=("NCBITaxon:9606",)),
    )
    request = client.search_calls[0]
    multi_match = request["query"]["bool"]["must"][0]["multi_match"]  # type: ignore[index]
    assert multi_match["fields"] == [
        "title^3",
        "summary^2",
        "overall_design",
        "embed_text",
    ]
    assert request["query"]["bool"]["filter"] == [  # type: ignore[index]
        {"terms": {"organism_ids": ["NCBITaxon:9606"]}}
    ]
    assert encode_calls == []


def test_dense_uses_only_deployment_selected_vector_field() -> None:
    client = _Client(search_responses=[_response(("GSE2", 0.9, {"title": "two"}))] + [
        _response() for _ in FACET_FIELDS
    ])
    service = _service(client)
    response = service.search("immune", mode="dense")
    request = client.search_calls[0]
    assert request["knn"]["field"] == "embedding_bge_384"  # type: ignore[index]
    assert len(request["knn"]["query_vector"]) == 384  # type: ignore[index]
    assert response.provenance is not None
    assert response.provenance.active_model_key == "bge_small_v15"
    assert response.provenance.vector_field == "embedding_bge_384"


def test_hybrid_uses_native_rrf_standard_and_knn_retrievers() -> None:
    client = _Client(search_responses=[_response(("GSE2", 0.03, {}))] + [
        _response() for _ in FACET_FIELDS
    ])
    service = _service(client)
    service.search(
        "immune",
        mode="hybrid",
        filters=SearchFilters(sex_ids=("PATO:0000383",)),
        deep=50,
        k0=20,
    )
    rrf = client.search_calls[0]["retriever"]["rrf"]  # type: ignore[index]
    assert "standard" in rrf["retrievers"][0]
    assert rrf["retrievers"][1]["knn"]["field"] == "embedding_bge_384"
    assert rrf["filter"] == [{"terms": {"sex_ids": ["PATO:0000383"]}}]
    assert rrf["rank_window_size"] == 50
    assert rrf["rank_constant"] == 20


def test_search_has_no_public_model_selector() -> None:
    parameters = inspect.signature(ElasticsearchSearchService.search).parameters
    assert "model" not in parameters
    assert "model_key" not in parameters
    assert "vector_field" not in parameters


def test_ranked_hits_use_score_then_stable_gse_secondary_order() -> None:
    client = _Client(
        search_responses=[
            _response(
                ("GSE2", 1.0, {"title": "two"}),
                ("GSE10", 1.0, {"title": "ten"}),
                ("GSE1", 2.0, {"title": "one"}),
            ),
            *[{"aggregations": {"values": {"buckets": []}}} for _ in FACET_FIELDS],
        ]
    )
    response = _service(client).search("immune", mode="bm25")
    assert [hit["gse"] for hit in response.hits] == ["GSE1", "GSE10", "GSE2"]


def test_query_vector_dimension_and_nonfinite_values_are_rejected() -> None:
    client = _Client()
    wrong = ElasticsearchSearchService(
        client,
        active_model_key="bge_small_v15",
        encode_query=lambda _: [0.0] * 383,
    )
    with pytest.raises(ValueError, match="384 dimensions"):
        wrong.search("immune", mode="dense")
    nonfinite = ElasticsearchSearchService(
        client,
        active_model_key="bge_small_v15",
        encode_query=lambda _: [float("nan")] * 384,
    )
    with pytest.raises(ValueError, match="nonfinite"):
        nonfinite.search("immune", mode="hybrid")


def test_blank_query_facets_omit_own_filter_and_cover_all_matches() -> None:
    facet_responses = [
        {
            "aggregations": {
                "values": {
                    "buckets": [
                        {"key": "NCBITaxon:10090", "doc_count": 1},
                        {"key": "NCBITaxon:9606", "doc_count": 1},
                    ]
                }
            }
        },
        *[{"aggregations": {"values": {"buckets": []}}} for _ in range(3)],
    ]
    client = _Client(search_responses=[_response(), *facet_responses])
    filters = SearchFilters(
        organism_ids=("NCBITaxon:9606",),
        sex_ids=("PATO:0000383",),
    )
    response = _service(client).search("", mode="bm25", filters=filters)
    organism_request = client.search_calls[1]
    filter_clauses = organism_request["query"]["bool"]["filter"]  # type: ignore[index]
    assert {"terms": {"organism_ids": ["NCBITaxon:9606"]}} not in filter_clauses
    assert {"terms": {"sex_ids": ["PATO:0000383"]}} in filter_clauses
    organism = response.facets["organism_ids"]
    assert organism.scope == "all_matches"
    assert organism.candidate_count is None
    assert [(bucket.value, bucket.count) for bucket in organism.buckets] == [
        ("NCBITaxon:10090", 1),
        ("NCBITaxon:9606", 1),
    ]


def test_query_facets_use_bounded_disjunctive_pools_and_embed_once() -> None:
    result = _response(("GSE2", 1.0, {"title": "two"}))
    candidate = _response(
        (
            "GSE2",
            1.0,
            {
                "organism_ids": ["NCBITaxon:9606", "NCBITaxon:9606"],
                "sex_ids": ["PATO:0000383"],
                "assay_categories": ["transcriptomic"],
                "assay_labels": ["RNA-seq"],
            },
        ),
        (
            "GSE10",
            0.9,
            {
                "organism_ids": ["NCBITaxon:10090"],
                "sex_ids": [],
                "assay_categories": ["transcriptomic"],
                "assay_labels": ["RNA-seq"],
            },
        ),
    )
    client = _Client(search_responses=[result, candidate, candidate, candidate, candidate])
    encode_calls: list[str] = []
    filters = SearchFilters(
        organism_ids=("NCBITaxon:9606",),
        sex_ids=("PATO:0000383",),
    )
    response = _service(client, encode_calls=encode_calls).search(
        "immune", mode="dense", filters=filters, facet_pool=1000
    )
    assert encode_calls == ["immune"]
    assert len(client.search_calls) == 5
    organism_knn = client.search_calls[1]["knn"]  # type: ignore[index]
    assert organism_knn["k"] == 1000
    assert {"terms": {"organism_ids": ["NCBITaxon:9606"]}} not in organism_knn["filter"]
    assert {"terms": {"sex_ids": ["PATO:0000383"]}} in organism_knn["filter"]
    organism = response.facets["organism_ids"]
    assert organism.scope == "candidate_pool"
    assert organism.candidate_count == 2
    assert [(bucket.value, bucket.count) for bucket in organism.buckets] == [
        ("NCBITaxon:10090", 1),
        ("NCBITaxon:9606", 1),
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": "bogus"}, "unsupported search mode"),
        ({"topk": 0}, "topk"),
        ({"topk": 10, "deep": 5}, "deep"),
        ({"num_candidates": 0}, "num_candidates"),
        ({"facet_pool": 0}, "facet_pool"),
        ({"bucket_limit": 0}, "bucket_limit"),
    ],
)
def test_search_rejects_invalid_bounds(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _service(_Client()).search("immune", **kwargs)
