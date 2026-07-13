from __future__ import annotations

import pytest

from geo_index.mcp_settings import MCP_PATH, McpSettings


VALID = {
    "ELASTICSEARCH_URL": "http://10.124.0.2:9200",
    "ELASTICSEARCH_USERNAME": "elastic",
    "ELASTICSEARCH_PASSWORD": "secret-password",
    "GEO_MCP_PUBLIC_BASE_URL": "https://geoscope.kevinformatics.com",
    "GEO_MCP_ALLOWED_HOSTS": "geoscope.kevinformatics.com",
}


def test_public_settings_apply_safe_admission_defaults() -> None:
    settings = McpSettings.from_env(VALID)

    assert MCP_PATH == "/mcp"
    assert settings.mcp_url == "https://geoscope.kevinformatics.com/mcp"
    assert settings.allowed_hosts == ("geoscope.kevinformatics.com",)
    assert settings.allowed_origins == ()
    assert settings.elasticsearch.active_model_key == "gemini_embedding_2_3072_v1"
    assert settings.rate_per_second == 1.0
    assert settings.burst_capacity == 5
    assert settings.max_concurrent_requests == 4
    assert not hasattr(settings, "jwks_uri")
    assert not hasattr(settings, "allowed_subjects")


def test_settings_strip_and_deduplicate_lists_without_reordering() -> None:
    settings = McpSettings.from_env(
        VALID
        | {
            "GEO_MCP_ALLOWED_HOSTS": (
                "geoscope.kevinformatics.com:443, geoscope.kevinformatics.com"
            ),
            "GEO_MCP_ALLOWED_ORIGINS": (
                "https://client.example.org, https://client.example.org"
            ),
        }
    )

    assert settings.allowed_hosts == (
        "geoscope.kevinformatics.com:443",
        "geoscope.kevinformatics.com",
    )
    assert settings.allowed_origins == ("https://client.example.org",)


def test_settings_repr_never_contains_elasticsearch_credentials() -> None:
    rendered = repr(McpSettings.from_env(VALID))

    assert VALID["ELASTICSEARCH_PASSWORD"] not in rendered
    assert "GEO_MCP_JWKS_URI" not in rendered
    assert "GEO_MCP_ALLOWED_SUBJECTS" not in rendered


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GEO_MCP_PUBLIC_BASE_URL", "http://geo.example.org"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://geo.example.org/prefix"),
        ("GEO_MCP_ALLOWED_HOSTS", "*"),
        ("GEO_MCP_ALLOWED_ORIGINS", "*"),
        ("GEO_MCP_ALLOWED_ORIGINS", "http://app.example.org"),
        ("GEO_MCP_RATE_PER_SECOND", "0"),
        ("GEO_MCP_RATE_PER_SECOND", "not-a-number"),
        ("GEO_MCP_BURST_CAPACITY", "0"),
        ("GEO_MCP_BURST_CAPACITY", "1.5"),
        ("GEO_MCP_MAX_CONCURRENT_REQUESTS", "0"),
        ("GEO_MCP_MAX_CONCURRENT_REQUESTS", "1.5"),
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


@pytest.mark.parametrize(
    "missing",
    [
        "ELASTICSEARCH_URL",
        "ELASTICSEARCH_USERNAME",
        "ELASTICSEARCH_PASSWORD",
        "GEO_MCP_PUBLIC_BASE_URL",
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
