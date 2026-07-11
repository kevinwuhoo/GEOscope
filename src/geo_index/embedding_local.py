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


def _token_lengths(tokenizer, inputs: Sequence[object]) -> list[int]:
    lengths: list[int] = []
    for batch in _chunks(inputs, 256):
        encoded = tokenizer(
            list(batch),
            add_special_tokens=True,
            padding=False,
            truncation=False,
            return_length=True,
        )
        batch_lengths = encoded.get("length")
        if batch_lengths is None:
            batch_lengths = [len(input_ids) for input_ids in encoded["input_ids"]]
        lengths.extend(int(length) for length in batch_lengths)
    return lengths


def _count_truncations(tokenizer, inputs: Sequence[object], max_length: int) -> int:
    return sum(length > max_length for length in _token_lengths(tokenizer, inputs))


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
        token_lengths = _token_lengths(self.tokenizer, texts)
        truncation_count = sum(
            length > self.variant.max_length for length in token_lengths
        )
        batch_count: int
        batch_policy: dict[str, int] | None = None
        if self.variant.model_key.startswith("qwen3_"):
            policies = (
                (512, batch_size),
                (2048, min(batch_size, 4)),
                (4096, min(batch_size, 2)),
                (self.variant.max_length, 1),
            )
            grouped_indices: list[list[int]] = [[] for _ in policies]
            for index, length in enumerate(token_lengths):
                for bucket, (maximum, _) in enumerate(policies):
                    if length <= maximum or bucket == len(policies) - 1:
                        grouped_indices[bucket].append(index)
                        break
            vectors = np.empty(
                (len(records), self.variant.dimensions),
                dtype=np.float32,
            )
            batch_count = 0
            for bucket in reversed(range(len(policies))):
                indices = grouped_indices[bucket]
                policy_batch_size = policies[bucket][1]
                if not indices:
                    continue
                encoded = self.model.encode(
                    [texts[index] for index in indices],
                    batch_size=policy_batch_size,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=True,
                )
                vectors[indices] = np.asarray(encoded, dtype=np.float32)
                batch_count += math.ceil(len(indices) / policy_batch_size)
            batch_policy = {
                f"max_{maximum}_tokens": policy_batch_size
                for maximum, policy_batch_size in policies
            }
        else:
            vectors = self.model.encode(
                texts,
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=True,
            )
            batch_count = math.ceil(len(records) / batch_size)
        usage: dict[str, object] = {
            "device": self.device,
            "batch_size": batch_size,
            "batch_count": batch_count,
        }
        if batch_policy is not None:
            usage["batch_policy"] = batch_policy
        return LocalProviderResult(
            vectors=np.asarray(vectors, dtype=np.float32),
            model_revision=self.revision,
            sdk_version=f"sentence-transformers/{version('sentence-transformers')}",
            truncation_count=truncation_count,
            usage=usage,
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
