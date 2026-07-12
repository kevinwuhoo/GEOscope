"""Build one complete canonical matrix artifact from canonical JSON records."""

from __future__ import annotations

import argparse
import hashlib
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


def _ids_sha256(ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for gse in ids:
        encoded = gse.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _write_sync_plan(
    path: Path,
    *,
    model_key: str,
    target_ids: tuple[str, ...],
    base_ids: tuple[str, ...],
    delta_ids: tuple[str, ...],
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    value = {
        "schema_version": 1,
        "model_key": model_key,
        "target_ids_sha256": _ids_sha256(target_ids),
        "base_ids_sha256": _ids_sha256(base_ids),
        "delta_ids": list(delta_ids),
    }
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _load_sync_plan(
    path: Path,
    *,
    model_key: str,
    target_ids: tuple[str, ...],
    base_ids: tuple[str, ...],
) -> tuple[str, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid incremental sync plan: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid incremental sync plan: {path}")
    expected = {
        "schema_version": 1,
        "model_key": model_key,
        "target_ids_sha256": _ids_sha256(target_ids),
        "base_ids_sha256": _ids_sha256(base_ids),
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            raise ValueError(
                f"incremental sync plan {field} does not match current artifact state"
            )
    raw_delta_ids = value.get("delta_ids")
    if not isinstance(raw_delta_ids, list) or any(
        not isinstance(gse, str) for gse in raw_delta_ids
    ):
        raise ValueError("incremental sync plan delta_ids must be a list of strings")
    delta_ids = tuple(raw_delta_ids)
    if len(delta_ids) != len(set(delta_ids)):
        raise ValueError("incremental sync plan delta_ids contains duplicates")
    target_id_set = set(target_ids)
    if any(gse not in target_id_set for gse in delta_ids):
        raise ValueError("incremental sync plan delta_ids are absent from target inventory")
    ordered_delta_ids = tuple(gse for gse in target_ids if gse in set(delta_ids))
    if delta_ids != ordered_delta_ids:
        raise ValueError("incremental sync plan delta_ids are not in target order")
    missing_ids = target_id_set - set(base_ids)
    if not missing_ids.issubset(delta_ids):
        raise ValueError("incremental sync plan omits records absent from base artifact")
    return delta_ids


def _encode(
    variant: EmbeddingVariant,
    records,
    temp_dir: Path,
    *,
    allow_paid_gemini: bool,
    gemini_max_cost_usd: float | None = None,
    gemini_concurrency: int = 1,
):
    if variant.provider == "google":
        from .embedding_gemini import build_gemini_vectors

        return build_gemini_vectors(
            records,
            variant,
            temp_dir,
            allow_paid=allow_paid_gemini,
            max_cost_usd=gemini_max_cost_usd,
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


def _artifact_ids(path: Path) -> tuple[str, ...]:
    raw = json.loads((path / "ids.json").read_text(encoding="utf-8"))
    if not isinstance(raw, list) or any(not isinstance(gse, str) for gse in raw):
        raise ValueError(f"invalid artifact IDs in {path}")
    return tuple(raw)


def _write_incremental_vectors(
    path: Path,
    target_ids: tuple[str, ...],
    base_dir: Path,
    base_ids: tuple[str, ...],
    delta_ids: tuple[str, ...],
    delta_vectors: np.ndarray,
    dimensions: int,
) -> None:
    base_vectors = np.load(
        base_dir / "vectors.npy", mmap_mode="r", allow_pickle=False
    )
    base_row = {gse: row for row, gse in enumerate(base_ids)}
    delta_row = {gse: row for row, gse in enumerate(delta_ids)}
    vectors = np.lib.format.open_memmap(
        path,
        mode="w+",
        dtype=np.float32,
        shape=(len(target_ids), dimensions),
    )
    for target_row, gse in enumerate(target_ids):
        if gse in delta_row:
            vectors[target_row] = delta_vectors[delta_row[gse]]
        else:
            vectors[target_row] = base_vectors[base_row[gse]]
    vectors.flush()
    del vectors


def _build(
    records_root: Path,
    output_root: Path,
    model_key: str,
    *,
    allow_paid_gemini: bool,
    force_replace: bool,
    replace_gses: AbstractSet[str] = frozenset(),
    gemini_max_cost_usd: float | None = None,
    gemini_concurrency: int = 1,
) -> EmbeddingBuildResult:
    started = time.perf_counter()
    variant = get_variant(model_key)
    final_dir = artifact_dir(output_root, model_key)
    recovery_requires_replace = _recover_interrupted_replacement(final_dir, variant)
    final_exists = final_dir.exists()
    existing = None
    existing_ids: tuple[str, ...] = ()
    if final_exists:
        existing = validate_artifact(final_dir, variant)
        existing_ids = _artifact_ids(final_dir)

    inventory = load_record_inventory(records_root)
    if not inventory.records:
        raise ValueError(f"no canonical records found under {records_root}")
    temp_dir = output_root / f".{model_key}.tmp"
    sync_plan_path = temp_dir / "sync_plan.json"
    resumed_delta_ids: tuple[str, ...] | None = None
    if recovery_requires_replace and sync_plan_path.exists():
        resumed_delta_ids = _load_sync_plan(
            sync_plan_path,
            model_key=model_key,
            target_ids=inventory.ids,
            base_ids=existing_ids,
        )
    elif recovery_requires_replace:
        force_replace = True
    if (
        final_exists
        and not force_replace
        and resumed_delta_ids is None
        and not replace_gses
        and existing_ids == inventory.ids
    ):
        return EmbeddingBuildResult(
            model_key=model_key,
            status="skipped",
            artifact_path=final_dir,
            record_count=len(inventory),
            duration_seconds=time.perf_counter() - started,
        )
    incremental = final_exists and not force_replace
    existing_id_set = set(existing_ids)
    if resumed_delta_ids is not None:
        delta_ids = resumed_delta_ids
    else:
        delta_id_set = (set(inventory.ids) - existing_id_set) | set(replace_gses)
        delta_ids = tuple(
            gse for gse in inventory.ids if not incremental or gse in delta_id_set
        )
    delta_id_set = set(delta_ids)
    provider_records = tuple(
        record for record in inventory.records if record.gse in delta_id_set
    )
    output_root.mkdir(parents=True, exist_ok=True)
    if final_exists:
        _persist_replacement_intent(
            output_root / f".{model_key}.replace.pending"
        )
    if variant.provider != "google" and temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    if resumed_delta_ids is None:
        _write_sync_plan(
            sync_plan_path,
            model_key=model_key,
            target_ids=inventory.ids,
            base_ids=existing_ids,
            delta_ids=delta_ids,
        )

    try:
        provider_started = time.perf_counter()
        provider = None
        if provider_records:
            provider = _encode(
                variant,
                provider_records,
                temp_dir,
                allow_paid_gemini=allow_paid_gemini,
                gemini_max_cost_usd=gemini_max_cost_usd,
                gemini_concurrency=gemini_concurrency,
            )
            vectors = np.ascontiguousarray(provider.vectors, dtype=np.float32)
        else:
            vectors = np.empty((0, variant.dimensions), dtype=np.float32)
        if incremental:
            _write_incremental_vectors(
                temp_dir / "vectors.npy",
                inventory.ids,
                final_dir,
                existing_ids,
                delta_ids,
                vectors,
                variant.dimensions,
            )
        else:
            np.save(temp_dir / "vectors.npy", vectors, allow_pickle=False)
        _write_json(temp_dir / "ids.json", list(inventory.ids))
        duration = time.perf_counter() - started
        metadata = {
            "schema_version": 1,
            "model_key": variant.model_key,
            "provider": variant.provider,
            "model_id": variant.document_model_id,
            "model_revision": (
                provider.model_revision if provider is not None else existing.model_revision
            ),
            "dimensions": variant.dimensions,
            "document_format": variant.document_format,
            "query_format": variant.query_format,
            "normalization": variant.normalization,
            "pooling": variant.pooling,
            "record_count": len(inventory),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "build_runtime_seconds": duration,
            "sdk_version": (
                provider.sdk_version if provider is not None else existing.sdk_version
            ),
            "max_length": variant.max_length,
            "truncation_count": (
                provider.truncation_count
                if provider is not None
                else existing.truncation_count
            ),
            "usage": {
                **(provider.usage if provider is not None else existing.usage),
                "provider_runtime_seconds": time.perf_counter() - provider_started,
                "encoded_record_count": len(delta_ids),
                "reused_record_count": len(inventory) - len(delta_ids),
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
    gemini_max_cost_usd: float | None = None,
    gemini_concurrency: int = 1,
) -> EmbeddingBuildResult:
    """Build a complete artifact, or skip a valid existing artifact."""
    return _build(
        records_root,
        output_root,
        model_key,
        allow_paid_gemini=allow_paid_gemini,
        force_replace=False,
        replace_gses=frozenset(),
        gemini_max_cost_usd=gemini_max_cost_usd,
        gemini_concurrency=gemini_concurrency,
    )


def build_missing_embeddings(
    records_root: Path,
    store_path: Path,
    model_key: str,
    *,
    replace_gses: AbstractSet[str],
    allow_paid_gemini: bool,
    gemini_max_cost_usd: float | None = None,
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
            replace_gses=frozenset(),
            gemini_max_cost_usd=gemini_max_cost_usd,
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
        force_replace=False,
        replace_gses=replace_gses,
        gemini_max_cost_usd=gemini_max_cost_usd,
        gemini_concurrency=gemini_concurrency,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a canonical embedding artifact")
    parser.add_argument("--records-root", type=Path, default=DEFAULT_RECORDS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model-key", required=True)
    parser.add_argument("--allow-paid-gemini", action="store_true")
    parser.add_argument("--gemini-max-cost-usd", type=float)
    parser.add_argument("--gemini-concurrency", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = build_embedding_artifact(
        args.records_root,
        args.output_root,
        args.model_key,
        allow_paid_gemini=args.allow_paid_gemini,
        gemini_max_cost_usd=args.gemini_max_cost_usd,
        gemini_concurrency=args.gemini_concurrency,
    )
    print(json.dumps({**asdict(result), "artifact_path": str(result.artifact_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
