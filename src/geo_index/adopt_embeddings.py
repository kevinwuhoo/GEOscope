"""Adopt the aligned legacy BGE matrix without modifying its source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .embedding_artifacts import artifact_dir, publish_artifact, validate_artifact
from .embedding_registry import get_variant


DEFAULT_MATRIX = Path("data/processed/embeddings.npy")
DEFAULT_IDS = Path("data/processed/embeddings.ids.json")
DEFAULT_OUTPUT_ROOT = Path("data/processed/embedding_artifacts")


@dataclass(frozen=True)
class AdoptionReport:
    model_key: str
    status: str
    artifact_path: Path
    record_count: int
    dimensions: int
    bytes_copied: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_legacy_ids(path: Path) -> tuple[list[str], dict[str, object]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid legacy IDs file {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("meta"), dict):
        raise ValueError("legacy IDs must contain meta and ids objects")
    ids = raw.get("ids")
    if not isinstance(ids, list) or any(not isinstance(gse, str) for gse in ids):
        raise ValueError("legacy ids must be a string list")
    return ids, dict(raw["meta"])


def _validate_legacy(
    matrix_path: Path,
    ids_path: Path,
) -> tuple[np.ndarray, list[str], dict[str, object]]:
    ids, legacy_meta = _load_legacy_ids(ids_path)
    if legacy_meta.get("model") != "BAAI/bge-small-en-v1.5":
        raise ValueError("legacy metadata model is not BAAI/bge-small-en-v1.5")
    try:
        vectors = np.load(matrix_path, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid legacy matrix {matrix_path}: {exc}") from exc
    if vectors.ndim != 2:
        raise ValueError("legacy matrix must be two-dimensional")
    if vectors.dtype != np.float32:
        raise ValueError(f"legacy matrix must use float32, got {vectors.dtype}")
    if vectors.shape[1] != 384:
        raise ValueError(f"expected 384 dimensions, got {vectors.shape[1]}")
    if not vectors.flags.c_contiguous:
        raise ValueError("legacy matrix must be C-contiguous")
    if not np.isfinite(vectors).all():
        raise ValueError("legacy matrix contains nonfinite values")
    if len(ids) != vectors.shape[0]:
        raise ValueError(f"ID count {len(ids)} does not match matrix rows {vectors.shape[0]}")
    if len(ids) != len(set(ids)):
        raise ValueError("legacy ids contain a duplicate GSE")

    def gse_number(gse: str) -> int:
        if not gse.startswith("GSE") or not gse[3:].isdigit() or int(gse[3:]) < 1:
            raise ValueError(f"malformed legacy GSE {gse!r}")
        return int(gse[3:])

    for gse in ids:
        gse_number(gse)
    if legacy_meta.get("dim") != 384:
        raise ValueError("legacy metadata dimension does not match BGE")
    if legacy_meta.get("count") != len(ids):
        raise ValueError("legacy metadata count does not match IDs")
    return vectors, ids, legacy_meta


def adopt_legacy_matrix(
    matrix_path: Path,
    ids_path: Path,
    output_root: Path,
    model_key: str,
) -> AdoptionReport:
    """Copy a proven aligned legacy BGE matrix into the canonical contract."""
    if model_key != "bge_small_v15":
        raise ValueError("legacy adoption supports only bge_small_v15")
    variant = get_variant(model_key)
    final_dir = artifact_dir(output_root, model_key)
    if final_dir.exists():
        metadata = validate_artifact(final_dir, variant)
        return AdoptionReport(
            model_key,
            "skipped",
            final_dir,
            metadata.record_count,
            metadata.dimensions,
            0,
        )

    started = time.perf_counter()
    vectors, ids, legacy_meta = _validate_legacy(matrix_path, ids_path)
    row_order = sorted(range(len(ids)), key=lambda index: int(ids[index][3:]))
    rows_reordered = row_order != list(range(len(ids)))
    canonical_ids = [ids[index] for index in row_order]
    matrix_sha256 = _sha256(matrix_path)
    ids_sha256 = _sha256(ids_path)
    temp_dir = output_root / f".{model_key}.adopt.tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    canonical_matrix = temp_dir / "vectors.npy"
    if rows_reordered:
        destination = np.lib.format.open_memmap(
            canonical_matrix,
            mode="w+",
            dtype=np.float32,
            shape=vectors.shape,
        )
        for start in range(0, len(row_order), 10_000):
            indices = row_order[start : start + 10_000]
            destination[start : start + len(indices)] = vectors[indices]
        destination.flush()
        del destination
    else:
        shutil.copyfile(matrix_path, canonical_matrix)
    (temp_dir / "ids.json").write_text(
        json.dumps(canonical_ids, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    metadata = {
        "schema_version": 1,
        "model_key": model_key,
        "provider": variant.provider,
        "model_id": variant.document_model_id,
        "model_revision": None,
        "dimensions": variant.dimensions,
        "document_format": variant.document_format,
        "query_format": variant.query_format,
        "normalization": variant.normalization,
        "pooling": variant.pooling,
        "record_count": len(ids),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_runtime_seconds": time.perf_counter() - started,
        "sdk_version": f"numpy/{version('numpy')}",
        "max_length": variant.max_length,
        "truncation_count": 0,
        "usage": {
            "adopted": True,
            "adoption_source_matrix": str(matrix_path),
            "adoption_source_ids": str(ids_path),
            "adoption_source_matrix_sha256": matrix_sha256,
            "adoption_source_ids_sha256": ids_sha256,
            "source_rows_reordered": rows_reordered,
            "legacy_metadata": legacy_meta,
            "document_provenance": "legacy geo_series.jsonl embed_text",
        },
    }
    (temp_dir / "metadata.json").write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    validate_artifact(temp_dir, variant)
    bytes_copied = canonical_matrix.stat().st_size
    publish_artifact(temp_dir, final_dir)
    return AdoptionReport(
        model_key,
        "adopted",
        final_dir,
        len(canonical_ids),
        variant.dimensions,
        bytes_copied,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adopt a legacy BGE matrix")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--ids", type=Path, default=DEFAULT_IDS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model-key", default="bge_small_v15")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = adopt_legacy_matrix(
        args.matrix,
        args.ids,
        args.output_root,
        args.model_key,
    )
    print(json.dumps({**asdict(report), "artifact_path": str(report.artifact_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
