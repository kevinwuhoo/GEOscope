"""Environment-only Elasticsearch connection and fixed vector configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


INDEX_NAME = "geo-series"
DEFAULT_ACTIVE_MODEL_KEY = "gemini_embedding_2_3072_v1"


@dataclass(frozen=True)
class VectorFieldSpec:
    """One immutable embedding variant exposed to Elasticsearch internals."""

    model_key: str
    field: str
    dimensions: int


VECTOR_FIELDS: dict[str, VectorFieldSpec] = {
    "bge_small_v15": VectorFieldSpec(
        "bge_small_v15", "embedding_bge_384", 384
    ),
    "medcpt_v1": VectorFieldSpec(
        "medcpt_v1", "embedding_medcpt_768", 768
    ),
    "qwen3_06b_1024_v1": VectorFieldSpec(
        "qwen3_06b_1024_v1", "embedding_qwen3_06b_1024", 1024
    ),
    "gemini_embedding_2_3072_v1": VectorFieldSpec(
        "gemini_embedding_2_3072_v1", "embedding_gemini_3072", 3072
    ),
}


@dataclass(frozen=True)
class ElasticsearchSettings:
    """Validated settings shared by local and later managed deployments."""

    url: str
    active_model_key: str
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    request_timeout: float = 30.0
    max_retries: int = 3

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> ElasticsearchSettings:
        source = os.environ if environ is None else environ
        url = source.get("ELASTICSEARCH_URL", "").strip()
        if not url:
            raise ValueError("ELASTICSEARCH_URL is required")

        username = source.get("ELASTICSEARCH_USERNAME", "").strip() or None
        password = source.get("ELASTICSEARCH_PASSWORD", "").strip() or None
        api_key = source.get("ELASTICSEARCH_API_KEY", "").strip() or None
        has_basic = username is not None or password is not None
        if api_key is not None and has_basic:
            raise ValueError("configure exactly one credential mode")
        if has_basic and (username is None or password is None):
            raise ValueError("both username and password are required")
        if api_key is None and not has_basic:
            raise ValueError("Elasticsearch credentials are required")

        active_model_key = source.get(
            "ELASTICSEARCH_ACTIVE_MODEL", DEFAULT_ACTIVE_MODEL_KEY
        ).strip()
        if active_model_key not in VECTOR_FIELDS:
            raise ValueError(f"unknown active model: {active_model_key}")

        try:
            request_timeout = float(
                source.get("ELASTICSEARCH_REQUEST_TIMEOUT", "30")
            )
        except ValueError as exc:
            raise ValueError("request timeout must be a positive number") from exc
        if request_timeout <= 0:
            raise ValueError("request timeout must be positive")

        try:
            max_retries = int(source.get("ELASTICSEARCH_MAX_RETRIES", "3"))
        except ValueError as exc:
            raise ValueError("max retries must be a nonnegative integer") from exc
        if max_retries < 0:
            raise ValueError("max retries must be nonnegative")

        return cls(
            url=url,
            active_model_key=active_model_key,
            username=username,
            password=password,
            api_key=api_key,
            request_timeout=request_timeout,
            max_retries=max_retries,
        )


def create_client(settings: ElasticsearchSettings):
    """Create the official client without performing network I/O."""

    from elasticsearch import Elasticsearch

    auth: dict[str, object]
    if settings.api_key is not None:
        auth = {"api_key": settings.api_key}
    else:
        auth = {"basic_auth": (settings.username, settings.password)}
    return Elasticsearch(
        settings.url,
        request_timeout=settings.request_timeout,
        max_retries=settings.max_retries,
        retry_on_timeout=True,
        retry_on_status=(429, 502, 503, 504),
        **auth,
    )


def response_body(response: object) -> dict[str, Any]:
    """Unwrap an official ``ObjectApiResponse`` or accept a fake dict."""

    body = getattr(response, "body", response)
    if not isinstance(body, dict):
        raise ValueError("Elasticsearch response body must be an object")
    return body
