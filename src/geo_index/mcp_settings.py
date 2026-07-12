from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .elasticsearch_config import ElasticsearchSettings


MCP_PATH = "/mcp"
PRIMARY_MODEL_KEY = "gemini_embedding_2_3072_v1"


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if value is None or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _split_csv(value: str, *, key: str, required: bool) -> tuple[str, ...]:
    if not value.strip():
        if required:
            raise ValueError(f"{key} must not be empty")
        return ()
    cleaned: list[str] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            raise ValueError(f"{key} contains a blank entry")
        if item not in cleaned:
            cleaned.append(item)
    return tuple(cleaned)


def _https_url(value: str, *, key: str, origin_only: bool) -> str:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{key} is not a valid HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{key} must be an HTTPS URL without userinfo or fragment")
    if origin_only and (parsed.path not in {"", "/"} or parsed.query):
        raise ValueError(f"{key} must be an HTTPS origin")
    if origin_only:
        host = parsed.hostname.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        authority = f"{host}:{port}" if port is not None else host
        return f"https://{authority}"
    return value


def _validated_host(value: str) -> tuple[str, str]:
    if "*" in value:
        raise ValueError("GEO_MCP_ALLOWED_HOSTS must not contain wildcards")
    parsed = urlparse(f"//{value}")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("GEO_MCP_ALLOWED_HOSTS contains an invalid host") from exc
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GEO_MCP_ALLOWED_HOSTS contains an invalid host")
    return value, parsed.hostname.lower()


def _validated_origin(value: str) -> str:
    if "*" in value:
        raise ValueError("GEO_MCP_ALLOWED_ORIGINS must not contain wildcards")
    return _https_url(value, key="GEO_MCP_ALLOWED_ORIGINS", origin_only=True)


@dataclass(frozen=True)
class McpSettings:
    elasticsearch: ElasticsearchSettings = field(repr=False)
    public_base_url: str
    jwks_uri: str
    issuer: str
    audience: str
    authorization_server: str
    allowed_subjects: frozenset[str] = field(repr=False)
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    rate_per_second: float = 5.0
    burst_capacity: int = 10
    required_scope: str = "geo:read"

    @property
    def mcp_url(self) -> str:
        return f"{self.public_base_url}{MCP_PATH}"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> McpSettings:
        elastic_env = dict(env)
        elastic_env.setdefault("ELASTICSEARCH_ACTIVE_MODEL", PRIMARY_MODEL_KEY)
        elasticsearch = ElasticsearchSettings.from_env(elastic_env)
        public_base_url = _https_url(
            _required(env, "GEO_MCP_PUBLIC_BASE_URL"),
            key="GEO_MCP_PUBLIC_BASE_URL",
            origin_only=True,
        )
        jwks_uri = _https_url(
            _required(env, "GEO_MCP_JWKS_URI"),
            key="GEO_MCP_JWKS_URI",
            origin_only=False,
        )
        issuer = _https_url(
            _required(env, "GEO_MCP_ISSUER"),
            key="GEO_MCP_ISSUER",
            origin_only=False,
        )
        audience = _required(env, "GEO_MCP_AUDIENCE")
        authorization_server = _https_url(
            _required(env, "GEO_MCP_AUTHORIZATION_SERVER"),
            key="GEO_MCP_AUTHORIZATION_SERVER",
            origin_only=True,
        )
        subjects = _split_csv(
            _required(env, "GEO_MCP_ALLOWED_SUBJECTS"),
            key="GEO_MCP_ALLOWED_SUBJECTS",
            required=True,
        )
        host_values = _split_csv(
            _required(env, "GEO_MCP_ALLOWED_HOSTS"),
            key="GEO_MCP_ALLOWED_HOSTS",
            required=True,
        )
        validated_hosts = tuple(_validated_host(value) for value in host_values)
        public_hostname = urlparse(public_base_url).hostname
        assert public_hostname is not None
        if public_hostname.lower() not in {host for _, host in validated_hosts}:
            raise ValueError("public hostname must appear in GEO_MCP_ALLOWED_HOSTS")
        origin_values = _split_csv(
            env.get("GEO_MCP_ALLOWED_ORIGINS", ""),
            key="GEO_MCP_ALLOWED_ORIGINS",
            required=False,
        )
        allowed_origins = tuple(_validated_origin(value) for value in origin_values)
        try:
            rate_per_second = float(env.get("GEO_MCP_RATE_PER_SECOND", "5"))
        except ValueError as exc:
            raise ValueError("GEO_MCP_RATE_PER_SECOND must be numeric") from exc
        if not math.isfinite(rate_per_second) or rate_per_second <= 0:
            raise ValueError("GEO_MCP_RATE_PER_SECOND must be positive")
        try:
            burst_capacity = int(env.get("GEO_MCP_BURST_CAPACITY", "10"))
        except ValueError as exc:
            raise ValueError("GEO_MCP_BURST_CAPACITY must be an integer") from exc
        if burst_capacity <= 0:
            raise ValueError("GEO_MCP_BURST_CAPACITY must be positive")
        return cls(
            elasticsearch=elasticsearch,
            public_base_url=public_base_url,
            jwks_uri=jwks_uri,
            issuer=issuer,
            audience=audience,
            authorization_server=authorization_server,
            allowed_subjects=frozenset(subjects),
            allowed_hosts=tuple(value for value, _ in validated_hosts),
            allowed_origins=allowed_origins,
            rate_per_second=rate_per_second,
            burst_capacity=burst_capacity,
        )
