from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]
CURRENT_PAGES = (
    "wiki/Home.md",
    "wiki/00-Overview.md",
    "wiki/20-Architecture-Overview.md",
    "wiki/21-Ingestion-Pipeline.md",
    "wiki/23-Search-and-Retrieval.md",
    "wiki/24-Faceted-Search.md",
    "wiki/40-Roadmap.md",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_documents_executable_elasticsearch_gemini_primary_path() -> None:
    readme = _read("README.md")
    required = (
        "Elasticsearch primary path",
        "gemini_embedding_2_3072_v1",
        "uv run geo-soft-etl --allow-paid-gemini",
        "uv run geo-search",
        "uv run geo-web",
        "Historical PostgreSQL baseline",
    )
    for phrase in required:
        assert phrase in readme
    assert "export ELASTICSEARCH_PASSWORD=..." not in readme
    assert readme.index("Elasticsearch primary path") < readme.index(
        "Historical PostgreSQL baseline"
    )
    environment = _read(".env.elasticsearch.example")
    assert "ELASTICSEARCH_ACTIVE_MODEL=gemini_embedding_2_3072_v1" in environment


def test_current_wiki_pages_do_not_claim_postgres_or_bge_is_primary() -> None:
    forbidden = (
        "One Postgres for everything",
        "One Postgres does it all",
        "Postgres-first",
        "Postgres, hybrid search",
        "write to Postgres",
    )
    for path in CURRENT_PAGES:
        text = _read(path)
        assert "Elasticsearch" in text, path
        for phrase in forbidden:
            assert phrase not in text, f"{path}: {phrase}"
    architecture = _read("wiki/20-Architecture-Overview.md")
    assert "gemini_embedding_2_3072_v1" in architecture
    assert "3,072" in architecture
    assert "Prefect" in architecture


def test_postgres_and_old_pipeline_pages_are_marked_historical() -> None:
    assert "Historical" in _read("wiki/26-Datastore-Postgres.md")[:800]
    prefect_plan = _read("wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md")
    assert "Superseded" in prefect_plan[:1000]
    assert "Elasticsearch" in prefect_plan[:1000]
    glossary = _read("wiki/90-Glossary.md")
    assert "Our chosen provider is" not in glossary
    assert "current 384-dimensional" not in glossary


def test_canonical_production_pipeline_is_gemini_only_and_operational() -> None:
    required = (
        "Canonical production pipeline",
        "geo-soft-etl",
        "gemini_embedding_2_3072_v1",
        "embedding_gemini_3072",
        "data/processed/series_records",
        "data/processed/embedding_artifacts",
        "data/processed/elasticsearch_load_report.json",
        "development/evaluation only",
    )
    for path in ("README.md", "wiki/57-Canonical-Production-Pipeline.md"):
        text = _read(path)
        for phrase in required:
            assert phrase in text, f"{path}: {phrase}"

    for path in (
        "wiki/Home.md",
        "wiki/00-Overview.md",
        "wiki/20-Architecture-Overview.md",
        "wiki/21-Ingestion-Pipeline.md",
    ):
        assert "[[57-Canonical-Production-Pipeline]]" in _read(path), path


def test_current_search_docs_pin_the_reranker_timeout_default() -> None:
    expected = (
        "The shared reranker request timeout defaults to 30 seconds via "
        "`GEO_RERANK_TIMEOUT_SECONDS=30`; keep the environment override available "
        "for operational tuning"
    )

    for path in ("README.md", "docs/deployment/digitalocean.md"):
        normalized = " ".join(_read(path).split())
        assert expected in normalized, path


def test_unified_search_rollout_is_documented_and_configurable() -> None:
    required_environment = (
        "ANTHROPIC_API_KEY=",
        "GEO_RERANK_ENABLED=false",
        "GEO_RERANK_MODEL=claude-sonnet-5",
        "GEO_RERANK_EFFORT=low",
        "GEO_RERANK_THINKING=disabled",
        "GEO_RERANK_CANDIDATE_LIMIT=40",
        "GEO_RERANK_TIMEOUT_SECONDS=30",
        "GEO_NCBI_TIMEOUT_SECONDS=5",
    )
    for path in ("deploy/geo-mcp.env.example", "deploy/app-platform.env.example"):
        environment = _read(path)
        for setting in required_environment:
            assert setting in environment, f"{path}: {setting}"
        assert "OPENAI_API_KEY" not in environment, path

    deployment = _read("docs/deployment/digitalocean.md")
    for phrase in (
        "GEO_RERANK_ENABLED=false",
        "GEO_TEST_ANTHROPIC=1",
        "geo-search-eval",
        "partial live records",
        "baseline versus Sonnet",
        "up to 100 Elasticsearch and 100 NCBI candidates",
    ):
        assert phrase in deployment, phrase
    normalized_deployment = " ".join(deployment.split())
    assert (
        "A production source deploy is incomplete until public provenance shows "
        "Sonnet applied"
        in normalized_deployment
    )
    assert (
        "never commit the generated spec or include the key in reports"
        in normalized_deployment
    )
    assert "never write it to the generated spec" not in deployment

    readme = _read("README.md")
    normalized_readme = " ".join(readme.split())
    for phrase in (
        "up to 100 Elasticsearch candidates",
        "up to 100 native NCBI GEO candidates",
        "Claude Sonnet 5",
        "query understanding",
        "Anthropic Structured Outputs",
    ):
        assert phrase in readme, phrase

    for path, text in (
        ("README.md", normalized_readme),
        ("docs/deployment/digitalocean.md", normalized_deployment),
    ):
        for phrase in (
            "10 results by default",
            "callers may request from 1 through 50",
            "Elasticsearch admits up to 100 candidates",
            "NCBI retrieves up to its configured page maximum of 100",
            "deduplicated union of up to 200 candidates reaches the reranker",
        ):
            assert phrase in text, f"{path}: {phrase}"

    provider_documentation = (
        "README.md",
        "docs/deployment/digitalocean.md",
    )
    smoke_queries = (
        "mouse skeletal muscle gene expression after endurance exercise in insulin resistance",
        "human breast cancer transcriptomics before and after neoadjuvant chemotherapy with treatment response data",
        "GSE310900",
    )
    official_references = (
        "https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5",
        "https://platform.claude.com/docs/en/build-with-claude/effort",
        "https://platform.claude.com/docs/en/build-with-claude/structured-outputs",
        "https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python",
    )
    for path in provider_documentation:
        text = _read(path)
        for phrase in (
            "Claude Sonnet 5",
            "Anthropic Structured Outputs",
            "GEO_TEST_ANTHROPIC=1",
            *smoke_queries,
            *official_references,
        ):
            assert phrase in text, f"{path}: {phrase}"

    project = tomllib.loads(_read("pyproject.toml"))
    assert project["project"]["scripts"]["geo-search-eval"] == (
        "geo_index.search_eval:main"
    )
    markers = project["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("provider_integration:") for marker in markers)

    app_spec = _read(".do/app.yaml.tmpl")
    for key in (
        "ANTHROPIC_API_KEY",
        "GEO_RERANK_ENABLED",
        "GEO_RERANK_MODEL",
        "GEO_RERANK_EFFORT",
        "GEO_RERANK_THINKING",
        "GEO_RERANK_CANDIDATE_LIMIT",
        "GEO_RERANK_TIMEOUT_SECONDS",
        "GEO_NCBI_TIMEOUT_SECONDS",
    ):
        assert f"key: {key}" in app_spec, key
        assert f'value: "${{{key}}}"' in app_spec, key
    assert "OPENAI_API_KEY" not in app_spec

    internal_design = _read(
        "docs/superpowers/specs/2026-07-13-unified-ncbi-reranking-design.md"
    )
    supersession = " ".join(internal_design[:1_200].split())
    assert "Superseded" in supersession
    assert "Claude Sonnet 5 Reranker Migration Design" in supersession
    assert "historical Luna design" in supersession
    assert "Sonnet 5 migration is deferred until after the Luna baseline" in (
        internal_design
    )
    assert "Request up to 100 native candidates" in internal_design
    assert "not in this NCBI candidate set (up to 100)" in internal_design
    assert "top 20" not in internal_design

    implementation_plan = _read(
        "docs/superpowers/plans/2026-07-13-unified-ncbi-reranking.md"
    )
    supersession = " ".join(implementation_plan[:3_000].split())
    for phrase in (
        "supersedes every historical 20-candidate and top-20 instruction below",
        "10 results by default",
        "caller-selected `limit` from 1 through 50",
        "up to 100 Elasticsearch candidates",
        "up to 100 NCBI candidates",
        "maximum page size of 10,000 records",
        "operational cap is 100",
        "deduplicated, filter-eligible union of up to 200 candidates",
        "final results are sliced to the requested `limit`",
        "GPT-5.6 Luna",
        "Sonnet 5 migration remains a later follow-up",
    ):
        assert phrase in supersession, phrase
    assert implementation_plan.index("## Current contract") < (
        implementation_plan.index("## Global Constraints")
    )
    assert "Query up to 20 NCBI GEO Series candidates" in implementation_plan
