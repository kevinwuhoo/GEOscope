from __future__ import annotations

import pytest

from geo_index.mcp_settings import MCP_PATH, McpSettings, SearchQualitySettings


VALID = {
    "ELASTICSEARCH_URL": "http://10.124.0.2:9200",
    "ELASTICSEARCH_USERNAME": "elastic",
    "ELASTICSEARCH_PASSWORD": "secret-password",
    "GEO_MCP_PUBLIC_BASE_URL": "https://geoscope.kevinformatics.com",
    "GEO_MCP_ALLOWED_HOSTS": "geoscope.kevinformatics.com",
}


def test_search_quality_defaults_are_bounded_and_disabled() -> None:
    quality = SearchQualitySettings.from_env({})

    assert quality.rerank_enabled is False
    assert quality.anthropic_api_key is None
    assert quality.rerank_model == "claude-sonnet-5"
    assert quality.reasoning_effort == "low"
    assert quality.thinking == "disabled"
    assert quality.candidate_limit == 40
    assert quality.rerank_timeout_seconds == 8.0
    assert quality.ncbi_timeout_seconds == 5.0


def test_enabled_reranker_requires_anthropic_key() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        SearchQualitySettings.from_env({"GEO_RERANK_ENABLED": "true"})


def test_enabled_sonnet_settings_are_fixed_and_secret_is_redacted() -> None:
    quality = SearchQualitySettings.from_env(
        {
            "GEO_RERANK_ENABLED": "true",
            "ANTHROPIC_API_KEY": " secret ",
            "GEO_RERANK_MODEL": "claude-sonnet-5",
            "GEO_RERANK_EFFORT": "low",
            "GEO_RERANK_THINKING": "disabled",
        }
    )
    assert quality.anthropic_api_key == "secret"
    assert quality.rerank_model == "claude-sonnet-5"
    assert quality.reasoning_effort == "low"
    assert quality.thinking == "disabled"
    assert "secret" not in repr(quality)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GEO_RERANK_MODEL", "gpt-5.6-luna"),
        ("GEO_RERANK_EFFORT", "medium"),
        ("GEO_RERANK_THINKING", "enabled"),
    ],
)
def test_sonnet_settings_reject_unapproved_values(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        SearchQualitySettings.from_env({key: value})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GEO_RERANK_ENABLED", "yes"),
        ("GEO_RERANK_CANDIDATE_LIMIT", "9"),
        ("GEO_RERANK_CANDIDATE_LIMIT", "101"),
        ("GEO_RERANK_TIMEOUT_SECONDS", "0"),
        ("GEO_NCBI_TIMEOUT_SECONDS", "nan"),
    ],
)
def test_search_quality_settings_fail_closed(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        SearchQualitySettings.from_env({key: value})


def test_mcp_settings_wires_search_quality_from_env() -> None:
    settings = McpSettings.from_env(
        VALID
        | {
            "GEO_RERANK_ENABLED": "true",
            "ANTHROPIC_API_KEY": " nested-secret ",
            "GEO_RERANK_MODEL": "claude-sonnet-5",
            "GEO_RERANK_EFFORT": "low",
            "GEO_RERANK_THINKING": "disabled",
            "GEO_RERANK_CANDIDATE_LIMIT": "64",
            "GEO_RERANK_TIMEOUT_SECONDS": "3.5",
            "GEO_NCBI_TIMEOUT_SECONDS": "2.25",
        }
    )

    assert settings.search_quality.rerank_enabled is True
    assert settings.search_quality.anthropic_api_key == "nested-secret"
    assert settings.search_quality.rerank_model == "claude-sonnet-5"
    assert settings.search_quality.reasoning_effort == "low"
    assert settings.search_quality.thinking == "disabled"
    assert settings.search_quality.candidate_limit == 64
    assert settings.search_quality.rerank_timeout_seconds == 3.5
    assert settings.search_quality.ncbi_timeout_seconds == 2.25


def test_public_settings_apply_safe_admission_defaults() -> None:
    settings = McpSettings.from_env(VALID)

    assert MCP_PATH == "/mcp"
    assert settings.mcp_url == "https://geoscope.kevinformatics.com/mcp"
    assert settings.allowed_hosts == ("geoscope.kevinformatics.com",)
    assert settings.allowed_origins == ()
    assert settings.elasticsearch.active_model_key == "gemini_embedding_2_3072_v1"
    assert settings.rate_per_second == 100.0
    assert settings.burst_capacity == 100
    assert settings.max_concurrent_requests == 20
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
