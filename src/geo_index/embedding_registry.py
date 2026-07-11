"""Lightweight fixed registry for canonical document embedding variants."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingVariant:
    model_key: str
    provider: str
    document_model_id: str
    query_model_id: str
    dimensions: int
    document_format: str
    query_format: str
    normalization: str
    max_length: int
    pooling: str
    default_batch_size: int


_VARIANTS = {
    "bge_small_v15": EmbeddingVariant(
        model_key="bge_small_v15",
        provider="huggingface",
        document_model_id="BAAI/bge-small-en-v1.5",
        query_model_id="BAAI/bge-small-en-v1.5",
        dimensions=384,
        document_format="{embed_text}",
        query_format="Represent this sentence for searching relevant passages: {query}",
        normalization="l2",
        max_length=512,
        pooling="cls",
        default_batch_size=128,
    ),
    "medcpt_v1": EmbeddingVariant(
        model_key="medcpt_v1",
        provider="huggingface",
        document_model_id="ncbi/MedCPT-Article-Encoder",
        query_model_id="ncbi/MedCPT-Query-Encoder",
        dimensions=768,
        document_format="[title, embed_text]",
        query_format="{query}",
        normalization="l2",
        max_length=512,
        pooling="cls",
        default_batch_size=64,
    ),
    "qwen3_06b_1024_v1": EmbeddingVariant(
        model_key="qwen3_06b_1024_v1",
        provider="huggingface",
        document_model_id="Qwen/Qwen3-Embedding-0.6B",
        query_model_id="Qwen/Qwen3-Embedding-0.6B",
        dimensions=1024,
        document_format="{embed_text}",
        query_format=(
            "Instruct: Given a web search query, retrieve relevant passages that "
            "answer the query\nQuery: {query}"
        ),
        normalization="l2",
        max_length=32768,
        pooling="last-token",
        default_batch_size=16,
    ),
    "gemini_embedding_2_3072_v1": EmbeddingVariant(
        model_key="gemini_embedding_2_3072_v1",
        provider="google",
        document_model_id="gemini-embedding-2",
        query_model_id="gemini-embedding-2",
        dimensions=3072,
        document_format="document: title: {title} | text: {embed_text}",
        query_format="task: search result | query: {query}",
        normalization="l2",
        max_length=8192,
        pooling="provider",
        default_batch_size=1000,
    ),
}

MODEL_KEYS = tuple(_VARIANTS)


def get_variant(model_key: str) -> EmbeddingVariant:
    """Resolve a safe public key to its immutable embedding contract."""
    try:
        return _VARIANTS[model_key]
    except KeyError as exc:
        raise ValueError(
            f"unknown model key {model_key!r}; choose one of {MODEL_KEYS}"
        ) from exc
