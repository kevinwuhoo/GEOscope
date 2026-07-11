"""Explicit settings, mappings, and lifecycle for the canonical index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .elasticsearch_config import INDEX_NAME, VECTOR_FIELDS, response_body


MAPPING_REVISION = "geo-series-v1"

_TEXT_FIELDS: dict[str, dict[str, object]] = {
    "title": {
        "type": "text",
        "fields": {
            "keyword": {"type": "keyword", "ignore_above": 1024},
        },
    },
    "summary": {"type": "text"},
    "overall_design": {"type": "text"},
    "embed_text": {"type": "text"},
}
_KEYWORD_FIELDS = (
    "gse",
    "type",
    "pubmed_ids",
    "platform_ids",
    "organism_ids",
    "organism_status",
    "sex_ids",
    "sex_status",
    "assay_categories",
    "assay_labels",
    "assay_status",
    "organisms",
    "molecules",
    "source_names",
    "library_strategies",
    "library_sources",
    "library_selections",
)


def index_definition() -> dict[str, Any]:
    """Return a fresh, fully explicit definition for ``geo-series``."""

    properties: dict[str, dict[str, object]] = {
        **{name: dict(mapping) for name, mapping in _TEXT_FIELDS.items()},
        **{name: {"type": "keyword"} for name in _KEYWORD_FIELDS},
        "n_samples": {"type": "integer"},
        "submission_date": {"type": "date", "ignore_malformed": False},
        "last_update_date": {"type": "date", "ignore_malformed": False},
    }
    for spec in VECTOR_FIELDS.values():
        properties[spec.field] = {
            "type": "dense_vector",
            "dims": spec.dimensions,
            "element_type": "float",
            "index": True,
            "similarity": "cosine",
            "index_options": {"type": "int8_hnsw"},
        }
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "dynamic": "strict",
            "_meta": {
                "mapping_revision": MAPPING_REVISION,
                "vector_fields": {
                    key: {
                        "field": spec.field,
                        "dimensions": spec.dimensions,
                    }
                    for key, spec in VECTOR_FIELDS.items()
                },
            },
            "properties": properties,
        },
    }


def _current_mapping(client: Any) -> dict[str, Any]:
    response = response_body(client.indices.get_mapping(index=INDEX_NAME))
    try:
        return dict(response[INDEX_NAME]["mappings"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"cannot read mapping for {INDEX_NAME}") from exc


def _validate_current_mapping(client: Any) -> dict[str, Any]:
    mapping = _current_mapping(client)
    metadata = mapping.get("_meta", {})
    revision = metadata.get("mapping_revision") if isinstance(metadata, dict) else None
    if revision != MAPPING_REVISION:
        raise ValueError(
            f"mapping revision {revision!r} does not match {MAPPING_REVISION!r}"
        )
    properties = mapping.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("index mapping properties are malformed")
    for spec in VECTOR_FIELDS.values():
        vector = properties.get(spec.field)
        if not isinstance(vector, dict) or vector.get("dims") != spec.dimensions:
            raise ValueError(
                f"active mapping field {spec.field!r} does not have "
                f"{spec.dimensions} dimensions"
            )
    return mapping


def ensure_index(client: Any) -> bool:
    """Create the canonical index if absent and validate it if present."""

    if client.indices.exists(index=INDEX_NAME):
        _validate_current_mapping(client)
        return False
    client.indices.create(index=INDEX_NAME, **index_definition())
    return True


def reset_index(client: Any, *, confirm: bool = False) -> None:
    """Explicitly recreate only the local canonical index."""

    if confirm is not True:
        raise ValueError("reset requires confirm=True")
    client.indices.delete(index=INDEX_NAME, ignore_unavailable=True)
    client.indices.create(index=INDEX_NAME, **index_definition())


@dataclass(frozen=True)
class IndexReadiness:
    ready: bool
    server_version: str
    index_name: str
    mapping_revision: str
    active_model_key: str
    active_vector_field: str


def index_readiness(client: Any, active_model_key: str) -> IndexReadiness:
    """Validate the live index and active vector-field contract."""

    try:
        spec = VECTOR_FIELDS[active_model_key]
    except KeyError as exc:
        raise ValueError(f"unknown active model: {active_model_key}") from exc
    if not client.indices.exists(index=INDEX_NAME):
        raise ValueError(f"required index {INDEX_NAME!r} does not exist")
    mapping = _validate_current_mapping(client)
    properties = mapping["properties"]
    if spec.field not in properties:
        raise ValueError(f"active vector field {spec.field!r} is not mapped")
    info = response_body(client.info())
    try:
        version = str(info["version"]["number"])
    except (KeyError, TypeError) as exc:
        raise ValueError("cannot read Elasticsearch server version") from exc
    return IndexReadiness(
        ready=True,
        server_version=version,
        index_name=INDEX_NAME,
        mapping_revision=MAPPING_REVISION,
        active_model_key=active_model_key,
        active_vector_field=spec.field,
    )
