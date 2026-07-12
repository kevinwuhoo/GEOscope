"""Resumable sharded Gemini batch embeddings for canonical GSE records."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Sequence

import numpy as np

from .embedding_artifacts import RecordRef
from .embedding_local import LocalProviderResult
from .embedding_registry import EmbeddingVariant


BATCH_PRICE_PER_MILLION_TOKENS_USD = 0.10
MAX_REQUESTS_PER_SHARD = 1_000
MAX_REQUEST_FILE_BYTES = 100 * 1024 * 1024
POLL_SECONDS = 30
TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


class GeminiAuthorizationError(RuntimeError):
    """Paid Gemini work was not explicitly and completely authorized."""


@dataclass(frozen=True)
class GeminiBatchRowFailure:
    """One provider row failure associated with its request identity."""

    gse: str
    error: object

    def as_payload(self) -> dict[str, object]:
        return {"gse": self.gse, "error": self.error}


class GeminiBatchRowError(RuntimeError):
    """One or more Gemini batch rows failed."""

    def __init__(self, failures: Sequence[GeminiBatchRowFailure]) -> None:
        self._row_failures = tuple(failures)
        self.failures = tuple(
            failure.as_payload() for failure in self._row_failures
        )
        failed_gses = ", ".join(failure.gse for failure in self._row_failures)
        super().__init__(f"Gemini row errors for: {failed_gses}")


@dataclass(frozen=True)
class GeminiRequestShard:
    index: int
    request_path: Path
    request_sha256: str
    gses: tuple[str, ...]
    estimated_tokens: int
    truncation_count: int


@dataclass(frozen=True)
class GeminiRequestEstimate:
    shards: tuple[GeminiRequestShard, ...]
    inventory_sha256: str
    estimated_tokens: int
    estimated_cost_usd: float
    truncation_count: int

    @property
    def request_path(self) -> Path:
        """Return the sole path for small one-shard callers."""
        if len(self.shards) != 1:
            raise ValueError("request estimate contains multiple shards")
        return self.shards[0].request_path


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _wrapped_document(record: RecordRef, variant: EmbeddingVariant) -> str:
    return variant.document_format.format(
        title=record.title,
        embed_text=record.embed_text,
    )


def _request_line(
    record: RecordRef,
    variant: EmbeddingVariant,
) -> tuple[bytes, int, bool]:
    text = _wrapped_document(record, variant)
    text_bytes = text.encode("utf-8")
    row = {
        "key": record.gse,
        "request": {
            "content": {"parts": [{"text": text}]},
            "output_dimensionality": variant.dimensions,
        },
    }
    line = (
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    # One token cannot encode less than one input byte. Treating every byte as
    # a token deliberately overestimates cost rather than understating it.
    estimated_tokens = max(1, len(text_bytes))
    return line, estimated_tokens, False


def prepare_gemini_requests(
    records: Sequence[RecordRef],
    variant: EmbeddingVariant,
    temp_dir: Path,
) -> GeminiRequestEstimate:
    """Write deterministic bounded embedding-batch shards and estimate cost."""
    if variant.model_key != "gemini_embedding_2_3072_v1":
        raise ValueError(f"not a Gemini embedding variant: {variant.model_key}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    shards: list[GeminiRequestShard] = []
    lines: list[bytes] = []
    gses: list[str] = []
    shard_bytes = 0
    shard_tokens = 0
    shard_truncations = 0

    def publish_shard() -> None:
        nonlocal lines, gses, shard_bytes, shard_tokens, shard_truncations
        if not lines:
            return
        index = len(shards)
        request_path = temp_dir / f"gemini_requests-{index:05d}.jsonl"
        temporary = request_path.with_suffix(".jsonl.tmp")
        with temporary.open("wb") as handle:
            for line in lines:
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, request_path)
        digest = hashlib.sha256(request_path.read_bytes()).hexdigest()
        shards.append(
            GeminiRequestShard(
                index=index,
                request_path=request_path,
                request_sha256=digest,
                gses=tuple(gses),
                estimated_tokens=shard_tokens,
                truncation_count=shard_truncations,
            )
        )
        lines = []
        gses = []
        shard_bytes = 0
        shard_tokens = 0
        shard_truncations = 0

    for record in records:
        line, estimated_tokens, truncated = _request_line(record, variant)
        if lines and (
            len(lines) >= MAX_REQUESTS_PER_SHARD
            or shard_bytes + len(line) > MAX_REQUEST_FILE_BYTES
        ):
            publish_shard()
        if len(line) > MAX_REQUEST_FILE_BYTES:
            raise ValueError(f"Gemini request for {record.gse} exceeds shard byte limit")
        lines.append(line)
        gses.append(record.gse)
        shard_bytes += len(line)
        shard_tokens += estimated_tokens
        shard_truncations += int(truncated)
    publish_shard()

    active_paths = {shard.request_path for shard in shards}
    for stale in temp_dir.glob("gemini_requests-*.jsonl"):
        if stale not in active_paths:
            stale.unlink()
    inventory_digest = hashlib.sha256(
        "\n".join(shard.request_sha256 for shard in shards).encode("ascii")
    ).hexdigest()
    total_tokens = sum(shard.estimated_tokens for shard in shards)
    total_truncations = sum(shard.truncation_count for shard in shards)
    return GeminiRequestEstimate(
        shards=tuple(shards),
        inventory_sha256=inventory_digest,
        estimated_tokens=total_tokens,
        estimated_cost_usd=(
            total_tokens / 1_000_000 * BATCH_PRICE_PER_MILLION_TOKENS_USD
        ),
        truncation_count=total_truncations,
    )


def _create_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _state_name(job) -> str:
    state = job.state
    return state.name if hasattr(state, "name") else str(state)


def _new_shard_state(shard: GeminiRequestShard) -> dict[str, object]:
    return {
        "index": shard.index,
        "request_sha256": shard.request_sha256,
        "gses": list(shard.gses),
        "uploaded_file_name": None,
        "job_name": None,
        "job_state": None,
        "output_file_name": None,
    }


def _load_state(path: Path, estimate: GeminiRequestEstimate) -> dict[str, object]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 2:
            raise ValueError(f"invalid Gemini state file: {path}")
        if state.get("inventory_sha256") != estimate.inventory_sha256:
            raise ValueError("Gemini request inventory changed; remove stale temp state")
        stored_shards = state.get("shards")
        if not isinstance(stored_shards, list) or len(stored_shards) != len(
            estimate.shards
        ):
            raise ValueError("Gemini shard inventory changed; remove stale temp state")
        for stored, shard in zip(stored_shards, estimate.shards, strict=True):
            if not isinstance(stored, dict) or (
                stored.get("request_sha256") != shard.request_sha256
                or stored.get("gses") != list(shard.gses)
            ):
                raise ValueError("Gemini shard inventory changed; remove stale temp state")
        return state
    state: dict[str, object] = {
        "schema_version": 2,
        "inventory_sha256": estimate.inventory_sha256,
        "estimated_tokens": estimate.estimated_tokens,
        "estimated_cost_usd": estimate.estimated_cost_usd,
        "truncation_count": estimate.truncation_count,
        "shards": [_new_shard_state(shard) for shard in estimate.shards],
    }
    _atomic_json(path, state)
    return state


def _assemble_results(
    result_path: Path,
    records: Sequence[RecordRef],
    dimensions: int,
) -> tuple[np.ndarray | None, int, tuple[GeminiBatchRowFailure, ...]]:
    expected = {record.gse for record in records}
    seen: set[str] = set()
    vectors: dict[str, np.ndarray] = {}
    failures: list[GeminiBatchRowFailure] = []
    actual_tokens = 0
    with result_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Gemini result line {line_number} is not an object")
            raw_gse = row.get("key")
            if raw_gse not in expected:
                raise ValueError(f"unexpected Gemini response {raw_gse}")
            gse = str(raw_gse)
            if gse in seen:
                raise ValueError(f"duplicate Gemini response {gse}")
            seen.add(gse)
            if row.get("error") is not None:
                failures.append(
                    GeminiBatchRowFailure(gse=gse, error=row["error"])
                )
                continue
            response = row.get("response")
            if not isinstance(response, dict):
                raise ValueError(f"Gemini response {gse} has no response object")
            embedding = response.get("embedding")
            values = embedding.get("values") if isinstance(embedding, dict) else None
            if not isinstance(values, list) or len(values) != dimensions:
                actual = len(values) if isinstance(values, list) else 0
                raise ValueError(
                    f"Gemini response {gse} must have {dimensions} dimensions, got {actual}"
                )
            vector = np.asarray(values, dtype=np.float32)
            if not np.isfinite(vector).all():
                raise ValueError(f"Gemini response {gse} contains nonfinite values")
            vectors[gse] = vector
            actual_tokens += int(response.get("tokenCount") or 0)
    missing = sorted(expected - seen, key=lambda gse: int(gse[3:]))
    if missing:
        raise ValueError(f"missing Gemini responses: {', '.join(missing)}")
    if failures:
        return None, actual_tokens, tuple(failures)
    matrix = np.vstack([vectors[record.gse] for record in records]).astype(
        np.float32,
        copy=False,
    )
    return np.ascontiguousarray(matrix), actual_tokens, ()


def _display_name(base: str, shard: GeminiRequestShard, count: int) -> str:
    return base if count == 1 else f"{base}-{shard.index:05d}"


def build_gemini_vectors(
    records: Sequence[RecordRef],
    variant: EmbeddingVariant,
    temp_dir: Path,
    *,
    allow_paid: bool,
) -> LocalProviderResult:
    """Submit or resume bounded file batches and assemble aligned vectors."""
    estimate = prepare_gemini_requests(records, variant, temp_dir)
    print(
        "estimated Gemini batch: "
        f"records={len(records):,} shards={len(estimate.shards):,} "
        f"tokens<={estimate.estimated_tokens:,} "
        f"cost_usd<=${estimate.estimated_cost_usd:.4f} "
        f"truncated={estimate.truncation_count:,}",
        flush=True,
    )
    if not allow_paid:
        raise GeminiAuthorizationError(
            "Gemini batch submission requires allow_paid_gemini=True"
        )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiAuthorizationError("Gemini batch submission requires GEMINI_API_KEY")

    state_path = temp_dir / "gemini_state.json"
    state = _load_state(state_path, estimate)
    state_shards = state["shards"]
    if not isinstance(state_shards, list):  # guarded by _load_state
        raise ValueError("invalid Gemini shard state")
    record_by_gse = {record.gse: record for record in records}
    vector_batches: list[np.ndarray] = []
    row_failures: list[GeminiBatchRowFailure] = []
    actual_tokens = 0
    uploaded_ids: list[str] = []
    job_ids: list[str] = []
    output_ids: list[str] = []
    client = _create_client(api_key)
    try:
        for shard, raw_shard_state in zip(
            estimate.shards, state_shards, strict=True
        ):
            if not isinstance(raw_shard_state, dict):
                raise ValueError("invalid Gemini shard state")
            shard_records = [record_by_gse[gse] for gse in shard.gses]
            result_path = temp_dir / f"gemini_results-{shard.index:05d}.jsonl"
            if not result_path.exists():
                uploaded_file_name = raw_shard_state.get("uploaded_file_name")
                if not uploaded_file_name:
                    uploaded = client.files.upload(
                        file=str(shard.request_path),
                        config={
                            "display_name": _display_name(
                                "geo-gemini-embedding-2-input",
                                shard,
                                len(estimate.shards),
                            ),
                            "mime_type": "jsonl",
                        },
                    )
                    uploaded_file_name = uploaded.name
                    raw_shard_state["uploaded_file_name"] = uploaded_file_name
                    _atomic_json(state_path, state)

                job_name = raw_shard_state.get("job_name")
                if not job_name:
                    job = client.batches.create_embeddings(
                        model=variant.document_model_id,
                        src={"file_name": uploaded_file_name},
                        config={
                            "display_name": _display_name(
                                "geo-gemini-embedding-2",
                                shard,
                                len(estimate.shards),
                            )
                        },
                    )
                    job_name = job.name
                    raw_shard_state["job_name"] = job_name
                    _atomic_json(state_path, state)

                while True:
                    job = client.batches.get(name=job_name)
                    job_state = _state_name(job)
                    raw_shard_state["job_state"] = job_state
                    _atomic_json(state_path, state)
                    if job_state in TERMINAL_STATES:
                        break
                    time.sleep(POLL_SECONDS)
                if job_state != "JOB_STATE_SUCCEEDED":
                    raise RuntimeError(
                        f"Gemini batch {job_name} ended as {job_state}: {job.error}"
                    )

                output_file_name = job.dest.file_name
                if not output_file_name:
                    raise RuntimeError(f"Gemini batch {job_name} has no output file")
                raw_shard_state["output_file_name"] = output_file_name
                _atomic_json(state_path, state)
                content = client.files.download(file=output_file_name)
                temporary = result_path.with_suffix(".jsonl.tmp")
                temporary.write_bytes(content)
                os.replace(temporary, result_path)

            vectors, shard_tokens, shard_failures = _assemble_results(
                result_path,
                shard_records,
                variant.dimensions,
            )
            if vectors is not None:
                vector_batches.append(vectors)
            row_failures.extend(shard_failures)
            actual_tokens += shard_tokens
            uploaded = raw_shard_state.get("uploaded_file_name")
            job_name = raw_shard_state.get("job_name")
            output = raw_shard_state.get("output_file_name")
            if uploaded:
                uploaded_ids.append(str(uploaded))
            if job_name:
                job_ids.append(str(job_name))
            if output:
                output_ids.append(str(output))
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            close()

    if row_failures:
        raise GeminiBatchRowError(row_failures)
    vectors = np.concatenate(vector_batches, axis=0)
    return LocalProviderResult(
        vectors=np.ascontiguousarray(vectors, dtype=np.float32),
        model_revision=variant.document_model_id,
        sdk_version=f"google-genai/{version('google-genai')}",
        truncation_count=estimate.truncation_count,
        usage={
            "estimated_tokens_upper_bound": estimate.estimated_tokens,
            "actual_tokens": actual_tokens,
            "estimated_charge_usd": (
                actual_tokens / 1_000_000 * BATCH_PRICE_PER_MILLION_TOKENS_USD
            ),
            "provider_file_ids": [*uploaded_ids, *output_ids],
            "provider_job_ids": job_ids,
            "output_dimensionality": variant.dimensions,
            "request_shards": len(estimate.shards),
            "max_requests_per_shard": MAX_REQUESTS_PER_SHARD,
            "max_request_file_bytes": MAX_REQUEST_FILE_BYTES,
        },
    )
