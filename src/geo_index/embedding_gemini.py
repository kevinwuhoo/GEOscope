"""Resumable file-based Gemini batch embeddings for canonical GSE records."""

from __future__ import annotations

import hashlib
import json
import math
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
MAX_ESTIMATED_INPUT_CHARS = 32_768
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
class GeminiRequestEstimate:
    request_path: Path
    request_sha256: str
    estimated_tokens: int
    estimated_cost_usd: float
    truncation_count: int


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _wrapped_document(record: RecordRef, variant: EmbeddingVariant) -> tuple[str, bool]:
    text = variant.document_format.format(
        title=record.title,
        embed_text=record.embed_text,
    )
    truncated = len(text) > MAX_ESTIMATED_INPUT_CHARS
    return text[:MAX_ESTIMATED_INPUT_CHARS], truncated


def prepare_gemini_requests(
    records: Sequence[RecordRef],
    variant: EmbeddingVariant,
    temp_dir: Path,
) -> GeminiRequestEstimate:
    """Write deterministic official embedding-batch JSONL and estimate cost."""
    if variant.model_key != "gemini_embedding_2_3072_v1":
        raise ValueError(f"not a Gemini embedding variant: {variant.model_key}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    request_path = temp_dir / "gemini_requests.jsonl"
    temporary = request_path.with_suffix(".jsonl.tmp")
    estimated_tokens = 0
    truncation_count = 0
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            text, truncated = _wrapped_document(record, variant)
            truncation_count += int(truncated)
            estimated_tokens += max(1, math.ceil(len(text) / 4))
            row = {
                "key": record.gse,
                "request": {
                    "content": {"parts": [{"text": text}]},
                    "output_dimensionality": variant.dimensions,
                },
            }
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, request_path)
    digest = hashlib.sha256(request_path.read_bytes()).hexdigest()
    cost = estimated_tokens / 1_000_000 * BATCH_PRICE_PER_MILLION_TOKENS_USD
    return GeminiRequestEstimate(
        request_path=request_path,
        request_sha256=digest,
        estimated_tokens=estimated_tokens,
        estimated_cost_usd=cost,
        truncation_count=truncation_count,
    )


def _create_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _state_name(job) -> str:
    state = job.state
    return state.name if hasattr(state, "name") else str(state)


def _load_state(path: Path, estimate: GeminiRequestEstimate) -> dict[str, object]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError(f"invalid Gemini state file: {path}")
        if state.get("request_sha256") != estimate.request_sha256:
            raise ValueError("Gemini request inventory changed; remove the stale temp state")
        return state
    state: dict[str, object] = {
        "schema_version": 1,
        "request_sha256": estimate.request_sha256,
        "estimated_tokens": estimate.estimated_tokens,
        "estimated_cost_usd": estimate.estimated_cost_usd,
        "truncation_count": estimate.truncation_count,
        "uploaded_file_name": None,
        "job_name": None,
        "job_state": None,
        "output_file_name": None,
    }
    _atomic_json(path, state)
    return state


def _assemble_results(
    result_path: Path,
    records: Sequence[RecordRef],
    dimensions: int,
) -> tuple[np.ndarray, int]:
    expected = {record.gse for record in records}
    vectors: dict[str, np.ndarray] = {}
    actual_tokens = 0
    with result_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Gemini result line {line_number} is not an object")
            if row.get("error"):
                raise RuntimeError(f"Gemini row error at line {line_number}: {row['error']}")
            gse = row.get("key")
            if gse not in expected:
                raise ValueError(f"unexpected Gemini response {gse}")
            if gse in vectors:
                raise ValueError(f"duplicate Gemini response {gse}")
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
            vectors[str(gse)] = vector
            actual_tokens += int(response.get("tokenCount") or 0)
    missing = sorted(expected - set(vectors), key=lambda gse: int(gse[3:]))
    if missing:
        raise ValueError(f"missing Gemini responses: {', '.join(missing)}")
    matrix = np.vstack([vectors[record.gse] for record in records]).astype(
        np.float32,
        copy=False,
    )
    return np.ascontiguousarray(matrix), actual_tokens


def build_gemini_vectors(
    records: Sequence[RecordRef],
    variant: EmbeddingVariant,
    temp_dir: Path,
    *,
    allow_paid: bool,
) -> LocalProviderResult:
    """Submit/resume a file-based embedding batch and assemble aligned vectors."""
    estimate = prepare_gemini_requests(records, variant, temp_dir)
    print(
        "estimated Gemini batch: "
        f"records={len(records):,} tokens={estimate.estimated_tokens:,} "
        f"cost_usd=${estimate.estimated_cost_usd:.4f} "
        f"truncated={estimate.truncation_count:,}",
        flush=True,
    )
    if not allow_paid:
        raise GeminiAuthorizationError(
            "Gemini batch submission requires allow_paid_gemini=True"
        )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiAuthorizationError(
            "Gemini batch submission requires GEMINI_API_KEY"
        )

    state_path = temp_dir / "gemini_state.json"
    state = _load_state(state_path, estimate)
    client = _create_client(api_key)
    try:
        uploaded_file_name = state.get("uploaded_file_name")
        if not uploaded_file_name:
            uploaded = client.files.upload(
                file=str(estimate.request_path),
                config={
                    "display_name": "geo-gemini-embedding-2-input",
                    "mime_type": "jsonl",
                },
            )
            uploaded_file_name = uploaded.name
            state["uploaded_file_name"] = uploaded_file_name
            _atomic_json(state_path, state)

        job_name = state.get("job_name")
        if not job_name:
            job = client.batches.create_embeddings(
                model=variant.document_model_id,
                src={"file_name": uploaded_file_name},
                config={"display_name": "geo-gemini-embedding-2"},
            )
            job_name = job.name
            state["job_name"] = job_name
            _atomic_json(state_path, state)

        while True:
            job = client.batches.get(name=job_name)
            job_state = _state_name(job)
            state["job_state"] = job_state
            _atomic_json(state_path, state)
            if job_state in TERMINAL_STATES:
                break
            time.sleep(POLL_SECONDS)
        if job_state != "JOB_STATE_SUCCEEDED":
            raise RuntimeError(f"Gemini batch {job_name} ended as {job_state}: {job.error}")

        output_file_name = job.dest.file_name
        if not output_file_name:
            raise RuntimeError(f"Gemini batch {job_name} has no output file")
        state["output_file_name"] = output_file_name
        _atomic_json(state_path, state)
        result_path = temp_dir / "gemini_results.jsonl"
        if not result_path.exists():
            content = client.files.download(file=output_file_name)
            temporary = result_path.with_suffix(".jsonl.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, result_path)
        vectors, actual_tokens = _assemble_results(
            result_path,
            records,
            variant.dimensions,
        )
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            close()

    return LocalProviderResult(
        vectors=vectors,
        model_revision=variant.document_model_id,
        sdk_version=f"google-genai/{version('google-genai')}",
        truncation_count=estimate.truncation_count,
        usage={
            "estimated_tokens": estimate.estimated_tokens,
            "actual_tokens": actual_tokens,
            "estimated_charge_usd": (
                actual_tokens / 1_000_000 * BATCH_PRICE_PER_MILLION_TOKENS_USD
            ),
            "provider_file_ids": [str(uploaded_file_name), str(output_file_name)],
            "provider_job_ids": [str(job_name)],
            "output_dimensionality": variant.dimensions,
        },
    )
