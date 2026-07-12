"""Build one complete canonical matrix artifact from canonical JSON records."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AbstractSet

import numpy as np

from . import embedding_local
from .embedding_artifacts import (
    artifact_dir,
    load_record_inventory,
    publish_artifact,
    validate_artifact,
)
from .embedding_registry import EmbeddingVariant, get_variant


DEFAULT_RECORDS_ROOT = Path("data/processed/series_records")
DEFAULT_OUTPUT_ROOT = Path("data/processed/embedding_artifacts")


@dataclass(frozen=True)
class EmbeddingBuildResult:
    model_key: str
    status: str
    artifact_path: Path
    record_count: int
    duration_seconds: float


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _encode(
    variant: EmbeddingVariant,
    records,
    temp_dir: Path,
    *,
    allow_paid_gemini: bool,
    gemini_concurrency: int = 1,
):
    if variant.provider == "google":
        from .embedding_gemini import build_gemini_vectors

        return build_gemini_vectors(
            records,
            variant,
            temp_dir,
            allow_paid=allow_paid_gemini,
            concurrency=gemini_concurrency,
        )
    encoder = embedding_local.create_local_encoder(variant)
    return encoder.encode(records, batch_size=variant.default_batch_size)


def _persist_replacement_intent(marker: Path) -> None:
    """Atomically persist replacement intent before any provider work."""
    temporary = marker.with_suffix(marker.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write('{"schema_version":1}\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
        directory_fd = os.open(marker.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_published_artifact(temp_dir: Path, final_dir: Path) -> None:
    backup = final_dir.parent / f".{final_dir.name}.backup"
    marker = final_dir.parent / f".{final_dir.name}.replace.pending"
    if backup.exists():
        raise FileExistsError(f"stale artifact backup exists: {backup}")
    _persist_replacement_intent(marker)
    os.rename(final_dir, backup)
    try:
        os.rename(temp_dir, final_dir)
    except BaseException:
        os.rename(backup, final_dir)
        raise
    shutil.rmtree(backup)
    marker.unlink()


def _recover_interrupted_replacement(
    final_dir: Path,
    variant: EmbeddingVariant,
) -> bool:
    """Finish a validated swap or preserve its durable replacement intent."""
    backup = final_dir.parent / f".{final_dir.name}.backup"
    temp = final_dir.parent / f".{final_dir.name}.tmp"
    marker = final_dir.parent / f".{final_dir.name}.replace.pending"
    pending = marker.exists()

    if backup.exists() and final_dir.exists():
        try:
            validate_artifact(final_dir, variant)
        except ValueError:
            shutil.rmtree(final_dir)
            validate_artifact(backup, variant)
            os.rename(backup, final_dir)
            return pending
        shutil.rmtree(backup)
        marker.unlink(missing_ok=True)
        return False

    if backup.exists() and not final_dir.exists():
        if temp.exists():
            try:
                validate_artifact(temp, variant)
            except ValueError:
                if variant.provider != "google":
                    shutil.rmtree(temp, ignore_errors=True)
            else:
                os.rename(temp, final_dir)
                shutil.rmtree(backup)
                marker.unlink(missing_ok=True)
                return False
        validate_artifact(backup, variant)
        os.rename(backup, final_dir)
        return pending

    if final_dir.exists() and pending and temp.exists():
        try:
            validate_artifact(temp, variant)
        except ValueError:
            if variant.provider != "google":
                shutil.rmtree(temp, ignore_errors=True)
            return True
        _replace_published_artifact(temp, final_dir)
        return False

    if final_dir.exists():
        validate_artifact(final_dir, variant)
    return pending


def _build(
    records_root: Path,
    output_root: Path,
    model_key: str,
    *,
    allow_paid_gemini: bool,
    force_replace: bool,
    gemini_concurrency: int = 1,
) -> EmbeddingBuildResult:
    started = time.perf_counter()
    variant = get_variant(model_key)
    final_dir = artifact_dir(output_root, model_key)
    recovery_requires_replace = _recover_interrupted_replacement(final_dir, variant)
    force_replace = force_replace or recovery_requires_replace
    final_exists = final_dir.exists()
    if final_exists:
        existing = validate_artifact(final_dir, variant)
        if not force_replace:
            return EmbeddingBuildResult(
                model_key=model_key,
                status="skipped",
                artifact_path=final_dir,
                record_count=existing.record_count,
                duration_seconds=time.perf_counter() - started,
            )

    inventory = load_record_inventory(records_root)
    if not inventory.records:
        raise ValueError(f"no canonical records found under {records_root}")
    temp_dir = output_root / f".{model_key}.tmp"
    output_root.mkdir(parents=True, exist_ok=True)
    if final_exists and force_replace:
        _persist_replacement_intent(
            output_root / f".{model_key}.replace.pending"
        )
    if variant.provider != "google" and temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        provider_started = time.perf_counter()
        provider = _encode(
            variant,
            inventory.records,
            temp_dir,
            allow_paid_gemini=allow_paid_gemini,
            gemini_concurrency=gemini_concurrency,
        )
        vectors = np.ascontiguousarray(provider.vectors, dtype=np.float32)
        np.save(temp_dir / "vectors.npy", vectors, allow_pickle=False)
        _write_json(temp_dir / "ids.json", list(inventory.ids))
        duration = time.perf_counter() - started
        metadata = {
            "schema_version": 1,
            "model_key": variant.model_key,
            "provider": variant.provider,
            "model_id": variant.document_model_id,
            "model_revision": provider.model_revision,
            "dimensions": variant.dimensions,
            "document_format": variant.document_format,
            "query_format": variant.query_format,
            "normalization": variant.normalization,
            "pooling": variant.pooling,
            "record_count": len(inventory),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "build_runtime_seconds": duration,
            "sdk_version": provider.sdk_version,
            "max_length": variant.max_length,
            "truncation_count": provider.truncation_count,
            "usage": {
                **provider.usage,
                "provider_runtime_seconds": time.perf_counter() - provider_started,
            },
        }
        _write_json(temp_dir / "metadata.json", metadata)
        validate_artifact(temp_dir, variant)

        if final_exists:
            _replace_published_artifact(temp_dir, final_dir)
            status = "replaced"
        else:
            publish_artifact(temp_dir, final_dir)
            (output_root / f".{model_key}.replace.pending").unlink(missing_ok=True)
            status = "created"
    except BaseException:
        if variant.provider != "google":
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return EmbeddingBuildResult(
        model_key=model_key,
        status=status,
        artifact_path=final_dir,
        record_count=len(inventory),
        duration_seconds=time.perf_counter() - started,
    )


def build_embedding_artifact(
    records_root: Path,
    output_root: Path,
    model_key: str,
    *,
    allow_paid_gemini: bool,
    gemini_concurrency: int = 1,
) -> EmbeddingBuildResult:
    """Build a complete artifact, or skip a valid existing artifact."""
    return _build(
        records_root,
        output_root,
        model_key,
        allow_paid_gemini=allow_paid_gemini,
        force_replace=False,
        gemini_concurrency=gemini_concurrency,
    )


def build_missing_embeddings(
    records_root: Path,
    store_path: Path,
    model_key: str,
    *,
    replace_gses: AbstractSet[str],
    allow_paid_gemini: bool,
    gemini_concurrency: int = 1,
) -> EmbeddingBuildResult:
    """ETL integration facade with explicit rebuilt-GSE replacement semantics."""
    if not replace_gses:
        return _build(
            records_root,
            store_path,
            model_key,
            allow_paid_gemini=allow_paid_gemini,
            force_replace=False,
            gemini_concurrency=gemini_concurrency,
        )
    inventory = load_record_inventory(records_root)
    missing = sorted(set(replace_gses) - set(inventory.ids))
    if missing:
        raise ValueError(f"replace_gses are absent from canonical inventory: {missing}")
    return _build(
        records_root,
        store_path,
        model_key,
        allow_paid_gemini=allow_paid_gemini,
        force_replace=bool(replace_gses),
        gemini_concurrency=gemini_concurrency,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a canonical embedding artifact")
    parser.add_argument("--records-root", type=Path, default=DEFAULT_RECORDS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model-key", required=True)
    parser.add_argument("--allow-paid-gemini", action="store_true")
    parser.add_argument("--gemini-concurrency", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = build_embedding_artifact(
        args.records_root,
        args.output_root,
        args.model_key,
        allow_paid_gemini=args.allow_paid_gemini,
        gemini_concurrency=args.gemini_concurrency,
    )
    print(json.dumps({**asdict(result), "artifact_path": str(result.artifact_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
