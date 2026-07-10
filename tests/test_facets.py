import pytest

from geo_index.facets import build_filter_clause
from geo_index.search_models import SearchFilters


def test_filter_sql_ors_within_and_ands_across_fields() -> None:
    sql, params = build_filter_clause(
        SearchFilters(
            organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
            sex_ids=("PATO:0000383",),
        ),
        alias="s",
    )
    assert "s.organism_ids && %(filter_organism_ids)s::text[]" in sql
    assert "s.sex_ids && %(filter_sex_ids)s::text[]" in sql
    assert " AND " in sql
    assert params["filter_organism_ids"] == ["NCBITaxon:9606", "NCBITaxon:10090"]


def test_filter_sql_can_exclude_its_own_facet() -> None:
    sql, params = build_filter_clause(
        SearchFilters(
            organism_ids=("NCBITaxon:9606",),
            sex_ids=("PATO:0000383",),
        ),
        exclude="organism_ids",
    )
    assert "organism_ids" not in sql
    assert "filter_organism_ids" not in params
    assert "sex_ids" in sql


def test_empty_filter_is_a_true_predicate() -> None:
    assert build_filter_clause(SearchFilters()) == ("TRUE", {})


def test_filter_builder_rejects_non_whitelisted_identifiers() -> None:
    with pytest.raises(ValueError, match="unsupported SQL alias"):
        build_filter_clause(SearchFilters(), alias="s; DROP TABLE series")
    with pytest.raises(ValueError, match="unknown facet field"):
        build_filter_clause(SearchFilters(), exclude="tissue_ids")  # type: ignore[arg-type]

