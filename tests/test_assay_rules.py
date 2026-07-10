import pytest

from geo_index.normalize import map_assay


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
