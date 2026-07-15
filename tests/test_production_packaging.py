from __future__ import annotations

import tomllib
from pathlib import Path


def test_production_dockerfile_builds_frontend_and_runs_combined_app() -> None:
    text = Path("Dockerfile").read_text()
    assert "pnpm install --frozen-lockfile" in text
    assert "pnpm build" in text
    assert "COPY --from=frontend" in text
    assert "geo_index.production_app:create_app" in text
    assert '"--factory"' in text
    assert '"--no-access-log"' in text


def test_heavy_packages_are_not_default_project_dependencies() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text())
    defaults = "\n".join(data["project"]["dependencies"])
    for forbidden in ("prefect", "sentence-transformers", "psycopg", "pgvector"):
        assert forbidden not in defaults
