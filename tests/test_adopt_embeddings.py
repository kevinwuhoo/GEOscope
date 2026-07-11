from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import geo_index.adopt_embeddings as adoption
from geo_index.adopt_embeddings import AdoptionReport, adopt_legacy_matrix
from geo_index.embedding_artifacts import validate_artifact
from geo_index.embedding_registry import get_variant


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy(
    tmp_path: Path,
    *,
    ids: list[str] | None = None,
    vectors: np.ndarray | None = None,
) -> tuple[Path, Path]:
    ids = ids or ["GSE2", "GSE10"]
    vectors = (
        vectors
        if vectors is not None
        else np.ones((len(ids), 384), dtype=np.float32)
    )
    matrix_path = tmp_path / "embeddings.npy"
    ids_path = tmp_path / "embeddings.ids.json"
    np.save(matrix_path, vectors)
    ids_path.write_text(
        json.dumps(
            {
                "meta": {
                    "model": "BAAI/bge-small-en-v1.5",
                    "dim": 384,
                    "count": len(ids),
                },
                "ids": ids,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return matrix_path, ids_path


def test_adoption_copies_aligned_bge_without_modifying_sources(tmp_path: Path) -> None:
    matrix_path, ids_path = _legacy(tmp_path)
    matrix_before = _sha256(matrix_path)
    ids_before = _sha256(ids_path)
    output = tmp_path / "canonical"

    report = adopt_legacy_matrix(
        matrix_path,
        ids_path,
        output,
        "bge_small_v15",
    )

    assert report.status == "adopted"
    assert report.record_count == 2
    assert report.dimensions == 384
    assert _sha256(matrix_path) == matrix_before
    assert _sha256(ids_path) == ids_before
    assert (report.artifact_path / "vectors.npy").read_bytes() == matrix_path.read_bytes()
    assert json.loads((report.artifact_path / "ids.json").read_text()) == [
        "GSE2",
        "GSE10",
    ]
    metadata = validate_artifact(
        report.artifact_path,
        get_variant("bge_small_v15"),
    )
    assert metadata.model_revision is None
    assert metadata.usage["adoption_source_matrix_sha256"] == matrix_before


def test_valid_existing_artifact_skips_without_reading_or_copying_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix_path, ids_path = _legacy(tmp_path)
    output = tmp_path / "canonical"
    adopt_legacy_matrix(matrix_path, ids_path, output, "bge_small_v15")
    matrix_path.unlink()
    ids_path.unlink()
    monkeypatch.setattr(
        adoption.shutil,
        "copyfile",
        lambda *args: (_ for _ in ()).throw(AssertionError("copied")),
    )

    report = adopt_legacy_matrix(
        matrix_path,
        ids_path,
        output,
        "bge_small_v15",
    )

    assert report.status == "skipped"
    assert report.record_count == 2


@pytest.mark.parametrize(
    ("ids", "vectors", "message"),
    [
        (["GSE2"], np.zeros((2, 384), dtype=np.float32), "ID count 1.*rows 2"),
        (["GSE2"], np.zeros((1, 383), dtype=np.float32), "expected 384 dimensions"),
        (["GSE2"], np.zeros((1, 384), dtype=np.float64), "float32"),
        (["GSE2"], np.full((1, 384), np.nan, dtype=np.float32), "nonfinite"),
        (["GSE2", "GSE2"], np.zeros((2, 384), dtype=np.float32), "duplicate GSE"),
    ],
)
def test_invalid_legacy_inputs_publish_no_final_artifact(
    tmp_path: Path,
    ids: list[str],
    vectors: np.ndarray,
    message: str,
) -> None:
    matrix_path, ids_path = _legacy(tmp_path, ids=ids, vectors=vectors)
    output = tmp_path / "canonical"

    with pytest.raises(ValueError, match=message):
        adopt_legacy_matrix(matrix_path, ids_path, output, "bge_small_v15")

    assert not (output / "bge_small_v15").exists()


def test_adoption_reorders_internally_aligned_legacy_rows_without_recomputation(
    tmp_path: Path,
) -> None:
    vectors = np.vstack(
        [
            np.full(384, 10, dtype=np.float32),
            np.full(384, 2, dtype=np.float32),
        ]
    )
    matrix_path, ids_path = _legacy(
        tmp_path,
        ids=["GSE10", "GSE2"],
        vectors=vectors,
    )
    matrix_before = _sha256(matrix_path)
    ids_before = _sha256(ids_path)

    report = adopt_legacy_matrix(
        matrix_path,
        ids_path,
        tmp_path / "canonical",
        "bge_small_v15",
    )

    assert json.loads((report.artifact_path / "ids.json").read_text()) == [
        "GSE2",
        "GSE10",
    ]
    adopted = np.load(report.artifact_path / "vectors.npy")
    assert np.all(adopted[0] == 2)
    assert np.all(adopted[1] == 10)
    metadata = json.loads((report.artifact_path / "metadata.json").read_text())
    assert metadata["usage"]["source_rows_reordered"] is True
    assert _sha256(matrix_path) == matrix_before
    assert _sha256(ids_path) == ids_before


def test_adoption_rejects_non_bge_and_never_relabels_pubmedbert(
    tmp_path: Path,
) -> None:
    matrix_path, ids_path = _legacy(
        tmp_path,
        ids=["GSE2"],
        vectors=np.zeros((1, 768), dtype=np.float32),
    )
    with pytest.raises(ValueError, match="only bge_small_v15"):
        adopt_legacy_matrix(matrix_path, ids_path, tmp_path / "out", "medcpt_v1")


def test_adoption_report_is_frozen() -> None:
    report = AdoptionReport(
        model_key="bge_small_v15",
        status="skipped",
        artifact_path=Path("artifact"),
        record_count=2,
        dimensions=384,
        bytes_copied=0,
    )
    with pytest.raises((AttributeError, TypeError)):
        report.status = "adopted"  # type: ignore[misc]
