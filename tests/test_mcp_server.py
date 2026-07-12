from __future__ import annotations

import json
from typing import Any

import fastmcp
import pytest
from fastmcp import Client
from fastmcp.server.auth import AccessToken

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_models import (
    DatasetDetail,
    DatasetSummary,
    FacetBucketOutput,
    FacetResultOutput,
    FacetValuesOutput,
    GetDatasetOutput,
    SearchDatasetsInput,
    SearchDatasetsOutput,
    SearchFiltersInput,
)
from geo_index.mcp_search_service import UnknownFilterValueError
from geo_index.mcp_server import (
    INSTRUCTIONS,
    MAX_REQUEST_BODY_BYTES,
    create_app,
    create_mcp,
)
from geo_index.mcp_settings import MCP_PATH, McpSettings
from geo_index.search_models import FACET_FIELDS, SearchFilters


def _settings() -> McpSettings:
    return McpSettings(
        elasticsearch=ElasticsearchSettings(
            url="https://elastic.internal:9200",
            api_key="secret",
            active_model_key="gemini_embedding_2_3072_v1",
        ),
        public_base_url="https://geo.example.org",
        jwks_uri="https://issuer.example.org/jwks",
        issuer="https://issuer.example.org/",
        audience="geo-mcp",
        authorization_server="https://issuer.example.org",
        allowed_subjects=frozenset({"invited-user"}),
        allowed_hosts=("geo.example.org",),
        allowed_origins=("https://client.example.org",),
        rate_per_second=1000,
        burst_capacity=100,
    )


def _facet(field: str) -> FacetResultOutput:
    return FacetResultOutput(
        field=field,
        buckets=[FacetBucketOutput(value="value", label="Label", count=1)],
        scope="candidate_pool",
        candidate_count=1,
    )


def _summary() -> DatasetSummary:
    return DatasetSummary(
        rank=1, gse="GSE123", score=0.9, title="Study title",
        snippet="Summary snippet", study_type="RNA-seq", n_samples=10,
        pubmed_id=12345678, organism_ids=["NCBITaxon:9606"],
        organism_status="mapped", sex_ids=[], sex_status="absent",
        assay_categories=["transcriptomics"], assay_labels=["scRNA-seq"],
        assay_status="mapped", truncated_fields=[],
    )


def _detail() -> DatasetDetail:
    return DatasetDetail(
        gse="GSE123", title="Study title", summary="Full summary",
        overall_design="Overall design", study_type="RNA-seq", n_samples=10,
        pubmed_id=12345678, organism_ids=["NCBITaxon:9606"],
        organism_status="mapped", sex_ids=[], sex_status="absent",
        assay_categories=["transcriptomics"], assay_labels=["scRNA-seq"],
        assay_status="mapped",
        geo_url="https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123",
        pubmed_url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
        truncated_fields=[],
    )


class FakeService:
    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.ping_calls = 0
        self.search_calls: list[dict[str, Any]] = []
        self.detail_calls: list[str] = []
        self.facet_calls: list[dict[str, Any]] = []
        self.search_error: BaseException | None = None
        self.ping_error: BaseException | None = None

    def open(self) -> None:
        self.open_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def ping(self) -> None:
        self.ping_calls += 1
        if self.ping_error:
            raise self.ping_error

    def search_datasets(self, **kwargs: Any) -> SearchDatasetsOutput:
        self.search_calls.append(kwargs)
        if self.search_error:
            raise self.search_error
        return SearchDatasetsOutput(
            query=kwargs["query"],
            filters=SearchFiltersInput(**kwargs["filters"].as_dict()),
            mode=kwargs["mode"],
            limit=kwargs["limit"],
            retrieval_version="geo-series-v1:gemini:embedding_gemini_3072:hybrid",
            embedding_variant="gemini_embedding_2_3072_v1",
            results=[_summary()],
            facets={field: _facet(field) for field in FACET_FIELDS},
        )

    def get_dataset(self, gse: str) -> GetDatasetOutput:
        self.detail_calls.append(gse)
        return GetDatasetOutput(found=True, dataset=_detail())

    def facet_values(self, **kwargs: Any) -> FacetValuesOutput:
        self.facet_calls.append(kwargs)
        scoped = kwargs["query"] is not None
        return FacetValuesOutput(
            field=kwargs["field"],
            buckets=[FacetBucketOutput(value="NCBITaxon:9606", label="Human", count=100)],
            scope="candidate_pool" if scoped else "all_matches",
            candidate_count=100 if scoped else None,
            retrieval_version="bm25-v1" if scoped else "facet-all-matches-v1",
            embedding_variant=None,
        )


@pytest.fixture
def fake_service() -> FakeService:
    return FakeService()


@pytest.fixture
def mcp(fake_service: FakeService, monkeypatch):
    token = AccessToken(
        token="offline-test-token", client_id="offline-test-client",
        scopes=["geo:read"], claims={"sub": "invited-user"},
    )
    monkeypatch.setattr(
        "fastmcp.server.middleware.authorization.get_access_token", lambda: token
    )
    return create_mcp(_settings(), fake_service)


async def test_exact_tool_list_annotations_and_stable_schema(mcp) -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools] == [
        "facet_values", "get_dataset", "search_datasets"
    ]
    for tool in tools:
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
        schema = str(tool.inputSchema)
        assert "embedding_variant" not in schema
        assert "deep" not in schema
        assert "k0" not in schema
        assert "facet_pool" not in schema


async def test_lifespan_opens_and_closes_injected_service(
    mcp, fake_service: FakeService
) -> None:
    async with Client(mcp):
        assert fake_service.open_calls == 1
        assert fake_service.close_calls == 0
    assert fake_service.close_calls == 1


async def test_all_tools_delegate_normalized_inputs(
    mcp, fake_service: FakeService
) -> None:
    async with Client(mcp) as client:
        search = await client.call_tool(
            "search_datasets",
            {"query": "single cell RNA", "filters": {"organism_ids": ["NCBITaxon:9606"]}, "limit": 5},
        )
        detail = await client.call_tool("get_dataset", {"gse": " gse123 "})
        facet = await client.call_tool(
            "facet_values",
            {"field": "organism_ids", "query": "   ", "filters": {"sex_ids": ["PATO:0000383"]}},
        )

    assert search.structured_content["results"][0]["gse"] == "GSE123"
    assert fake_service.search_calls[0]["filters"] == SearchFilters(
        organism_ids=("NCBITaxon:9606",)
    )
    assert detail.structured_content["found"] is True
    assert fake_service.detail_calls == ["GSE123"]
    assert facet.structured_content["scope"] == "all_matches"
    assert fake_service.facet_calls[0]["query"] is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "x", "limit": "5"},
        {"query": "x", "filters": {"invented": ["x"]}},
        {"query": " ", "limit": 5},
    ],
)
async def test_validation_fails_before_service(
    mcp, fake_service: FakeService, arguments: dict[str, object]
) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("search_datasets", arguments, raise_on_error=False)
    assert result.is_error is True
    assert fake_service.search_calls == []


async def test_unknown_filter_error_is_concise_and_nonrevealing(
    mcp, fake_service: FakeService
) -> None:
    fake_service.search_error = UnknownFilterValueError("private SENTINEL detail")
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_datasets", {"query": "x"}, raise_on_error=False
        )
    rendered = " ".join(block.text for block in result.content if hasattr(block, "text"))
    assert result.is_error is True
    assert "facet_values" in rendered
    assert "SENTINEL" not in rendered


def test_server_uses_pinned_fastmcp_and_instructions(mcp) -> None:
    assert fastmcp.__version__ == "3.4.4"
    assert "series aggregates" in INSTRUCTIONS
    assert mcp.instructions == INSTRUCTIONS


def test_create_app_uses_elasticsearch_adapter_and_http_guards(monkeypatch) -> None:
    settings = _settings()
    sentinel_service = FakeService()
    sentinel_app = object()
    calls: dict[str, object] = {}

    class FakeMcp:
        def http_app(self, **kwargs: object):
            calls.update(kwargs)
            return sentinel_app

    monkeypatch.setattr(
        "geo_index.mcp_server.McpSearchService.from_settings",
        lambda received: sentinel_service,
    )
    monkeypatch.setattr("geo_index.mcp_server.create_mcp", lambda *args, **kwargs: FakeMcp())
    app = create_app(settings=settings, auth_provider=object())

    assert app.app.app is sentinel_app
    assert app.app.max_body_bytes == MAX_REQUEST_BODY_BYTES
    assert calls == {
        "path": MCP_PATH,
        "stateless_http": True,
        "host_origin_protection": True,
        "allowed_hosts": ["geo.example.org"],
        "allowed_origins": ["https://client.example.org"],
    }
    assert sentinel_service.open_calls == 0


def test_body_limit_accommodates_largest_valid_unicode_search() -> None:
    filters = SearchFiltersInput(
        assay_categories=[f"{i:02d}" + "🧬" * 254 for i in range(20)],
        assay_labels=[f"{i:02d}" + "🧪" * 254 for i in range(20)],
    )
    request = SearchDatasetsInput(query="🧬" * 1000, filters=filters)
    encoded = json.dumps({"arguments": request.model_dump()}).encode()
    assert 64 * 1024 < len(encoded) <= MAX_REQUEST_BODY_BYTES
