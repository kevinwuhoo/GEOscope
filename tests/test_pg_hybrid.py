from __future__ import annotations

from typing import Any

import pytest

from geo_index.pg_hybrid import _search_statement, search_rows
from geo_index.search_models import SearchFilters


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, statement: str, params: dict[str, object] | None = None) -> None:
        self.calls.append((statement, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()

    def cursor(self) -> _Cursor:
        return self.cursor_instance


@pytest.mark.parametrize("mode", ["bm25", "dense"])
def test_single_branch_statement_filters_before_order_and_limit(mode: str) -> None:
    predicate = "s.organism_ids && %(filter_organism_ids)s::text[]"
    statement = _search_statement(mode, predicate)
    assert predicate in statement
    assert statement.index(predicate) < statement.index("LIMIT")
    if mode == "dense":
        assert statement.index(predicate) < statement.index("ORDER BY s.embedding <=>")


def test_hybrid_statement_filters_both_branches_before_branch_limits() -> None:
    predicate = "s.organism_ids && %(filter_organism_ids)s::text[]"
    statement = _search_statement("hybrid", predicate)
    assert statement.count(predicate) == 2
    assert statement.index(predicate) < statement.index("LIMIT %(deep)s")
    second_predicate = statement.index(predicate, statement.index(predicate) + 1)
    second_limit = statement.index("LIMIT %(deep)s", statement.index("LIMIT %(deep)s") + 1)
    assert second_predicate < second_limit


def test_filtered_search_uses_parameters_for_all_user_values() -> None:
    conn = _Connection()
    filters = SearchFilters(
        organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
        sex_ids=("PATO:0000383",),
    )
    assert search_rows(conn, "immune", mode="bm25", filters=filters) == []

    statement, params = conn.cursor_instance.calls[-1]
    assert "NCBITaxon:9606" not in statement
    assert "PATO:0000383" not in statement
    assert params is not None
    assert params["filter_organism_ids"] == ["NCBITaxon:9606", "NCBITaxon:10090"]
    assert params["filter_sex_ids"] == ["PATO:0000383"]


def test_dense_search_enables_iterative_hnsw_scanning() -> None:
    conn = _Connection()
    search_rows(conn, "immune", mode="dense", qv=object())
    calls = conn.cursor_instance.calls
    assert calls[0] == ("SET LOCAL hnsw.iterative_scan = 'relaxed_order'", None)
    assert "ORDER BY s.embedding <=>" in calls[1][0]


def test_search_rejects_unknown_mode_and_invalid_depths() -> None:
    conn = _Connection()
    with pytest.raises(ValueError, match="unsupported search mode"):
        search_rows(conn, "immune", mode="bogus")
    with pytest.raises(ValueError, match="require topk"):
        search_rows(conn, "immune", mode="bm25", topk=10, deep=5)

