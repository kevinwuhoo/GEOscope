"""Elasticsearch lifecycle and bounded output adapter for the hosted MCP."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Protocol, cast

import numpy as np
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
from .mcp_models import (
    DatasetDetail,
    DatasetSummary,
    FacetBucketOutput,
    FacetResultOutput,
    FacetValuesOutput,
    GetDatasetOutput,
    SearchDatasetsOutput,
    SearchFiltersInput,
)
from .mcp_settings import McpSettings
from .search_models import FACET_FIELDS, FacetField, FacetResult, SearchFilters


_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
_FACET_VOCABULARY_LIMIT = 10_000


class UnknownFilterValueError(ValueError):
    """A requested closed-vocabulary value is not present in the index."""


class QueryEncoder(Protocol):
    def encode(self, query: str) -> Sequence[float]: ...
    def close(self) -> None: ...


class DomainSearch(Protocol):
    def search(self, query: str, **kwargs: object): ...
    def get_dataset(self, gse: str) -> dict[str, object] | None: ...


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
    ) -> None:
        self.elasticsearch = elasticsearch
        self._client_factory = client_factory
        self._query_encoder_factory = (
            query_encoder_factory or self._default_query_encoder_factory
        )
        self._search_service_factory = search_service_factory
        self._readiness_check = readiness_check
        self._client: object | None = None
        self._search: DomainSearch | None = None
        self._encoder: QueryEncoder | None = None
        self._facet_vocabulary: Mapping[FacetField, frozenset[str]] = (
            MappingProxyType({})
        )
        self._state_lock = threading.Lock()
        self._encoder_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: McpSettings, **kwargs: object) -> McpSearchService:
        return cls(elasticsearch=settings.elasticsearch, **kwargs)

    @property
    def is_open(self) -> bool:
        return self._client is not None and self._search is not None

    @property
    def facet_vocabulary(self) -> Mapping[FacetField, frozenset[str]]:
        return self._facet_vocabulary

    def _default_query_encoder_factory(self, model_key: str) -> QueryEncoder:
        return create_query_encoder(model_key)

    def open(self) -> None:
        with self._state_lock:
            if self.is_open:
                return
            client = self._client_factory(self.elasticsearch)
            try:
                self._readiness_check(client, self.elasticsearch.active_model_key)
                vocabulary = self._load_facet_vocabulary(client)
                search = self._search_service_factory(
                    client,
                    active_model_key=self.elasticsearch.active_model_key,
                    encode_query=self._encode_query,
                )
            except BaseException:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
                raise
            self._client = client
            self._search = search
            self._facet_vocabulary = MappingProxyType(vocabulary)

    def close(self) -> None:
        with self._state_lock:
            encoder = self._encoder
            client = self._client
            self._encoder = None
            self._search = None
            self._client = None
            self._facet_vocabulary = MappingProxyType({})
            if encoder is not None:
                encoder.close()
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

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
        query: str, filters: SearchFilters, mode: str, limit: int
    ) -> str:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        normalized = query.strip()
        if not 1 <= len(normalized) <= 1000:
            raise ValueError("query must contain between 1 and 1,000 characters")
        if not isinstance(filters, SearchFilters):
            raise TypeError("filters must be SearchFilters")
        if mode not in {"hybrid", "bm25", "dense"}:
            raise ValueError(f"unsupported retrieval mode: {mode}")
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

    def search_datasets(
        self, *, query: str, filters: SearchFilters, mode: str, limit: int
    ) -> SearchDatasetsOutput:
        query = self._validate_search_request(query, filters, mode, limit)
        self._require_filters(filters)
        client, search = self._require_open()
        response = search.search(
            query,
            filters=filters,
            mode=mode,
            topk=limit,
            bucket_limit=50,
        )
        hits = list(response.hits[:limit])
        documents = self._hydrate_documents(client, hits)
        summaries: list[DatasetSummary] = []
        for rank, (hit, document) in enumerate(zip(hits, documents, strict=True), 1):
            truncated: set[str] = set()
            score = hit.get("score")
            summaries.append(
                DatasetSummary(
                    rank=rank,
                    score=float(score) if score is not None else None,
                    snippet=_cap_text(document.get("summary"), 1000, "snippet", truncated),
                    truncated_fields=sorted(truncated),
                    **self._metadata_fields(document, truncated),
                )
            )
            summaries[-1].truncated_fields = sorted(truncated)
        facets = {
            field: self._facet_output(response.facets[field], limit=50)
            for field in FACET_FIELDS
        }
        return SearchDatasetsOutput(
            query=query,
            filters=SearchFiltersInput(**filters.as_dict()),
            mode=mode,
            limit=limit,
            retrieval_version=_retrieval_version(response.provenance),
            embedding_variant=(
                None if mode == "bm25" else self.elasticsearch.active_model_key
            ),
            results=summaries,
            facets=facets,
        )

    def get_dataset(self, gse: str) -> GetDatasetOutput:
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
        mode: str,
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
        effective_mode = mode if normalized_query else "bm25"
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
                if normalized_query and mode != "bm25"
                else None
            ),
        )

    def ping(self) -> None:
        client, _ = self._require_open()
        self._readiness_check(client, self.elasticsearch.active_model_key)
