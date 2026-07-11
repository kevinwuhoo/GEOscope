from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from geo_index.embedding_artifacts import (
    artifact_dir,
    load_record_inventory,
    publish_artifact,
    validate_artifact,
)
from geo_index.embedding_registry import MODEL_KEYS, get_variant


def _bucket(gse: str) -> str:
    digits = gse[3:]
    return f"GSE{digits[:-3]}nnn" if len(digits) > 3 else "GSEnnn"


def _write_record(
    root: Path,
    gse: str,
    *,
    payload_gse: str | None = None,
    title: object = "Title",
    embed_text: object = "Title: Title\nSummary: text",
    directory: str | None = None,
) -> Path:
    path = root / (directory or _bucket(gse)) / f"{gse}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gse": payload_gse or gse,
                "title": title,
                "embed_text": embed_text,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _metadata(model_key: str, count: int) -> dict[str, object]:
    variant = get_variant(model_key)
    return {
        "schema_version": 1,
        "model_key": model_key,
        "provider": variant.provider,
        "model_id": variant.document_model_id,
        "model_revision": "resolved-revision",
        "dimensions": variant.dimensions,
        "document_format": variant.document_format,
        "query_format": variant.query_format,
        "normalization": variant.normalization,
        "pooling": variant.pooling,
        "record_count": count,
        "created_at": "2026-07-11T00:00:00+00:00",
        "build_runtime_seconds": 1.25,
        "sdk_version": "fake-1.0",
        "max_length": variant.max_length,
        "truncation_count": 0,
        "usage": {},
    }


def _write_artifact(
    path: Path,
    model_key: str,
    ids: list[str],
    *,
    vectors: np.ndarray | None = None,
    metadata: dict[str, object] | None = None,
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    variant = get_variant(model_key)
    if vectors is None:
        vectors = np.zeros((len(ids), variant.dimensions), dtype=np.float32)
    np.save(path / "vectors.npy", vectors)
    (path / "ids.json").write_text(json.dumps(ids) + "\n", encoding="utf-8")
    (path / "metadata.json").write_text(
        json.dumps(metadata or _metadata(model_key, len(ids))) + "\n",
        encoding="utf-8",
    )
    return path


def test_registry_is_fixed_lightweight_and_dimensioned() -> None:
    assert MODEL_KEYS == (
        "bge_small_v15",
        "medcpt_v1",
        "qwen3_06b_1024_v1",
        "gemini_embedding_2_3072_v1",
    )
    assert get_variant("bge_small_v15").dimensions == 384
    assert get_variant("medcpt_v1").dimensions == 768
    assert get_variant("qwen3_06b_1024_v1").dimensions == 1024
    assert get_variant("gemini_embedding_2_3072_v1").dimensions == 3072
    assert get_variant("bge_small_v15").pooling == "cls"
    assert get_variant("medcpt_v1").pooling == "cls"
    assert get_variant("qwen3_06b_1024_v1").pooling == "last-token"


def test_registry_rejects_unknown_model_key() -> None:
    with pytest.raises(ValueError, match="unknown model key"):
        get_variant("../../unsafe")


def test_artifact_dir_uses_fixed_safe_model_key(tmp_path: Path) -> None:
    assert artifact_dir(tmp_path, "bge_small_v15") == tmp_path / "bge_small_v15"
    with pytest.raises(ValueError, match="unknown model key"):
        artifact_dir(tmp_path, "unknown")


def test_inventory_uses_stable_numeric_gse_order(tmp_path: Path) -> None:
    _write_record(tmp_path, "GSE10", title="Ten", embed_text="ten")
    _write_record(tmp_path, "GSE2", title="Two", embed_text="two")
    _write_record(tmp_path, "GSE1001", title="Thousand", embed_text="thousand")

    inventory = load_record_inventory(tmp_path)

    assert inventory.ids == ("GSE2", "GSE10", "GSE1001")
    assert [record.title for record in inventory.records] == ["Two", "Ten", "Thousand"]
    assert [record.embed_text for record in inventory.records] == [
        "two",
        "ten",
        "thousand",
    ]


def test_inventory_rejects_path_payload_mismatch(tmp_path: Path) -> None:
    _write_record(tmp_path, "GSE2", payload_gse="GSE3")
    with pytest.raises(ValueError, match="path GSE2.*payload GSE3"):
        load_record_inventory(tmp_path)


def test_inventory_rejects_duplicate_payload_gse(tmp_path: Path) -> None:
    _write_record(tmp_path, "GSE2")
    duplicate = tmp_path / "another" / "GSE2.json"
    duplicate.parent.mkdir()
    duplicate.write_text(
        json.dumps({"gse": "GSE2", "title": "x", "embed_text": "x"}) + "\n"
    )
    with pytest.raises(ValueError, match="duplicate canonical record GSE2"):
        load_record_inventory(tmp_path)


@pytest.mark.parametrize(
    ("title", "embed_text", "message"),
    [(None, "text", "title must be a string"), ("title", None, "embed_text must be a string")],
)
def test_inventory_rejects_malformed_document_fields(
    tmp_path: Path,
    title: object,
    embed_text: object,
    message: str,
) -> None:
    _write_record(tmp_path, "GSE2", title=title, embed_text=embed_text)
    with pytest.raises(ValueError, match=message):
        load_record_inventory(tmp_path)


def test_validate_artifact_accepts_complete_aligned_float32_matrix(tmp_path: Path) -> None:
    path = _write_artifact(
        tmp_path / "bge_small_v15",
        "bge_small_v15",
        ["GSE2", "GSE10"],
        vectors=np.ones((2, 384), dtype=np.float32),
    )

    metadata = validate_artifact(path, get_variant("bge_small_v15"))

    assert metadata.model_key == "bge_small_v15"
    assert metadata.record_count == 2
    assert metadata.dimensions == 384


def test_validate_artifact_rejects_wrong_dimension(tmp_path: Path) -> None:
    path = _write_artifact(
        tmp_path / "bge_small_v15",
        "bge_small_v15",
        ["GSE2"],
        vectors=np.zeros((1, 383), dtype=np.float32),
    )
    with pytest.raises(ValueError, match="expected 384 dimensions"):
        validate_artifact(path, get_variant("bge_small_v15"))


def test_validate_artifact_rejects_wrong_dtype_and_nonfinite_values(tmp_path: Path) -> None:
    path = _write_artifact(
        tmp_path / "dtype" / "bge_small_v15",
        "bge_small_v15",
        ["GSE2"],
        vectors=np.zeros((1, 384), dtype=np.float64),
    )
    with pytest.raises(ValueError, match="float32"):
        validate_artifact(path, get_variant("bge_small_v15"))

    path = _write_artifact(
        tmp_path / "finite" / "bge_small_v15",
        "bge_small_v15",
        ["GSE2"],
        vectors=np.full((1, 384), np.nan, dtype=np.float32),
    )
    with pytest.raises(ValueError, match="nonfinite"):
        validate_artifact(path, get_variant("bge_small_v15"))


@pytest.mark.parametrize(
    ("ids", "rows", "message"),
    [
        (["GSE2", "GSE2"], 2, "duplicate GSE"),
        (["GSE10", "GSE2"], 2, "numeric GSE order"),
        (["GSE2"], 2, "ID count 1 does not match matrix rows 2"),
    ],
)
def test_validate_artifact_rejects_invalid_id_alignment(
    tmp_path: Path,
    ids: list[str],
    rows: int,
    message: str,
) -> None:
    path = _write_artifact(
        tmp_path / message.replace(" ", "_") / "bge_small_v15",
        "bge_small_v15",
        ids,
        vectors=np.zeros((rows, 384), dtype=np.float32),
        metadata=_metadata("bge_small_v15", rows),
    )
    with pytest.raises(ValueError, match=message):
        validate_artifact(path, get_variant("bge_small_v15"))


def test_validate_artifact_rejects_incomplete_metadata(tmp_path: Path) -> None:
    metadata = _metadata("bge_small_v15", 1)
    del metadata["document_format"]
    path = _write_artifact(
        tmp_path / "bge_small_v15",
        "bge_small_v15",
        ["GSE2"],
        metadata=metadata,
    )
    with pytest.raises(ValueError, match="missing metadata fields.*document_format"):
        validate_artifact(path, get_variant("bge_small_v15"))


def test_validate_artifact_requires_pooling_provenance(tmp_path: Path) -> None:
    metadata = _metadata("bge_small_v15", 1)
    del metadata["pooling"]
    path = _write_artifact(
        tmp_path / "bge_small_v15",
        "bge_small_v15",
        ["GSE2"],
        metadata=metadata,
    )
    with pytest.raises(ValueError, match="missing metadata fields.*pooling"):
        validate_artifact(path, get_variant("bge_small_v15"))


def test_publish_artifact_validates_before_atomic_directory_rename(tmp_path: Path) -> None:
    temporary = _write_artifact(
        tmp_path / ".bge_small_v15.tmp",
        "bge_small_v15",
        ["GSE2"],
    )
    final = tmp_path / "bge_small_v15"

    publish_artifact(temporary, final)

    assert final.is_dir()
    assert not temporary.exists()
    validate_artifact(final, get_variant("bge_small_v15"))


def test_publish_artifact_leaves_incomplete_temp_unpublished(tmp_path: Path) -> None:
    temporary = tmp_path / ".bge_small_v15.tmp"
    temporary.mkdir()
    final = tmp_path / "bge_small_v15"

    with pytest.raises(ValueError, match="missing artifact file"):
        publish_artifact(temporary, final)

    assert temporary.exists()
    assert not final.exists()


def test_publish_artifact_refuses_existing_destination(tmp_path: Path) -> None:
    temporary = _write_artifact(
        tmp_path / ".bge_small_v15.tmp",
        "bge_small_v15",
        ["GSE2"],
    )
    final = tmp_path / "bge_small_v15"
    final.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        publish_artifact(temporary, final)
