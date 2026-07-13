from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_settings import McpSettings, SearchQualitySettings
from geo_index.production_app import create_app


class FakeService:
    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.ping_calls = 0

    @property
    def is_open(self) -> bool:
        return self.open_calls == 1 and self.close_calls == 0

    def open(self) -> None:
        self.open_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def ping(self) -> None:
        self.ping_calls += 1


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
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(
        "geo_index.production_app.McpSearchService.from_settings", from_settings
    )

    app = create_app()

    assert app.state.search_service is constructed["service"]
    assert app.state.search_service.quality == SearchQualitySettings(
        openai_api_key="test-openai-key",
        rerank_enabled=True,
    )
