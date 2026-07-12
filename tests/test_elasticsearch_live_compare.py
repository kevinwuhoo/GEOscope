from __future__ import annotations

import json
from pathlib import Path

import pytest

from geo_index.elasticsearch_live_compare import load_query_cases
from geo_index.search_models import SearchFilters


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

