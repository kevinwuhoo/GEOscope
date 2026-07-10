from __future__ import annotations

from typing import Any

import pytest

from geo_index.facets import (
    build_filter_clause,
    facet_buckets,
    facet_counts,
    facet_label,
)
from geo_index.search_models import FACET_FIELDS, SearchFilters


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, statement: str, params: dict[str, object] | None = None) -> None:
        self.calls.append((statement, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.cursors: list[_Cursor] = []
        self.rows = rows

    def cursor(self) -> _Cursor:
        cursor = _Cursor(self.rows)
        self.cursors.append(cursor)
        return cursor


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


def test_facet_labels_resolve_supported_ids_and_preserve_unknowns() -> None:
    assert facet_label("organism_ids", "NCBITaxon:9606") == "Homo sapiens"
    assert facet_label("sex_ids", "PATO:0000383") == "female"
    assert facet_label("assay_labels", "scRNA-seq") == "scRNA-seq"
    assert facet_label("organism_ids", "NCBITaxon:999999") == "NCBITaxon:999999"


def test_facet_buckets_omit_own_filter_and_count_distinct_series_values() -> None:
    conn = _Connection(
        [("NCBITaxon:9606", 1), ("NCBITaxon:10090", 1)]
    )
    buckets = facet_buckets(
        conn,
        "organism_ids",
        filters=SearchFilters(
            organism_ids=("NCBITaxon:9606",),
            sex_ids=("PATO:0000383",),
        ),
        candidate_gses=None,
        limit=50,
    )
    statement, params = conn.cursors[0].calls[0]
    assert "SELECT DISTINCT s.id, u.value" in statement
    assert "unnest(s.organism_ids)" in statement
    assert "filter_organism_ids" not in statement
    assert "s.sex_ids && %(filter_sex_ids)s::text[]" in statement
    assert params == {"filter_sex_ids": ["PATO:0000383"], "bucket_limit": 50}
    assert [(bucket.value, bucket.count) for bucket in buckets] == [
        ("NCBITaxon:9606", 1),
        ("NCBITaxon:10090", 1),
    ]


def test_empty_candidate_pool_returns_no_buckets_without_querying() -> None:
    conn = _Connection([])
    assert (
        facet_buckets(
            conn,
            "organism_ids",
            filters=SearchFilters(),
            candidate_gses=[],
            limit=50,
        )
        == ()
    )
    assert conn.cursors == []


def test_blank_query_facets_are_exact_and_do_not_call_retrieval() -> None:
    conn = _Connection([])

    def fail_retrieve(*args: Any, **kwargs: Any) -> list[dict]:
        raise AssertionError("blank-query facets must aggregate directly")

    results = facet_counts(
        conn,
        query="  ",
        mode="hybrid",
        qv=None,
        filters=SearchFilters(organism_ids=("NCBITaxon:9606",)),
        retrieve=fail_retrieve,
    )
    assert tuple(results) == FACET_FIELDS
    assert all(result.scope == "all_matches" for result in results.values())
    assert all(result.candidate_count is None for result in results.values())


def test_text_query_facets_use_disjunctive_bounded_candidate_pools() -> None:
    conn = _Connection([])
    qv = object()
    filters = SearchFilters(
        organism_ids=("NCBITaxon:9606",),
        sex_ids=("PATO:0000383",),
    )
    retrieve_calls: list[dict[str, object]] = []

    def retrieve(_conn: object, query: str, **kwargs: object) -> list[dict]:
        retrieve_calls.append({"query": query, **kwargs})
        return [{"gse": "GSE1"}, {"gse": "GSE2"}]

    results = facet_counts(
        conn,
        query="immune cells",
        mode="hybrid",
        qv=qv,
        filters=filters,
        retrieve=retrieve,
        deep=200,
    )
    assert len(retrieve_calls) == 4
    for field, call in zip(FACET_FIELDS, retrieve_calls):
        assert call["topk"] == 1000
        assert call["deep"] == 1000
        assert call["qv"] is qv
        assert call["filters"] == filters.without(field)
    assert all(result.scope == "candidate_pool" for result in results.values())
    assert all(result.candidate_count == 2 for result in results.values())
    for cursor in conn.cursors:
        _, params = cursor.calls[0]
        assert params is not None
        assert params["candidate_gses"] == ["GSE1", "GSE2"]
