"""Elasticsearch lifecycle and bounded output adapter for the hosted MCP."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeVar, cast

import httpx
import numpy as np
from openai import APITimeoutError
from pydantic import ValidationError

from .elasticsearch_config import (
    INDEX_NAME,
    ElasticsearchSettings,
    create_client,
    response_body,
)
from .elasticsearch_index import index_readiness
from .elasticsearch_query_embeddings import create_query_encoder
from .elasticsearch_search import ElasticsearchSearchService
from .eutils import EutilsClient
from .facets import facet_label
from .mcp_models import (
    DegradationCategory,
    DatasetDetail,
    DatasetSummary,
    FacetBucketOutput,
    FacetResultOutput,
    FacetValuesOutput,
    GetDatasetOutput,
    SearchDatasetsOutput,
    SearchFiltersInput,
    SearchLatencyOutput,
    SearchProvenanceOutput,
)
from .mcp_settings import McpSettings, SearchQualitySettings
from .ncbi_search import (
    NativeError,
    NativeSearchResult,
    NcbiCandidateSource,
)
from .reranker import (
    InvalidRerankOutputError,
    OpenAIReranker,
    RerankRefusalError,
    RerankResult,
    RerankResponseError,
    rank_candidates,
)
from .search_candidates import (
    MAX_SOURCE_CANDIDATES,
    SearchCandidate,
    candidate_matches_filters,
    candidate_pool_limit,
    fallback_order,
    merge_candidates,
)
from .search_models import (
    FACET_FIELDS,
    FacetField,
    FacetResult,
    SearchFilters,
    SearchResponse,
)


_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
_FACET_VOCABULARY_LIMIT = 10_000


class UnknownFilterValueError(ValueError):
    """A requested closed-vocabulary value is not present in the index."""


class QueryEncoder(Protocol):
    def encode(self, query: str) -> Sequence[float]: ...
    def close(self) -> None: ...


class DomainSearch(Protocol):
    def search(self, query: str, **kwargs: object) -> SearchResponse: ...
    def get_dataset(self, gse: str) -> dict[str, object] | None: ...


@dataclass(frozen=True)
class SearchExecution:
    output: SearchDatasetsOutput
    native: NativeSearchResult
    candidates: tuple[SearchCandidate, ...]


class NativeSource(Protocol):
    def search(
        self, query: str, limit: int = MAX_SOURCE_CANDIDATES
    ) -> NativeSearchResult:
        raise NotImplementedError

    def lookup(self, gse: str) -> SearchCandidate | None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class CandidateReranker(Protocol):
    model: str
    reasoning_effort: str

    def rerank(
        self, query: str, candidates: Sequence[SearchCandidate], *, limit: int
    ) -> RerankResult:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


T = TypeVar("T")


@dataclass(frozen=True)
class _TimedCall(Generic[T]):
    value: T | None
    error: Exception | None
    elapsed_ms: int


def _capture_timed(
    clock: Callable[[], float],
    function: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> _TimedCall[T]:
    started = clock()
    try:
        return _TimedCall(
            value=function(*args, **kwargs),
            error=None,
            elapsed_ms=max(0, round((clock() - started) * 1000)),
        )
    except Exception as exc:
        return _TimedCall(
            value=None,
            error=exc,
            elapsed_ms=max(0, round((clock() - started) * 1000)),
        )


def _has_cause(
    exc: BaseException, kinds: tuple[type[BaseException], ...]
) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, kinds):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _ncbi_error_category(exc: BaseException) -> NativeError:
    if _has_cause(exc, (TimeoutError, httpx.TimeoutException)):
        return "ncbi_timeout"
    return "ncbi_error"


def _rerank_error_category(exc: BaseException) -> DegradationCategory:
    if _has_cause(exc, (APITimeoutError, TimeoutError, httpx.TimeoutException)):
        return "rerank_timeout"
    if isinstance(exc, RerankRefusalError):
        return "rerank_refusal"
    if isinstance(exc, InvalidRerankOutputError):
        return "rerank_invalid"
    return "rerank_error"


def _cap_text(
    value: object | None, limit: int, field: str, truncated: set[str]
) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value)
    if len(text) > limit:
        truncated.add(field)
        return text[:limit]
    return text


def _cap_array(
    value: object | None, field: str, truncated: set[str]
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values: list[object] = [value]
    elif isinstance(value, Sequence):
        raw_values = list(cast(Sequence[object], value))
    else:
        raw_values = [value]
    if len(raw_values) > 100:
        truncated.add(field)
    output: list[str] = []
    for raw in raw_values[:100]:
        text = str(raw)
        if len(text) > 256:
            text = text[:256]
            truncated.add(field)
        output.append(text)
    return output


def _pubmed_id(value: object | None) -> int | None:
    values: list[object]
    if value is None:
        return None
    if isinstance(value, (str, int)):
        values = [value]
    elif isinstance(value, Sequence):
        values = list(cast(Sequence[object], value))
    else:
        return None
    if len(values) != 1:
        return None
    try:
        parsed = int(values[0])
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _retrieval_version(provenance) -> str:
    if provenance is None:
        raise RuntimeError("Elasticsearch search returned no provenance")
    return (
        f"{provenance.mapping_revision}:{provenance.active_model_key}:"
        f"{provenance.vector_field}:{provenance.mode}"
    )


def _exact_facets(
    candidate: SearchCandidate | None,
) -> dict[FacetField, FacetResultOutput]:
    candidate_count = 1 if candidate is not None else 0
    results: dict[FacetField, FacetResultOutput] = {}
    for field in FACET_FIELDS:
        values = tuple(getattr(candidate, field)) if candidate is not None else ()
        results[field] = FacetResultOutput(
            field=field,
            buckets=[
                FacetBucketOutput(
                    value=value,
                    label=facet_label(field, value),
                    count=1,
                )
                for value in values[:50]
            ],
            scope="candidate_pool",
            candidate_count=candidate_count,
        )
    return results


class McpSearchService:
    """Own live Elasticsearch resources and adapt them to bounded MCP models."""

    def __init__(
        self,
        *,
        elasticsearch: ElasticsearchSettings,
        client_factory: Callable[[ElasticsearchSettings], object] = create_client,
        query_encoder_factory: Callable[[str], QueryEncoder] | None = None,
        search_service_factory: Callable[..., DomainSearch] = ElasticsearchSearchService,
        readiness_check: Callable[[object, str], object] = index_readiness,
        quality: SearchQualitySettings | None = None,
        ncbi_source_factory: Callable[[float], NativeSource] | None = None,
        reranker_factory: Callable[[SearchQualitySettings], CandidateReranker]
        | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.elasticsearch = elasticsearch
        self.quality = quality or SearchQualitySettings.disabled()
        self._client_factory = client_factory
        self._query_encoder_factory = (
            query_encoder_factory or self._default_query_encoder_factory
        )
        self._search_service_factory = search_service_factory
        self._readiness_check = readiness_check
        self._ncbi_source_factory = ncbi_source_factory or (
            lambda timeout: NcbiCandidateSource(
                EutilsClient(timeout=timeout, max_retries=1),
                timeout_seconds=timeout,
            )
        )
        self._reranker_factory = reranker_factory or (
            lambda settings: OpenAIReranker(
                api_key=settings.openai_api_key or "",
                model=settings.rerank_model,
                reasoning_effort=settings.reasoning_effort,
                timeout_seconds=settings.rerank_timeout_seconds,
            )
        )
        self._clock = clock
        self._client: object | None = None
        self._search: DomainSearch | None = None
        self._encoder: QueryEncoder | None = None
        self._ncbi_source: NativeSource | None = None
        self._reranker: CandidateReranker | None = None
        self._facet_vocabulary: Mapping[FacetField, frozenset[str]] = (
            MappingProxyType({})
        )
        self._state_lock = threading.Lock()
        self._state_condition = threading.Condition(self._state_lock)
        self._closing = False
        self._active_operations = 0
        self._encoder_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: McpSettings, **kwargs: object) -> McpSearchService:
        return cls(
            elasticsearch=settings.elasticsearch,
            quality=settings.search_quality,
            **kwargs,
        )

    @property
    def is_open(self) -> bool:
        return (
            self._client is not None
            and self._search is not None
            and self._ncbi_source is not None
            and (not self.quality.rerank_enabled or self._reranker is not None)
        )

    @property
    def facet_vocabulary(self) -> Mapping[FacetField, frozenset[str]]:
        return self._facet_vocabulary

    def _default_query_encoder_factory(self, model_key: str) -> QueryEncoder:
        return create_query_encoder(model_key)

    def open(self) -> None:
        with self._state_condition:
            while self._closing:
                self._state_condition.wait()
            if self.is_open:
                return
            client = self._client_factory(self.elasticsearch)
            native_source: NativeSource | None = None
            reranker: CandidateReranker | None = None
            try:
                self._readiness_check(client, self.elasticsearch.active_model_key)
                vocabulary = self._load_facet_vocabulary(client)
                search = self._search_service_factory(
                    client,
                    active_model_key=self.elasticsearch.active_model_key,
                    encode_query=self._encode_query,
                )
                native_source = self._ncbi_source_factory(
                    self.quality.ncbi_timeout_seconds
                )
                if self.quality.rerank_enabled:
                    reranker = self._reranker_factory(self.quality)
            except BaseException:
                encoder = self._encoder
                self._encoder = None
                for resource in (reranker, native_source, encoder, client):
                    close = getattr(resource, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass
                raise
            self._client = client
            self._search = search
            self._ncbi_source = native_source
            self._reranker = reranker
            self._facet_vocabulary = MappingProxyType(vocabulary)

    def close(self) -> None:
        with self._state_condition:
            while self._closing:
                self._state_condition.wait()
            if all(
                resource is None
                for resource in (
                    self._reranker,
                    self._ncbi_source,
                    self._encoder,
                    self._search,
                    self._client,
                )
            ):
                return
            self._closing = True
            while self._active_operations:
                self._state_condition.wait()
            reranker = self._reranker
            native_source = self._ncbi_source
            encoder = self._encoder
            client = self._client
            self._reranker = None
            self._ncbi_source = None
            self._encoder = None
            self._search = None
            self._client = None
            self._facet_vocabulary = MappingProxyType({})
        close_error: BaseException | None = None
        try:
            for resource in (reranker, native_source, encoder, client):
                close = getattr(resource, "close", None)
                if callable(close):
                    try:
                        close()
                    except BaseException as exc:
                        if close_error is None:
                            close_error = exc
        finally:
            with self._state_condition:
                self._closing = False
                self._state_condition.notify_all()
        if close_error is not None:
            raise close_error

    @contextmanager
    def _operation_lease(self) -> Iterator[None]:
        with self._state_condition:
            if self._closing:
                raise RuntimeError("McpSearchService is closing")
            if not self.is_open:
                raise RuntimeError("McpSearchService is not open")
            self._active_operations += 1
        try:
            yield
        finally:
            with self._state_condition:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._state_condition.notify_all()

    @staticmethod
    def _load_facet_vocabulary(
        client: object,
    ) -> dict[FacetField, frozenset[str]]:
        aggregations = {
            field: {
                "terms": {
                    "field": field,
                    "size": _FACET_VOCABULARY_LIMIT,
                }
            }
            for field in FACET_FIELDS
        }
        body = response_body(
            client.search(index=INDEX_NAME, size=0, aggs=aggregations)
        )
        raw_aggregations = body.get("aggregations")
        if not isinstance(raw_aggregations, dict):
            raise RuntimeError("Elasticsearch facet vocabulary response is malformed")
        vocabulary: dict[FacetField, frozenset[str]] = {}
        for field in FACET_FIELDS:
            raw_result = raw_aggregations.get(field)
            raw_buckets = raw_result.get("buckets") if isinstance(raw_result, dict) else None
            if not isinstance(raw_buckets, list):
                raise RuntimeError(f"Elasticsearch {field} vocabulary is malformed")
            raw_omitted = raw_result.get("sum_other_doc_count", 0)
            if not isinstance(raw_omitted, int) or raw_omitted < 0:
                raise RuntimeError(f"Elasticsearch {field} vocabulary is malformed")
            if raw_omitted:
                raise RuntimeError(
                    f"Elasticsearch {field} vocabulary exceeds the startup bound"
                )
            values: list[str] = []
            for bucket in raw_buckets:
                if not isinstance(bucket, dict) or "key" not in bucket:
                    raise RuntimeError(f"Elasticsearch {field} vocabulary is malformed")
                value = str(bucket["key"])
                try:
                    validated = SearchFiltersInput(**{field: [value]})
                except ValidationError:
                    raise RuntimeError(
                        f"Elasticsearch {field} vocabulary contains invalid values"
                    ) from None
                if getattr(validated, field) != [value]:
                    raise RuntimeError(
                        f"Elasticsearch {field} vocabulary contains invalid values"
                    )
                values.append(value)
            vocabulary[field] = frozenset(values)
        return vocabulary

    def _query_encoder(self) -> QueryEncoder:
        if self._encoder is None:
            with self._encoder_lock:
                if self._encoder is None:
                    self._encoder = self._query_encoder_factory(
                        self.elasticsearch.active_model_key
                    )
        return self._encoder

    def _encode_query(self, query: str) -> Sequence[float]:
        with self._inference_lock:
            return self._query_encoder().encode(query)

    def _require_open(self) -> tuple[object, DomainSearch]:
        if self._client is None or self._search is None:
            raise RuntimeError("McpSearchService is not open")
        return self._client, self._search

    def _require_filters(self, filters: SearchFilters) -> None:
        if not isinstance(filters, SearchFilters):
            raise TypeError("filters must be SearchFilters")
        for field in FACET_FIELDS:
            allowed = self._facet_vocabulary.get(field, frozenset())
            if any(value not in allowed for value in getattr(filters, field)):
                raise UnknownFilterValueError(
                    f"unknown {field} value; call facet_values to list valid values"
                )

    @staticmethod
    def _validate_search_request(
        query: str, filters: SearchFilters, limit: int
    ) -> str:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        normalized = query.strip()
        if not 1 <= len(normalized) <= 1000:
            raise ValueError("query must contain between 1 and 1,000 characters")
        if not isinstance(filters, SearchFilters):
            raise TypeError("filters must be SearchFilters")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        return normalized

    @staticmethod
    def _metadata_fields(
        document: Mapping[str, object], truncated: set[str]
    ) -> dict[str, object]:
        n_samples = document.get("n_samples")
        return {
            "gse": str(document.get("gse", "")),
            "title": _cap_text(document.get("title"), 500, "title", truncated),
            "study_type": _cap_text(document.get("type"), 200, "study_type", truncated),
            "n_samples": int(n_samples) if n_samples is not None else None,
            "pubmed_id": _pubmed_id(document.get("pubmed_ids")),
            "organism_ids": _cap_array(document.get("organism_ids"), "organism_ids", truncated),
            "organism_status": _cap_text(document.get("organism_status"), 256, "organism_status", truncated),
            "sex_ids": _cap_array(document.get("sex_ids"), "sex_ids", truncated),
            "sex_status": _cap_text(document.get("sex_status"), 256, "sex_status", truncated),
            "assay_categories": _cap_array(document.get("assay_categories"), "assay_categories", truncated),
            "assay_labels": _cap_array(document.get("assay_labels"), "assay_labels", truncated),
            "assay_status": _cap_text(document.get("assay_status"), 256, "assay_status", truncated),
        }

    @staticmethod
    def _hydrate_documents(
        client: object, hits: Sequence[Mapping[str, object]]
    ) -> list[dict[str, object]]:
        if not hits:
            return []
        ranked = [str(hit.get("gse", "")) for hit in hits]
        if any(not _GSE_RE.fullmatch(gse) for gse in ranked) or len(set(ranked)) != len(ranked):
            raise RuntimeError("Elasticsearch search returned invalid GSE identifiers")
        body = response_body(client.mget(index=INDEX_NAME, ids=ranked))
        raw_docs = body.get("docs")
        if not isinstance(raw_docs, list):
            raise RuntimeError("Elasticsearch mget response is malformed")
        documents: dict[str, dict[str, object]] = {}
        for raw in raw_docs:
            if not isinstance(raw, dict) or raw.get("found") is not True:
                raise RuntimeError("ranked Elasticsearch metadata changed during hydration")
            source = raw.get("_source")
            gse = str(raw.get("_id", ""))
            if not isinstance(source, dict) or gse in documents:
                raise RuntimeError("Elasticsearch mget response is malformed")
            document = dict(source)
            document["gse"] = gse
            documents[gse] = document
        if set(documents) != set(ranked):
            raise RuntimeError("ranked Elasticsearch metadata changed during hydration")
        return [documents[gse] for gse in ranked]

    @staticmethod
    def _facet_output(result: FacetResult, *, limit: int) -> FacetResultOutput:
        return FacetResultOutput(
            field=result.field,
            buckets=[
                FacetBucketOutput(
                    value=str(bucket.value)[:256],
                    label=str(bucket.label)[:256],
                    count=int(bucket.count),
                )
                for bucket in result.buckets[:limit]
            ],
            scope=result.scope,
            candidate_count=result.candidate_count,
        )

    def _candidate_from_document(
        self,
        document: Mapping[str, object],
        *,
        original_rank: int,
        retrieval_score: float | None,
    ) -> SearchCandidate:
        truncated: set[str] = set()
        metadata = self._metadata_fields(document, truncated)
        return SearchCandidate(
            gse=cast(str, metadata["gse"]),
            title=cast(str | None, metadata["title"]),
            snippet=_cap_text(document.get("summary"), 1000, "snippet", truncated),
            study_type=cast(str | None, metadata["study_type"]),
            n_samples=cast(int | None, metadata["n_samples"]),
            pubmed_id=cast(int | None, metadata["pubmed_id"]),
            organism_ids=tuple(cast(list[str], metadata["organism_ids"])),
            organism_status=cast(str | None, metadata["organism_status"]),
            sex_ids=tuple(cast(list[str], metadata["sex_ids"])),
            sex_status=cast(str | None, metadata["sex_status"]),
            assay_categories=tuple(cast(list[str], metadata["assay_categories"])),
            assay_labels=tuple(cast(list[str], metadata["assay_labels"])),
            assay_status=cast(str | None, metadata["assay_status"]),
            source="elasticsearch",
            retrieval_score=retrieval_score,
            original_rank=original_rank,
            native_rank=None,
            taxon=_cap_text(document.get("taxon"), 256, "taxon", truncated),
            truncated_fields=tuple(sorted(truncated)),
        )

    def _local_candidates(
        self,
        client: object,
        search: DomainSearch,
        *,
        query: str,
        filters: SearchFilters,
        topk: int,
    ) -> tuple[tuple[SearchCandidate, ...], SearchResponse]:
        response = search.search(
            query,
            filters=filters,
            mode="hybrid",
            topk=topk,
            bucket_limit=50,
        )
        hits = tuple(response.hits[:topk])
        documents = self._hydrate_documents(client, hits)
        candidates = tuple(
            self._candidate_from_document(
                document,
                original_rank=rank,
                retrieval_score=(
                    float(hit["score"]) if hit.get("score") is not None else None
                ),
            )
            for rank, (hit, document) in enumerate(
                zip(hits, documents, strict=True), 1
            )
        )
        return candidates, response

    def _summary_from_candidate(
        self,
        candidate: SearchCandidate,
        *,
        rank: int,
        final_score: float | None,
    ) -> DatasetSummary:
        truncated = set(candidate.truncated_fields)
        text_fields = {
            "title": (candidate.title, 500),
            "snippet": (candidate.snippet, 1000),
            "study_type": (candidate.study_type, 200),
            "organism_status": (candidate.organism_status, 256),
            "sex_status": (candidate.sex_status, 256),
            "assay_status": (candidate.assay_status, 256),
        }
        for field, (value, bound) in text_fields.items():
            if value is not None and len(value) > bound:
                truncated.add(field)
        array_fields = {
            "organism_ids": candidate.organism_ids,
            "sex_ids": candidate.sex_ids,
            "assay_categories": candidate.assay_categories,
            "assay_labels": candidate.assay_labels,
        }
        for field, values in array_fields.items():
            if len(values) > 100 or any(len(value) > 256 for value in values):
                truncated.add(field)
        summary = DatasetSummary(
            rank=rank,
            score=final_score,
            source=candidate.source,
            retrieval_score=candidate.retrieval_score,
            original_rank=candidate.original_rank,
            gse=candidate.gse,
            title=_cap_text(candidate.title, 500, "title", truncated),
            snippet=_cap_text(candidate.snippet, 1000, "snippet", truncated),
            study_type=_cap_text(
                candidate.study_type, 200, "study_type", truncated
            ),
            n_samples=candidate.n_samples,
            pubmed_id=candidate.pubmed_id,
            organism_ids=_cap_array(
                candidate.organism_ids, "organism_ids", truncated
            ),
            organism_status=_cap_text(
                candidate.organism_status, 256, "organism_status", truncated
            ),
            sex_ids=_cap_array(candidate.sex_ids, "sex_ids", truncated),
            sex_status=_cap_text(
                candidate.sex_status, 256, "sex_status", truncated
            ),
            assay_categories=_cap_array(
                candidate.assay_categories, "assay_categories", truncated
            ),
            assay_labels=_cap_array(
                candidate.assay_labels, "assay_labels", truncated
            ),
            assay_status=_cap_text(
                candidate.assay_status, 256, "assay_status", truncated
            ),
            truncated_fields=sorted(truncated),
        )
        summary.truncated_fields = sorted(truncated)
        return summary

    def _exact_execution(
        self,
        gse: str,
        *,
        filters: SearchFilters,
        limit: int,
        search: DomainSearch,
        native_source: NativeSource,
    ) -> SearchExecution:
        local_call = _capture_timed(self._clock, search.get_dataset, gse)
        if local_call.error is not None:
            raise local_call.error

        local_document = local_call.value
        native_call: _TimedCall[SearchCandidate | None] | None = None
        degradation: list[DegradationCategory] = []
        resolved: SearchCandidate | None
        if local_document is not None:
            resolved = self._candidate_from_document(
                local_document,
                original_rank=1,
                retrieval_score=None,
            )
            native = NativeSearchResult(count=None, candidates=())
            retrieval_version = "geo-series-v1:exact-accession"
        else:
            native_call = _capture_timed(self._clock, native_source.lookup, gse)
            if native_call.error is not None:
                category = _ncbi_error_category(native_call.error)
                native = NativeSearchResult.unavailable(category)
                degradation.append(category)
                resolved = None
            else:
                resolved = native_call.value
                native = NativeSearchResult(
                    count=1 if resolved is not None else 0,
                    candidates=(resolved,) if resolved is not None else (),
                )
            retrieval_version = (
                "ncbi-gds:exact-accession-v1"
                if resolved is not None
                else "geo-series-v1:exact-accession-miss"
            )

        eligible = (
            resolved
            if resolved is not None and candidate_matches_filters(resolved, filters)
            else None
        )
        candidates = (eligible,) if eligible is not None else ()
        results = (
            [
                self._summary_from_candidate(
                    eligible,
                    rank=1,
                    final_score=eligible.retrieval_score,
                )
            ]
            if eligible is not None
            else []
        )
        output = SearchDatasetsOutput(
            query=gse,
            filters=SearchFiltersInput(**filters.as_dict()),
            limit=limit,
            retrieval_version=retrieval_version,
            embedding_variant=None,
            results=results,
            facets=_exact_facets(eligible),
            provenance=SearchProvenanceOutput(
                exact_accession=True,
                elasticsearch_candidates=1 if local_document is not None else 0,
                ncbi_candidates=len(native.candidates),
                merged_candidates=len(candidates),
                rerank_attempted=False,
                rerank_applied=False,
                rerank_model=None,
                rerank_reasoning_effort=None,
                rerank_input_tokens=0,
                rerank_output_tokens=0,
                latency=SearchLatencyOutput(
                    elasticsearch_ms=local_call.elapsed_ms,
                    ncbi_ms=native_call.elapsed_ms if native_call is not None else 0,
                    reranker_ms=0,
                ),
                degradation=degradation,
            ),
        )
        return SearchExecution(output=output, native=native, candidates=candidates)

    def search_datasets(
        self, *, query: str, filters: SearchFilters, limit: int
    ) -> SearchDatasetsOutput:
        return self.search_execution(
            query=query,
            filters=filters,
            limit=limit,
        ).output

    def search_execution(
        self, *, query: str, filters: SearchFilters, limit: int
    ) -> SearchExecution:
        with self._operation_lease():
            return self._search_execution(
                query=query,
                filters=filters,
                limit=limit,
            )

    def _search_execution(
        self, *, query: str, filters: SearchFilters, limit: int
    ) -> SearchExecution:
        query = self._validate_search_request(query, filters, limit)
        self._require_filters(filters)
        client, search = self._require_open()
        if self._ncbi_source is None:
            raise RuntimeError("McpSearchService NCBI source is not open")
        native_source = self._ncbi_source
        if _GSE_RE.fullmatch(query.upper()):
            return self._exact_execution(
                query.upper(),
                filters=filters,
                limit=limit,
                search=search,
                native_source=native_source,
            )

        pool_size = candidate_pool_limit(limit, self.quality.candidate_limit)
        with ThreadPoolExecutor(max_workers=2) as executor:
            elastic_future = executor.submit(
                _capture_timed,
                self._clock,
                self._local_candidates,
                client,
                search,
                query=query,
                filters=filters,
                topk=pool_size,
            )
            ncbi_future = executor.submit(
                _capture_timed,
                self._clock,
                native_source.search,
                query,
                MAX_SOURCE_CANDIDATES,
            )
            local_call = elastic_future.result()
            native_call = ncbi_future.result()

        if local_call.error is not None:
            raise local_call.error
        if local_call.value is None:
            raise RuntimeError("Elasticsearch candidate retrieval returned no value")
        local_candidates, response = local_call.value

        degradation: list[DegradationCategory] = []
        if native_call.error is not None:
            category = _ncbi_error_category(native_call.error)
            native = NativeSearchResult.unavailable(category)
            degradation.append(category)
        elif native_call.value is None:
            raise RuntimeError("NCBI candidate retrieval returned no value")
        else:
            native = native_call.value
            if native.error is not None:
                degradation.append(native.error)

        merged = merge_candidates(local_candidates, native.candidates, filters)
        ordered = fallback_order(merged)
        rerank_result = RerankResult(scores={}, input_tokens=0, output_tokens=0)
        rerank_attempted = self._reranker is not None and len(merged) > 1
        rerank_applied = False
        reranker_ms = 0
        if rerank_attempted:
            assert self._reranker is not None
            rerank_call = _capture_timed(
                self._clock,
                self._reranker.rerank,
                query,
                merged,
                limit=limit,
            )
            reranker_ms = rerank_call.elapsed_ms
            if rerank_call.error is None and rerank_call.value is not None:
                rerank_result = rerank_call.value
                ordered = rank_candidates(merged, rerank_result)
                rerank_applied = True
            elif rerank_call.error is not None:
                if isinstance(rerank_call.error, RerankResponseError):
                    rerank_result = RerankResult(
                        scores={},
                        input_tokens=rerank_call.error.input_tokens,
                        output_tokens=rerank_call.error.output_tokens,
                    )
                degradation.append(_rerank_error_category(rerank_call.error))
            else:
                degradation.append("rerank_error")

        selected = ordered[:limit]
        summaries = [
            self._summary_from_candidate(
                candidate,
                rank=rank,
                final_score=(
                    float(rerank_result.scores[candidate.gse])
                    if rerank_applied
                    else candidate.retrieval_score
                ),
            )
            for rank, candidate in enumerate(selected, 1)
        ]
        facets = {
            field: self._facet_output(response.facets[field], limit=50)
            for field in FACET_FIELDS
        }
        output = SearchDatasetsOutput(
            query=query,
            filters=SearchFiltersInput(**filters.as_dict()),
            limit=limit,
            retrieval_version=_retrieval_version(response.provenance),
            embedding_variant=self.elasticsearch.active_model_key,
            results=summaries,
            facets=facets,
            provenance=SearchProvenanceOutput(
                exact_accession=False,
                elasticsearch_candidates=len(local_candidates),
                ncbi_candidates=len(native.candidates),
                merged_candidates=len(merged),
                rerank_attempted=rerank_attempted,
                rerank_applied=rerank_applied,
                rerank_model=(
                    self._reranker.model if rerank_attempted and self._reranker else None
                ),
                rerank_reasoning_effort=(
                    cast(Any, self._reranker.reasoning_effort)
                    if rerank_attempted and self._reranker
                    else None
                ),
                rerank_input_tokens=rerank_result.input_tokens,
                rerank_output_tokens=rerank_result.output_tokens,
                latency=SearchLatencyOutput(
                    elasticsearch_ms=local_call.elapsed_ms,
                    ncbi_ms=native_call.elapsed_ms,
                    reranker_ms=reranker_ms,
                ),
                degradation=degradation,
            ),
        )
        return SearchExecution(output=output, native=native, candidates=merged)

    def get_dataset(self, gse: str) -> GetDatasetOutput:
        with self._operation_lease():
            return self._get_dataset(gse)

    def _get_dataset(self, gse: str) -> GetDatasetOutput:
        if not isinstance(gse, str) or not _GSE_RE.fullmatch(gse):
            raise ValueError("gse must be a normalized GSE accession")
        _, search = self._require_open()
        document = search.get_dataset(gse)
        if document is None:
            return GetDatasetOutput(found=False, dataset=None)
        truncated: set[str] = set()
        pubmed_id = _pubmed_id(document.get("pubmed_ids"))
        dataset = DatasetDetail(
            summary=_cap_text(document.get("summary"), 8000, "summary", truncated),
            overall_design=_cap_text(document.get("overall_design"), 8000, "overall_design", truncated),
            geo_url=f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse}",
            pubmed_url=(
                f"https://pubmed.ncbi.nlm.nih.gov/{pubmed_id}/"
                if pubmed_id is not None
                else None
            ),
            truncated_fields=sorted(truncated),
            **self._metadata_fields(document, truncated),
        )
        dataset.truncated_fields = sorted(truncated)
        return GetDatasetOutput(found=True, dataset=dataset)

    def facet_values(
        self,
        *,
        field: FacetField,
        query: str | None,
        filters: SearchFilters,
        limit: int,
    ) -> FacetValuesOutput:
        with self._operation_lease():
            return self._facet_values(
                field=field,
                query=query,
                filters=filters,
                limit=limit,
            )

    def _facet_values(
        self,
        *,
        field: FacetField,
        query: str | None,
        filters: SearchFilters,
        limit: int,
    ) -> FacetValuesOutput:
        if field not in FACET_FIELDS:
            raise ValueError(f"unsupported facet field: {field}")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        normalized_query = query.strip() if isinstance(query, str) else ""
        if query is not None and query.strip() and len(query.strip()) > 1000:
            raise ValueError("query must contain at most 1,000 characters")
        self._require_filters(filters)
        _, search = self._require_open()
        effective_mode = "hybrid" if normalized_query else "bm25"
        response = search.search(
            normalized_query,
            filters=filters,
            mode=effective_mode,
            topk=1,
            bucket_limit=limit,
        )
        try:
            bounded = self._facet_output(response.facets[field], limit=limit)
        except KeyError as exc:
            raise RuntimeError("Elasticsearch search returned incomplete facet data") from exc
        return FacetValuesOutput(
            field=field,
            buckets=bounded.buckets,
            scope=bounded.scope,
            candidate_count=bounded.candidate_count,
            retrieval_version=(
                _retrieval_version(response.provenance)
                if normalized_query
                else "facet-all-matches-v1"
            ),
            embedding_variant=(
                self.elasticsearch.active_model_key
                if normalized_query
                else None
            ),
        )

    def ping(self) -> None:
        with self._operation_lease():
            client, _ = self._require_open()
            self._readiness_check(client, self.elasticsearch.active_model_key)
