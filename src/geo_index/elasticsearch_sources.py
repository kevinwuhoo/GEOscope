"""Read-only validation and joining of canonical records and vector matrices."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np

from .elasticsearch_config import VECTOR_FIELDS, VectorFieldSpec


GSE_RE = re.compile(r"^GSE([1-9][0-9]*)$")
INDEXED_RECORD_FIELDS = (
    "gse",
    "title",
    "summary",
    "overall_design",
    "embed_text",
    "type",
    "pubmed_ids",
    "submission_date",
    "last_update_date",
    "platform_ids",
    "n_samples",
    "organisms",
    "molecules",
    "source_names",
    "library_strategies",
    "library_sources",
    "library_selections",
    "organism_ids",
    "organism_status",
    "sex_ids",
    "sex_status",
    "assay_categories",
    "assay_labels",
    "assay_status",
)
_ARRAY_FIELDS = (
    "type",
    "pubmed_ids",
    "platform_ids",
    "organisms",
    "molecules",
    "source_names",
    "library_strategies",
    "library_sources",
    "library_selections",
    "organism_ids",
    "sex_ids",
    "assay_categories",
    "assay_labels",
)
_STRING_FIELDS = (
    "title",
    "summary",
    "overall_design",
    "embed_text",
    "submission_date",
    "last_update_date",
    "organism_status",
    "sex_status",
    "assay_status",
)
_FINITE_SCAN_ROWS = 4096


def _gse_number(gse: str) -> int:
    match = GSE_RE.fullmatch(gse)
    if match is None:
        raise ValueError(f"malformed GSE accession {gse!r}")
    return int(match.group(1))


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc


@dataclass(frozen=True)
class CanonicalRecord:
    gse: str
    source: dict[str, object]
    path: Path


def _project_record(path: Path, payload: object) -> CanonicalRecord:
    if not isinstance(payload, dict):
        raise ValueError(f"canonical record {path} must contain an object")
    path_gse = path.stem
    payload_gse = payload.get("gse")
    if not isinstance(payload_gse, str) or not payload_gse:
        raise ValueError(f"canonical record {path} has no payload GSE")
    if path_gse != payload_gse:
        raise ValueError(f"record path {path_gse} does not match payload {payload_gse}")
    _gse_number(payload_gse)

    for field in _ARRAY_FIELDS:
        value = payload.get(field, [])
        if not isinstance(value, list):
            raise ValueError(f"{payload_gse} {field} must be an array")
        if any(not isinstance(item, str) for item in value):
            raise ValueError(f"{payload_gse} {field} must contain only strings")
    for field in _STRING_FIELDS:
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{payload_gse} {field} must be a string or null")
    n_samples = payload.get("n_samples")
    if n_samples is not None and (
        type(n_samples) is not int or n_samples < 0
    ):
        raise ValueError(f"{payload_gse} n_samples must be a nonnegative integer")

    source = {
        field: payload[field]
        for field in INDEXED_RECORD_FIELDS
        if field in payload and payload[field] is not None
    }
    source["gse"] = payload_gse
    return CanonicalRecord(payload_gse, source, path)


def iter_records(records_root: Path) -> Iterator[CanonicalRecord]:
    """Stream canonical records after sorting only their lightweight paths."""

    paths = list(records_root.rglob("*.json"))
    paths.sort(key=lambda path: _gse_number(path.stem))
    seen: set[str] = set()
    for path in paths:
        record = _project_record(path, _load_json(path))
        if record.gse in seen:
            raise ValueError(f"duplicate canonical record {record.gse}")
        seen.add(record.gse)
        yield record


def load_records(records_root: Path) -> tuple[CanonicalRecord, ...]:
    """Load canonical JSON records in stable numeric-GSE order."""

    return tuple(iter_records(records_root))


@dataclass(frozen=True)
class EmbeddingArtifact:
    spec: VectorFieldSpec
    path: Path
    vectors: np.memmap
    ids: tuple[str, ...]
    row_by_gse: dict[str, int]
    metadata: dict[str, object]


def load_artifact(path: Path, spec: VectorFieldSpec) -> EmbeddingArtifact:
    """Validate and memory-map one canonical embedding artifact."""

    if not path.is_dir():
        raise ValueError(f"artifact directory does not exist: {path}")
    for filename in ("vectors.npy", "ids.json", "metadata.json"):
        if not (path / filename).is_file():
            raise ValueError(f"missing artifact file: {filename}")
    try:
        vectors = np.load(path / "vectors.npy", mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid vectors.npy in {path}: {exc}") from exc
    if not isinstance(vectors, np.memmap):
        raise ValueError("vectors.npy could not be memory-mapped")
    if vectors.ndim != 2:
        raise ValueError(f"vectors.npy must be two-dimensional, got {vectors.shape}")
    if vectors.dtype != np.float32:
        raise ValueError(f"vectors.npy must use float32, got {vectors.dtype}")
    if vectors.shape[1] != spec.dimensions:
        raise ValueError(
            f"{spec.model_key} requires {spec.dimensions} dimensions, "
            f"got {vectors.shape[1]}"
        )
    if not vectors.flags.c_contiguous:
        raise ValueError("vectors.npy must be C-contiguous")
    for start in range(0, vectors.shape[0], _FINITE_SCAN_ROWS):
        if not np.isfinite(vectors[start : start + _FINITE_SCAN_ROWS]).all():
            raise ValueError("vectors.npy contains nonfinite values")

    raw_ids = _load_json(path / "ids.json")
    if not isinstance(raw_ids, list) or any(
        not isinstance(gse, str) for gse in raw_ids
    ):
        raise ValueError("ids.json must contain a string array")
    ids = tuple(raw_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("ids.json contains a duplicate GSE")
    numbers = [_gse_number(gse) for gse in ids]
    if numbers != sorted(numbers):
        raise ValueError("ids.json is not in numeric GSE order")
    if len(ids) != vectors.shape[0]:
        raise ValueError(
            f"ID count {len(ids)} does not match matrix rows {vectors.shape[0]}"
        )

    raw_metadata = _load_json(path / "metadata.json")
    if not isinstance(raw_metadata, dict):
        raise ValueError("metadata.json must contain an object")
    if raw_metadata.get("model_key") != spec.model_key:
        raise ValueError("metadata model_key does not match registry")
    if raw_metadata.get("dimensions") != spec.dimensions:
        raise ValueError("metadata dimensions do not match registry")
    if raw_metadata.get("record_count") != len(ids):
        raise ValueError("metadata record_count does not match matrix rows")
    return EmbeddingArtifact(
        spec=spec,
        path=path,
        vectors=vectors,
        ids=ids,
        row_by_gse={gse: row for row, gse in enumerate(ids)},
        metadata=dict(raw_metadata),
    )


@dataclass(frozen=True)
class IndexDocument:
    gse: str
    source: dict[str, object]


def iter_index_documents(
    records_root: Path,
    artifacts_root: Path,
    model_keys: Sequence[str],
) -> Iterator[IndexDocument]:
    """Join canonical records to available vector rows by GSE."""

    specs: list[VectorFieldSpec] = []
    for model_key in model_keys:
        try:
            specs.append(VECTOR_FIELDS[model_key])
        except KeyError as exc:
            raise ValueError(f"unknown model key: {model_key}") from exc
    artifacts = {
        spec.model_key: load_artifact(artifacts_root / spec.model_key, spec)
        for spec in specs
        if (artifacts_root / spec.model_key).exists()
    }
    for record in iter_records(records_root):
        source = dict(record.source)
        for spec in specs:
            artifact = artifacts.get(spec.model_key)
            if artifact is None:
                continue
            row = artifact.row_by_gse.get(record.gse)
            if row is not None:
                source[spec.field] = [
                    float(value) for value in artifact.vectors[row]
                ]
        yield IndexDocument(record.gse, source)
