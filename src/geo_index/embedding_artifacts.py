"""Canonical JSON-record inventory and NumPy matrix artifact validation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .embedding_registry import EmbeddingVariant, get_variant


GSE_RE = re.compile(r"^GSE([1-9][0-9]*)$")
ARTIFACT_FILES = ("vectors.npy", "ids.json", "metadata.json")
REQUIRED_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "model_key",
        "provider",
        "model_id",
        "model_revision",
        "dimensions",
        "document_format",
        "query_format",
        "normalization",
        "pooling",
        "record_count",
        "created_at",
        "build_runtime_seconds",
        "sdk_version",
        "max_length",
        "truncation_count",
        "usage",
    }
)


def _gse_number(gse: str) -> int:
    match = GSE_RE.fullmatch(gse)
    if not match:
        raise ValueError(f"malformed GSE accession {gse!r}")
    return int(match.group(1))


@dataclass(frozen=True)
class RecordRef:
    gse: str
    title: str
    embed_text: str
    path: Path


@dataclass(frozen=True)
class RecordInventory:
    records: tuple[RecordRef, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(record.gse for record in self.records)

    def __len__(self) -> int:
        return len(self.records)


@dataclass(frozen=True)
class ArtifactMetadata:
    schema_version: int
    model_key: str
    provider: str
    model_id: str
    model_revision: str | None
    dimensions: int
    document_format: str
    query_format: str
    normalization: str
    pooling: str
    record_count: int
    created_at: str
    build_runtime_seconds: float
    sdk_version: str
    max_length: int
    truncation_count: int
    usage: dict[str, object]


def artifact_dir(output_root: Path, model_key: str) -> Path:
    """Return the canonical final directory for a fixed model key."""
    get_variant(model_key)
    return output_root / model_key


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc


def load_record_inventory(records_root: Path) -> RecordInventory:
    """Load and validate canonical records in stable numeric GSE order."""
    records: list[RecordRef] = []
    seen: set[str] = set()
    for path in records_root.rglob("*.json"):
        payload = _load_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"canonical record {path} must contain an object")
        path_gse = path.stem
        payload_gse = payload.get("gse")
        if path_gse != payload_gse:
            raise ValueError(f"record path {path_gse} does not match payload {payload_gse}")
        _gse_number(path_gse)
        if path_gse in seen:
            raise ValueError(f"duplicate canonical record {path_gse}")
        title = payload.get("title")
        embed_text = payload.get("embed_text")
        if not isinstance(title, str):
            raise ValueError(f"{path_gse} title must be a string")
        if not isinstance(embed_text, str):
            raise ValueError(f"{path_gse} embed_text must be a string")
        seen.add(path_gse)
        records.append(RecordRef(path_gse, title, embed_text, path))
    records.sort(key=lambda record: _gse_number(record.gse))
    return RecordInventory(tuple(records))


def _load_metadata(path: Path, variant: EmbeddingVariant) -> ArtifactMetadata:
    raw = _load_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a metadata object")
    missing = sorted(REQUIRED_METADATA_FIELDS - raw.keys())
    if missing:
        raise ValueError(f"missing metadata fields: {', '.join(missing)}")
    if raw["schema_version"] != 1:
        raise ValueError("unsupported artifact metadata schema_version")
    if raw["model_key"] != variant.model_key:
        raise ValueError(
            f"metadata model_key {raw['model_key']!r} != {variant.model_key!r}"
        )
    if raw["provider"] != variant.provider:
        raise ValueError(f"metadata provider {raw['provider']!r} != {variant.provider!r}")
    if raw["model_id"] != variant.document_model_id:
        raise ValueError(
            f"metadata model_id {raw['model_id']!r} != {variant.document_model_id!r}"
        )
    if raw["dimensions"] != variant.dimensions:
        raise ValueError("metadata dimensions do not match registry")
    if raw["document_format"] != variant.document_format:
        raise ValueError("metadata document_format does not match registry")
    if raw["query_format"] != variant.query_format:
        raise ValueError("metadata query_format does not match registry")
    if raw["normalization"] != variant.normalization:
        raise ValueError("metadata normalization does not match registry")
    if raw["pooling"] != variant.pooling:
        raise ValueError("metadata pooling does not match registry")
    if raw["max_length"] != variant.max_length:
        raise ValueError("metadata max_length does not match registry")
    if not isinstance(raw["record_count"], int) or raw["record_count"] < 0:
        raise ValueError("metadata record_count must be a nonnegative integer")
    if (
        not isinstance(raw["build_runtime_seconds"], (int, float))
        or raw["build_runtime_seconds"] < 0
    ):
        raise ValueError("metadata build_runtime_seconds must be nonnegative")
    if not isinstance(raw["truncation_count"], int) or raw["truncation_count"] < 0:
        raise ValueError("metadata truncation_count must be a nonnegative integer")
    if not isinstance(raw["usage"], dict):
        raise ValueError("metadata usage must be an object")
    if not isinstance(raw["created_at"], str) or not raw["created_at"]:
        raise ValueError("metadata created_at must be a nonempty string")
    if not isinstance(raw["sdk_version"], str) or not raw["sdk_version"]:
        raise ValueError("metadata sdk_version must be a nonempty string")
    if raw["model_revision"] is not None and not isinstance(raw["model_revision"], str):
        raise ValueError("metadata model_revision must be a string or null")
    return ArtifactMetadata(
        schema_version=1,
        model_key=raw["model_key"],
        provider=raw["provider"],
        model_id=raw["model_id"],
        model_revision=raw["model_revision"],
        dimensions=raw["dimensions"],
        document_format=raw["document_format"],
        query_format=raw["query_format"],
        normalization=raw["normalization"],
        pooling=raw["pooling"],
        record_count=raw["record_count"],
        created_at=raw["created_at"],
        build_runtime_seconds=float(raw["build_runtime_seconds"]),
        sdk_version=raw["sdk_version"],
        max_length=raw["max_length"],
        truncation_count=raw["truncation_count"],
        usage=dict(raw["usage"]),
    )


def validate_artifact(path: Path, variant: EmbeddingVariant) -> ArtifactMetadata:
    """Validate the complete canonical artifact for ``variant``."""
    if not path.is_dir():
        raise ValueError(f"artifact directory does not exist: {path}")
    for filename in ARTIFACT_FILES:
        if not (path / filename).is_file():
            raise ValueError(f"missing artifact file: {filename}")

    try:
        vectors = np.load(path / "vectors.npy", mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid vectors.npy: {exc}") from exc
    if vectors.ndim != 2:
        raise ValueError(f"vectors.npy must be two-dimensional, got {vectors.shape}")
    if vectors.dtype != np.float32:
        raise ValueError(f"vectors.npy must use float32, got {vectors.dtype}")
    if vectors.shape[1] != variant.dimensions:
        raise ValueError(
            f"expected {variant.dimensions} dimensions, got {vectors.shape[1]}"
        )
    if not vectors.flags.c_contiguous:
        raise ValueError("vectors.npy must be C-contiguous")
    if not np.isfinite(vectors).all():
        raise ValueError("vectors.npy contains nonfinite values")

    ids = _load_json(path / "ids.json")
    if not isinstance(ids, list) or any(not isinstance(gse, str) for gse in ids):
        raise ValueError("ids.json must contain a string list")
    if len(ids) != len(set(ids)):
        raise ValueError("ids.json contains a duplicate GSE")
    expected_ids = sorted(ids, key=_gse_number)
    if ids != expected_ids:
        raise ValueError("ids.json is not in numeric GSE order")
    if len(ids) != vectors.shape[0]:
        raise ValueError(
            f"ID count {len(ids)} does not match matrix rows {vectors.shape[0]}"
        )

    metadata = _load_metadata(path / "metadata.json", variant)
    if metadata.record_count != vectors.shape[0]:
        raise ValueError(
            f"metadata record_count {metadata.record_count} does not match "
            f"matrix rows {vectors.shape[0]}"
        )
    return metadata


def publish_artifact(temp_dir: Path, final_dir: Path) -> None:
    """Validate and atomically rename a sibling temp artifact into place."""
    if final_dir.exists():
        raise FileExistsError(f"artifact destination already exists: {final_dir}")
    variant = get_variant(final_dir.name)
    validate_artifact(temp_dir, variant)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    os.rename(temp_dir, final_dir)
