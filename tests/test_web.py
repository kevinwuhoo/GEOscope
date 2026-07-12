from __future__ import annotations

import pytest

from geo_index import web
from geo_index.search_models import (
    FacetBucket,
    FacetResult,
    SearchFilters,
    SearchResponse,
)
from geo_index.web import _parse_search_request, _serialize_search


def test_parse_search_request_preserves_repeated_filter_values() -> None:
    query, mode, topk, filters = _parse_search_request(
        {
            "q": [" immune cells "],
            "mode": ["bm25"],
            "topk": ["20"],
            "organism_id": ["NCBITaxon:9606", "NCBITaxon:10090"],
            "sex_id": ["PATO:0000383"],
            "assay_category": ["Expression profiling by high throughput sequencing"],
            "assay_label": ["scRNA-seq"],
        }
    )
    assert (query, mode, topk) == ("immune cells", "bm25", 20)
    assert filters == SearchFilters(
        organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
        sex_ids=("PATO:0000383",),
        assay_categories=("Expression profiling by high throughput sequencing",),
        assay_labels=("scRNA-seq",),
    )


@pytest.mark.parametrize(
    ("raw_mode", "raw_topk", "expected_mode", "expected_topk"),
    [
        ("unknown", "not-an-int", "hybrid", 15),
        ("dense", "0", "dense", 1),
        ("bm25", "999", "bm25", 50),
    ],
)
def test_parse_search_request_normalizes_mode_and_topk(
    raw_mode: str,
    raw_topk: str,
    expected_mode: str,
    expected_topk: int,
) -> None:
    _, mode, topk, _ = _parse_search_request(
        {"q": ["query"], "mode": [raw_mode], "topk": [raw_topk]}
    )
    assert (mode, topk) == (expected_mode, expected_topk)


def test_parse_search_request_rejects_blank_filter_values() -> None:
    with pytest.raises(ValueError, match="contains a blank value"):
        _parse_search_request({"q": ["query"], "organism_id": [""]})


def test_serialize_search_includes_filters_and_scoped_facet_metadata() -> None:
    filters = SearchFilters(organism_ids=("NCBITaxon:9606",))
    response = SearchResponse(
        hits=({"gse": "GSE1"},),
        facets={
            "organism_ids": FacetResult(
                field="organism_ids",
                buckets=(
                    FacetBucket(value="NCBITaxon:10090", label="Mus musculus", count=7),
                ),
                scope="candidate_pool",
                candidate_count=1000,
            )
        },
    )
    payload = _serialize_search(response, filters)
    assert payload["ours"] == [{"gse": "GSE1"}]
    assert payload["filters"] == {
        "organism_ids": ["NCBITaxon:9606"],
        "sex_ids": [],
        "assay_categories": [],
        "assay_labels": [],
    }
    facet = payload["facets"]["organism_ids"]  # type: ignore[index]
    assert facet["scope"] == "candidate_pool"
    assert facet["candidate_count"] == 1000


def test_our_search_delegates_filters_to_scoped_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    response = SearchResponse(hits=())
    runtime = type(
        "Runtime",
        (),
        {"search": lambda self, query, **kwargs: calls.append(
            {"query": query, **kwargs}
        ) or response},
    )()
    monkeypatch.setattr(web, "_runtime", runtime)
    filters = SearchFilters(organism_ids=("NCBITaxon:9606",))
    assert web._our_search("immune", "hybrid", 5, filters) is response
    assert calls == [
        {
            "query": "immune",
            "mode": "hybrid",
            "topk": 5,
            "filters": filters,
        }
    ]
