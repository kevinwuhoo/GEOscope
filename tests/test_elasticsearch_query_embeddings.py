from __future__ import annotations

import numpy as np
import pytest

from geo_index.elasticsearch_query_embeddings import (
    QueryEncoderInfo,
    _GeminiQueryEncoder,
    _SentenceTransformerQueryEncoder,
    create_query_encoder,
    format_query,
    validate_query_vector,
)
from geo_index.embedding_registry import get_variant


def test_format_query_uses_each_fixed_registry_template() -> None:
    assert format_query("bge_small_v15", "immune cells") == (
        "Represent this sentence for searching relevant passages: immune cells"
    )
    assert format_query("medcpt_v1", "immune cells") == "immune cells"
    assert format_query("qwen3_06b_1024_v1", "immune cells") == (
        "Instruct: Given a web search query, retrieve relevant passages that "
        "answer the query\nQuery: immune cells"
    )
    assert format_query("gemini_embedding_2_3072_v1", "immune cells") == (
        "task: search result | query: immune cells"
    )


def test_format_query_rejects_blank_query() -> None:
    with pytest.raises(ValueError, match="blank query"):
        format_query("bge_small_v15", "  ")


class _FakeGeminiModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        embedding = type("Embedding", (), {"values": [1.0] * 3072})()
        return type("Response", (), {"embeddings": [embedding]})()


class _FakeGeminiClient:
    def __init__(self) -> None:
        self.models = _FakeGeminiModels()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_gemini_query_encoder_uses_retrieval_query_and_3072_dimensions() -> None:
    variant = get_variant("gemini_embedding_2_3072_v1")
    client = _FakeGeminiClient()
    encoder = _GeminiQueryEncoder(variant, api_key="gemini-key", client=client)

    vector = encoder.encode("immune cells")

    assert client.models.calls == [
        {
            "model": "gemini-embedding-2",
            "contents": "task: search result | query: immune cells",
            "config": {
                "task_type": "RETRIEVAL_QUERY",
                "output_dimensionality": 3072,
            },
        }
    ]
    assert vector.shape == (3072,)
    assert vector.dtype == np.float32
    assert np.linalg.norm(vector) == pytest.approx(1.0)
    encoder.close()
    assert client.closed


def test_gemini_query_encoder_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        _GeminiQueryEncoder(get_variant("gemini_embedding_2_3072_v1"))


def test_validate_query_vector_converts_normalizes_and_makes_contiguous() -> None:
    raw = np.ones(768, dtype=np.float64)[::2]

    vector = validate_query_vector("bge_small_v15", raw)

    assert vector.dtype == np.float32
    assert vector.flags.c_contiguous
    assert vector.shape == (384,)
    assert np.linalg.norm(vector) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (np.zeros(383), "expected 384"),
        (np.zeros((1, 384)), "expected 384"),
        (np.full(384, np.nan), "nonfinite"),
        (np.full(384, np.inf), "nonfinite"),
        (np.zeros(384), "zero norm"),
    ],
)
def test_validate_query_vector_rejects_invalid_values(
    value: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_query_vector("bge_small_v15", value)


class _FakeSentenceModel:
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return np.ones((1, self.dimensions), dtype=np.float32)


def test_sentence_transformer_encoder_formats_and_validates_query() -> None:
    variant = get_variant("bge_small_v15")
    encoder = object.__new__(_SentenceTransformerQueryEncoder)
    encoder.variant = variant
    encoder.info = QueryEncoderInfo(
        variant.model_key, variant.query_model_id, "revision", variant.dimensions
    )
    encoder.model = _FakeSentenceModel(variant.dimensions)
    encoder.tokenizer = None

    vector = encoder.encode("immune cells")

    texts, kwargs = encoder.model.calls[0]
    assert texts == [
        "Represent this sentence for searching relevant passages: immune cells"
    ]
    assert kwargs == {
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "show_progress_bar": False,
    }
    assert vector.shape == (384,)
    assert np.linalg.norm(vector) == pytest.approx(1.0)


def test_sentence_transformer_encoder_close_releases_references() -> None:
    encoder = object.__new__(_SentenceTransformerQueryEncoder)
    encoder.model = object()
    encoder.tokenizer = object()

    encoder.close()

    assert encoder.model is None
    assert encoder.tokenizer is None


def test_create_query_encoder_routes_fixed_model_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import geo_index.elasticsearch_query_embeddings as module

    sentence = object()
    medcpt = object()
    gemini = object()
    monkeypatch.setattr(module, "_SentenceTransformerQueryEncoder", lambda _: sentence)
    monkeypatch.setattr(module, "_MedCPTQueryEncoder", lambda _: medcpt)
    monkeypatch.setattr(module, "_GeminiQueryEncoder", lambda _: gemini)

    assert create_query_encoder("bge_small_v15") is sentence
    assert create_query_encoder("qwen3_06b_1024_v1") is sentence
    assert create_query_encoder("medcpt_v1") is medcpt
    assert create_query_encoder("gemini_embedding_2_3072_v1") is gemini
