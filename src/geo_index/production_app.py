"""Combined production ASGI app for App Platform."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.routing import Route
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .marketing_api import install_marketing_routes
from .mcp_search_service import McpSearchService
from .mcp_server import McpService, create_mcp_http_mount
from .mcp_settings import McpSettings


_FRONTEND_DIST = Path(__file__).parents[2] / "frontend" / "dist"


class _McpRootEndpoint:
    """Forward the exact public MCP path to a sub-app mounted at its root."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        forwarded_scope = dict(scope)
        forwarded_scope["root_path"] = f"{scope.get('root_path', '')}/mcp"
        forwarded_scope["path"] = "/"
        forwarded_scope["raw_path"] = b"/"
        await self.app(forwarded_scope, receive, send)


def create_app(
    settings: McpSettings | None = None,
    service: McpService | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    settings = settings or McpSettings.from_env(os.environ)
    service = service or McpSearchService.from_settings(settings)
    mcp_mount = create_mcp_http_mount(settings, service, path="/")
    if not callable(mcp_mount.lifespan):
        raise RuntimeError("FastMCP HTTP lifespan is unavailable")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_mount.lifespan(app):
            yield

    app = FastAPI(title="GEOscope", lifespan=lifespan)
    app.state.search_service = service

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            await asyncio.to_thread(service.ping)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    app.router.routes.append(
        Route(
            "/mcp",
            endpoint=_McpRootEndpoint(mcp_mount.app),
            methods=["POST", "DELETE"],
        )
    )
    app.mount("/mcp", mcp_mount.app)
    resolved_static = static_dir
    if resolved_static is None and (_FRONTEND_DIST / "index.html").is_file():
        resolved_static = _FRONTEND_DIST
    install_marketing_routes(app, static_dir=resolved_static)
    return app
