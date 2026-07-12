from __future__ import annotations

import pytest

from geo_index.mcp_settings import MCP_PATH, McpSettings


VALID = {
    "ELASTICSEARCH_URL": "https://elastic.internal:9200",
    "ELASTICSEARCH_API_KEY": "secret-api-key",
    "GEO_MCP_PUBLIC_BASE_URL": "https://geo.example.org",
    "GEO_MCP_JWKS_URI": "https://login.example.org/.well-known/jwks.json",
    "GEO_MCP_ISSUER": "https://login.example.org/",
    "GEO_MCP_AUDIENCE": "geo-mcp",
    "GEO_MCP_AUTHORIZATION_SERVER": "https://login.example.org/",
    "GEO_MCP_ALLOWED_SUBJECTS": "user-1,user-2,user-1",
    "GEO_MCP_ALLOWED_HOSTS": "geo.example.org",
}


def test_settings_normalize_values_and_apply_primary_defaults() -> None:
    settings = McpSettings.from_env(VALID)

    assert MCP_PATH == "/mcp"
    assert settings.allowed_subjects == frozenset({"user-1", "user-2"})
    assert settings.allowed_hosts == ("geo.example.org",)
    assert settings.allowed_origins == ()
    assert settings.public_base_url == "https://geo.example.org"
    assert settings.mcp_url == "https://geo.example.org/mcp"
    assert settings.elasticsearch.active_model_key == "gemini_embedding_2_3072_v1"
    assert settings.rate_per_second == 5.0
    assert settings.burst_capacity == 10
    assert settings.required_scope == "geo:read"


def test_settings_strip_and_deduplicate_lists_without_reordering() -> None:
    settings = McpSettings.from_env(
        VALID
        | {
            "GEO_MCP_ALLOWED_SUBJECTS": " user-2 , user-1 , user-2 ",
            "GEO_MCP_ALLOWED_HOSTS": "geo.example.org:443, geo.example.org",
            "GEO_MCP_ALLOWED_ORIGINS": (
                "https://app.example.org, https://app.example.org"
            ),
        }
    )

    assert settings.allowed_subjects == frozenset({"user-1", "user-2"})
    assert settings.allowed_hosts == ("geo.example.org:443", "geo.example.org")
    assert settings.allowed_origins == ("https://app.example.org",)


def test_settings_repr_never_contains_credentials_or_subjects() -> None:
    rendered = repr(McpSettings.from_env(VALID))

    assert VALID["ELASTICSEARCH_API_KEY"] not in rendered
    assert "user-1" not in rendered
    assert "user-2" not in rendered
    assert "GEO_PG_DSN" not in rendered


def test_settings_respect_explicit_active_model() -> None:
    settings = McpSettings.from_env(
        VALID | {"ELASTICSEARCH_ACTIVE_MODEL": "bge_small_v15"}
    )

    assert settings.elasticsearch.active_model_key == "bge_small_v15"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GEO_MCP_PUBLIC_BASE_URL", "http://geo.example.org"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://geo.example.org/prefix"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://user@geo.example.org"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://geo.example.org?tenant=1"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://geo.example.org/#fragment"),
        ("GEO_MCP_JWKS_URI", "http://login.example.org/jwks"),
        ("GEO_MCP_AUTHORIZATION_SERVER", "http://login.example.org"),
        ("GEO_MCP_AUTHORIZATION_SERVER", "https://login.example.org/oauth"),
        ("GEO_MCP_ALLOWED_SUBJECTS", ""),
        ("GEO_MCP_ALLOWED_SUBJECTS", "user-1,,user-2"),
        ("GEO_MCP_ALLOWED_HOSTS", "*"),
        ("GEO_MCP_ALLOWED_HOSTS", "geo.example.org,*"),
        ("GEO_MCP_ALLOWED_ORIGINS", "*"),
        ("GEO_MCP_ALLOWED_ORIGINS", "http://app.example.org"),
        ("GEO_MCP_RATE_PER_SECOND", "0"),
        ("GEO_MCP_RATE_PER_SECOND", "not-a-number"),
        ("GEO_MCP_BURST_CAPACITY", "0"),
        ("GEO_MCP_BURST_CAPACITY", "1.5"),
        ("ELASTICSEARCH_ACTIVE_MODEL", "not-configured"),
    ],
)
def test_settings_fail_closed(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        McpSettings.from_env(VALID | {key: value})


def test_public_hostname_must_be_in_allowed_hosts() -> None:
    with pytest.raises(ValueError, match="public hostname"):
        McpSettings.from_env(
            VALID | {"GEO_MCP_ALLOWED_HOSTS": "internal.example.org"}
        )


def test_allowed_host_port_does_not_break_public_hostname_match() -> None:
    settings = McpSettings.from_env(
        VALID
        | {
            "GEO_MCP_PUBLIC_BASE_URL": "https://geo.example.org:8443/",
            "GEO_MCP_ALLOWED_HOSTS": "geo.example.org:8443",
        }
    )

    assert settings.public_base_url == "https://geo.example.org:8443"
    assert settings.mcp_url == "https://geo.example.org:8443/mcp"


@pytest.mark.parametrize(
    "missing",
    [
        "ELASTICSEARCH_URL",
        "ELASTICSEARCH_API_KEY",
        "GEO_MCP_PUBLIC_BASE_URL",
        "GEO_MCP_JWKS_URI",
        "GEO_MCP_ISSUER",
        "GEO_MCP_AUDIENCE",
        "GEO_MCP_AUTHORIZATION_SERVER",
        "GEO_MCP_ALLOWED_SUBJECTS",
        "GEO_MCP_ALLOWED_HOSTS",
    ],
)
def test_required_setting_must_be_present_and_nonblank(missing: str) -> None:
    env = dict(VALID)
    del env[missing]
    with pytest.raises(ValueError):
        McpSettings.from_env(env)

    with pytest.raises(ValueError):
        McpSettings.from_env(VALID | {missing: " "})
