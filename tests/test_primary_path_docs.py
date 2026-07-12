from __future__ import annotations

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
