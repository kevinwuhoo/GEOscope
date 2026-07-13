from __future__ import annotations

import asyncio
import json
import logging
import warnings

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_models import (
    FacetResultOutput,
    FacetValuesOutput,
    GetDatasetOutput,
    SearchDatasetsOutput,
    SearchFiltersInput,
)
from geo_index.mcp_server import (
    MAX_REQUEST_BODY_BYTES,
    RequestBodyLimitMiddleware,
    create_app,
)
from geo_index.mcp_settings import McpSettings
from geo_index.search_models import FACET_FIELDS


SENTINEL_QUERY = "SENTINEL-RAW-QUERY-9f7c"
SENTINEL_SECRET = "SENTINEL-ELASTIC-SECRET-314f"


def _settings() -> McpSettings:
    return McpSettings(
        elasticsearch=ElasticsearchSettings(
            url="https://elastic.internal:9200", api_key=SENTINEL_SECRET,
            active_model_key="gemini_embedding_2_3072_v1",
        ),
        public_base_url="https://geo.test",
        allowed_hosts=("geo.test",), allowed_origins=("https://client.test",),
        rate_per_second=1000, burst_capacity=100, max_concurrent_requests=100,
    )


class FakeService:
    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.ping_calls = 0
        self.search_calls: list[dict[str, object]] = []
        self.ping_error: BaseException | None = None
        self.search_error: BaseException | None = None

    def open(self): self.open_calls += 1
    def close(self): self.close_calls += 1
    def ping(self):
        self.ping_calls += 1
        if self.ping_error: raise self.ping_error

    def search_datasets(self, **kwargs):
        self.search_calls.append(kwargs)
        if self.search_error: raise self.search_error
        return SearchDatasetsOutput(
            query=kwargs["query"], filters=SearchFiltersInput(**kwargs["filters"].as_dict()),
            mode=kwargs["mode"], limit=kwargs["limit"], retrieval_version="bm25-v1",
            embedding_variant=None, results=[],
            facets={
                field: FacetResultOutput(
                    field=field, buckets=[], scope="candidate_pool", candidate_count=0
                ) for field in FACET_FIELDS
            },
        )

    def get_dataset(self, gse):
        return GetDatasetOutput(found=False, dataset=None)

    def facet_values(self, **kwargs):
        scoped = kwargs["query"] is not None
        return FacetValuesOutput(
            field=kwargs["field"], buckets=[],
            scope="candidate_pool" if scoped else "all_matches",
            candidate_count=0 if scoped else None,
            retrieval_version="bm25-v1" if scoped else "facet-all-matches-v1",
            embedding_variant=None,
        )


def _initialize_body() -> dict[str, object]:
    return {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "http-test", "version": "1"}},
    }


def _headers(*, host: str = "geo.test", origin: str | None = None):
    values = {"Host": host, "Accept": "application/json, text/event-stream"}
    if origin: values["Origin"] = origin
    return values


def test_raw_asgi_anonymous_guards_health_readiness_and_log_redaction(caplog) -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=Warning)
        from starlette.testclient import TestClient

    caplog.set_level(logging.DEBUG)
    settings = _settings()
    service = FakeService()
    app = create_app(settings=settings, service=service)

    with TestClient(app, base_url=settings.public_base_url) as client:
        accepted = client.post(
            "/mcp", json=_initialize_body(),
            headers=_headers(origin="https://client.test"),
        )
        assert accepted.status_code == 200
        assert "www-authenticate" not in accepted.headers
        assert "mcp-session-id" not in accepted.headers

        oversized = client.post(
            "/mcp", content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
            headers={**_headers(), "Content-Type": "application/json"},
        )
        assert oversized.status_code == 413
        assert oversized.json() == {"error": "request_too_large"}
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}
        service.ping_error = RuntimeError("private readiness detail")
        unavailable = client.get("/readyz")
        assert unavailable.status_code == 503
        assert unavailable.json() == {"status": "unavailable"}
        assert client.get("/healthz", headers={"Host": "attacker.test"}).status_code == 421
        assert client.get(
            "/healthz", headers={"Host": "geo.test", "Origin": "https://attacker.test"}
        ).status_code == 403

        call = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "search_datasets", "arguments": {"query": SENTINEL_QUERY}},
        }
        assert client.post("/mcp", json=call, headers=_headers()).status_code == 200
        assert service.search_calls[-1]["query"] == SENTINEL_QUERY
        service.search_error = RuntimeError("SENTINEL-PRIVATE-ERROR")
        assert client.post("/mcp", json=call | {"id": 3}, headers=_headers()).status_code == 200

    assert service.open_calls == 1
    assert service.close_calls == 1
    assert SENTINEL_SECRET not in caplog.text
    assert SENTINEL_QUERY not in caplog.text
    assert "SENTINEL-PRIVATE-ERROR" not in caplog.text


def _post_scope() -> dict[str, object]:
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "https", "path": "/mcp", "raw_path": b"/mcp",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 1234),
        "server": ("geo.test", 443),
    }


async def test_body_limit_deadlines_stalled_segmented_body() -> None:
    messages: list[dict[str, object]] = []
    first = True
    never = asyncio.Event()

    async def inner(scope, receive, send):
        await receive()
        await receive()

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"{", "more_body": True}
        await never.wait()

    async def send(message): messages.append(message)

    middleware = RequestBodyLimitMiddleware(
        inner, max_body_bytes=MAX_REQUEST_BODY_BYTES, body_read_timeout_seconds=0.01
    )
    await asyncio.wait_for(middleware(_post_scope(), receive, send), timeout=0.2)
    assert messages[0]["status"] == 408
    assert json.loads(messages[1]["body"]) == {"error": "request_timeout"}
