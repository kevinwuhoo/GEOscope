from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from fastmcp import Client

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.marketing_api import app as production_app
from geo_index.marketing_api import create_app
from geo_index.mcp_models import (
    DatasetSummary,
    FacetResultOutput,
    SearchDatasetsOutput,
    SearchFiltersInput,
    SearchLatencyOutput,
    SearchProvenanceOutput,
)
from geo_index.mcp_search_service import SearchExecution
from geo_index.mcp_server import create_mcp
from geo_index.mcp_settings import McpSettings, SearchQualitySettings
from geo_index.ncbi_search import NativeSearchResult
from geo_index.search_candidates import SearchCandidate
from geo_index.search_models import FACET_FIELDS, SearchFilters


class _Service:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    @property
    def is_open(self) -> bool:
        return self.opened and not self.closed

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def test_module_exposes_production_fastapi_app_without_connecting_at_import() -> None:
    assert production_app.title == "GEOscope"


def _search_output() -> SearchDatasetsOutput:
    return SearchDatasetsOutput(
        query="transcriptomes of individual cells",
        filters=SearchFiltersInput(),
        limit=5,
        retrieval_version="geo-series-v1:gemini:embedding:hybrid",
        embedding_variant="gemini_embedding_2_3072_v1",
        results=[
            DatasetSummary(
                gse="GSE123",
                rank=1,
                score=0.91,
                title="Chromium single-cell study",
                snippet="Profiles individual immune cells using 10x Chromium.",
                study_type="Expression profiling by high throughput sequencing",
                n_samples=12,
                pubmed_id=12345678,
                organism_ids=["NCBITaxon:9606"],
                organism_status="mapped",
                sex_ids=[],
                sex_status=None,
                assay_categories=["transcriptomics"],
                assay_labels=["scRNA-seq"],
                assay_status="mapped",
                source="elasticsearch",
                retrieval_score=0.91,
                original_rank=1,
            ),
            DatasetSummary(
                gse="GSE999",
                rank=2,
                score=None,
                title="Literal keyword match",
                snippet="A literal query match.",
                study_type="Expression profiling",
                n_samples=None,
                pubmed_id=None,
                organism_ids=["NCBITaxon:9606"],
                organism_status="mapped",
                sex_ids=[],
                sex_status="unavailable",
                assay_categories=["transcriptomics"],
                assay_labels=["expression profiling"],
                assay_status="mapped",
                source="ncbi",
                retrieval_score=None,
                original_rank=None,
            ),
        ],
        facets={
            field: FacetResultOutput(
                field=field,
                buckets=[],
                scope="candidate_pool",
                candidate_count=1,
            )
            for field in FACET_FIELDS
        },
        provenance=SearchProvenanceOutput(
            exact_accession=False,
            elasticsearch_candidates=1,
            ncbi_candidates=1,
            merged_candidates=2,
            rerank_attempted=False,
            rerank_applied=False,
            rerank_model=None,
            rerank_reasoning_effort=None,
            rerank_input_tokens=0,
            rerank_output_tokens=0,
            latency=SearchLatencyOutput(
                elasticsearch_ms=5,
                ncbi_ms=7,
                reranker_ms=0,
            ),
            degradation=[],
        ),
    )


def _local_candidate(gse: str, rank: int) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title="Chromium single-cell study",
        snippet="Profiles individual immune cells using 10x Chromium.",
        study_type="Expression profiling by high throughput sequencing",
        n_samples=12,
        pubmed_id=12345678,
        organism_ids=("NCBITaxon:9606",),
        organism_status="mapped",
        sex_ids=(),
        sex_status=None,
        assay_categories=("transcriptomics",),
        assay_labels=("scRNA-seq",),
        assay_status="mapped",
        source="elasticsearch",
        retrieval_score=0.91,
        original_rank=rank,
        native_rank=None,
    )


def _native_candidate(gse: str, rank: int) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title="Literal keyword match",
        snippet="A literal query match.",
        study_type="Expression profiling",
        n_samples=None,
        pubmed_id=None,
        organism_ids=("NCBITaxon:9606",),
        organism_status="mapped",
        sex_ids=(),
        sex_status="unavailable",
        assay_categories=("transcriptomics",),
        assay_labels=("expression profiling",),
        assay_status="mapped",
        source="ncbi",
        retrieval_score=None,
        original_rank=None,
        native_rank=rank,
        taxon="Homo sapiens",
    )


def _execution(
    native: NativeSearchResult | None = None,
) -> SearchExecution:
    return SearchExecution(
        output=_search_output(),
        native=native
        or NativeSearchResult(
            count=1,
            candidates=(_native_candidate("GSE999", 1),),
        ),
        candidates=(
            _local_candidate("GSE123", 1),
            _native_candidate("GSE999", 1),
        ),
    )


class _DemoService(_Service):
    def __init__(self, execution: SearchExecution | None = None) -> None:
        super().__init__()
        self.execution = execution or _execution()
        self.execution_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []

    def search_execution(self, **kwargs: object) -> SearchExecution:
        self.execution_calls.append(kwargs)
        return self.execution

    def search_datasets(self, **kwargs: object) -> SearchDatasetsOutput:
        self.search_calls.append(kwargs)
        return self.execution.output


def _mcp_settings() -> McpSettings:
    return McpSettings(
        elasticsearch=ElasticsearchSettings(
            url="https://elastic.internal:9200",
            api_key="secret",
            active_model_key="gemini_embedding_2_3072_v1",
        ),
        public_base_url="https://geo.example.org",
        allowed_hosts=("geo.example.org",),
        allowed_origins=(),
        rate_per_second=1000,
        burst_capacity=100,
        max_concurrent_requests=100,
    )


def test_app_opens_shared_search_service_and_reports_health() -> None:
    service = _Service()
    app = create_app(service_factory=lambda: service)

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "search": "ready"}
        assert service.opened

    assert service.closed


async def test_demo_search_uses_shared_mcp_service_and_returns_comparison() -> None:
    service = _DemoService()
    execution = service.execution
    app = create_app(service_factory=lambda: service)

    with TestClient(app) as client:
        response = client.get(
            "/api/demo/search",
            params={
                "q": " transcriptomes of individual cells ",
                "limit": "5",
            },
        )

    async with Client(create_mcp(_mcp_settings(), service)) as mcp_client:
        mcp_response = await mcp_client.call_tool(
            "search_datasets",
            {"query": "transcriptomes of individual cells"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "transcriptomes of individual cells"
    assert "mode" not in payload
    assert "mode" not in payload["geoscope"]
    assert payload["geo"] == {
        "count": 1,
        "results": [
            {
                "gse": "GSE999",
                "title": "Literal keyword match",
                "study_type": "Expression profiling",
                "taxon": "Homo sapiens",
                "summary": "A literal query match.",
            }
        ],
    }
    assert payload["geoscope"] == execution.output.model_dump(mode="json")
    marketing_order = [result["gse"] for result in payload["geoscope"]["results"]]
    mcp_order = [
        result["gse"] for result in mcp_response.structured_content["results"]
    ]
    assert marketing_order == mcp_order == ["GSE123", "GSE999"]
    assert (
        payload["geoscope"]["provenance"]
        == mcp_response.structured_content["provenance"]
    )
    assert payload["membership"] == {"GSE123": False, "GSE999": True}
    assert service.execution_calls == [
        {
            "query": "transcriptomes of individual cells",
            "filters": SearchFilters(),
            "limit": 5,
        }
    ]
    assert len(service.search_calls) == 1


def test_demo_search_ignores_legacy_mode_query_parameter() -> None:
    service = _DemoService()
    app = create_app(service_factory=lambda: service)

    with TestClient(app) as client:
        response = client.get(
            "/api/demo/search",
            params={"q": "immune cells", "mode": "bm25", "limit": "5"},
        )

    assert response.status_code == 200
    assert "mode" not in response.json()
    assert service.execution_calls[0] == {
        "query": "immune cells",
        "filters": SearchFilters(),
        "limit": 5,
    }


def test_demo_search_keeps_geoscope_results_when_ncbi_is_unavailable() -> None:
    execution = _execution(NativeSearchResult.unavailable("ncbi_timeout"))
    app = create_app(service_factory=lambda: _DemoService(execution))

    with TestClient(app) as client:
        response = client.get(
            "/api/demo/search",
            params={"q": "immune cells", "limit": "5"},
        )

    assert response.status_code == 200
    assert response.json()["geoscope"]["results"][0]["gse"] == "GSE123"
    assert response.json()["geo"] == {
        "count": None,
        "results": [],
        "error": "Native GEO search is temporarily unavailable.",
    }
    assert response.json()["membership"] is None


def test_demo_search_accepts_the_shared_explicit_limit_range() -> None:
    service = _DemoService()
    app = create_app(service_factory=lambda: service)

    with TestClient(app) as client:
        assert client.get(
            "/api/demo/search", params={"q": "immune cells", "limit": "20"}
        ).status_code == 200
        assert client.get(
            "/api/demo/search", params={"q": "immune cells", "limit": "21"}
        ).status_code == 422

    assert service.execution_calls[0]["limit"] == 20


def test_standalone_factory_enables_the_same_luna_quality_settings(
    monkeypatch,
) -> None:
    service = _DemoService()
    constructed: dict[str, Any] = {}

    def service_factory(**kwargs: object) -> _DemoService:
        constructed.update(kwargs)
        return service

    monkeypatch.setenv("ELASTICSEARCH_URL", "http://10.124.0.2:9200")
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "elastic")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "secret")
    monkeypatch.setenv("GEO_RERANK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr("geo_index.marketing_api.McpSearchService", service_factory)

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert constructed["quality"] == SearchQualitySettings(
        openai_api_key="test-openai-key",
        rerank_enabled=True,
    )


def test_production_assets_and_frontend_routes_do_not_shadow_api(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<main>GEOscope</main>")
    (assets / "app.js").write_text("window.GEOscope = true")
    app = create_app(service_factory=_DemoService, static_dir=dist)

    with TestClient(app) as client:
        assert client.get("/").text == "<main>GEOscope</main>"
        assert client.get("/demo").text == "<main>GEOscope</main>"
        assert "window.GEOscope" in client.get("/assets/app.js").text
        assert client.get("/api/not-a-route").status_code == 404
        assert client.get("/mcp/not-a-route").status_code == 404
        assert client.get("/healthz/not-a-route").status_code == 404
