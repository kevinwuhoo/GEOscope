"""Lazy query encoders for the fixed Elasticsearch comparison models."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np

from .embedding_registry import EmbeddingVariant, get_variant


COMPARISON_MODEL_KEYS: Final[tuple[str, ...]] = (
    "bge_small_v15",
    "medcpt_v1",
    "qwen3_06b_1024_v1",
)


def _search_variant(model_key: str) -> EmbeddingVariant:
    if model_key not in VECTOR_MODEL_KEYS:
        raise ValueError(f"not an Elasticsearch search model: {model_key}")
    return get_variant(model_key)


VECTOR_MODEL_KEYS: Final[tuple[str, ...]] = (
    *COMPARISON_MODEL_KEYS,
    "gemini_embedding_2_3072_v1",
)


def format_query(model_key: str, query: str) -> str:
    """Apply exactly one fixed registry query template."""

    text = query.strip()
    if not text:
        raise ValueError("blank query")
    return _search_variant(model_key).query_format.format(query=text)


def validate_query_vector(model_key: str, value: object) -> np.ndarray:
    """Return a finite, normalized float32 vector of the registered size."""

    variant = _search_variant(model_key)
    vector = np.asarray(value, dtype=np.float32)
    if vector.ndim != 1 or vector.shape != (variant.dimensions,):
        raise ValueError(
            f"{model_key} expected {variant.dimensions} query dimensions, "
            f"got {vector.shape}"
        )
    if not np.isfinite(vector).all():
        raise ValueError(f"{model_key} query vector contains nonfinite values")
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError(f"{model_key} query vector has zero norm")
    return np.ascontiguousarray(vector / norm, dtype=np.float32)


@dataclass(frozen=True)
class QueryEncoderInfo:
    model_key: str
    model_id: str
    revision: str
    dimensions: int


class QueryEncoder(Protocol):
    info: QueryEncoderInfo

    def encode(self, query: str) -> np.ndarray: ...

    def close(self) -> None: ...


def _device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _resolved_revision(model_id: str) -> str:
    from huggingface_hub import HfApi

    revision = HfApi().model_info(model_id).sha
    if not revision:
        raise RuntimeError(f"Hugging Face did not resolve a revision for {model_id}")
    return revision


class _SentenceTransformerQueryEncoder:
    def __init__(self, variant: EmbeddingVariant) -> None:
        from sentence_transformers import SentenceTransformer

        self.variant = variant
        revision = _resolved_revision(variant.query_model_id)
        self.info = QueryEncoderInfo(
            variant.model_key,
            variant.query_model_id,
            revision,
            variant.dimensions,
        )
        self.model = SentenceTransformer(
            variant.query_model_id,
            revision=revision,
            device=_device(),
            trust_remote_code=variant.model_key.startswith("qwen3_"),
        )
        self.model.max_seq_length = variant.max_length
        self.tokenizer = self.model[0].tokenizer
        if variant.model_key.startswith("qwen3_"):
            self.tokenizer.padding_side = "left"

    def encode(self, query: str) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("query encoder is closed")
        vectors = self.model.encode(
            [format_query(self.variant.model_key, query)],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return validate_query_vector(self.variant.model_key, np.asarray(vectors)[0])

    def close(self) -> None:
        self.model = None
        self.tokenizer = None


class _MedCPTQueryEncoder:
    def __init__(self, variant: EmbeddingVariant) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.variant = variant
        self.device = _device()
        revision = _resolved_revision(variant.query_model_id)
        self.info = QueryEncoderInfo(
            variant.model_key,
            variant.query_model_id,
            revision,
            variant.dimensions,
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            variant.query_model_id, revision=revision
        )
        self.model = AutoModel.from_pretrained(
            variant.query_model_id, revision=revision
        )
        self.model.to(self.device)
        self.model.eval()
        self.torch = torch

    def encode(self, query: str) -> np.ndarray:
        import torch.nn.functional as functional

        if self.model is None or self.tokenizer is None:
            raise RuntimeError("query encoder is closed")
        encoded = self.tokenizer(
            [format_query(self.variant.model_key, query)],
            truncation=True,
            padding=True,
            return_tensors="pt",
            max_length=self.variant.max_length,
        )
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        with self.torch.inference_mode():
            cls = self.model(**encoded).last_hidden_state[:, 0, :]
            vector = functional.normalize(cls, p=2, dim=1)[0].cpu().numpy()
        return validate_query_vector(self.variant.model_key, vector)

    def close(self) -> None:
        self.model = None
        self.tokenizer = None


class _GeminiQueryEncoder:
    def __init__(
        self,
        variant: EmbeddingVariant,
        *,
        api_key: str | None = None,
        client: object | None = None,
    ) -> None:
        key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        if not key:
            raise ValueError("GEMINI_API_KEY is required for Gemini query embeddings")
        if variant.model_key != "gemini_embedding_2_3072_v1":
            raise ValueError(f"not a Gemini query model: {variant.model_key}")
        if client is None:
            from google import genai

            client = genai.Client(api_key=key)
        self.variant = variant
        self.client = client
        self.info = QueryEncoderInfo(
            variant.model_key,
            variant.query_model_id,
            "provider-managed",
            variant.dimensions,
        )

    def encode(self, query: str) -> np.ndarray:
        if self.client is None:
            raise RuntimeError("query encoder is closed")
        response = self.client.models.embed_content(
            model=self.variant.query_model_id,
            contents=format_query(self.variant.model_key, query),
            config={
                "task_type": "RETRIEVAL_QUERY",
                "output_dimensionality": self.variant.dimensions,
            },
        )
        embeddings = getattr(response, "embeddings", None)
        if not isinstance(embeddings, list) or len(embeddings) != 1:
            raise ValueError("Gemini query response must contain exactly one embedding")
        values = getattr(embeddings[0], "values", None)
        return validate_query_vector(self.variant.model_key, values)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None


def create_query_encoder(model_key: str) -> QueryEncoder:
    """Create one lazy real query encoder for a fixed comparison model."""

    variant = _search_variant(model_key)
    if model_key == "gemini_embedding_2_3072_v1":
        return _GeminiQueryEncoder(variant)
    if model_key == "medcpt_v1":
        return _MedCPTQueryEncoder(variant)
    return _SentenceTransformerQueryEncoder(variant)
