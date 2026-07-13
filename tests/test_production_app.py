from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_models import GetDatasetOutput
from geo_index.mcp_settings import McpSettings, SearchQualitySettings
from geo_index.production_app import create_app


class FakeService:
    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.ping_calls = 0
        self.marketing_started: threading.Event | None = None
        self.dataset_started: threading.Event | None = None
        self.request_release: threading.Event | None = None

    @property
    def is_open(self) -> bool:
        return self.open_calls == 1 and self.close_calls == 0

    def open(self) -> None:
        self.open_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def ping(self) -> None:
        self.ping_calls += 1

    def search_execution(self, **kwargs: object) -> SimpleNamespace:
        if self.marketing_started is not None:
            self.marketing_started.set()
            assert self.request_release is not None
            assert self.request_release.wait(timeout=5)
        return SimpleNamespace(
            native=SimpleNamespace(count=0, candidates=(), error=None),
            output=SimpleNamespace(
                results=(),
                model_dump=lambda *, mode: {"results": []},
            ),
        )

    def get_dataset(self, gse: str) -> GetDatasetOutput:
        if self.dataset_started is not None:
            self.dataset_started.set()
            assert self.request_release is not None
            assert self.request_release.wait(timeout=5)
        return GetDatasetOutput(found=False, dataset=None)


def _settings() -> McpSettings:
    return McpSettings(
        elasticsearch=ElasticsearchSettings(
            url="http://10.124.0.2:9200",
            username="elastic",
            password="secret",
            active_model_key="gemini_embedding_2_3072_v1",
        ),
        public_base_url="https://geoscope.kevinformatics.com",
        allowed_hosts=("geoscope.kevinformatics.com",),
        allowed_origins=(),
        rate_per_second=1000,
        burst_capacity=100,
        max_concurrent_requests=100,
    )


def _initialize_body() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "production-test", "version": "1"},
        },
    }


def _mcp_headers() -> dict[str, str]:
    return {"Accept": "application/json, text/event-stream"}


def _mcp_initialize(client: TestClient):
    return client.post(
        "/mcp",
        json=_initialize_body(),
        headers=_mcp_headers(),
    )


def _mcp_get_dataset(client: TestClient):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_dataset",
                "arguments": {"gse": "GSE123"},
            },
        },
        headers=_mcp_headers(),
    )


def _limited_settings(
    *, burst_capacity: int, max_concurrent_requests: int
) -> McpSettings:
    settings = _settings()
    return McpSettings(
        elasticsearch=settings.elasticsearch,
        public_base_url=settings.public_base_url,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
        search_quality=settings.search_quality,
        rate_per_second=1e-9,
        burst_capacity=burst_capacity,
        max_concurrent_requests=max_concurrent_requests,
    )


@pytest.mark.parametrize("first_transport", ["marketing", "mcp"])
def test_production_rate_budget_is_shared_by_marketing_and_mcp(
    first_transport: str,
) -> None:
    app = create_app(
        settings=_limited_settings(
            burst_capacity=1, max_concurrent_requests=10
        ),
        service=FakeService(),
    )

    with TestClient(
        app,
        base_url="https://geoscope.kevinformatics.com",
    ) as client:
        if first_transport == "marketing":
            first = client.get("/api/demo/search", params={"q": "mouse"})
            second = _mcp_initialize(client)
        else:
            first = _mcp_initialize(client)
            second = client.get(
                "/api/demo/search", params={"q": "mouse"}
            )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"error": "rate_limited"}


def test_production_mcp_is_charged_once_and_health_stays_unlimited() -> None:
    app = create_app(
        settings=_limited_settings(
            burst_capacity=2, max_concurrent_requests=10
        ),
        service=FakeService(),
    )

    with TestClient(
        app,
        base_url="https://geoscope.kevinformatics.com",
    ) as client:
        assert _mcp_initialize(client).status_code == 200
        assert client.get(
            "/api/demo/search", params={"q": "mouse"}
        ).status_code == 200
        assert client.get(
            "/api/demo/search", params={"q": "mouse"}
        ).status_code == 429
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/health").status_code == 200


def test_production_mcp_receives_concurrency_429_while_marketing_is_active() -> None:
    service = FakeService()
    service.marketing_started = threading.Event()
    service.request_release = threading.Event()
    app = create_app(
        settings=_limited_settings(
            burst_capacity=10, max_concurrent_requests=1
        ),
        service=service,
    )
    responses: list[object] = []

    with TestClient(
        app,
        base_url="https://geoscope.kevinformatics.com",
    ) as client:
        request = threading.Thread(
            target=lambda: responses.append(
                client.get("/api/demo/search", params={"q": "mouse"})
            )
        )
        request.start()
        assert service.marketing_started.wait(timeout=2)
        rejected = _mcp_initialize(client)
        service.request_release.set()
        request.join(timeout=2)

    assert rejected.status_code == 429
    assert rejected.json() == {"error": "rate_limited"}
    assert not request.is_alive()
    assert responses[0].status_code == 200


def test_production_marketing_receives_concurrency_429_while_mcp_is_active() -> None:
    service = FakeService()
    service.dataset_started = threading.Event()
    service.request_release = threading.Event()
    app = create_app(
        settings=_limited_settings(
            burst_capacity=10, max_concurrent_requests=1
        ),
        service=service,
    )
    responses: list[object] = []

    with TestClient(
        app,
        base_url="https://geoscope.kevinformatics.com",
    ) as client:
        request = threading.Thread(
            target=lambda: responses.append(_mcp_get_dataset(client))
        )
        request.start()
        assert service.dataset_started.wait(timeout=2)
        rejected = client.get(
            "/api/demo/search", params={"q": "mouse"}
        )
        service.request_release.set()
        request.join(timeout=2)

    assert rejected.status_code == 429
    assert rejected.json() == {"error": "rate_limited"}
    assert not request.is_alive()
    assert responses[0].status_code == 200


def test_one_app_serves_health_frontend_and_anonymous_mcp(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>GEOscope</main>")
    service = FakeService()
    app = create_app(settings=_settings(), service=service, static_dir=dist)

    with TestClient(
        app,
        base_url="https://geoscope.kevinformatics.com",
        follow_redirects=False,
    ) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}
        assert client.get("/").text == "<main>GEOscope</main>"
        initialized = client.post(
            "/mcp",
            json=_initialize_body(),
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert initialized.status_code == 200

        initialized_with_slash = client.post(
            "/mcp/",
            json=_initialize_body(),
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert initialized_with_slash.status_code == 200

    assert service.open_calls == 1
    assert service.close_calls == 1


def test_default_factory_constructs_the_shared_quality_aware_service(
    monkeypatch,
) -> None:
    constructed: dict[str, object] = {}

    def from_settings(settings: McpSettings) -> FakeService:
        service = FakeService()
        service.quality = settings.search_quality
        constructed["service"] = service
        return service

    monkeypatch.setenv("ELASTICSEARCH_URL", "http://10.124.0.2:9200")
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "elastic")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "secret")
    monkeypatch.setenv(
        "GEO_MCP_PUBLIC_BASE_URL", "https://geoscope.kevinformatics.com"
    )
    monkeypatch.setenv(
        "GEO_MCP_ALLOWED_HOSTS", "geoscope.kevinformatics.com"
    )
    monkeypatch.setenv("GEO_RERANK_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(
        "geo_index.production_app.McpSearchService.from_settings", from_settings
    )

    app = create_app()

    assert app.state.search_service is constructed["service"]
    assert app.state.search_service.quality == SearchQualitySettings(
        anthropic_api_key="test-anthropic-key",
        rerank_enabled=True,
    )
