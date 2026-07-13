from __future__ import annotations

import os

import pytest
from fastmcp import Client
from fastmcp.server.auth import AccessToken

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_search_service import McpSearchService
from geo_index.mcp_server import create_mcp
from geo_index.mcp_settings import McpSettings


pytestmark = pytest.mark.skipif(
    os.environ.get("GEO_TEST_ELASTIC") != "1",
    reason="set GEO_TEST_ELASTIC=1 for the live Elasticsearch MCP smoke",
)


def _settings() -> McpSettings:
    return McpSettings(
        elasticsearch=ElasticsearchSettings.from_env(),
        public_base_url="https://geo.test",
        jwks_uri="https://issuer.test/jwks",
        issuer="https://issuer.test/",
        audience="geo-mcp-test",
        authorization_server="https://issuer.test",
        allowed_subjects=frozenset({"live-smoke"}),
        allowed_hosts=("geo.test",),
        allowed_origins=(),
        rate_per_second=1000,
        burst_capacity=100,
    )


async def test_live_elasticsearch_serves_all_three_mcp_tools(monkeypatch) -> None:
    settings = _settings()
    service = McpSearchService.from_settings(settings)
    token = AccessToken(
        token="offline-live-smoke-token",
        client_id="offline-live-smoke-client",
        scopes=["geo:read"],
        claims={"sub": "live-smoke"},
    )
    monkeypatch.setattr(
        "fastmcp.server.middleware.authorization.get_access_token", lambda: token
    )
    mcp = create_mcp(settings, service)

    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == [
            "facet_values", "get_dataset", "search_datasets"
        ]
        facet = await client.call_tool(
            "facet_values", {"field": "organism_ids", "limit": 5}
        )
        assert facet.is_error is False
        mode = os.environ.get("GEO_MCP_SMOKE_MODE", "bm25")
        assert mode in {"bm25", "dense", "hybrid"}
        result = await client.call_tool(
            "search_datasets",
            {"query": "cancer", "mode": mode, "limit": 3},
        )
        assert result.is_error is False
        assert result.structured_content["embedding_variant"] == (
            None if mode == "bm25" else settings.elasticsearch.active_model_key
        )
        rows = result.structured_content["results"]
        assert rows
        exact = await client.call_tool(
            "search_datasets",
            {"query": f"  {rows[0]['gse'].lower()}  ", "mode": mode, "limit": 3},
        )
        assert exact.is_error is False
        assert [row["gse"] for row in exact.structured_content["results"]] == [
            rows[0]["gse"]
        ]
        assert exact.structured_content["provenance"]["exact_accession"] is True
        assert exact.structured_content["provenance"]["rerank_attempted"] is False
        assert exact.structured_content["embedding_variant"] is None
        detail = await client.call_tool("get_dataset", {"gse": rows[0]["gse"]})
        assert detail.is_error is False
        assert detail.structured_content["found"] is True
