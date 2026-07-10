import json
from pathlib import Path

import pytest

from geo_index.assay_rules import has_single_cell_technology
from geo_index.normalize import map_assay
from geo_index.search_test import _load_light_docs


@pytest.mark.parametrize(
    "text",
    [
        "Images were acquired at 10X magnification.",
        "Effects of hexavalent chromium exposure in fish liver.",
        "Cells were treated with chromium chloride.",
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
