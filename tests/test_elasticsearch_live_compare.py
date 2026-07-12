from __future__ import annotations

import json
from pathlib import Path

import pytest

from geo_index.elasticsearch_index import index_definition
from geo_index.elasticsearch_live_compare import (
    inspect_index,
    load_query_cases,
    run_comparison,
)
from geo_index.elasticsearch_query_embeddings import QueryEncoderInfo
from geo_index.search_models import (
    FACET_FIELDS,
    FacetBucket,
    FacetResult,
    SearchFilters,
    SearchProvenance,
    SearchResponse,
)


def _write_rows(path: Path, rows: list[object]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_load_query_cases_preserves_order_and_normalizes_filters(
    tmp_path: Path,
) -> None:
    path = tmp_path / "queries.jsonl"
    _write_rows(
        path,
        [
            {
                "query_id": "human_scrna",
                "query": "human tumor single-cell RNA sequencing",
                "intent": "human tumor scRNA-seq",
                "filters": {
                    "organism_ids": ["NCBITaxon:9606"],
                    "assay_labels": ["scRNA-seq"],
                },
            },
            {
                "query_id": "mouse_spatial",
                "query": "mouse brain spatial transcriptomics",
                "intent": "mouse spatial studies",
                "filters": {"organism_ids": ["NCBITaxon:10090"]},
            },
        ],
    )

    cases = load_query_cases(path)

    assert [case.query_id for case in cases] == ["human_scrna", "mouse_spatial"]
    assert cases[0].filters == SearchFilters(
        organism_ids=("NCBITaxon:9606",),
        assay_labels=("scRNA-seq",),
    )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {"query_id": "same", "query": "one", "intent": "one"},
                {"query_id": "same", "query": "two", "intent": "two"},
            ],
            "duplicate query_id",
        ),
        ([{"query_id": "Bad ID", "query": "one", "intent": "one"}], "query_id"),
        ([{"query_id": "blank", "query": " ", "intent": "one"}], "blank query"),
        ([{"query_id": "blank", "query": "one", "intent": " "}], "blank intent"),
        (
            [
                {
                    "query_id": "unknown_filter",
                    "query": "one",
                    "intent": "one",
                    "filters": {"tissue": ["lung"]},
                }
            ],
            "unknown filter",
        ),
    ],
)
def test_load_query_cases_rejects_invalid_rows(
    tmp_path: Path, rows: list[object], message: str
) -> None:
    path = tmp_path / "queries.jsonl"
    _write_rows(path, rows)

    with pytest.raises(ValueError, match=message):
        load_query_cases(path)


def test_load_query_cases_reports_malformed_json_line(tmp_path: Path) -> None:
    path = tmp_path / "queries.jsonl"
    path.write_text('{"query_id":\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 1"):
        load_query_cases(path)


class _FakeIndices:
    def __init__(self, mapping: dict[str, object]) -> None:
        self.mapping = mapping

    def get_mapping(self, *, index: str):
        assert index == "geo-series"
        return {"geo-series": self.mapping}


class _FakeCluster:
    def __init__(self, status: str) -> None:
        self.status = status

    def health(self, *, index: str):
        assert index == "geo-series"
        return {"status": self.status}


class _FakeInspectionClient:
    def __init__(
        self,
        *,
        version: str = "9.4.2",
        health: str = "green",
        document_count: int = 10,
        coverage: dict[str, int] | None = None,
        mapping: dict[str, object] | None = None,
    ) -> None:
        self.version = version
        self.document_count = document_count
        self.coverage = coverage or {
            "embedding_bge_384": document_count,
            "embedding_medcpt_768": document_count,
            "embedding_qwen3_06b_1024": document_count,
            "embedding_gemini_3072": 0,
        }
        self.indices = _FakeIndices(mapping or index_definition()["mappings"])
        self.cluster = _FakeCluster(health)

    def info(self):
        return {"version": {"number": self.version}}

    def count(self, *, index: str, query=None):
        assert index == "geo-series"
        if query is None:
            return {"count": self.document_count}
        field = query["exists"]["field"]
        return {"count": self.coverage[field]}


def test_inspect_index_validates_live_mapping_and_vector_coverage() -> None:
    snapshot = inspect_index(_FakeInspectionClient())

    assert snapshot.server_version == "9.4.2"
    assert snapshot.mapping_revision == "geo-series-v1"
    assert snapshot.document_count == 10
    assert snapshot.vector_coverage == {
        "bge_small_v15": 10,
        "medcpt_v1": 10,
        "qwen3_06b_1024_v1": 10,
        "gemini_embedding_2_3072_v1": 0,
    }


@pytest.mark.parametrize(
    ("client", "message"),
    [
        (_FakeInspectionClient(version="9.5.0"), "requires Elasticsearch 9.4.2"),
        (_FakeInspectionClient(health="red"), "cluster health is red"),
        (_FakeInspectionClient(document_count=0), "empty"),
        (
            _FakeInspectionClient(
                coverage={
                    "embedding_bge_384": 9,
                    "embedding_medcpt_768": 10,
                    "embedding_qwen3_06b_1024": 10,
                    "embedding_gemini_3072": 0,
                }
            ),
            "incomplete vector coverage",
        ),
    ],
)
def test_inspect_index_rejects_invalid_live_state(
    client: _FakeInspectionClient, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        inspect_index(client)


_MODEL_DIMENSIONS = {
    "bge_small_v15": ("embedding_bge_384", 384),
    "medcpt_v1": ("embedding_medcpt_768", 768),
    "qwen3_06b_1024_v1": ("embedding_qwen3_06b_1024", 1024),
}


class _FakeEncoder:
    def __init__(self, model_key: str) -> None:
        field, dimensions = _MODEL_DIMENSIONS[model_key]
        del field
        self.info = QueryEncoderInfo(model_key, f"query/{model_key}", "sha", dimensions)
        self.closed = False

    def encode(self, _query: str):
        return [0.0] * (self.info.dimensions - 1) + [1.0]

    def close(self) -> None:
        self.closed = True


def _response(
    model_key: str,
    mode: str,
    filters: SearchFilters,
    *,
    blank: bool = False,
) -> SearchResponse:
    field, dimensions = _MODEL_DIMENSIONS[model_key]
    organism = list(filters.organism_ids or ("NCBITaxon:9606",))
    assay = list(filters.assay_categories or ("expression (array)",))
    labels = list(filters.assay_labels or ("scRNA-seq",))
    hits = tuple(
        {
            "gse": f"GSE{index}",
            "title": f"title {index}",
            "score": 1.0 if blank else float(10 - index),
            "organism_ids": organism,
            "sex_ids": [],
            "assay_categories": assay,
            "assay_labels": labels,
        }
        for index in range(1, 6)
    )
    scope = "all_matches" if blank else "candidate_pool"
    candidate_count = None if blank else 5
    facets = {
        facet: FacetResult(
            field=facet,
            buckets=(
                FacetBucket("NCBITaxon:9606", "human", 5),
                FacetBucket("NCBITaxon:10090", "mouse", 4),
            )
            if facet == "organism_ids"
            else (FacetBucket("value", "value", 5),),
            scope=scope,
            candidate_count=candidate_count,
        )
        for facet in FACET_FIELDS
    }
    return SearchResponse(
        hits=hits,
        facets=facets,
        provenance=SearchProvenance(
            backend="elasticsearch",
            mapping_revision="geo-series-v1",
            active_model_key=model_key,
            vector_field=field,
            dimensions=dimensions,
            mode=mode,
        ),
    )


class _RecordingService:
    def __init__(self, model_key: str) -> None:
        self.model_key = model_key
        self.calls: list[dict[str, object]] = []

    def get_dataset(self, gse: str):
        assert gse == "gse1124"
        return {"gse": "GSE1124", "title": "childhood malaria"}

    def search(self, query: str, **kwargs):
        call = {"query": query, **kwargs}
        self.calls.append(call)
        return _response(
            self.model_key,
            str(kwargs["mode"]),
            kwargs.get("filters") or SearchFilters(),
            blank=not query.strip(),
        )


def test_run_comparison_executes_full_hybrid_and_diagnostic_paths() -> None:
    cases = (
        load_query_cases(
            Path("eval/elasticsearch_live_queries.jsonl")
        )[0],
    )
    services: list[_RecordingService] = []
    encoders: list[_FakeEncoder] = []

    def service_factory(_client, *, active_model_key, encode_query):
        assert callable(encode_query)
        service = _RecordingService(active_model_key)
        services.append(service)
        return service

    def encoder_factory(model_key: str):
        encoder = _FakeEncoder(model_key)
        encoders.append(encoder)
        return encoder

    run = run_comparison(
        _FakeInspectionClient(),
        cases,
        encoder_factory=encoder_factory,
        service_factory=service_factory,
    )

    assert list(run.models) == list(_MODEL_DIMENSIONS)
    assert len(run.bm25_by_query[cases[0].query_id].hits) == 5
    assert all(encoder.closed for encoder in encoders)
    query_calls = [
        call
        for service in services
        for call in service.calls
        if call["query"] == cases[0].query
    ]
    assert [call["mode"] for call in query_calls].count("bm25") == 1
    assert [call["mode"] for call in query_calls].count("dense") == 3
    assert [call["mode"] for call in query_calls].count("hybrid") == 3
    for call in query_calls:
        assert call["topk"] == 5
        assert call["deep"] == 100
        assert call["num_candidates"] == 500
        assert call["k0"] == 60
        assert call["facet_pool"] == 100
        assert call["bucket_limit"] == 10
