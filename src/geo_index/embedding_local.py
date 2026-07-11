"""Lazy local Hugging Face adapters for canonical embedding artifacts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib.metadata import version
from typing import Protocol, Sequence, TypeVar

import numpy as np

from .embedding_artifacts import RecordRef
from .embedding_registry import EmbeddingVariant


@dataclass(frozen=True)
class LocalProviderResult:
    vectors: np.ndarray
    model_revision: str | None
    sdk_version: str
    truncation_count: int
    usage: dict[str, object]


class LocalEncoder(Protocol):
    def encode(
        self,
        records: Sequence[RecordRef],
        *,
        batch_size: int,
    ) -> LocalProviderResult: ...


T = TypeVar("T")


def _chunks(values: Sequence[T], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


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


def _count_truncations(tokenizer, inputs: Sequence[object], max_length: int) -> int:
    count = 0
    for batch in _chunks(inputs, 256):
        encoded = tokenizer(
            list(batch),
            add_special_tokens=True,
            padding=False,
            truncation=False,
            return_length=True,
        )
        lengths = encoded.get("length")
        if lengths is None:
            lengths = [len(input_ids) for input_ids in encoded["input_ids"]]
        count += sum(int(length) > max_length for length in lengths)
    return count


class _SentenceTransformerEncoder:
    def __init__(self, variant: EmbeddingVariant) -> None:
        from sentence_transformers import SentenceTransformer

        self.variant = variant
        self.device = _device()
        self.revision = _resolved_revision(variant.document_model_id)
        self.model = SentenceTransformer(
            variant.document_model_id,
            revision=self.revision,
            device=self.device,
            trust_remote_code=variant.model_key.startswith("qwen3_"),
        )
        self.model.max_seq_length = variant.max_length
        first_module = self.model[0]
        self.tokenizer = first_module.tokenizer
        if variant.model_key.startswith("qwen3_"):
            self.tokenizer.padding_side = "left"

    def encode(
        self,
        records: Sequence[RecordRef],
        *,
        batch_size: int,
    ) -> LocalProviderResult:
        texts = [record.embed_text for record in records]
        truncation_count = _count_truncations(
            self.tokenizer,
            texts,
            self.variant.max_length,
        )
        vectors = self.model.encode(
            texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        return LocalProviderResult(
            vectors=np.asarray(vectors, dtype=np.float32),
            model_revision=self.revision,
            sdk_version=f"sentence-transformers/{version('sentence-transformers')}",
            truncation_count=truncation_count,
            usage={
                "device": self.device,
                "batch_size": batch_size,
                "batch_count": math.ceil(len(records) / batch_size),
            },
        )


class _MedCPTEncoder:
    def __init__(self, variant: EmbeddingVariant) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.variant = variant
        self.device = _device()
        self.revision = _resolved_revision(variant.document_model_id)
        self.tokenizer = AutoTokenizer.from_pretrained(
            variant.document_model_id,
            revision=self.revision,
        )
        self.model = AutoModel.from_pretrained(
            variant.document_model_id,
            revision=self.revision,
        )
        self.model.to(self.device)
        self.model.eval()
        self.torch = torch

    def encode(
        self,
        records: Sequence[RecordRef],
        *,
        batch_size: int,
    ) -> LocalProviderResult:
        import torch.nn.functional as functional

        articles: list[list[str]] = [
            [record.title, record.embed_text] for record in records
        ]
        truncation_count = _count_truncations(
            self.tokenizer,
            articles,
            self.variant.max_length,
        )
        batches: list[np.ndarray] = []
        with self.torch.inference_mode():
            for batch in _chunks(articles, batch_size):
                encoded = self.tokenizer(
                    list(batch),
                    truncation=True,
                    padding=True,
                    return_tensors="pt",
                    max_length=self.variant.max_length,
                )
                encoded = {key: value.to(self.device) for key, value in encoded.items()}
                cls = self.model(**encoded).last_hidden_state[:, 0, :]
                normalized = functional.normalize(cls, p=2, dim=1)
                batches.append(normalized.cpu().numpy().astype(np.float32, copy=False))
        vectors = np.concatenate(batches, axis=0)
        return LocalProviderResult(
            vectors=vectors,
            model_revision=self.revision,
            sdk_version=f"transformers/{version('transformers')}",
            truncation_count=truncation_count,
            usage={
                "device": self.device,
                "batch_size": batch_size,
                "batch_count": len(batches),
            },
        )


def create_local_encoder(variant: EmbeddingVariant) -> LocalEncoder:
    """Construct exactly one lazy local encoder for a complete model build."""
    if variant.provider != "huggingface":
        raise ValueError(f"{variant.model_key} is not a local Hugging Face model")
    if variant.model_key == "medcpt_v1":
        return _MedCPTEncoder(variant)
    return _SentenceTransformerEncoder(variant)
