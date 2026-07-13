from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from geo_index.marketing_api import EutilsGeoComparison
from geo_index.marketing_api import app as production_app
from geo_index.marketing_api import create_app
from geo_index.mcp_models import (
    DatasetSummary,
    FacetResultOutput,
    SearchDatasetsOutput,
    SearchFiltersInput,
)
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


def test_native_geo_comparison_bounds_results_and_checks_full_membership() -> None:
    class Eutils:
        def esearch(self, db: str, term: str) -> SimpleNamespace:
            assert (db, term) == ("gds", "immune cells AND gse[ETYP]")
            return SimpleNamespace(count=2)

        def esummary_page(
            self, db: str, search: object, retstart: int, retmax: int
        ) -> dict[str, object]:
            assert (db, retstart, retmax) == ("gds", 0, 3)
            return {
                "uids": ["1", "2"],
                "1": {
                    "entrytype": "GSE",
                    "accession": "GSE123",
                    "title": "Immune study",
                    "gdstype": "Expression profiling",
                    "taxon": "Homo sapiens",
                    "summary": "summary",
                },
                "2": {"entrytype": "GPL", "accession": "GPL1"},
            }

        def esearch_ids(self, db: str, term: str, retmax: int) -> list[str]:
            assert db == "gds"
            assert "GSE123[ACCN]" in term
            assert retmax == 11
            return ["200000123"]

        def close(self) -> None:
            pass

    comparison = EutilsGeoComparison(Eutils())  # type: ignore[arg-type]
    native = comparison.keyword_search("immune cells", 1)

    assert native["count"] == 2
    assert native["results"] == [
        {
            "gse": "GSE123",
            "title": "Immune study",
            "study_type": "Expression profiling",
            "taxon": "Homo sapiens",
            "summary": "summary",
        }
    ]
    assert comparison.membership("immune cells", ["GSE123"]) == {"GSE123": True}


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
            )
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
    )


class _DemoService(_Service):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []

    def search_datasets(self, **kwargs: object) -> SearchDatasetsOutput:
        self.calls.append(kwargs)
        return _search_output()


class _Geo:
    def keyword_search(self, query: str, limit: int) -> dict[str, object]:
        return {
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

    def membership(
        self, query: str, accessions: list[str]
    ) -> dict[str, bool] | None:
        return {accession: False for accession in accessions}


class _FailingGeo(_Geo):
    def keyword_search(self, query: str, limit: int) -> dict[str, object]:
        raise RuntimeError("NCBI unavailable")


def test_app_opens_shared_search_service_and_reports_health() -> None:
    service = _Service()
    app = create_app(service_factory=lambda: service)

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "search": "ready"}
        assert service.opened

    assert service.closed


def test_demo_search_uses_shared_mcp_service_and_returns_comparison() -> None:
    service = _DemoService()
    app = create_app(service_factory=lambda: service, geo_factory=_Geo)

    with TestClient(app) as client:
        response = client.get(
            "/api/demo/search",
            params={
                "q": " transcriptomes of individual cells ",
                "mode": "hybrid",
                "limit": "5",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "transcriptomes of individual cells"
    assert payload["geo"]["results"][0]["gse"] == "GSE999"
    assert payload["geoscope"]["results"][0]["gse"] == "GSE123"
    assert payload["membership"] == {"GSE123": False}
    assert service.calls == [
        {
            "query": "transcriptomes of individual cells",
            "filters": SearchFilters(),
            "mode": "hybrid",
            "limit": 5,
        }
    ]


def test_demo_search_keeps_geoscope_results_when_ncbi_is_unavailable() -> None:
    app = create_app(service_factory=_DemoService, geo_factory=_FailingGeo)

    with TestClient(app) as client:
        response = client.get(
            "/api/demo/search",
            params={"q": "immune cells", "mode": "hybrid", "limit": "5"},
        )

    assert response.status_code == 200
    assert response.json()["geoscope"]["results"][0]["gse"] == "GSE123"
    assert response.json()["geo"] == {
        "count": None,
        "results": [],
        "error": "Native GEO search is temporarily unavailable.",
    }
    assert response.json()["membership"] is None


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
