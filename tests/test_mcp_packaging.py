from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_packages_one_worker_elasticsearch_mcp() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "geo_index.mcp_server:create_app" in dockerfile
    assert (
        'CMD ["uvicorn", "geo_index.mcp_server:create_app", "--factory", '
        '"--host", "0.0.0.0", "--port", "8000", "--workers", "1"]'
        in dockerfile
    )
    assert "GEO_PG_DSN" not in dockerfile
    assert "BGE_QUERY_REVISION" not in dockerfile


def test_environment_example_is_elasticsearch_only_and_contains_no_real_secrets() -> None:
    example = (ROOT / "deploy" / "geo-mcp.env.example").read_text()
    required = {
        "ELASTICSEARCH_URL",
        "ELASTICSEARCH_USERNAME",
        "ELASTICSEARCH_PASSWORD",
        "ELASTICSEARCH_ACTIVE_MODEL",
        "GEMINI_API_KEY",
        "GEO_MCP_PUBLIC_BASE_URL",
        "GEO_MCP_ALLOWED_HOSTS",
        "GEO_MCP_MAX_CONCURRENT_REQUESTS",
    }
    keys = {
        line.split("=", 1)[0]
        for line in example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert required <= keys
    assert "GEO_MCP_JWKS_URI" not in example
    assert "GEO_MCP_ALLOWED_SUBJECTS" not in example
    assert "GEO_PG_DSN" not in example
    assert "GEO_EMBEDDING_VARIANT" not in example
    assert "gemini_embedding_2_3072_v1" in example
    assert "set-in-app-platform" in example
    assert "SENTINEL" not in example


def test_dockerignore_excludes_local_credentials_and_corpus() -> None:
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env*" in ignored
    assert "data" in ignored
    assert ".worktrees" in ignored
