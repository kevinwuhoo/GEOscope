from __future__ import annotations

from pathlib import Path

import numpy as np

from geo_index.embedding_artifacts import RecordRef
from geo_index.embedding_local import _SentenceTransformerEncoder
from geo_index.embedding_registry import get_variant


class _FakeTokenizer:
    def __init__(self, lengths: dict[str, int]) -> None:
        self.lengths = lengths

    def __call__(self, texts, **_kwargs):
        return {"length": [self.lengths[text] for text in texts]}


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int]] = []

    def encode(self, texts, *, batch_size, **_kwargs):
        values = list(texts)
        self.calls.append((values, batch_size))
        return np.asarray(
            [[float(text.removeprefix("text"))] * 1024 for text in values],
            dtype=np.float32,
        )


def test_qwen_uses_bounded_adaptive_batches_and_restores_record_order() -> None:
    variant = get_variant("qwen3_06b_1024_v1")
    assert variant.max_length == 8192
    records = tuple(
        RecordRef(f"GSE{index + 1}", "title", f"text{index}", Path("record.json"))
        for index in range(4)
    )
    encoder = object.__new__(_SentenceTransformerEncoder)
    encoder.variant = variant
    encoder.device = "mps"
    encoder.revision = "revision"
    encoder.tokenizer = _FakeTokenizer(
        {"text0": 100, "text1": 3000, "text2": 600, "text3": 9000}
    )
    encoder.model = _FakeModel()

    result = encoder.encode(records, batch_size=16)

    assert encoder.model.calls == [
        (["text1", "text3"], 1),
        (["text2"], 4),
        (["text0"], 16),
    ]
    assert result.vectors[:, 0].tolist() == [0.0, 1.0, 2.0, 3.0]
    assert result.truncation_count == 1
    assert result.usage["batch_count"] == 4
    assert result.usage["batch_policy"] == {
        "max_512_tokens": 16,
        "max_2048_tokens": 4,
        "max_8192_tokens": 1,
    }
    assert np.isfinite(result.vectors).all()
