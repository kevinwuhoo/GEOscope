from __future__ import annotations

from typing import Any

import pytest

from geo_index.elasticsearch_config import INDEX_NAME
from geo_index.elasticsearch_index import (
    MAPPING_REVISION,
    ensure_index,
    index_definition,
    index_readiness,
    reset_index,
)


class _Indices:
    def __init__(
        self,
        *,
        exists: bool = False,
        mapping_revision: str = MAPPING_REVISION,
    ) -> None:
        self.exists_value = exists
        self.mapping_revision = mapping_revision
        self.create_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []

    def exists(self, *, index: str) -> bool:
        assert index == INDEX_NAME
        return self.exists_value

    def create(self, **kwargs: object) -> dict[str, bool]:
        self.create_calls.append(kwargs)
        self.exists_value = True
        return {"acknowledged": True}

    def delete(self, **kwargs: object) -> dict[str, bool]:
        self.delete_calls.append(kwargs)
        self.exists_value = False
        return {"acknowledged": True}

    def get_mapping(self, *, index: str) -> dict[str, object]:
        assert index == INDEX_NAME
        definition = index_definition()
        mappings = dict(definition["mappings"])
        metadata = dict(mappings["_meta"])
        metadata["mapping_revision"] = self.mapping_revision
        mappings["_meta"] = metadata
        return {INDEX_NAME: {"mappings": mappings}}


class _Client:
    def __init__(self, indices: _Indices, version: str = "9.4.2") -> None:
        self.indices = indices
        self.version = version

    def info(self) -> dict[str, object]:
        return {"version": {"number": self.version}}


def test_index_definition_has_explicit_settings_and_mappings() -> None:
    definition = index_definition()
    assert definition["settings"]["number_of_shards"] == 1
    assert definition["settings"]["number_of_replicas"] == 0
    mappings = definition["mappings"]
    assert mappings["dynamic"] == "strict"
    assert mappings["_meta"]["mapping_revision"] == MAPPING_REVISION
    properties = mappings["properties"]
    assert properties["gse"] == {"type": "keyword"}
    assert properties["title"]["type"] == "text"
    assert properties["title"]["fields"]["keyword"]["type"] == "keyword"
    assert properties["n_samples"] == {"type": "integer"}
    assert properties["submission_date"] == {
        "type": "date",
        "ignore_malformed": False,
    }
    assert properties["last_update_date"] == {
        "type": "date",
        "ignore_malformed": False,
    }
    for field in (
        "organism_ids",
        "sex_ids",
        "assay_categories",
        "assay_labels",
    ):
        assert properties[field] == {"type": "keyword"}


def test_index_definition_has_explicit_vector_dimensions_and_options() -> None:
    properties = index_definition()["mappings"]["properties"]
    expected = {
        "embedding_bge_384": 384,
        "embedding_medcpt_768": 768,
        "embedding_qwen3_06b_1024": 1024,
        "embedding_gemini_3072": 3072,
    }
    for field, dimensions in expected.items():
        assert properties[field] == {
            "type": "dense_vector",
            "dims": dimensions,
            "element_type": "float",
            "index": True,
            "similarity": "cosine",
            "index_options": {"type": "int8_hnsw"},
        }


def test_ensure_index_creates_fixed_definition_only_when_absent() -> None:
    indices = _Indices(exists=False)
    client = _Client(indices)
    assert ensure_index(client) is True
    assert indices.create_calls == [
        {"index": INDEX_NAME, **index_definition()}
    ]
    assert ensure_index(client) is False
    assert len(indices.create_calls) == 1


def test_ensure_index_rejects_mapping_revision_mismatch() -> None:
    client = _Client(_Indices(exists=True, mapping_revision="old-revision"))
    with pytest.raises(ValueError, match="mapping revision"):
        ensure_index(client)


def test_reset_requires_explicit_confirmation_and_targets_only_geo_series() -> None:
    indices = _Indices(exists=True)
    client = _Client(indices)
    with pytest.raises(ValueError, match="confirm"):
        reset_index(client)
    reset_index(client, confirm=True)
    assert indices.delete_calls == [
        {"index": INDEX_NAME, "ignore_unavailable": True}
    ]
    assert indices.create_calls == [{"index": INDEX_NAME, **index_definition()}]


def test_index_readiness_reports_server_mapping_and_active_vector() -> None:
    readiness = index_readiness(
        _Client(_Indices(exists=True)), "qwen3_06b_1024_v1"
    )
    assert readiness.ready is True
    assert readiness.server_version == "9.4.2"
    assert readiness.index_name == "geo-series"
    assert readiness.mapping_revision == MAPPING_REVISION
    assert readiness.active_model_key == "qwen3_06b_1024_v1"
    assert readiness.active_vector_field == "embedding_qwen3_06b_1024"


def test_index_readiness_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="unknown active model"):
        index_readiness(_Client(_Indices(exists=True)), "unknown")
