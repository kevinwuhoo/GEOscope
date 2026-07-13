"""Invite-only FastMCP application exposing the stable GEO retrieval tools."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol, cast

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .mcp_models import (
    FacetFieldName,
    FacetValuesInput,
    FacetValuesOutput,
    GetDatasetInput,
    GetDatasetOutput,
    SearchDatasetsInput,
    SearchDatasetsOutput,
    SearchFiltersInput,
    SearchMode,
)
from .mcp_search_service import McpSearchService, UnknownFilterValueError
from .mcp_settings import MCP_PATH, McpSettings


INSTRUCTIONS = (
    "Use search_datasets first for GEO series discovery, and expand assay "
    "synonyms in the client before searching. Use facet_values to select valid "
    "closed filter values and get_dataset to inspect one accession. Filters "
    "describe GSE series aggregates: values can occur on different samples "
    "within the same series."
)
MAX_REQUEST_BODY_BYTES = 256 * 1024
BODY_READ_TIMEOUT_SECONDS = 10.0
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=False,
)


class McpService(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def ping(self) -> None: ...
    def search_datasets(self, **kwargs: object) -> SearchDatasetsOutput: ...
    def get_dataset(self, gse: str) -> GetDatasetOutput: ...
    def facet_values(self, **kwargs: object) -> FacetValuesOutput: ...


class _SensitiveToolLogFilter(logging.Filter):
    """Drop framework records that contain tool inputs, outputs, or errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        template = str(record.msg)
        if record.name == "fastmcp.server.mixins.mcp_operations":
            return "Handler called: call_tool" not in template
        if record.name == "fastmcp.server.server":
            return not (
                "Invalid arguments for tool" in template
                or "Error calling tool" in template
            )
        if record.name == "sse_starlette.sse":
            return not template.startswith("chunk:")
        if record.name == "fastmcp.server.auth.providers.jwt":
            return False
        return True


_SENSITIVE_TOOL_LOG_FILTER = _SensitiveToolLogFilter()


class _RequestBodyTooLarge(BaseException):
    pass


class _RequestBodyTimedOut(BaseException):
    pass


class RequestBodyLimitMiddleware:
    """Lazily bound request-body bytes and total read time."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        body_read_timeout_seconds: float = BODY_READ_TIMEOUT_SECONDS,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.body_read_timeout_seconds = body_read_timeout_seconds

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", ()):
            if name.lower() != b"content-length":
                continue
            try:
                content_length = int(value)
            except ValueError:
                continue
            if content_length > self.max_body_bytes:
                await self._reject(
                    scope, receive, send,
                    status_code=413, error="request_too_large",
                )
                return

        body_bytes = 0
        deadline: float | None = None
        body_complete = False

        async def limited_receive() -> Message:
            nonlocal body_bytes, body_complete, deadline
            if body_complete:
                return await receive()
            loop = asyncio.get_running_loop()
            if deadline is None:
                deadline = loop.time() + self.body_read_timeout_seconds
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise _RequestBodyTimedOut
            try:
                async with asyncio.timeout(remaining):
                    message = await receive()
            except TimeoutError:
                raise _RequestBodyTimedOut from None
            if message["type"] == "http.request":
                body_bytes += len(message.get("body", b""))
                if body_bytes > self.max_body_bytes:
                    raise _RequestBodyTooLarge
                if not message.get("more_body", False):
                    body_complete = True
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(
                scope, receive, send,
                status_code=413, error="request_too_large",
            )
        except _RequestBodyTimedOut:
            await self._reject(
                scope, receive, send,
                status_code=408, error="request_timeout",
            )

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        error: str,
    ) -> None:
        response = JSONResponse({"error": error}, status_code=status_code)
        await response(scope, receive, send)


class HttpAdmissionMiddleware:
    """Bound all HTTP traffic before auth, readiness, or MCP work."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        rate_per_second: float,
        burst_capacity: int,
        max_concurrent_requests: int,
    ) -> None:
        self.app = app
        self.rate_per_second = rate_per_second
        self.burst_capacity = burst_capacity
        self.max_concurrent_requests = max_concurrent_requests
        self._tokens = float(burst_capacity)
        self._updated_at = time.monotonic()
        self._active_requests = 0
        self._lock = asyncio.Lock()

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not await self._try_admit():
            response = JSONResponse(
                {"error": "rate_limited"}, status_code=429,
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            async with self._lock:
                self._active_requests -= 1

    async def _try_admit(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self._updated_at)
            self._tokens = min(
                float(self.burst_capacity),
                self._tokens + elapsed * self.rate_per_second,
            )
            self._updated_at = now
            if (
                self._tokens < 1.0
                or self._active_requests >= self.max_concurrent_requests
            ):
                return False
            self._tokens -= 1.0
            self._active_requests += 1
            return True


def _configure_safe_logging() -> None:
    for name in (
        "fastmcp.server.mixins.mcp_operations",
        "fastmcp.server.server",
        "fastmcp.server.auth.providers.jwt",
        "sse_starlette.sse",
    ):
        logger = logging.getLogger(name)
        if _SENSITIVE_TOOL_LOG_FILTER not in logger.filters:
            logger.addFilter(_SENSITIVE_TOOL_LOG_FILTER)


def _service_from_context(ctx: Context) -> McpService:
    service = ctx.lifespan_context.get("service")
    if service is None:
        raise RuntimeError("service lifecycle is unavailable")
    return cast(McpService, service)


def create_mcp(
    settings: McpSettings,
    service: McpService,
) -> FastMCP:
    _configure_safe_logging()

    @lifespan
    async def app_lifespan(server):
        service.open()
        try:
            yield {"service": service}
        finally:
            service.close()

    mcp = FastMCP(
        "GEO Metadata Index",
        instructions=INSTRUCTIONS,
        lifespan=app_lifespan,
        strict_input_validation=True,
        mask_error_details=True,
        on_duplicate="error",
    )

    @mcp.tool(timeout=60.0, annotations=_READ_ONLY)
    def facet_values(
        field: FacetFieldName,
        ctx: Context,
        query: str | None = None,
        filters: SearchFiltersInput | None = None,
        mode: SearchMode = "hybrid",
        limit: int = 50,
    ) -> FacetValuesOutput:
        request = FacetValuesInput(
            field=field, query=query,
            filters=filters or SearchFiltersInput(), mode=mode, limit=limit,
        )
        try:
            response = _service_from_context(ctx).facet_values(
                field=request.field, query=request.query,
                filters=request.filters.to_domain(), mode=request.mode,
                limit=request.limit,
            )
        except UnknownFilterValueError as exc:
            raise ToolError(
                "Unknown filter value; call facet_values to list valid values."
            ) from exc
        return FacetValuesOutput.model_validate(response, strict=True)

    @mcp.tool(timeout=15.0, annotations=_READ_ONLY)
    def get_dataset(gse: str, ctx: Context) -> GetDatasetOutput:
        request = GetDatasetInput(gse=gse)
        response = _service_from_context(ctx).get_dataset(request.gse)
        return GetDatasetOutput.model_validate(response, strict=True)

    @mcp.tool(timeout=60.0, annotations=_READ_ONLY)
    def search_datasets(
        query: str,
        ctx: Context,
        filters: SearchFiltersInput | None = None,
        mode: SearchMode = "hybrid",
        limit: int = 15,
    ) -> SearchDatasetsOutput:
        request = SearchDatasetsInput(
            query=query, filters=filters or SearchFiltersInput(),
            mode=mode, limit=limit,
        )
        try:
            response = _service_from_context(ctx).search_datasets(
                query=request.query, filters=request.filters.to_domain(),
                mode=request.mode, limit=request.limit,
            )
        except UnknownFilterValueError as exc:
            raise ToolError(
                "Unknown filter value; call facet_values to list valid values."
            ) from exc
        return SearchDatasetsOutput.model_validate(response, strict=True)

    _register_health_routes(mcp, service)
    return mcp


def _register_health_routes(mcp: FastMCP, service: McpService) -> None:
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def readyz(request):
        try:
            await asyncio.to_thread(service.ping)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})


@dataclass(frozen=True)
class McpHttpMount:
    app: ASGIApp
    lifespan: object | None


def create_mcp_http_mount(
    settings: McpSettings,
    service: McpService,
    *,
    path: str,
) -> McpHttpMount:
    mcp = create_mcp(settings, service)
    base = mcp.http_app(
        path=path,
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )
    bounded = HttpAdmissionMiddleware(
        RequestBodyLimitMiddleware(
            base,
            max_body_bytes=MAX_REQUEST_BODY_BYTES,
        ),
        rate_per_second=settings.rate_per_second,
        burst_capacity=settings.burst_capacity,
        max_concurrent_requests=settings.max_concurrent_requests,
    )
    return McpHttpMount(app=bounded, lifespan=getattr(base, "lifespan", None))


def create_app(settings=None, service=None):
    settings = settings or McpSettings.from_env(os.environ)
    service = service or McpSearchService.from_settings(settings)
    return create_mcp_http_mount(settings, service, path=MCP_PATH).app
