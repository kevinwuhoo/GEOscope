from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import geo_index.build_embedding_artifact as builder
import geo_index.embedding_gemini as gemini
from geo_index.build_embedding_artifact import (
    EmbeddingBuildResult,
    build_embedding_artifact,
    build_missing_embeddings,
)
from geo_index.embedding_artifacts import validate_artifact
from geo_index.embedding_local import LocalProviderResult
from geo_index.embedding_registry import get_variant


def _write_record(root: Path, gse: str, title: str, text: str) -> None:
    digits = gse[3:]
    bucket = f"GSE{digits[:-3]}nnn" if len(digits) > 3 else "GSEnnn"
    path = root / bucket / f"{gse}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"gse": gse, "title": title, "embed_text": text}) + "\n",
        encoding="utf-8",
    )


class FakeEncoder:
    def __init__(
        self,
        dimensions: int,
        *,
        value_offset: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.dimensions = dimensions
        self.value_offset = value_offset
        self.error = error
        self.calls: list[tuple[tuple[str, ...], int]] = []

    def encode(self, records, *, batch_size: int) -> LocalProviderResult:
        self.calls.append((tuple(record.gse for record in records), batch_size))
        if self.error is not None:
            raise self.error
        vectors = np.vstack(
            [
                np.full(
                    self.dimensions,
                    int(record.gse[3:]) + self.value_offset,
                    dtype=np.float32,
                )
                for record in records
            ]
        )
        return LocalProviderResult(
            vectors=vectors,
            model_revision="fake-revision",
            sdk_version="fake-sdk-1",
            truncation_count=0,
            usage={"device": "fake", "batch_count": 1},
        )


def _factory(monkeypatch: pytest.MonkeyPatch, encoder: FakeEncoder) -> list[str]:
    calls: list[str] = []

    def create(variant):
        calls.append(variant.model_key)
        return encoder

    monkeypatch.setattr(builder.embedding_local, "create_local_encoder", create)
    return calls


def _records(tmp_path: Path) -> Path:
    records = tmp_path / "records"
    _write_record(records, "GSE10", "Ten", "document ten")
    _write_record(records, "GSE2", "Two", "document two")
    return records


def test_encode_forwards_explicit_gemini_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build(records, variant, temp_dir, *, allow_paid, concurrency):
        captured["allow_paid"] = allow_paid
        captured["concurrency"] = concurrency
        return SimpleNamespace(vectors=np.empty((0, 3072), dtype=np.float32))

    monkeypatch.setattr(gemini, "build_gemini_vectors", fake_build)
    builder._encode(
        get_variant("gemini_embedding_2_3072_v1"),
        (),
        tmp_path,
        allow_paid_gemini=True,
        gemini_concurrency=4,
    )

    assert captured == {"allow_paid": True, "concurrency": 4}


def test_parser_defaults_to_sequential_gemini_batches() -> None:
    args = builder._parser().parse_args(
        ["--model-key", "gemini_embedding_2_3072_v1"]
    )
    assert args.gemini_concurrency == 1


def test_parser_accepts_explicit_gemini_concurrency() -> None:
    args = builder._parser().parse_args(
        [
            "--model-key",
            "gemini_embedding_2_3072_v1",
            "--gemini-concurrency",
            "4",
        ]
    )
    assert args.gemini_concurrency == 4


def test_builder_encodes_numeric_order_once_and_aligns_ids_to_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    encoder = FakeEncoder(384)
    factory_calls = _factory(monkeypatch, encoder)

    result = build_embedding_artifact(
        records,
        output,
        "bge_small_v15",
        allow_paid_gemini=False,
    )

    assert result.status == "created"
    assert result.record_count == 2
    assert factory_calls == ["bge_small_v15"]
    assert encoder.calls == [(('GSE2', 'GSE10'), 128)]
    assert json.loads((result.artifact_path / "ids.json").read_text()) == [
        "GSE2",
        "GSE10",
    ]
    vectors = np.load(result.artifact_path / "vectors.npy")
    assert vectors.dtype == np.float32
    assert vectors.flags.c_contiguous
    assert np.all(vectors[0] == 2)
    assert np.all(vectors[1] == 10)
    validate_artifact(result.artifact_path, get_variant("bge_small_v15"))


def test_valid_existing_artifact_skips_encoder_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    first = FakeEncoder(384)
    _factory(monkeypatch, first)
    build_embedding_artifact(records, output, "bge_small_v15", allow_paid_gemini=False)
    monkeypatch.setattr(
        builder.embedding_local,
        "create_local_encoder",
        lambda variant: (_ for _ in ()).throw(AssertionError("encoder constructed")),
    )
    monkeypatch.setattr(
        builder,
        "load_record_inventory",
        lambda records_root: (_ for _ in ()).throw(
            AssertionError("completed canonical records opened")
        ),
    )

    second = build_embedding_artifact(
        records,
        output,
        "bge_small_v15",
        allow_paid_gemini=False,
    )

    assert second.status == "skipped"
    assert second.record_count == 2


def test_wrong_dimension_leaves_no_published_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(383))

    with pytest.raises(ValueError, match="expected 384 dimensions"):
        build_embedding_artifact(
            records,
            output,
            "bge_small_v15",
            allow_paid_gemini=False,
        )

    assert not (output / "bge_small_v15").exists()
    assert not (output / ".bge_small_v15.tmp").exists()


def test_provider_failure_leaves_no_published_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384, error=RuntimeError("provider failed")))

    with pytest.raises(RuntimeError, match="provider failed"):
        build_embedding_artifact(
            records,
            output,
            "bge_small_v15",
            allow_paid_gemini=False,
        )

    assert not (output / "bge_small_v15").exists()
    assert not (output / ".bge_small_v15.tmp").exists()


def test_builder_records_complete_provider_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))

    result = build_embedding_artifact(
        records,
        output,
        "bge_small_v15",
        allow_paid_gemini=False,
    )
    metadata = json.loads((result.artifact_path / "metadata.json").read_text())

    assert metadata["model_revision"] == "fake-revision"
    assert metadata["sdk_version"] == "fake-sdk-1"
    assert metadata["record_count"] == 2
    assert metadata["usage"]["batch_count"] == 1
    assert metadata["usage"]["device"] == "fake"
    assert metadata["usage"]["provider_runtime_seconds"] >= 0
    assert metadata["build_runtime_seconds"] >= 0
    assert metadata["created_at"].endswith("+00:00")


def test_build_missing_embeddings_replaces_complete_artifact_for_rebuilt_gse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    build_embedding_artifact(records, output, "bge_small_v15", allow_paid_gemini=False)
    replacement = FakeEncoder(384, value_offset=100)
    factory_calls = _factory(monkeypatch, replacement)

    result = build_missing_embeddings(
        records,
        output,
        "bge_small_v15",
        replace_gses=frozenset({"GSE2"}),
        allow_paid_gemini=False,
    )

    assert result.status == "replaced"
    assert factory_calls == ["bge_small_v15"]
    vectors = np.load(output / "bge_small_v15" / "vectors.npy")
    assert np.all(vectors[0] == 102)
    assert not (output / ".bge_small_v15.backup").exists()


def test_build_missing_embeddings_with_empty_replace_set_skips_encoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    build_embedding_artifact(records, output, "bge_small_v15", allow_paid_gemini=False)
    monkeypatch.setattr(
        builder.embedding_local,
        "create_local_encoder",
        lambda variant: (_ for _ in ()).throw(AssertionError("encoder constructed")),
    )
    monkeypatch.setattr(
        builder,
        "load_record_inventory",
        lambda records_root: (_ for _ in ()).throw(
            AssertionError("completed canonical records opened")
        ),
    )

    result = build_missing_embeddings(
        records,
        output,
        "bge_small_v15",
        replace_gses=frozenset(),
        allow_paid_gemini=False,
    )

    assert result.status == "skipped"


def test_replacement_provider_failure_preserves_previous_valid_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    original = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    )
    original_vectors = (original.artifact_path / "vectors.npy").read_bytes()
    _factory(monkeypatch, FakeEncoder(384, error=RuntimeError("replacement failed")))

    with pytest.raises(RuntimeError, match="replacement failed"):
        build_missing_embeddings(
            records,
            output,
            "bge_small_v15",
            replace_gses={"GSE2"},
            allow_paid_gemini=False,
        )

    assert (original.artifact_path / "vectors.npy").read_bytes() == original_vectors
    assert not (output / ".bge_small_v15.tmp").exists()
    validate_artifact(original.artifact_path, get_variant("bge_small_v15"))


def test_failed_forced_replacement_is_retried_without_new_replace_gses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    final = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    ).artifact_path
    original_vectors = (final / "vectors.npy").read_bytes()
    marker = output / ".bge_small_v15.replace.pending"
    _factory(monkeypatch, FakeEncoder(384, error=RuntimeError("encode failed")))

    with pytest.raises(RuntimeError, match="encode failed"):
        build_missing_embeddings(
            records,
            output,
            "bge_small_v15",
            replace_gses={"GSE2"},
            allow_paid_gemini=False,
        )

    assert marker.exists()
    assert (final / "vectors.npy").read_bytes() == original_vectors

    _factory(monkeypatch, FakeEncoder(384, value_offset=100))
    result = build_missing_embeddings(
        records,
        output,
        "bge_small_v15",
        replace_gses=frozenset(),
        allow_paid_gemini=False,
    )

    assert result.status == "replaced"
    assert np.all(np.load(final / "vectors.npy")[0] == 102)
    assert not marker.exists()


def test_builder_recovers_backup_when_replacement_crashed_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    original = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    )
    backup = output / ".bge_small_v15.backup"
    original.artifact_path.rename(backup)
    monkeypatch.setattr(
        builder,
        "load_record_inventory",
        lambda records_root: (_ for _ in ()).throw(
            AssertionError("canonical inventory opened")
        ),
    )

    result = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    )

    assert result.status == "skipped"
    assert original.artifact_path.exists()
    assert not backup.exists()


def test_builder_promotes_validated_temp_after_replacement_swap_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    final = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    ).artifact_path

    replacement_output = tmp_path / "replacement-artifacts"
    _factory(monkeypatch, FakeEncoder(384, value_offset=100))
    replacement = build_embedding_artifact(
        records,
        replacement_output,
        "bge_small_v15",
        allow_paid_gemini=False,
    ).artifact_path
    temp = output / ".bge_small_v15.tmp"
    replacement.rename(temp)
    backup = output / ".bge_small_v15.backup"
    final.rename(backup)
    (output / ".bge_small_v15.replace.pending").write_text("pending\n")
    monkeypatch.setattr(
        builder,
        "load_record_inventory",
        lambda records_root: (_ for _ in ()).throw(
            AssertionError("canonical inventory opened")
        ),
    )

    result = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    )

    assert result.status == "skipped"
    vectors = np.load(final / "vectors.npy")
    assert np.all(vectors[0] == 102)
    assert not temp.exists()
    assert not backup.exists()
    assert not (output / ".bge_small_v15.replace.pending").exists()


def test_pending_marker_forces_replacement_rebuild_when_temp_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    final = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    ).artifact_path
    marker = output / ".bge_small_v15.replace.pending"
    marker.write_text("pending\n")
    _factory(monkeypatch, FakeEncoder(384, value_offset=100))

    result = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    )

    assert result.status == "replaced"
    vectors = np.load(final / "vectors.npy")
    assert np.all(vectors[0] == 102)
    assert not marker.exists()


def test_pending_gemini_replacement_preserves_provider_state_for_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"

    def provider(records, offset: float) -> LocalProviderResult:
        return FakeEncoder(3072, value_offset=offset).encode(records, batch_size=1)

    monkeypatch.setattr(
        builder,
        "_encode",
        lambda variant, inventory, temp_dir, **kwargs: provider(inventory, 0),
    )
    final = build_embedding_artifact(
        records,
        output,
        "gemini_embedding_2_3072_v1",
        allow_paid_gemini=False,
    ).artifact_path
    marker = output / ".gemini_embedding_2_3072_v1.replace.pending"
    marker.write_text("pending\n")

    def interrupt(variant, inventory, temp_dir, **kwargs):
        (temp_dir / "gemini_state.json").write_text('{"job":"persist-me"}\n')
        raise RuntimeError("provider interrupted")

    monkeypatch.setattr(builder, "_encode", interrupt)
    with pytest.raises(RuntimeError, match="provider interrupted"):
        build_embedding_artifact(
            records,
            output,
            "gemini_embedding_2_3072_v1",
            allow_paid_gemini=False,
        )
    temp = output / ".gemini_embedding_2_3072_v1.tmp"
    assert (temp / "gemini_state.json").exists()

    def resume(variant, inventory, temp_dir, **kwargs):
        assert json.loads((temp_dir / "gemini_state.json").read_text()) == {
            "job": "persist-me"
        }
        return provider(inventory, 100)

    monkeypatch.setattr(builder, "_encode", resume)
    result = build_embedding_artifact(
        records,
        output,
        "gemini_embedding_2_3072_v1",
        allow_paid_gemini=False,
    )

    assert result.status == "replaced"
    vectors = np.load(final / "vectors.npy")
    assert np.all(vectors[0] == 102)
    assert not marker.exists()


def test_builder_removes_stale_backup_after_replacement_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(tmp_path)
    output = tmp_path / "artifacts"
    _factory(monkeypatch, FakeEncoder(384))
    artifact = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    ).artifact_path
    backup = output / ".bge_small_v15.backup"
    shutil.copytree(artifact, backup)
    monkeypatch.setattr(
        builder,
        "load_record_inventory",
        lambda records_root: (_ for _ in ()).throw(
            AssertionError("canonical inventory opened")
        ),
    )

    result = build_embedding_artifact(
        records, output, "bge_small_v15", allow_paid_gemini=False
    )

    assert result.status == "skipped"
    assert artifact.exists()
    assert not backup.exists()


def test_result_is_a_frozen_public_value_object() -> None:
    result = EmbeddingBuildResult(
        model_key="bge_small_v15",
        status="skipped",
        artifact_path=Path("artifact"),
        record_count=2,
        duration_seconds=0.0,
    )
    with pytest.raises((AttributeError, TypeError)):
        result.status = "created"  # type: ignore[misc]
