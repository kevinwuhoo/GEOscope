"""Browser-safe FastAPI application for the GEOscope marketing demo."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .elasticsearch_config import ElasticsearchSettings
from .mcp_search_service import McpSearchService, SearchExecution
from .mcp_settings import SearchQualitySettings
from .search_models import SearchFilters


class SearchService(Protocol):
    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def search_execution(
        self, *, query: str, filters: SearchFilters, limit: int
    ) -> SearchExecution: ...


def _default_service_factory() -> McpSearchService:
    return McpSearchService(
        elasticsearch=ElasticsearchSettings.from_env(os.environ),
        quality=SearchQualitySettings.from_env(os.environ),
    )


def install_marketing_routes(
    app: FastAPI,
    *,
    static_dir: Path | None = None,
) -> None:
    """Install browser routes after server routes such as /mcp and health."""

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        service: SearchService = app.state.search_service
        return {
            "status": "ok",
            "search": "ready" if service.is_open else "unavailable",
        }

    @app.get("/api/demo/search")
    async def demo_search(
        q: str = Query(min_length=1, max_length=1000),
        limit: int = Query(default=8, ge=1, le=20),
    ) -> dict[str, object]:
        query = q.strip()
        if not query:
            raise HTTPException(status_code=422, detail="query must not be blank")
        service: SearchService = app.state.search_service
        execution = await asyncio.to_thread(
            service.search_execution,
            query=query,
            filters=SearchFilters(),
            limit=limit,
        )
        native: dict[str, object] = {
            "count": execution.native.count,
            "results": [
                {
                    "gse": candidate.gse,
                    "title": candidate.title,
                    "study_type": candidate.study_type,
                    "taxon": candidate.taxon,
                    "summary": candidate.snippet,
                }
                for candidate in execution.native.candidates
            ],
        }
        if execution.native.error is not None:
            native["error"] = "Native GEO search is temporarily unavailable."
        membership = (
            None
            if execution.native.error is not None
            or execution.native.count is None
            else {
                result.gse: result.source in {"ncbi", "both"}
                for result in execution.output.results
            }
        )
        return {
            "query": query,
            "geo": native,
            "geoscope": execution.output.model_dump(mode="json"),
            "membership": membership,
        }

    if static_dir is None:
        return
    index_path = static_dir / "index.html"
    assets_path = static_dir / "assets"
    if assets_path.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    @app.get("/", include_in_schema=False)
    async def frontend_root() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/{frontend_path:path}", include_in_schema=False)
    async def frontend_fallback(frontend_path: str) -> FileResponse:
        reserved = ("api", "mcp", "healthz", "readyz")
        if any(
            frontend_path == prefix or frontend_path.startswith(f"{prefix}/")
            for prefix in reserved
        ):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(index_path)


def create_app(
    *,
    service_factory: Callable[[], SearchService] | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    resolved_service_factory = service_factory or _default_service_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = resolved_service_factory()
        service.open()
        app.state.search_service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="GEOscope", lifespan=lifespan)
    install_marketing_routes(app, static_dir=static_dir)
    return app


_FRONTEND_DIST = Path(__file__).parents[2] / "frontend" / "dist"
app = create_app(
    static_dir=_FRONTEND_DIST if (_FRONTEND_DIST / "index.html").is_file() else None
)


def main() -> None:
    import uvicorn

    uvicorn.run("geo_index.marketing_api:app", host="127.0.0.1", port=8000)
