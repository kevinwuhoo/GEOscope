import json
from pathlib import Path

import pytest

from geo_index.assay_rules import has_single_cell_technology
import geo_index.normalize as normalize
from geo_index.normalize import map_assay, normalize_assay_fields
from geo_index.search_test import _load_light_docs


@pytest.mark.parametrize(
    "text",
    [
        "Images were acquired at 10X magnification.",
        "Effects of hexavalent chromium exposure in fish liver.",
        "Cells were treated with chromium chloride.",
        "Mice received chromium 3 mg/kg daily.",
        "Chromium 35 isotope uptake was measured.",
        "Chromium v2 exposure cohort.",
    ],
)
def test_non_assay_10x_and_chromium_are_not_10x_genomics(text: str) -> None:
    _, labels, _ = map_assay("", text)
    assert "10x Chromium" not in labels


@pytest.mark.parametrize(
    "text",
    [
        "10x Genomics Chromium Single Cell 3' Gene Expression",
        "Libraries were prepared on the Chromium Controller.",
        "10x Chromium 5' v2 chemistry",
        "Chromium 3' gene expression libraries",
        "Chromium v3 chemistry libraries",
    ],
)
def test_contextual_10x_genomics_phrases_are_detected(text: str) -> None:
    _, labels, status = map_assay("", text)
    assert "10x Chromium" in labels
    assert status == "detailed"


def test_single_cell_hint_uses_contextual_rules() -> None:
    assert has_single_cell_technology("10x Genomics Chromium libraries") is True
    assert has_single_cell_technology("10X magnification of chromium-treated fish") is False


def test_search_harness_uses_contextual_single_cell_hint(tmp_path: Path) -> None:
    docs = [
        {
            "gse": "GSE-REAL-10X",
            "title": "10x Genomics Chromium libraries",
            "type": "Expression profiling by high throughput sequencing",
            "overall_design": "",
            "summary": "",
            "n_samples": 1,
            "organisms": ["Homo sapiens"],
        },
        {
            "gse": "GSE-MICROSCOPY",
            "title": "10X magnification of chromium-treated fish",
            "type": "Expression profiling by high throughput sequencing",
            "overall_design": "",
            "summary": "",
            "n_samples": 1,
            "organisms": ["Danio rerio"],
        },
    ]
    docs_path = tmp_path / "docs.jsonl"
    docs_path.write_text("\n".join(json.dumps(doc) for doc in docs))

    loaded = _load_light_docs(docs_path)

    assert loaded["GSE-REAL-10X"]["sc_hint"] is True
    assert loaded["GSE-MICROSCOPY"]["sc_hint"] is False


def test_normalize_assay_fields_returns_only_persisted_assay_columns() -> None:
    result = normalize_assay_fields(
        {
            "title": "Chromium exposure at 10X magnification",
            "summary": "",
            "overall_design": "",
            "type": "Expression profiling by high throughput sequencing",
        }
    )
    assert result == {
        "assay_categories": ["expression (seq)"],
        "assay_labels": [],
        "assay_status": "category",
    }


class _CursorContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _ScanCursor(_CursorContext):
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.itersize: int | None = None
        self.sql: str | None = None

    def execute(self, sql: str) -> None:
        self.sql = sql

    def __iter__(self):
        return iter(self.rows)


class _WriteCursor(_CursorContext):
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple[object, ...]]]] = []

    def executemany(self, sql: str, params: list[tuple[object, ...]]) -> None:
        self.calls.append((sql, list(params)))


class _ReadConnection:
    def __init__(self, scan: _ScanCursor) -> None:
        self.scan = scan
        self.closed = False

    def cursor(self, *, name: str) -> _ScanCursor:
        assert name == "assay_refresh_scan"
        return self.scan

    def close(self) -> None:
        self.closed = True


class _WriteConnection:
    def __init__(self, cursor: _WriteCursor) -> None:
        self.write_cursor = cursor
        self.commits = 0
        self.closed = False

    def cursor(self) -> _WriteCursor:
        return self.write_cursor

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def test_refresh_assays_updates_only_persisted_assay_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        (
            "GSE-MICROSCOPY",
            "Chromium exposure at 10X magnification",
            "",
            "",
            "Expression profiling by high throughput sequencing",
        ),
        (
            "GSE-REAL-10X",
            "10x Genomics Chromium libraries",
            "",
            "",
            "Expression profiling by high throughput sequencing",
        ),
    ]
    scan = _ScanCursor(rows)
    read = _ReadConnection(scan)
    write_cursor = _WriteCursor()
    write = _WriteConnection(write_cursor)
    connections = iter((read, write))
    monkeypatch.setattr(normalize, "migrate", lambda: 0)
    monkeypatch.setattr(normalize, "_connect", lambda: next(connections))

    refreshed = normalize.refresh_assays(limit=2, batch=1)

    assert refreshed == 2
    assert scan.itersize == 1
    assert scan.sql == (
        "SELECT id, title, summary, overall_design, type "
        "FROM series ORDER BY id LIMIT 2"
    )
    update_sql = (
        "UPDATE series SET assay_categories=%s, assay_labels=%s, "
        "assay_status=%s WHERE id=%s"
    )
    assert write_cursor.calls == [
        (
            update_sql,
            [(["expression (seq)"], None, "category", "GSE-MICROSCOPY")],
        ),
        (
            update_sql,
            [
                (
                    ["expression (seq)"],
                    ["10x Chromium"],
                    "detailed",
                    "GSE-REAL-10X",
                )
            ],
        ),
    ]
    assert write.commits == 2
    assert read.closed is True
    assert write.closed is True


def test_assay_refresh_cli_dispatches_limit_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits: list[int | None] = []

    def fake_refresh_assays(limit: int | None = None, batch: int = 5000) -> int:
        limits.append(limit)
        return 7

    monkeypatch.setattr(normalize, "refresh_assays", fake_refresh_assays, raising=False)

    assert normalize.main(["assay-refresh", "--limit", "7"]) == 0
    assert limits == [7]
