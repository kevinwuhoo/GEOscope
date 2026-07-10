from dataclasses import FrozenInstanceError

import pytest

from geo_index.search_models import (
    FACET_FIELDS,
    FacetBucket,
    FacetResult,
    SearchFilters,
)


def test_filters_deduplicate_without_reordering() -> None:
    filters = SearchFilters.from_mapping(
        {"organism_ids": ["NCBITaxon:9606", "NCBITaxon:9606", "NCBITaxon:10090"]}
    )
    assert filters.organism_ids == ("NCBITaxon:9606", "NCBITaxon:10090")


def test_without_removes_only_the_requested_facet() -> None:
    filters = SearchFilters(
        organism_ids=("NCBITaxon:9606",),
        sex_ids=("PATO:0000383",),
    )
    assert filters.without("organism_ids").organism_ids == ()
    assert filters.without("organism_ids").sex_ids == ("PATO:0000383",)


def test_unknown_filter_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown filter field"):
        SearchFilters.from_mapping({"tissue_ids": ["UBERON:0000955"]})


def test_filter_values_must_be_nonblank_sequences() -> None:
    with pytest.raises(ValueError, match="must be a sequence"):
        SearchFilters.from_mapping({"organism_ids": "NCBITaxon:9606"})
    with pytest.raises(ValueError, match="contains a blank value"):
        SearchFilters.from_mapping({"sex_ids": [" "]})


def test_contract_exposes_exactly_four_v1_fields() -> None:
    assert FACET_FIELDS == (
        "organism_ids",
        "sex_ids",
        "assay_categories",
        "assay_labels",
    )


def test_filter_and_facet_models_are_frozen() -> None:
    filters = SearchFilters(organism_ids=("NCBITaxon:9606",))
    bucket = FacetBucket(value="NCBITaxon:9606", label="Human", count=2)
    result = FacetResult(
        field="organism_ids",
        buckets=(bucket,),
        scope="all_matches",
        candidate_count=None,
    )

    with pytest.raises(FrozenInstanceError):
        filters.organism_ids = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        bucket.count = 3  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.scope = "candidate_pool"  # type: ignore[misc]
