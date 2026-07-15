from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_haiku_rollout_deployment_contract() -> None:
    errors: list[str] = []
    required_environment = (
        "ANTHROPIC_API_KEY=",
        "GEO_RERANK_MODEL=claude-haiku-4-5",
        "GEO_RERANK_THINKING=disabled",
        "GEO_RERANK_TIMEOUT_SECONDS=30",
    )
    for relative_path in (
        "deploy/geo-mcp.env.example",
        "deploy/app-platform.env.example",
    ):
        text = (ROOT / relative_path).read_text()
        errors.extend(
            f"{relative_path}: missing {setting}"
            for setting in required_environment
            if setting not in text
        )
        if "OPENAI_API_KEY" in text:
            errors.append(f"{relative_path}: stale OPENAI_API_KEY")
        if "gpt-5.6-luna" in text:
            errors.append(f"{relative_path}: stale gpt-5.6-luna")
        if "GEO_RERANK_REASONING_EFFORT" in text:
            errors.append(f"{relative_path}: stale GEO_RERANK_REASONING_EFFORT")

    app_spec = (ROOT / ".do" / "app.yaml.tmpl").read_text()
    required_app_mappings = (
        'key: ANTHROPIC_API_KEY\n        value: "${ANTHROPIC_API_KEY}"',
        'key: GEO_RERANK_MODEL\n        value: "${GEO_RERANK_MODEL}"',
        'key: GEO_RERANK_THINKING\n        value: "${GEO_RERANK_THINKING}"',
        'key: GEO_RERANK_TIMEOUT_SECONDS\n        value: "${GEO_RERANK_TIMEOUT_SECONDS}"',
    )
    errors.extend(
        f".do/app.yaml.tmpl: missing {mapping.splitlines()[0]}"
        for mapping in required_app_mappings
        if mapping not in app_spec
    )
    if "OPENAI_API_KEY" in app_spec:
        errors.append(".do/app.yaml.tmpl: stale OPENAI_API_KEY")
    if "GEO_RERANK_REASONING_EFFORT" in app_spec:
        errors.append(".do/app.yaml.tmpl: stale GEO_RERANK_REASONING_EFFORT")

    assert not errors, "\n".join(errors)


def test_deployment_runbook_documents_enabled_runtime_degradation() -> None:
    runbook = (ROOT / "docs" / "deployment" / "digitalocean.md").read_text()
    normalized = " ".join(runbook.split())
    for phrase in (
        "The shared reranker request timeout defaults to 30 seconds via "
        "`GEO_RERANK_TIMEOUT_SECONDS=30`; keep the environment override available "
        "for operational tuning",
        "NCBI timeout or failure degrades to Elasticsearch-only candidate generation",
        "Anthropic timeout, refusal, truncation, malformed response, or invalid "
        "output fails open to deterministic pre-rerank Elasticsearch-first union ordering",
        "Elasticsearch failure remains fatal",
        "Provider response text and API keys are never exposed",
    ):
        assert phrase in normalized, phrase


def test_docker_packages_one_worker_combined_production_app() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "geo_index.production_app:create_app" in dockerfile
    assert (
        'CMD ["uvicorn", "geo_index.production_app:create_app", "--factory", '
        '"--host", "0.0.0.0", "--port", "8000", "--workers", "1", '
        '"--no-access-log"]'
        in dockerfile
    )
    assert "GEO_PG_DSN" not in dockerfile
    assert "BGE_QUERY_REVISION" not in dockerfile


def test_environment_example_is_elasticsearch_only_and_contains_no_real_secrets() -> None:
    example = (ROOT / "deploy" / "geo-mcp.env.example").read_text()
    required = {
        "ANTHROPIC_API_KEY",
        "ELASTICSEARCH_URL",
        "ELASTICSEARCH_USERNAME",
        "ELASTICSEARCH_PASSWORD",
        "ELASTICSEARCH_ACTIVE_MODEL",
        "GEMINI_API_KEY",
        "GEO_MCP_PUBLIC_BASE_URL",
        "GEO_MCP_ALLOWED_HOSTS",
        "GEO_MCP_MAX_CONCURRENT_REQUESTS",
        "GEO_LOG_EXPORT_ENABLED",
        "GEO_LOG_EXPORT_URL",
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
    assert "GEO_MCP_RATE_PER_SECOND=100" in example
    assert "GEO_MCP_BURST_CAPACITY=100" in example
    assert "GEO_MCP_MAX_CONCURRENT_REQUESTS=20" in example
    assert "GEO_LOG_EXPORT_ENABLED=false" in example
    assert "GEO_LOG_EXPORT_URL=" in example
    assert "GEO_RERANK_MODEL=claude-haiku-4-5" in example
    assert "GEO_RERANK_THINKING=disabled" in example
    assert "GEO_RERANK_TIMEOUT_SECONDS=30" in example
    assert "OPENAI_API_KEY" not in example
    assert "set-in-app-platform" in example
    assert "SENTINEL" not in example

    app_spec = (ROOT / ".do" / "app.yaml.tmpl").read_text()
    assert 'key: GEO_MCP_RATE_PER_SECOND\n        value: "100"' in app_spec
    assert 'key: GEO_MCP_BURST_CAPACITY\n        value: "100"' in app_spec
    assert (
        'key: GEO_MCP_MAX_CONCURRENT_REQUESTS\n        value: "20"'
        in app_spec
    )
    assert 'key: ANTHROPIC_API_KEY\n        value: "${ANTHROPIC_API_KEY}"' in app_spec
    assert 'key: GEO_RERANK_MODEL\n        value: "${GEO_RERANK_MODEL}"' in app_spec
    assert (
        'key: GEO_RERANK_THINKING\n        value: "${GEO_RERANK_THINKING}"'
        in app_spec
    )
    assert "OPENAI_API_KEY" not in app_spec


def test_dockerignore_excludes_local_credentials_and_corpus() -> None:
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env*" in ignored
    assert "data" in ignored
    assert ".worktrees" in ignored
