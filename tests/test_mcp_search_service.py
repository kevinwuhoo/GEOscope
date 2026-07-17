from __future__ import annotations

import threading

import numpy as np
import pytest

import geo_index.mcp_search_service as mcp_search
from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_search_service import (
    McpSearchService,
    UnknownFilterValueError,
)
from geo_index.mcp_settings import SearchQualitySettings
from geo_index.ncbi_search import NativeSearchResult
from geo_index.reranker import (
    InvalidRerankOutputError,
    RerankInputTooLargeError,
    RerankRefusalError,
    RerankResult,
    RerankUsage,
)
from geo_index.search_candidates import SearchCandidate
from geo_index.search_models import (
    FACET_FIELDS,
    FacetBucket,
    FacetResult,
    SearchFilters,
    SearchProvenance,
    SearchResponse,
)


SETTINGS = ElasticsearchSettings(
    url="https://elastic.internal:9200",
    api_key="secret",
    active_model_key="gemini_embedding_2_3072_v1",
)

_UNSET = object()


def _document(gse: str = "GSE123", **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "gse": gse,
        "title": "Study title",
        "summary": "Study summary",
        "overall_design": "Study design",
        "type": "Expression profiling by high throughput sequencing",
        "n_samples": 12,
        "pubmed_ids": ["12345678"],
        "organism_ids": ["NCBITaxon:9606"],
        "organism_status": "mapped",
        "sex_ids": ["PATO:0000383"],
        "sex_status": "mapped",
        "assay_categories": ["transcriptomics"],
        "assay_labels": ["scRNA-seq"],
        "assay_status": "mapped",
    }
    value.update(overrides)
    return value


def _facets(scope: str = "candidate_pool") -> dict[str, FacetResult]:
    return {
        field: FacetResult(
            field=field,
            buckets=(FacetBucket(value="value", label="Value", count=3),),
            scope=scope,  # type: ignore[arg-type]
            candidate_count=4 if scope == "candidate_pool" else None,
        )
        for field in FACET_FIELDS
    }


def _response(*, mode: str = "bm25", hits: tuple[dict[str, object], ...] | None = None):
    return SearchResponse(
        hits=hits or ({"gse": "GSE123", "score": 0.75},),
        facets=_facets(),
        provenance=SearchProvenance(
            backend="elasticsearch",
            mapping_revision="geo-series-v1",
            active_model_key="gemini_embedding_2_3072_v1",
            vector_field="embedding_gemini_3072",
            dimensions=3072,
            mode=mode,  # type: ignore[arg-type]
        ),
    )


def _native_candidate(gse: str, rank: int, **overrides: object) -> SearchCandidate:
    values: dict[str, object] = {
        "gse": gse,
        "title": f"Native {gse}",
        "snippet": "Native study summary",
        "study_type": "Expression profiling",
        "n_samples": None,
        "pubmed_id": None,
        "organism_ids": ("NCBITaxon:9606",),
        "organism_status": "mapped",
        "sex_ids": (),
        "sex_status": "unavailable",
        "assay_categories": ("transcriptomics",),
        "assay_labels": ("RNA-seq",),
        "assay_status": "mapped",
        "source": "ncbi",
        "retrieval_score": None,
        "original_rank": None,
        "native_rank": rank,
        "taxon": "Homo sapiens",
    }
    values.update(overrides)
    return SearchCandidate(**values)  # type: ignore[arg-type]


class FakeNativeSource:
    def __init__(
        self,
        *,
        exact: SearchCandidate | None = None,
        search_result: NativeSearchResult | None = None,
        error: Exception | None = None,
        on_search=None,
        close_events: list[str] | None = None,
    ) -> None:
        self.exact = exact
        self.search_result = search_result or NativeSearchResult(count=0, candidates=())
        self.error = error
        self.on_search = on_search
        self.search_calls: list[tuple[str, int]] = []
        self.lookup_calls: list[str] = []
        self.closed = False
        self.close_events = close_events

    def search(self, query: str, limit: int = 20) -> NativeSearchResult:
        self.search_calls.append((query, limit))
        if self.on_search is not None:
            self.on_search()
        if self.error is not None:
            raise self.error
        return self.search_result

    def lookup(self, gse: str) -> SearchCandidate | None:
        self.lookup_calls.append(gse)
        if self.error is not None:
            raise self.error
        return self.exact

    def close(self) -> None:
        self.closed = True
        if self.close_events is not None:
            self.close_events.append("ncbi")


class FakeReranker:
    model = "claude-haiku-4-5"
    thinking = "disabled"

    def __init__(
        self,
        *,
        scores: dict[str, int] | None = None,
        error: Exception | None = None,
        on_rerank=None,
        close_error: Exception | None = None,
        close_events: list[str] | None = None,
    ) -> None:
        self.scores = scores
        self.error = error
        self.on_rerank = on_rerank
        self.close_error = close_error
        self.rerank_calls: list[tuple[str, tuple[SearchCandidate, ...], int]] = []
        self.closed = False
        self.close_events = close_events

    def rerank(
        self, query: str, candidates: tuple[SearchCandidate, ...], *, limit: int
    ) -> RerankResult:
        self.rerank_calls.append((query, tuple(candidates), limit))
        if self.on_rerank is not None:
            self.on_rerank()
        if self.error is not None:
            raise self.error
        scores = self.scores or {
            candidate.gse: len(candidates) - index
            for index, candidate in enumerate(candidates)
        }
        return RerankResult(scores=scores, input_tokens=123, output_tokens=45)

    def close(self) -> None:
        self.closed = True
        if self.close_events is not None:
            self.close_events.append("reranker")
        if self.close_error is not None:
            raise self.close_error


class _Client:
    def __init__(
        self,
        documents: dict[str, dict[str, object]] | None = None,
        *,
        facet_values: dict[str, tuple[str, ...]] | None = None,
        close_events: list[str] | None = None,
    ) -> None:
        self.documents = documents or {
            **{f"GSE{i}": _document(f"GSE{i}") for i in range(1, 41)},
            "GSE123": _document(),
        }
        self.facet_values = facet_values or {
            "organism_ids": ("NCBITaxon:9606",),
            "sex_ids": ("PATO:0000383",),
            "assay_categories": ("transcriptomics",),
            "assay_labels": ("scRNA-seq",),
        }
        self.closed = False
        self.search_calls: list[dict[str, object]] = []
        self.close_events = close_events

    def search(self, **kwargs: object) -> dict[str, object]:
        self.search_calls.append(kwargs)
        aggregations = {
            field: {
                "buckets": [
                    {"key": value, "doc_count": 1}
                    for value in self.facet_values[field]
                ]
            }
            for field in FACET_FIELDS
        }
        return {"aggregations": aggregations}

    def mget(self, **kwargs: object) -> dict[str, object]:
        return {
            "docs": [
                {"_id": gse, "found": gse in self.documents,
                 "_source": self.documents.get(gse)}
                for gse in kwargs["ids"]  # type: ignore[union-attr]
            ]
        }

    def close(self) -> None:
        self.closed = True
        if self.close_events is not None:
            self.close_events.append("client")


class _DomainSearch:
    def __init__(
        self,
        encode_query,
        responses: list[SearchResponse] | None = None,
        *,
        exact_document: object = _UNSET,
        on_search=None,
    ):
        self.encode_query = encode_query
        default_hits = tuple(
            {"gse": f"GSE{i}", "score": 1 - i / 100} for i in range(1, 41)
        )
        self.responses = list(responses or [_response(hits=default_hits)])
        self.exact_document = (
            _document() if exact_document is _UNSET else exact_document
        )
        self.on_search = on_search
        self.search_calls: list[dict[str, object]] = []

    def search(self, query: str, **kwargs: object) -> SearchResponse:
        self.search_calls.append({"query": query, **kwargs})
        if self.on_search is not None:
            self.on_search()
        if kwargs["mode"] != "bm25":
            self.encode_query(query)
        return self.responses.pop(0)

    def get_dataset(self, gse: str) -> dict[str, object] | None:
        if not isinstance(self.exact_document, dict):
            return None
        return (
            dict(self.exact_document)
            if self.exact_document.get("gse") == gse
            else None
        )


class _Encoder:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = False

    def encode(self, query: str) -> np.ndarray:
        self.queries.append(query)
        return np.ones(3072, dtype=np.float32)

    def close(self) -> None:
        self.closed = True


def _service(
    *,
    client: _Client | None = None,
    encoder: _Encoder | None = None,
    responses: list[SearchResponse] | None = None,
    exact_document: object = _UNSET,
    native: FakeNativeSource | None = None,
    reranker: FakeReranker | None = None,
    domain_on_search=None,
    facet_values: dict[str, tuple[str, ...]] | None = None,
    quality: SearchQualitySettings | None = None,
):
    active_client = client or _Client(facet_values=facet_values)
    active_encoder = encoder or _Encoder()
    active_native = native or FakeNativeSource()
    readiness_calls: list[tuple[object, str]] = []
    domain = _DomainSearch(
        active_encoder.encode,
        responses,
        exact_document=exact_document,
        on_search=domain_on_search,
    )

    def search_factory(client, *, active_model_key, encode_query):
        domain.encode_query = encode_query
        return domain

    active_quality = quality or SearchQualitySettings(
        anthropic_api_key="test-key" if reranker is not None else None,
        rerank_enabled=reranker is not None,
    )

    service = McpSearchService(
        elasticsearch=SETTINGS,
        client_factory=lambda settings: active_client,
        query_encoder_factory=lambda key: active_encoder,
        search_service_factory=search_factory,
        readiness_check=lambda client, key: readiness_calls.append((client, key)),
        quality=active_quality,
        ncbi_source_factory=lambda timeout: active_native,
        reranker_factory=(lambda settings: reranker) if reranker is not None else None,
    )
    return service, active_client, domain, active_encoder, readiness_calls


def test_open_validates_readiness_loads_fixed_vocabulary_and_close_is_idempotent() -> None:
    service, client, _, encoder, readiness_calls = _service()
    service.open()

    assert service.is_open
    assert readiness_calls == [(client, "gemini_embedding_2_3072_v1")]
    assert set(client.search_calls[0]["aggs"]) == set(FACET_FIELDS)  # type: ignore[arg-type]
    assert service.facet_vocabulary["organism_ids"] == frozenset({"NCBITaxon:9606"})

    service.close()
    service.close()
    assert client.closed
    assert not encoder.closed
    assert not service.is_open


def test_startup_failure_closes_client_and_leaves_service_closed() -> None:
    client = _Client()
    service = McpSearchService(
        elasticsearch=SETTINGS,
        client_factory=lambda settings: client,
        readiness_check=lambda client, key: (_ for _ in ()).throw(RuntimeError("bad index")),
    )

    with pytest.raises(RuntimeError, match="bad index"):
        service.open()
    assert client.closed
    assert not service.is_open


def test_open_and_close_own_ncbi_and_enabled_reranker_resources() -> None:
    events: list[str] = []
    client = _Client(close_events=events)
    native = FakeNativeSource(close_events=events)
    reranker = FakeReranker(close_events=events)
    service, _, _, encoder, _ = _service(
        client=client, native=native, reranker=reranker
    )

    service.open()
    assert service._ncbi_source is native
    assert service._reranker is reranker

    service.close()
    service.close()

    assert events == ["reranker", "ncbi", "client"]
    assert not encoder.closed
    assert service._ncbi_source is None
    assert service._reranker is None


def test_reranker_startup_failure_closes_ncbi_and_client() -> None:
    events: list[str] = []
    client = _Client(close_events=events)
    native = FakeNativeSource(close_events=events)
    quality = SearchQualitySettings(
        anthropic_api_key="test-key", rerank_enabled=True
    )
    service = McpSearchService(
        elasticsearch=SETTINGS,
        client_factory=lambda settings: client,
        search_service_factory=lambda client, **kwargs: _DomainSearch(
            kwargs["encode_query"]
        ),
        readiness_check=lambda client, key: None,
        quality=quality,
        ncbi_source_factory=lambda timeout: native,
        reranker_factory=lambda settings: (_ for _ in ()).throw(
            RuntimeError("reranker startup failed")
        ),
    )

    with pytest.raises(RuntimeError, match="reranker startup failed"):
        service.open()

    assert events == ["ncbi", "client"]
    assert not service.is_open
    assert service._ncbi_source is None
    assert service._reranker is None


def test_default_reranker_factory_constructs_approved_haiku_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: dict[str, object] = {}
    reranker = FakeReranker()

    def reranker_factory(**kwargs: object) -> FakeReranker:
        constructed.update(kwargs)
        return reranker

    monkeypatch.setattr(
        mcp_search, "AnthropicReranker", reranker_factory, raising=False
    )
    quality = SearchQualitySettings(
        anthropic_api_key="test-anthropic-key",
        rerank_enabled=True,
        rerank_timeout_seconds=3.5,
    )
    service, _, _, _, _ = _service(quality=quality)

    service.open()

    assert constructed == {
        "api_key": "test-anthropic-key",
        "model": "claude-haiku-4-5",
        "thinking": "disabled",
        "timeout_seconds": 3.5,
    }
    service.close()
    assert reranker.closed


def test_close_attempts_every_resource_when_reranker_close_fails() -> None:
    events: list[str] = []
    client = _Client(close_events=events)
    native = FakeNativeSource(close_events=events)
    reranker = FakeReranker(
        close_events=events,
        close_error=RuntimeError("reranker close failed"),
    )
    service, _, _, _, _ = _service(
        client=client,
        native=native,
        reranker=reranker,
    )
    service.open()

    with pytest.raises(RuntimeError, match="reranker close failed"):
        service.close()

    assert events == ["reranker", "ncbi", "client"]
    assert not service.is_open


def test_close_waits_for_inflight_search_and_rejects_new_operations() -> None:
    rerank_started = threading.Event()
    allow_rerank = threading.Event()
    close_started = threading.Event()
    close_finished = threading.Event()

    def pause_rerank() -> None:
        rerank_started.set()
        assert allow_rerank.wait(timeout=2)

    native = FakeNativeSource()
    reranker = FakeReranker(on_rerank=pause_rerank)
    service, client, _, encoder, _ = _service(
        native=native,
        reranker=reranker,
    )
    service.open()
    executions = []
    search_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    def run_search() -> None:
        try:
            executions.append(
                service.search_execution(
                    query="immune",
                    filters=SearchFilters(),
                    limit=10,
                )
            )
        except BaseException as exc:
            search_errors.append(exc)

    def run_close() -> None:
        close_started.set()
        try:
            service.close()
        except BaseException as exc:
            close_errors.append(exc)
        finally:
            close_finished.set()

    search_thread = threading.Thread(target=run_search)
    close_thread = threading.Thread(target=run_close)
    search_thread.start()
    assert rerank_started.wait(timeout=2)
    close_thread.start()
    assert close_started.wait(timeout=2)
    with service._state_condition:
        assert service._state_condition.wait_for(
            lambda: service._closing, timeout=2
        )

    assert not close_finished.is_set()
    with pytest.raises(RuntimeError, match="closing"):
        service.ping()

    allow_rerank.set()
    search_thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert not search_thread.is_alive()
    assert not close_thread.is_alive()
    assert search_errors == []
    assert close_errors == []
    assert len(executions) == 1
    provenance = executions[0].output.provenance
    assert provenance.rerank_attempted is True
    assert provenance.rerank_applied is True
    assert provenance.rerank_model == "claude-haiku-4-5"
    assert provenance.rerank_reasoning_effort is None
    assert provenance.rerank_thinking == "disabled"
    assert reranker.closed
    assert native.closed
    assert encoder.closed
    assert client.closed


def test_truncated_facet_vocabulary_fails_closed() -> None:
    class TruncatedClient(_Client):
        def search(self, **kwargs: object) -> dict[str, object]:
            response = super().search(**kwargs)
            response["aggregations"]["assay_labels"]["sum_other_doc_count"] = 1
            return response

    client = TruncatedClient()
    service = McpSearchService(
        elasticsearch=SETTINGS,
        client_factory=lambda settings: client,
        readiness_check=lambda client, key: None,
    )

    with pytest.raises(RuntimeError, match="assay_labels vocabulary exceeds"):
        service.open()
    assert client.closed
    assert not service.is_open


def test_public_search_always_uses_hybrid_retrieval() -> None:
    encoder = _Encoder()
    responses = [_response(mode="hybrid")]
    service, _, domain, _, _ = _service(encoder=encoder, responses=responses)
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=5
    )

    assert domain.search_calls[0]["mode"] == "hybrid"
    assert domain.search_calls[0]["topk"] == 40
    assert encoder.queries == ["immune"]
    assert output.embedding_variant == "gemini_embedding_2_3072_v1"
    service.close()
    assert encoder.closed


def test_search_hydrates_ranked_hits_maps_provenance_and_bounds_output() -> None:
    client = _Client(
        {
            "GSE123": _document(
                title="t" * 600,
                summary="s" * 1200,
                assay_labels=["x" * 300] + [f"label-{i}" for i in range(110)],
            )
        }
    )
    service, _, _, _, _ = _service(
        client=client,
        responses=[
            _response(
                mode="hybrid",
                hits=({"gse": "GSE123", "score": 0.75},),
            )
        ],
    )
    service.open()
    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=5
    )

    assert output.results[0].gse == "GSE123"
    assert output.results[0].rank == 1
    assert output.results[0].score == 0.75
    assert len(output.results[0].title or "") == 500
    assert len(output.results[0].snippet or "") == 1000
    assert len(output.results[0].assay_labels) == 100
    assert output.results[0].truncated_fields == ["assay_labels", "snippet", "title"]
    assert output.retrieval_version == (
        "geo-series-v1:gemini_embedding_2_3072_v1:"
        "embedding_gemini_3072:hybrid"
    )
    assert output.embedding_variant == "gemini_embedding_2_3072_v1"
    assert set(output.facets) == set(FACET_FIELDS)


def test_exact_indexed_gse_bypasses_embedding_search_ncbi_and_reranking() -> None:
    service, _, domain, encoder, _ = _service(
        exact_document=_document("GSE310900"),
        native=FakeNativeSource(),
        reranker=FakeReranker(),
    )
    service.open()

    output = service.search_datasets(
        query="  gse310900 ", filters=SearchFilters(), limit=10
    )

    assert [result.gse for result in output.results] == ["GSE310900"]
    assert output.results[0].source == "elasticsearch"
    assert output.provenance.exact_accession is True
    assert domain.search_calls == []
    assert encoder.queries == []
    assert service._ncbi_source.lookup_calls == []
    assert service._reranker.rerank_calls == []
    assert output.retrieval_version == "geo-series-v1:exact-accession"
    assert output.embedding_variant is None
    assert output.provenance.rerank_model is None
    assert output.provenance.rerank_thinking is None


def test_exact_gse_missing_locally_uses_ncbi_without_reranking() -> None:
    native = FakeNativeSource(exact=_native_candidate("GSE310900", 1))
    service, _, domain, encoder, _ = _service(
        exact_document=None,
        native=native,
        reranker=FakeReranker(),
    )
    service.open()

    output = service.search_datasets(
        query="GSE310900", filters=SearchFilters(), limit=10
    )

    assert output.results[0].source == "ncbi"
    assert output.results[0].gse == "GSE310900"
    assert native.lookup_calls == ["GSE310900"]
    assert domain.search_calls == []
    assert encoder.queries == []
    assert service._reranker.rerank_calls == []
    assert output.retrieval_version == "ncbi-gds:exact-accession-v1"


def test_exact_ncbi_record_that_cannot_prove_filter_returns_no_results() -> None:
    native = FakeNativeSource(exact=_native_candidate("GSE310900", 1))
    service, _, domain, _, _ = _service(
        exact_document=None,
        native=native,
        reranker=FakeReranker(),
        facet_values={
            "organism_ids": ("NCBITaxon:9606",),
            "sex_ids": ("PATO:0000383", "PATO:0000384"),
            "assay_categories": ("transcriptomics",),
            "assay_labels": ("scRNA-seq",),
        },
    )
    service.open()

    output = service.search_datasets(
        query="GSE310900",
        filters=SearchFilters(sex_ids=("PATO:0000384",)),
        limit=10,
    )

    assert output.results == []
    assert output.provenance.exact_accession is True
    assert output.provenance.merged_candidates == 0
    assert output.facets["sex_ids"].candidate_count == 0
    assert native.lookup_calls == ["GSE310900"]
    assert domain.search_calls == []
    assert output.retrieval_version == "ncbi-gds:exact-accession-v1"


def test_exact_lookup_failure_is_bounded_and_fails_open() -> None:
    native = FakeNativeSource(error=TimeoutError("provider secret"))
    service, _, _, _, _ = _service(exact_document=None, native=native)
    service.open()

    output = service.search_datasets(
        query="GSE310900", filters=SearchFilters(), limit=10
    )

    assert output.results == []
    assert output.retrieval_version == "geo-series-v1:exact-accession-miss"
    assert output.provenance.degradation == ["ncbi_timeout"]
    assert output.provenance.ncbi_candidates == 0
    assert native.lookup_calls == ["GSE310900"]


def test_exact_facets_respect_the_transport_bucket_bound() -> None:
    labels = [f"label-{index}" for index in range(60)]
    service, _, _, _, _ = _service(
        exact_document=_document("GSE310900", assay_labels=labels)
    )
    service.open()

    output = service.search_datasets(
        query="GSE310900", filters=SearchFilters(), limit=10
    )

    assert len(output.facets["assay_labels"].buckets) == 50
    assert output.facets["assay_labels"].candidate_count == 1


def test_natural_search_requests_deep_pool_merges_and_reranks_top_ten() -> None:
    native = FakeNativeSource(
        search_result=NativeSearchResult(
            count=1,
            candidates=(_native_candidate("GSE999999", 1),),
        )
    )
    reranker = FakeReranker(
        scores={"GSE999999": 100, **{f"GSE{i}": 50 - i for i in range(1, 41)}}
    )
    service, _, domain, _, _ = _service(native=native, reranker=reranker)
    service.open()

    execution = service.search_execution(
        query="mouse exercise",
        filters=SearchFilters(),
        limit=10,
    )

    assert domain.search_calls[0]["topk"] == 40
    assert native.search_calls == [("mouse exercise", 100)]
    assert len(execution.output.results) == 10
    assert execution.output.results[0].gse == "GSE999999"
    assert execution.output.results[0].source == "ncbi"
    assert execution.output.results[0].score == 100
    assert execution.output.provenance.rerank_applied is True
    assert execution.output.provenance.rerank_attempted is True
    assert execution.output.provenance.rerank_model == "claude-haiku-4-5"
    assert execution.output.provenance.rerank_reasoning_effort is None
    assert execution.output.provenance.rerank_thinking == "disabled"
    assert execution.output.provenance.rerank_input_tokens == 123
    assert execution.output.provenance.rerank_output_tokens == 45
    assert execution.native is native.search_result
    assert len(execution.candidates) == 41


def test_ncbi_and_reranker_failures_keep_elasticsearch_order() -> None:
    service, _, _, _, _ = _service(
        native=FakeNativeSource(error=TimeoutError("NCBI timeout")),
        reranker=FakeReranker(error=RuntimeError("Anthropic unavailable")),
    )
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=10
    )

    assert [result.original_rank for result in output.results] == list(range(1, 11))
    assert output.provenance.rerank_applied is False
    assert output.provenance.rerank_attempted is True
    assert output.provenance.rerank_model == "claude-haiku-4-5"
    assert output.provenance.rerank_reasoning_effort is None
    assert output.provenance.rerank_thinking == "disabled"
    assert output.provenance.rerank_input_tokens == 0
    assert output.provenance.rerank_output_tokens == 0
    assert output.provenance.degradation == ["ncbi_timeout", "rerank_error"]


def test_limit_fifty_reranks_nonoverlapping_100_plus_100_union_before_slicing() -> None:
    local_documents = {
        f"GSE{index}": _document(f"GSE{index}") for index in range(1, 101)
    }
    local_hits = tuple(
        {"gse": f"GSE{index}", "score": 1 - index / 1000}
        for index in range(1, 101)
    )
    native_candidates = tuple(
        _native_candidate(f"GSE{index}", index - 100)
        for index in range(101, 201)
    )
    native = FakeNativeSource(
        search_result=NativeSearchResult(
            count=100,
            candidates=native_candidates,
        )
    )
    reranker = FakeReranker(
        scores={f"GSE{index}": 201 - index for index in range(1, 201)}
    )
    service, _, domain, _, _ = _service(
        client=_Client(local_documents),
        responses=[_response(mode="hybrid", hits=local_hits)],
        native=native,
        reranker=reranker,
    )
    service.open()

    execution = service.search_execution(
        query="mouse endurance exercise",
        filters=SearchFilters(),
        limit=50,
    )

    assert domain.search_calls[0]["topk"] == 100
    assert native.search_calls == [("mouse endurance exercise", 100)]
    reranked_candidates = reranker.rerank_calls[0][1]
    assert len(reranked_candidates) == 200
    assert {candidate.gse for candidate in reranked_candidates} == {
        f"GSE{index}" for index in range(1, 201)
    }
    assert len(execution.output.results) == 50
    assert execution.output.provenance.elasticsearch_candidates == 100
    assert execution.output.provenance.ncbi_candidates == 100
    assert execution.output.provenance.merged_candidates == 200
    assert len(execution.candidates) == 200


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (TimeoutError("slow"), "rerank_timeout"),
        (RerankRefusalError("refused"), "rerank_refusal"),
        (InvalidRerankOutputError("bad ids"), "rerank_invalid"),
    ],
)
def test_every_untrusted_reranker_result_discards_the_model_order(
    error: Exception, category: str
) -> None:
    service, _, _, _, _ = _service(
        native=FakeNativeSource(), reranker=FakeReranker(error=error)
    )
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=10
    )

    assert [result.original_rank for result in output.results] == list(range(1, 11))
    assert output.provenance.rerank_applied is False
    assert output.provenance.degradation == [category]


def test_oversized_reranker_input_degrades_safely_with_bounded_provenance() -> None:
    service, _, _, _, _ = _service(
        native=FakeNativeSource(),
        reranker=FakeReranker(
            error=RerankInputTooLargeError("sensitive candidate payload")
        ),
    )
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=10
    )

    assert [result.original_rank for result in output.results] == list(range(1, 11))
    assert output.provenance.rerank_attempted is True
    assert output.provenance.rerank_applied is False
    assert output.provenance.rerank_input_tokens == 0
    assert output.provenance.rerank_output_tokens == 0
    assert output.provenance.degradation == ["rerank_invalid"]
    assert "sensitive candidate payload" not in output.model_dump_json()


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (
            RerankRefusalError(
                "sensitive refusal text",
                usage=RerankUsage(input_tokens=321, output_tokens=54),
            ),
            "rerank_refusal",
        ),
        (
            InvalidRerankOutputError(
                "sensitive invalid output",
                usage=RerankUsage(input_tokens=321, output_tokens=54),
            ),
            "rerank_invalid",
        ),
    ],
)
def test_completed_unusable_reranks_preserve_usage_while_failing_open(
    error: Exception, category: str
) -> None:
    service, _, _, _, _ = _service(
        native=FakeNativeSource(), reranker=FakeReranker(error=error)
    )
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=10
    )

    assert [result.original_rank for result in output.results] == list(range(1, 11))
    assert output.provenance.rerank_applied is False
    assert output.provenance.rerank_input_tokens == 321
    assert output.provenance.rerank_output_tokens == 54
    assert output.provenance.degradation == [category]
    serialized = output.model_dump_json()
    assert "sensitive refusal text" not in serialized
    assert "sensitive invalid output" not in serialized


def test_natural_sources_start_concurrently() -> None:
    barrier = threading.Barrier(2, timeout=1)
    native = FakeNativeSource(on_search=barrier.wait)
    service, _, _, _, _ = _service(
        domain_on_search=barrier.wait,
        native=native,
        reranker=FakeReranker(),
    )
    service.open()

    service.search_datasets(
        query="immune", filters=SearchFilters(), limit=10
    )

    assert native.search_calls == [("immune", 100)]


def test_elasticsearch_candidate_text_and_arrays_are_bounded_before_reranking() -> None:
    documents = {
        gse: _document(
            gse,
            title="t" * 600,
            summary="s" * 1200,
            type="y" * 300,
            organism_ids=["o" * 300] * 110,
            organism_status="m" * 300,
            sex_ids=["x" * 300] * 110,
            sex_status="u" * 300,
            assay_categories=["c" * 300] * 110,
            assay_labels=["l" * 300] * 110,
            assay_status="a" * 300,
        )
        for gse in ("GSE1", "GSE2")
    }
    hits = (
        {"gse": "GSE1", "score": 0.9},
        {"gse": "GSE2", "score": 0.8},
    )
    reranker = FakeReranker(scores={"GSE1": 2, "GSE2": 1})
    service, _, _, _, _ = _service(
        client=_Client(documents),
        responses=[_response(mode="hybrid", hits=hits)],
        reranker=reranker,
    )
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=10
    )

    candidates = reranker.rerank_calls[0][1]
    assert len(candidates[0].title or "") == 500
    assert len(candidates[0].snippet or "") == 1000
    assert len(candidates[0].study_type or "") == 200
    assert len(candidates[0].organism_ids) == 100
    assert len(candidates[0].organism_ids[0]) == 256
    assert len(candidates[0].organism_status or "") == 256
    assert len(candidates[0].sex_ids) == 100
    assert len(candidates[0].assay_categories) == 100
    assert len(candidates[0].assay_labels) == 100
    assert len(candidates[0].assay_status or "") == 256
    assert output.results[0].truncated_fields == [
        "assay_categories",
        "assay_labels",
        "assay_status",
        "organism_ids",
        "organism_status",
        "sex_ids",
        "sex_status",
        "snippet",
        "study_type",
        "title",
    ]


def test_summary_marks_only_values_strictly_over_the_output_bounds() -> None:
    service, _, _, _, _ = _service()
    exact = _native_candidate(
        "GSE310900",
        1,
        title="t" * 500,
        snippet="s" * 1000,
        study_type="y" * 200,
        organism_ids=tuple("o" * 256 for _ in range(100)),
        organism_status="m" * 256,
        sex_ids=tuple("x" * 256 for _ in range(100)),
        sex_status="u" * 256,
        assay_categories=tuple("c" * 256 for _ in range(100)),
        assay_labels=tuple("l" * 256 for _ in range(100)),
        assay_status="a" * 256,
    )
    over = _native_candidate(
        "GSE310901",
        1,
        title="t" * 501,
        snippet="s" * 1001,
        study_type="y" * 201,
        organism_ids=tuple("o" * 257 for _ in range(101)),
        organism_status="m" * 257,
        sex_ids=tuple("x" * 257 for _ in range(101)),
        sex_status="u" * 257,
        assay_categories=tuple("c" * 257 for _ in range(101)),
        assay_labels=tuple("l" * 257 for _ in range(101)),
        assay_status="a" * 257,
    )

    exact_summary = service._summary_from_candidate(
        exact, rank=1, final_score=None
    )
    over_summary = service._summary_from_candidate(
        over, rank=1, final_score=None
    )

    assert exact_summary.truncated_fields == []
    assert over_summary.truncated_fields == [
        "assay_categories",
        "assay_labels",
        "assay_status",
        "organism_ids",
        "organism_status",
        "sex_ids",
        "sex_status",
        "snippet",
        "study_type",
        "title",
    ]


def test_shared_metadata_resolves_organism_labels_and_preserves_unknown_ids() -> None:
    client = _Client(
        {
            "GSE123": _document(
                organism_ids=["NCBITaxon:9606", "NCBITaxon:999999"],
            )
        }
    )
    service, _, _, _, _ = _service(
        client=client, responses=[_response(mode="hybrid")]
    )
    service.open()

    result = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=5
    ).results[0]

    assert result.organism_ids == ["NCBITaxon:9606", "NCBITaxon:999999"]
    assert result.organism_labels == ["Homo sapiens", "NCBITaxon:999999"]


def test_exact_lookup_maps_urls_pubmed_and_missing() -> None:
    service, _, _, _, _ = _service()
    service.open()

    found = service.get_dataset("GSE123")
    assert found.found
    assert found.dataset.organism_labels == ["Homo sapiens"]
    assert str(found.dataset.geo_url).endswith("acc=GSE123")
    assert str(found.dataset.pubmed_url) == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert service.get_dataset("GSE999").model_dump() == {"found": False, "dataset": None}


def test_unknown_filter_is_rejected_before_search() -> None:
    service, _, domain, _, _ = _service()
    service.open()
    with pytest.raises(UnknownFilterValueError, match="unknown organism_ids"):
        service.search_datasets(
            query="immune",
            filters=SearchFilters(organism_ids=("NCBITaxon:10090",)),
            limit=5,
        )
    assert domain.search_calls == []


def test_facet_values_use_filter_only_for_blank_and_hybrid_for_query() -> None:
    encoder = _Encoder()
    blank = SearchResponse(hits=(), facets=_facets("all_matches"), provenance=None)
    service, _, domain, _, _ = _service(
        encoder=encoder,
        responses=[blank, _response(mode="hybrid")],
    )
    service.open()

    all_values = service.facet_values(
        field="organism_ids", query=None, filters=SearchFilters(), limit=10
    )
    assert all_values.scope == "all_matches"
    assert all_values.retrieval_version == "facet-all-matches-v1"
    assert all_values.embedding_variant is None

    candidates = service.facet_values(
        field="organism_ids", query="immune", filters=SearchFilters(), limit=10
    )
    assert candidates.scope == "candidate_pool"
    assert candidates.embedding_variant == "gemini_embedding_2_3072_v1"
    assert domain.search_calls[0]["query"] == ""
    assert domain.search_calls[0]["mode"] == "bm25"
    assert domain.search_calls[1]["mode"] == "hybrid"
    assert encoder.queries == ["immune"]


def test_ping_requires_open_service_and_rechecks_readiness() -> None:
    service, client, _, _, readiness_calls = _service()
    with pytest.raises(RuntimeError, match="not open"):
        service.ping()
    service.open()
    service.ping()
    assert readiness_calls == [
        (client, "gemini_embedding_2_3072_v1"),
        (client, "gemini_embedding_2_3072_v1"),
    ]


def test_default_encoder_factory_delegates_to_shared_elasticsearch_factory(
    monkeypatch,
) -> None:
    sentinel = _Encoder()
    calls: list[str] = []

    def shared_factory(model_key: str):
        calls.append(model_key)
        return sentinel

    monkeypatch.setattr(mcp_search, "create_query_encoder", shared_factory)
    service = McpSearchService(elasticsearch=SETTINGS)

    assert service._default_query_encoder_factory(
        "gemini_embedding_2_3072_v1"
    ) is sentinel
    assert calls == ["gemini_embedding_2_3072_v1"]
