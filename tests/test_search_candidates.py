from geo_index.search_candidates import (
    SearchCandidate,
    candidate_matches_filters,
    candidate_pool_limit,
    fallback_order,
    merge_candidates,
)
from geo_index.search_models import SearchFilters


def candidate(gse: str, source: str, rank: int, **overrides: object) -> SearchCandidate:
    values: dict[str, object] = {
        "gse": gse,
        "title": f"Title {gse}",
        "snippet": f"Summary {gse}",
        "study_type": "Expression profiling by array",
        "n_samples": None,
        "pubmed_id": None,
        "organism_ids": ("NCBITaxon:10090",),
        "organism_status": "mapped",
        "sex_ids": (),
        "sex_status": "unavailable" if source == "ncbi" else "absent",
        "assay_categories": ("expression (array)",),
        "assay_labels": (),
        "assay_status": "category",
        "source": source,
        "retrieval_score": 0.25 if source == "elasticsearch" else None,
        "original_rank": rank if source == "elasticsearch" else None,
        "native_rank": rank if source == "ncbi" else None,
        "taxon": "Mus musculus",
    }
    values.update(overrides)
    return SearchCandidate(**values)


def test_candidate_pool_floor_target_and_cap() -> None:
    assert candidate_pool_limit(5, 40) == 40
    assert candidate_pool_limit(5, 20) == 40
    assert candidate_pool_limit(20, 40) == 80
    assert candidate_pool_limit(50, 40) == 100


def test_merge_prefers_local_metadata_and_marks_both_sources() -> None:
    local = candidate(
        "GSE1",
        "elasticsearch",
        1,
        title="Indexed title",
        retrieval_score=0.75,
    )
    native = candidate("GSE1", "ncbi", 3, title="Native title")

    merged = merge_candidates((local,), (native,), SearchFilters())

    assert len(merged) == 1
    assert merged[0].source == "both"
    assert merged[0].title == "Indexed title"
    assert merged[0].retrieval_score == 0.75
    assert merged[0].original_rank == 1
    assert merged[0].native_rank == 3


def test_merge_preserves_local_truncation_provenance() -> None:
    local = candidate(
        "GSE1",
        "elasticsearch",
        1,
        truncated_fields=("snippet", "title"),
    )
    native = candidate("GSE1", "ncbi", 3)

    merged = merge_candidates((local,), (native,), SearchFilters())

    assert merged[0].source == "both"
    assert merged[0].truncated_fields == ("snippet", "title")


def test_merge_keeps_first_repeated_ncbi_candidate_as_ncbi_only() -> None:
    merged = merge_candidates(
        (),
        (
            candidate("GSE5", "ncbi", 2, title="First native title"),
            candidate("GSE5", "ncbi", 1, title="Later native title"),
        ),
        SearchFilters(),
    )

    assert len(merged) == 1
    assert merged[0].source == "ncbi"
    assert merged[0].title == "First native title"
    assert merged[0].native_rank == 2


def test_ncbi_only_candidate_must_prove_every_active_filter() -> None:
    native = candidate("GSE2", "ncbi", 1)

    assert candidate_matches_filters(
        native, SearchFilters(organism_ids=("NCBITaxon:10090",))
    )
    assert not candidate_matches_filters(
        native, SearchFilters(sex_ids=("PATO:0000384",))
    )


def test_fallback_keeps_elasticsearch_order_then_ncbi_only_order() -> None:
    candidates = merge_candidates(
        (
            candidate("GSE2", "elasticsearch", 2),
            candidate("GSE1", "elasticsearch", 1),
        ),
        (
            candidate("GSE3", "ncbi", 2),
            candidate("GSE4", "ncbi", 1),
        ),
        SearchFilters(),
    )

    assert [item.gse for item in fallback_order(candidates)] == [
        "GSE1",
        "GSE2",
        "GSE4",
        "GSE3",
    ]
