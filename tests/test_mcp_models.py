from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from geo_index.mcp_models import (
    DatasetDetail,
    DatasetSummary,
    FacetBucketOutput,
    FacetResultOutput,
    FacetValuesInput,
    FacetValuesOutput,
    GetDatasetInput,
    GetDatasetOutput,
    SearchDatasetsInput,
    SearchDatasetsOutput,
    SearchFiltersInput,
    SearchLatencyOutput,
    SearchProvenanceOutput,
)
from geo_index.search_models import SearchFilters


def _summary(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "rank": 1,
        "gse": "GSE123",
        "score": 0.75,
        "source": "elasticsearch",
        "retrieval_score": 0.75,
        "original_rank": 1,
        "title": "Study title",
        "snippet": "Short summary",
        "study_type": "Expression profiling by high throughput sequencing",
        "n_samples": 12,
        "pubmed_id": 12345678,
        "organism_ids": ["NCBITaxon:9606"],
        "organism_status": "mapped",
        "sex_ids": ["PATO:0000383"],
        "sex_status": "mapped",
        "assay_categories": ["transcriptomics"],
        "assay_labels": ["scRNA-seq"],
        "assay_status": "mapped",
        "truncated_fields": [],
    }
    values.update(overrides)
    return values


def _detail(**overrides: object) -> dict[str, object]:
    values = _summary()
    values.pop("rank")
    values.pop("score")
    values.pop("source")
    values.pop("retrieval_score")
    values.pop("original_rank")
    values.pop("snippet")
    values.update(
        {
            "summary": "Full indexed summary",
            "overall_design": "Indexed design",
            "geo_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123",
            "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
        }
    )
    values.update(overrides)
    return values


def _facet_result(field: str = "organism_ids") -> FacetResultOutput:
    return FacetResultOutput(
        field=field,
        buckets=[
            FacetBucketOutput(value="NCBITaxon:9606", label="Human", count=10)
        ],
        scope="all_matches",
        candidate_count=None,
    )


def _provenance() -> SearchProvenanceOutput:
    return SearchProvenanceOutput(
        exact_accession=False,
        elasticsearch_candidates=40,
        ncbi_candidates=20,
        merged_candidates=55,
        rerank_attempted=True,
        rerank_applied=True,
        rerank_model="claude-sonnet-5",
        rerank_reasoning_effort="low",
        rerank_thinking="disabled",
        rerank_input_tokens=1200,
        rerank_output_tokens=400,
        latency=SearchLatencyOutput(
            elasticsearch_ms=120,
            ncbi_ms=80,
            reranker_ms=200,
        ),
        degradation=[],
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"elasticsearch_candidates": 101}, id="candidate-count"),
        pytest.param({"rerank_input_tokens": -1}, id="token-count"),
        pytest.param(
            {
                "latency": {
                    "elasticsearch_ms": -1,
                    "ncbi_ms": 80,
                    "reranker_ms": 200,
                }
            },
            id="latency",
        ),
    ],
)
def test_provenance_rejects_invalid_bounds(overrides: dict[str, object]) -> None:
    values = _provenance().model_dump()
    values.update(overrides)

    with pytest.raises(ValidationError):
        SearchProvenanceOutput(**values)


def test_provenance_accepts_full_bounded_source_union() -> None:
    values = _provenance().model_dump()
    values.update(ncbi_candidates=100, merged_candidates=200)

    provenance = SearchProvenanceOutput(**values)

    assert provenance.ncbi_candidates == 100
    assert provenance.merged_candidates == 200


@pytest.mark.parametrize(
    "field,value",
    [("ncbi_candidates", 101), ("merged_candidates", 201)],
)
def test_provenance_rejects_counts_above_the_bounded_source_union(
    field: str, value: int
) -> None:
    values = _provenance().model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        SearchProvenanceOutput(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {
                "rerank_attempted": False,
                "rerank_applied": True,
                "rerank_model": None,
                "rerank_reasoning_effort": None,
                "rerank_thinking": None,
            },
            id="applied-without-attempt",
        ),
        pytest.param(
            {
                "rerank_attempted": True,
                "rerank_applied": False,
                "rerank_model": None,
                "rerank_reasoning_effort": "low",
                "rerank_thinking": "disabled",
            },
            id="attempt-without-model",
        ),
        pytest.param(
            {
                "rerank_attempted": False,
                "rerank_applied": False,
                "rerank_model": "claude-sonnet-5",
                "rerank_reasoning_effort": None,
                "rerank_thinking": None,
            },
            id="model-without-attempt",
        ),
        pytest.param(
            {
                "rerank_attempted": True,
                "rerank_applied": False,
                "rerank_model": "claude-sonnet-5",
                "rerank_reasoning_effort": None,
                "rerank_thinking": "disabled",
            },
            id="attempt-without-effort",
        ),
        pytest.param(
            {
                "rerank_attempted": False,
                "rerank_applied": False,
                "rerank_model": None,
                "rerank_reasoning_effort": "low",
                "rerank_thinking": None,
            },
            id="effort-without-attempt",
        ),
        pytest.param(
            {
                "rerank_attempted": True,
                "rerank_applied": False,
                "rerank_model": "claude-sonnet-5",
                "rerank_reasoning_effort": "low",
                "rerank_thinking": None,
            },
            id="attempt-without-thinking",
        ),
        pytest.param(
            {
                "rerank_attempted": False,
                "rerank_applied": False,
                "rerank_model": None,
                "rerank_reasoning_effort": None,
                "rerank_thinking": "disabled",
            },
            id="thinking-without-attempt",
        ),
    ],
)
def test_provenance_rejects_inconsistent_reranker_state(
    overrides: dict[str, object],
) -> None:
    values = _provenance().model_dump()
    values.update(overrides)

    with pytest.raises(ValidationError):
        SearchProvenanceOutput(**values)


def test_provenance_rejects_unapproved_rerank_thinking_value() -> None:
    values = _provenance().model_dump()
    values["rerank_thinking"] = "enabled"

    with pytest.raises(ValidationError):
        SearchProvenanceOutput(**values)


def test_search_input_bounds_and_forbids_unknown_fields() -> None:
    assert SearchDatasetsInput(query="x").limit == 10
    assert SearchDatasetsInput(query="x", limit=1).limit == 1
    assert SearchDatasetsInput(query="x", limit=50).limit == 50
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query=" ", limit=15)
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query="x", limit=0)
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query="x", limit=51)
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query="x", invented=True)
    with pytest.raises(ValidationError):
        SearchFiltersInput(organism_ids=[], invented=["x"])
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query="x", limit="5")


def test_filters_deduplicate_without_reordering_and_convert_to_domain() -> None:
    filters = SearchFiltersInput(
        organism_ids=["NCBITaxon:9606", "NCBITaxon:10090", "NCBITaxon:9606"],
        assay_labels=[" scRNA-seq ", "scRNA-seq", "ChIP-seq"],
    )

    assert filters.organism_ids == ["NCBITaxon:9606", "NCBITaxon:10090"]
    assert filters.assay_labels == ["scRNA-seq", "ChIP-seq"]
    assert filters.to_domain() == SearchFilters(
        organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
        assay_labels=("scRNA-seq", "ChIP-seq"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("organism_ids", "NCBITaxon:0"),
        ("organism_ids", "9606"),
        ("sex_ids", "PATO:383"),
        ("sex_ids", "PATO:000038X"),
        ("assay_categories", " "),
        ("assay_labels", "x" * 257),
    ],
)
def test_filters_reject_malformed_or_unbounded_values(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        SearchFiltersInput(**{field: [value]})


def test_filters_reject_more_than_twenty_values() -> None:
    with pytest.raises(ValidationError):
        SearchFiltersInput(assay_labels=[f"assay-{index}" for index in range(21)])
    with pytest.raises(ValidationError):
        SearchFiltersInput(organism_ids=["NCBITaxon:9606"] * 21)

    properties = SearchFiltersInput.model_json_schema()["properties"]
    assert all(properties[field]["maxItems"] == 20 for field in SearchFilters().as_dict())


def test_query_gse_and_public_fields_are_strict_and_normalized() -> None:
    request = SearchDatasetsInput(query="  single cell RNA  ")
    assert request.query == "single cell RNA"

    browse = FacetValuesInput(field="organism_ids", query="   ")
    assert browse.query is None
    assert GetDatasetInput(gse="  gse123 ").gse == "GSE123"
    for value in ("GSE0", "GSM123", "GSE-1", 123):
        with pytest.raises(ValidationError):
            GetDatasetInput(gse=value)
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query="x", mode="hybrid")
    with pytest.raises(ValidationError):
        FacetValuesInput(field="organism_ids", mode="hybrid")
    with pytest.raises(ValidationError):
        FacetValuesInput(field="tissue_ids")


def test_output_models_enforce_scalar_array_and_number_bounds() -> None:
    with pytest.raises(ValidationError):
        DatasetDetail(**_detail(summary="x" * 8001))
    with pytest.raises(ValidationError):
        DatasetDetail(**_detail(overall_design="x" * 8001))
    with pytest.raises(ValidationError):
        DatasetSummary(**_summary(organism_ids=["NCBITaxon:9606"] * 101))
    with pytest.raises(ValidationError):
        DatasetSummary(**_summary(assay_labels=["x" * 257]))
    with pytest.raises(ValidationError):
        DatasetSummary(**_summary(title="x" * 501))
    with pytest.raises(ValidationError):
        DatasetSummary(**_summary(snippet="x" * 1001))
    with pytest.raises(ValidationError):
        DatasetSummary(**_summary(study_type="x" * 201))
    with pytest.raises(ValidationError):
        DatasetSummary(**_summary(score=math.inf))


def test_truncated_fields_are_deduplicated_and_stably_sorted() -> None:
    summary = DatasetSummary(
        **_summary(truncated_fields=["title", "assay_labels", "title"])
    )
    assert summary.truncated_fields == ["assay_labels", "title"]


def test_outputs_have_exact_top_level_contracts() -> None:
    facets = {
        field: _facet_result(field)
        for field in SearchFilters().as_dict()
    }
    search = SearchDatasetsOutput(
        query="single cell RNA",
        filters=SearchFiltersInput(organism_ids=["NCBITaxon:9606"]),
        limit=5,
        retrieval_version="geo-series-v1:gemini:embedding_gemini_3072:hybrid",
        embedding_variant="gemini_embedding_2_3072_v1",
        results=[DatasetSummary(**_summary())],
        facets=facets,
        provenance=_provenance(),
    )
    assert set(search.model_dump(mode="json")) == {
        "query",
        "filters",
        "limit",
        "retrieval_version",
        "embedding_variant",
        "results",
        "facets",
        "provenance",
    }

    detail = GetDatasetOutput(found=True, dataset=DatasetDetail(**_detail()))
    assert set(detail.model_dump(mode="json")) == {"found", "dataset"}

    facet = FacetValuesOutput(
        field="organism_ids",
        buckets=_facet_result().buckets,
        scope="all_matches",
        candidate_count=None,
        retrieval_version="facet-all-matches-v1",
        embedding_variant=None,
    )
    assert set(facet.model_dump(mode="json")) == {
        "field", "buckets", "scope", "candidate_count",
        "retrieval_version", "embedding_variant",
    }


def test_found_and_facet_scope_cross_field_invariants() -> None:
    with pytest.raises(ValidationError):
        GetDatasetOutput(found=False, dataset=DatasetDetail(**_detail()))
    with pytest.raises(ValidationError):
        GetDatasetOutput(found=True, dataset=None)
    with pytest.raises(ValidationError):
        FacetValuesOutput(
            field="organism_ids", buckets=[], scope="all_matches",
            candidate_count=10, retrieval_version="facet-all-matches-v1",
            embedding_variant=None,
        )
    with pytest.raises(ValidationError):
        FacetValuesOutput(
            field="organism_ids", buckets=[], scope="candidate_pool",
            candidate_count=None, retrieval_version="bm25-v1",
            embedding_variant=None,
        )
