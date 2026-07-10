from __future__ import annotations

import os
from typing import Any

import pytest

from geo_index import pg_hybrid
from geo_index.facets import facet_buckets
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


class _TransactionalConnection(_Connection):
    def __init__(self) -> None:
        super().__init__()
        self.commits = 0

    def __enter__(self) -> "_TransactionalConnection":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


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


def test_fresh_schema_contains_normalized_filter_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _TransactionalConnection()
    monkeypatch.setattr(pg_hybrid, "_connect", lambda: conn)
    assert pg_hybrid.init() == 0
    ddl = "\n".join(statement for statement, _ in conn.cursor_instance.calls)
    assert "organism_ids TEXT[]" in ddl
    assert "organism_status TEXT" in ddl
    assert "sex_ids TEXT[]" in ddl
    assert "sex_status TEXT" in ddl
    assert "assay_categories TEXT[]" in ddl
    assert "assay_labels TEXT[]" in ddl
    assert "assay_status TEXT" in ddl


def test_filter_index_builder_only_creates_four_array_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _TransactionalConnection()
    monkeypatch.setattr(pg_hybrid, "_connect", lambda: conn)
    assert pg_hybrid.build_filter_indexes() == 0
    statements = [statement for statement, _ in conn.cursor_instance.calls]
    gin_statements = [statement for statement in statements if "USING gin" in statement]
    assert len(gin_statements) == 4
    for field in (
        "organism_ids",
        "sex_ids",
        "assay_categories",
        "assay_labels",
    ):
        assert any(f"series_{field}_gin" in statement for statement in gin_statements)
        assert any(f"({field})" in statement for statement in gin_statements)
    assert statements[-1] == "ANALYZE series"
    assert conn.commits == 1


def test_filter_index_cli_dispatches_without_rebuilding_other_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_filter_indexes() -> int:
        nonlocal calls
        calls += 1
        return 7

    monkeypatch.setattr(pg_hybrid, "build_filter_indexes", fake_filter_indexes)
    assert pg_hybrid.main(["filter-index"]) == 7
    assert calls == 1


def test_search_with_facets_embeds_once_and_reuses_query_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qv = object()
    embed_calls: list[tuple[object, str]] = []
    search_calls: list[dict[str, object]] = []
    facet_calls: list[dict[str, object]] = []

    monkeypatch.setattr(pg_hybrid, "load_model", lambda: "model")

    def fake_embed(model: object, query: str) -> object:
        embed_calls.append((model, query))
        return qv

    def fake_search(_conn: object, query: str, **kwargs: object) -> list[dict]:
        search_calls.append({"query": query, **kwargs})
        return [{"gse": "GSE1"}]

    def fake_facets(_conn: object, **kwargs: object) -> dict:
        facet_calls.append(kwargs)
        return {}

    monkeypatch.setattr(pg_hybrid, "embed_query", fake_embed)
    monkeypatch.setattr(pg_hybrid, "search_rows", fake_search)
    monkeypatch.setattr(pg_hybrid, "facet_counts", fake_facets)

    filters = SearchFilters(organism_ids=("NCBITaxon:9606",))
    response = pg_hybrid.search_with_facets(
        object(), "immune cells", mode="hybrid", filters=filters
    )
    assert embed_calls == [("model", "immune cells")]
    assert response.hits == ({"gse": "GSE1"},)
    assert search_calls[0]["qv"] is qv
    assert facet_calls[0]["qv"] is qv
    assert search_calls[0]["filters"] is filters
    assert facet_calls[0]["filters"] is filters


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("GEO_TEST_PG") != "1", reason="set GEO_TEST_PG=1")
def test_live_schema_has_normalized_columns_and_array_indexes() -> None:
    expected_columns = {
        "organism_ids",
        "sex_ids",
        "assay_categories",
        "assay_labels",
    }
    expected_indexes = {f"series_{field}_gin" for field in expected_columns}
    with pg_hybrid._connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'series'"
        )
        columns = {row[0] for row in cur.fetchall()}
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = 'series'"
        )
        indexes = {row[0] for row in cur.fetchall()}
    assert expected_columns <= columns
    assert expected_indexes <= indexes


def _normalized_values(conn: object, gses: list[str]) -> dict[str, tuple[list[str], list[str]]]:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT gse, organism_ids, sex_ids FROM series "
            "WHERE gse = ANY(%(gses)s::text[])",
            {"gses": gses},
        )
        return {
            gse: (organism_ids or [], sex_ids or [])
            for gse, organism_ids, sex_ids in cur.fetchall()
        }


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("GEO_TEST_PG") != "1", reason="set GEO_TEST_PG=1")
def test_live_bm25_filters_human_mouse_or_and_impossible_values() -> None:
    human = "NCBITaxon:9606"
    mouse = "NCBITaxon:10090"
    female = "PATO:0000383"
    with pg_hybrid._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")

        human_rows = search_rows(
            conn,
            "cancer",
            mode="bm25",
            topk=10,
            filters=SearchFilters(organism_ids=(human,)),
        )
        mouse_rows = search_rows(
            conn,
            "cancer",
            mode="bm25",
            topk=10,
            filters=SearchFilters(organism_ids=(mouse,)),
        )
        or_rows = search_rows(
            conn,
            "immune",
            mode="bm25",
            topk=10,
            filters=SearchFilters(organism_ids=(human, mouse)),
        )
        and_rows = search_rows(
            conn,
            "immune",
            mode="bm25",
            topk=10,
            filters=SearchFilters(organism_ids=(human,), sex_ids=(female,)),
        )
        impossible_rows = search_rows(
            conn,
            "immune",
            mode="bm25",
            filters=SearchFilters(organism_ids=("NCBITaxon:impossible",)),
        )
        all_rows = human_rows + mouse_rows + or_rows + and_rows
        values = _normalized_values(conn, [str(row["gse"]) for row in all_rows])

    assert len(human_rows) == 10
    assert len(mouse_rows) == 10
    assert all(human in values[str(row["gse"])][0] for row in human_rows)
    assert all(mouse in values[str(row["gse"])][0] for row in mouse_rows)
    assert all({human, mouse} & set(values[str(row["gse"])][0]) for row in or_rows)
    assert all(
        human in values[str(row["gse"])][0]
        and female in values[str(row["gse"])][1]
        for row in and_rows
    )
    assert impossible_rows == []


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("GEO_TEST_PG") != "1", reason="set GEO_TEST_PG=1")
def test_live_selected_organism_facet_still_shows_alternatives() -> None:
    with pg_hybrid._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
        buckets = facet_buckets(
            conn,
            "organism_ids",
            filters=SearchFilters(organism_ids=("NCBITaxon:9606",)),
            candidate_gses=None,
            limit=50,
        )
    counts = {bucket.value: bucket.count for bucket in buckets}
    assert counts["NCBITaxon:9606"] > 0
    assert counts["NCBITaxon:10090"] > 0


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("GEO_TEST_PG") != "1", reason="set GEO_TEST_PG=1")
def test_live_filtered_dense_search_fills_topk_with_iterative_scan() -> None:
    rare_filter = SearchFilters(sex_ids=("PATO:0001340",))
    topk = 5
    with pg_hybrid._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                "SELECT count(*), (array_agg(embedding))[1] "
                "FROM series WHERE sex_ids && %(sex_ids)s::text[] "
                "AND embedding IS NOT NULL",
                {"sex_ids": list(rare_filter.sex_ids)},
            )
            count, qv = cur.fetchone()
        if count < topk:
            pytest.skip("fewer than five hermaphrodite rows in the local corpus")
        rows = search_rows(
            conn,
            "",
            mode="dense",
            qv=qv,
            topk=topk,
            filters=rare_filter,
        )
        values = _normalized_values(conn, [str(row["gse"]) for row in rows])
    assert len(rows) == topk
    assert all("PATO:0001340" in values[str(row["gse"])][1] for row in rows)
