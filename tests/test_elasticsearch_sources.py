from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from geo_index.elasticsearch_config import VECTOR_FIELDS
from geo_index.elasticsearch_sources import (
    iter_index_documents,
    load_artifact,
    load_records,
)


def _write_record(root: Path, gse: str, **overrides: object) -> Path:
    path = root / f"{gse[:-3]}nnn" / f"{gse}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "gse": gse,
        "source_soft": f"{gse[:-3]}nnn/{gse}_family.soft.gz",
        "title": f"Title {gse}",
        "summary": "immune cell expression",
        "overall_design": "two groups",
        "embed_text": f"Title: Title {gse}",
        "type": ["Expression profiling by high throughput sequencing"],
        "pubmed_ids": ["12345678"],
        "submission_date": "2024-01-01",
        "last_update_date": "2024-01-02",
        "platform_ids": ["GPL1"],
        "n_samples": 2,
        "organisms": ["Homo sapiens"],
        "molecules": ["total RNA"],
        "source_names": ["blood"],
        "library_strategies": ["RNA-Seq"],
        "library_sources": ["TRANSCRIPTOMIC"],
        "library_selections": ["cDNA"],
        "organism_ids": ["NCBITaxon:9606"],
        "organism_status": "mapped",
        "sex_ids": ["PATO:0000383"],
        "sex_status": "mapped",
        "assay_categories": ["transcriptomic"],
        "assay_labels": ["RNA-seq"],
        "assay_status": "mapped",
        "samples": [{"gsm": "GSM1"}],
        "series_attributes": {"Series_relation": ["BioProject: PRJ1"]},
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_artifact(
    root: Path,
    *,
    model_key: str = "bge_small_v15",
    ids: list[str] | None = None,
    dimensions: int | None = None,
    fill: float = 0.0,
    metadata_overrides: dict[str, object] | None = None,
) -> Path:
    spec = VECTOR_FIELDS[model_key]
    artifact = root / model_key
    artifact.mkdir(parents=True, exist_ok=True)
    artifact_ids = ids or ["GSE2", "GSE10"]
    width = dimensions if dimensions is not None else spec.dimensions
    vectors = np.full((len(artifact_ids), width), fill, dtype=np.float32)
    if width:
        vectors[:, -1] = 1.0
    np.save(artifact / "vectors.npy", vectors, allow_pickle=False)
    (artifact / "ids.json").write_text(json.dumps(artifact_ids), encoding="utf-8")
    metadata: dict[str, object] = {
        "schema_version": 1,
        "model_key": model_key,
        "dimensions": width,
        "record_count": len(artifact_ids),
    }
    metadata.update(metadata_overrides or {})
    (artifact / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return artifact


def test_load_records_uses_stable_numeric_gse_order_and_whitelist(tmp_path: Path) -> None:
    root = tmp_path / "records"
    _write_record(root, "GSE10")
    _write_record(root, "GSE2")
    records = load_records(root)
    assert [record.gse for record in records] == ["GSE2", "GSE10"]
    assert "samples" not in records[0].source
    assert "series_attributes" not in records[0].source
    assert records[0].source["organism_ids"] == ["NCBITaxon:9606"]


def test_load_records_rejects_malformed_json_missing_and_mismatched_gse(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"
    bad = root / "bucket" / "GSE2.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        load_records(root)
    bad.write_text(json.dumps({"title": "missing"}), encoding="utf-8")
    with pytest.raises(ValueError, match="payload GSE"):
        load_records(root)
    bad.write_text(json.dumps({"gse": "GSE3"}), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        load_records(root)


def test_load_records_rejects_duplicate_accessions_and_invalid_field_types(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"
    first = _write_record(root, "GSE2")
    duplicate = root / "other" / first.name
    duplicate.parent.mkdir()
    duplicate.write_bytes(first.read_bytes())
    with pytest.raises(ValueError, match="duplicate canonical record"):
        load_records(root)
    duplicate.unlink()
    _write_record(root, "GSE2", organism_ids="NCBITaxon:9606")
    with pytest.raises(ValueError, match="organism_ids must be an array"):
        load_records(root)


def test_load_artifact_memory_maps_aligned_float32_rows(tmp_path: Path) -> None:
    path = _write_artifact(tmp_path)
    artifact = load_artifact(path, VECTOR_FIELDS["bge_small_v15"])
    assert isinstance(artifact.vectors, np.memmap)
    assert artifact.row_by_gse == {"GSE2": 0, "GSE10": 1}
    assert artifact.metadata["record_count"] == 2


def test_load_artifact_scans_finite_values_in_bounded_row_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_artifact(
        tmp_path,
        ids=["GSE2", "GSE3", "GSE4", "GSE5", "GSE6"],
    )
    calls: list[tuple[int, ...]] = []
    original = np.isfinite

    def recording_isfinite(values: np.ndarray) -> np.ndarray:
        calls.append(values.shape)
        return original(values)

    monkeypatch.setattr("geo_index.elasticsearch_sources._FINITE_SCAN_ROWS", 2)
    monkeypatch.setattr("geo_index.elasticsearch_sources.np.isfinite", recording_isfinite)
    load_artifact(path, VECTOR_FIELDS["bge_small_v15"])
    assert calls == [(2, 384), (2, 384), (1, 384)]


def test_load_artifact_rejects_wrong_dimensions_and_nonfinite_vectors(
    tmp_path: Path,
) -> None:
    path = _write_artifact(tmp_path / "wrong", dimensions=383)
    with pytest.raises(ValueError, match="384 dimensions"):
        load_artifact(path, VECTOR_FIELDS["bge_small_v15"])
    path = _write_artifact(tmp_path / "nonfinite")
    vectors = np.load(path / "vectors.npy")
    vectors[0, 0] = np.nan
    np.save(path / "vectors.npy", vectors, allow_pickle=False)
    with pytest.raises(ValueError, match="nonfinite"):
        load_artifact(path, VECTOR_FIELDS["bge_small_v15"])


@pytest.mark.parametrize(
    ("ids", "metadata_overrides", "message"),
    [
        (["GSE2", "GSE2"], {}, "duplicate GSE"),
        (["GSE10", "GSE2"], {}, "numeric GSE order"),
        (["GSE2", "bad"], {}, "malformed GSE"),
        (["GSE2", "GSE10"], {"record_count": 3}, "record_count"),
        (["GSE2", "GSE10"], {"model_key": "other"}, "model_key"),
        (["GSE2", "GSE10"], {"dimensions": 768}, "dimensions"),
    ],
)
def test_load_artifact_rejects_invalid_ids_and_metadata(
    tmp_path: Path,
    ids: list[str],
    metadata_overrides: dict[str, object],
    message: str,
) -> None:
    path = _write_artifact(
        tmp_path, ids=ids, metadata_overrides=metadata_overrides
    )
    with pytest.raises(ValueError, match=message):
        load_artifact(path, VECTOR_FIELDS["bge_small_v15"])


def test_documents_join_vector_rows_by_gse(tmp_path: Path) -> None:
    records_root = tmp_path / "records"
    artifacts_root = tmp_path / "artifacts"
    _write_record(records_root, "GSE10")
    _write_record(records_root, "GSE2")
    _write_artifact(artifacts_root)
    documents = list(
        iter_index_documents(
            records_root,
            artifacts_root,
            model_keys=("bge_small_v15",),
        )
    )
    assert [document.gse for document in documents] == ["GSE2", "GSE10"]
    assert documents[0].source["embedding_bge_384"] == pytest.approx(
        [0.0] * 383 + [1.0]
    )


def test_documents_stream_records_without_materializing_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records_root = tmp_path / "records"
    _write_record(records_root, "GSE2")
    _write_record(records_root, "GSE10")
    monkeypatch.setattr(
        "geo_index.elasticsearch_sources.load_records",
        lambda _root: (_ for _ in ()).throw(AssertionError("materialized records")),
    )
    documents = list(
        iter_index_documents(
            records_root,
            tmp_path / "artifacts",
            model_keys=(),
        )
    )
    assert [document.gse for document in documents] == ["GSE2", "GSE10"]


def test_documents_allow_missing_artifact_and_partial_model_coverage(
    tmp_path: Path,
) -> None:
    records_root = tmp_path / "records"
    artifacts_root = tmp_path / "artifacts"
    _write_record(records_root, "GSE2")
    _write_record(records_root, "GSE10")
    _write_artifact(artifacts_root, ids=["GSE10"])
    documents = list(
        iter_index_documents(
            records_root,
            artifacts_root,
            model_keys=("bge_small_v15", "medcpt_v1"),
        )
    )
    assert "embedding_bge_384" not in documents[0].source
    assert "embedding_bge_384" in documents[1].source
    assert "embedding_medcpt_768" not in documents[1].source


def test_documents_reject_unknown_model_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown model key"):
        list(
            iter_index_documents(
                tmp_path / "records",
                tmp_path / "artifacts",
                model_keys=("unknown",),
            )
        )
