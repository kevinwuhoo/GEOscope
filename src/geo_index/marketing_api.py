"""Browser-safe FastAPI application for the GEOscope marketing demo."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .mcp_models import SearchDatasetsOutput
from .eutils import EutilsClient
from .elasticsearch_config import ElasticsearchSettings
from .mcp_search_service import McpSearchService
from .search_models import SearchFilters


class SearchService(Protocol):
    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def search_datasets(
        self, *, query: str, filters: SearchFilters, mode: str, limit: int
    ) -> SearchDatasetsOutput: ...


class GeoComparison(Protocol):
    def keyword_search(self, query: str, limit: int) -> dict[str, object]: ...

    def membership(
        self, query: str, accessions: list[str]
    ) -> dict[str, bool] | None: ...


class EutilsGeoComparison:
    """Polite native-GEO comparison used only by the public demo."""

    def __init__(self, client: EutilsClient | None = None) -> None:
        self._client = client or EutilsClient()
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def keyword_search(self, query: str, limit: int) -> dict[str, object]:
        with self._lock:
            search = self._client.esearch("gds", f"{query} AND gse[ETYP]")
            if search.count == 0:
                return {"count": 0, "results": []}
            page = self._client.esummary_page(
                "gds", search, 0, min(limit * 3, 100)
            )
        results: list[dict[str, object]] = []
        for uid in page.get("uids", []):
            raw = page.get(uid, {})
            if str(raw.get("entrytype", "")).upper() != "GSE":
                continue
            results.append(
                {
                    "gse": str(raw.get("accession") or ""),
                    "title": raw.get("title"),
                    "study_type": raw.get("gdstype"),
                    "taxon": raw.get("taxon"),
                    "summary": str(raw.get("summary") or "")[:500] or None,
                }
            )
            if len(results) >= limit:
                break
        return {"count": search.count, "results": results}

    def membership(
        self, query: str, accessions: list[str]
    ) -> dict[str, bool] | None:
        valid = [
            accession
            for accession in accessions
            if accession.startswith("GSE") and accession[3:].isdigit()
        ]
        if not valid:
            return {}
        term = f"({query}) AND (" + " OR ".join(
            f"{accession}[ACCN]" for accession in valid
        ) + ")"
        with self._lock:
            ids = set(
                self._client.esearch_ids("gds", term, retmax=len(valid) + 10)
            )
        return {
            accession: str(200_000_000 + int(accession[3:])) in ids
            for accession in valid
        }


def _default_service_factory() -> McpSearchService:
    return McpSearchService(
        elasticsearch=ElasticsearchSettings.from_env(os.environ)
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
        mode: Literal["hybrid", "bm25", "dense"] = "hybrid",
        limit: int = Query(default=8, ge=1, le=20),
    ) -> dict[str, object]:
        query = q.strip()
        if not query:
            raise HTTPException(status_code=422, detail="query must not be blank")
        service: SearchService = app.state.search_service
        geoscope = await asyncio.to_thread(
            service.search_datasets,
            query=query,
            filters=SearchFilters(),
            mode=mode,
            limit=limit,
        )
        geo: GeoComparison | None = app.state.geo
        if geo is None:
            native: dict[str, object] = {
                "count": None,
                "results": [],
                "error": "Native GEO comparison is not configured.",
            }
            membership = None
        else:
            try:
                native = await asyncio.to_thread(geo.keyword_search, query, limit)
                membership = await asyncio.to_thread(
                    geo.membership,
                    query,
                    [result.gse for result in geoscope.results],
                )
            except Exception:
                native = {
                    "count": None,
                    "results": [],
                    "error": "Native GEO search is temporarily unavailable.",
                }
                membership = None
        return {
            "query": query,
            "mode": mode,
            "geo": native,
            "geoscope": geoscope.model_dump(mode="json"),
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
    geo_factory: Callable[[], GeoComparison] | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    resolved_service_factory = service_factory or _default_service_factory
    resolved_geo_factory = geo_factory or EutilsGeoComparison

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = resolved_service_factory()
        service.open()
        app.state.search_service = service
        geo = resolved_geo_factory()
        app.state.geo = geo
        try:
            yield
        finally:
            geo_close = getattr(geo, "close", None)
            if callable(geo_close):
                geo_close()
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
