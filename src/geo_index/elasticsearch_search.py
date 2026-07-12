"""Backend-neutral exact, lexical, dense, hybrid, and facet search on Elastic."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, Literal, cast

import numpy as np

from .elasticsearch_config import (
    INDEX_NAME,
    VECTOR_FIELDS,
    VectorFieldSpec,
    response_body,
)
from .elasticsearch_index import MAPPING_REVISION
from .facets import facet_label
from .search_models import (
    FACET_FIELDS,
    FacetBucket,
    FacetField,
    FacetResult,
    SearchFilters,
    SearchHit,
    SearchProvenance,
    SearchResponse,
)


SearchMode = Literal["bm25", "dense", "hybrid"]
_SEARCH_MODES = frozenset({"bm25", "dense", "hybrid"})
_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
_BM25_FIELDS = ("title^3", "summary^2", "overall_design", "embed_text")
_HIT_SOURCE_FIELDS = (
    "gse",
    "title",
    "summary",
    "overall_design",
    "type",
    "n_samples",
    "organism_ids",
    "sex_ids",
    "assay_categories",
    "assay_labels",
)


def build_filter_query(filters: SearchFilters) -> list[dict[str, object]]:
    """Translate the closed normalized-filter contract into terms clauses."""

    return [
        {"terms": {field: list(getattr(filters, field))}}
        for field in FACET_FIELDS
        if getattr(filters, field)
    ]


def _bm25_query(query: str, filters: SearchFilters) -> dict[str, object]:
    text_query: dict[str, object]
    if query.strip():
        text_query = {
            "multi_match": {
                "query": query,
                "fields": list(_BM25_FIELDS),
            }
        }
    else:
        text_query = {"match_all": {}}
    return {
        "bool": {
            "must": [text_query],
            "filter": build_filter_query(filters),
        }
    }


def _response_hits(response: object) -> list[dict[str, object]]:
    body = response_body(response)
    hit_container = body.get("hits")
    if hit_container is None:
        return []
    if not isinstance(hit_container, dict) or not isinstance(
        hit_container.get("hits"), list
    ):
        raise ValueError("Elasticsearch search response has malformed hits")
    rows: list[dict[str, object]] = []
    for raw in hit_container["hits"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("_source"), dict):
            raise ValueError("Elasticsearch hit has no object _source")
        source = dict(raw["_source"])
        gse = str(source.get("gse") or raw.get("_id") or "")
        if not _GSE_RE.fullmatch(gse):
            raise ValueError(f"Elasticsearch hit has malformed GSE {gse!r}")
        score_value = raw.get("_score")
        score = float(score_value) if score_value is not None else 0.0
        source["gse"] = gse
        source["score"] = score
        rows.append(source)
    rows.sort(key=lambda row: (-float(row["score"]), str(row["gse"])))
    return rows


def _validate_query_vector(value: Sequence[float], spec: VectorFieldSpec) -> list[float]:
    vector = np.asarray(value, dtype=np.float32)
    if vector.ndim != 1 or vector.shape[0] != spec.dimensions:
        actual = vector.shape[0] if vector.ndim == 1 else vector.shape
        raise ValueError(
            f"query vector requires {spec.dimensions} dimensions, got {actual}"
        )
    if not np.isfinite(vector).all():
        raise ValueError("query vector contains nonfinite values")
    return [float(item) for item in vector]


class ElasticsearchSearchService:
    """Fixed-model search service that never exposes raw Elasticsearch input."""

    def __init__(
        self,
        client: Any,
        *,
        active_model_key: str,
        encode_query: Callable[[str], Sequence[float]],
    ) -> None:
        try:
            spec = VECTOR_FIELDS[active_model_key]
        except KeyError as exc:
            raise ValueError(f"unknown active model: {active_model_key}") from exc
        self._client = client
        self._spec = spec
        self._encode_query = encode_query

    def get_dataset(self, gse: str) -> dict[str, object] | None:
        accession = gse.strip().upper()
        if not _GSE_RE.fullmatch(accession):
            raise ValueError(f"malformed GSE accession: {gse}")
        if not self._client.exists(index=INDEX_NAME, id=accession):
            return None
        response = response_body(self._client.get(index=INDEX_NAME, id=accession))
        if not isinstance(response.get("_source"), dict):
            raise ValueError(f"Elasticsearch get response for {accession} is malformed")
        source = dict(response["_source"])
        source["gse"] = accession
        return source

    def _retrieve(
        self,
        query: str,
        *,
        mode: SearchMode,
        filters: SearchFilters,
        query_vector: list[float] | None,
        topk: int,
        deep: int,
        num_candidates: int,
        k0: int,
        source_fields: Sequence[str] = _HIT_SOURCE_FIELDS,
    ) -> list[dict[str, object]]:
        filter_clauses = build_filter_query(filters)
        common: dict[str, object] = {
            "index": INDEX_NAME,
            "size": topk,
            "source": list(source_fields),
        }
        if mode == "bm25":
            response = self._client.search(
                **common,
                query=_bm25_query(query, filters),
            )
        elif mode == "dense":
            if query_vector is None:
                raise ValueError("dense retrieval requires a query vector")
            knn: dict[str, object] = {
                "field": self._spec.field,
                "query_vector": query_vector,
                "k": topk,
                "num_candidates": max(num_candidates, topk),
            }
            if filter_clauses:
                knn["filter"] = filter_clauses
            response = self._client.search(**common, knn=knn)
        else:
            if query_vector is None:
                raise ValueError("hybrid retrieval requires a query vector")
            rrf: dict[str, object] = {
                "retrievers": [
                    {"standard": {"query": _bm25_query(query, SearchFilters())}},
                    {
                        "knn": {
                            "field": self._spec.field,
                            "query_vector": query_vector,
                            "k": deep,
                            "num_candidates": max(num_candidates, deep),
                        }
                    },
                ],
                "rank_constant": k0,
                "rank_window_size": deep,
            }
            if filter_clauses:
                rrf["filter"] = filter_clauses
            response = self._client.search(
                **common,
                retriever={"rrf": rrf},
            )
        return _response_hits(response)

    def _blank_facets(
        self,
        filters: SearchFilters,
        bucket_limit: int,
    ) -> dict[FacetField, FacetResult]:
        results: dict[FacetField, FacetResult] = {}
        for field in FACET_FIELDS:
            response = self._client.search(
                index=INDEX_NAME,
                size=0,
                query=_bm25_query("", filters.without(field)),
                aggs={
                    "values": {
                        "terms": {
                            "field": field,
                            "size": bucket_limit,
                        }
                    }
                },
            )
            buckets = self._aggregation_buckets(response, field, bucket_limit)
            results[field] = FacetResult(
                field=field,
                buckets=buckets,
                scope="all_matches",
                candidate_count=None,
            )
        return results

    @staticmethod
    def _aggregation_buckets(
        response: object,
        field: FacetField,
        bucket_limit: int,
    ) -> tuple[FacetBucket, ...]:
        body = response_body(response)
        aggregations = body.get("aggregations", {})
        values = aggregations.get("values", {}) if isinstance(aggregations, dict) else {}
        raw_buckets = values.get("buckets", []) if isinstance(values, dict) else []
        if not isinstance(raw_buckets, list):
            raise ValueError("Elasticsearch facet buckets must be an array")
        buckets = [
            FacetBucket(
                value=str(raw["key"]),
                label=facet_label(field, str(raw["key"])),
                count=int(raw["doc_count"]),
            )
            for raw in raw_buckets
            if isinstance(raw, dict) and "key" in raw and "doc_count" in raw
        ]
        buckets.sort(key=lambda bucket: (-bucket.count, bucket.value))
        return tuple(buckets[:bucket_limit])

    def _candidate_facets(
        self,
        query: str,
        *,
        mode: SearchMode,
        filters: SearchFilters,
        query_vector: list[float] | None,
        deep: int,
        num_candidates: int,
        k0: int,
        facet_pool: int,
        bucket_limit: int,
    ) -> dict[FacetField, FacetResult]:
        results: dict[FacetField, FacetResult] = {}
        for field in FACET_FIELDS:
            candidates = self._retrieve(
                query,
                mode=mode,
                filters=filters.without(field),
                query_vector=query_vector,
                topk=facet_pool,
                deep=max(deep, facet_pool),
                num_candidates=max(num_candidates, facet_pool),
                k0=k0,
                source_fields=("gse", field),
            )
            counts: dict[str, int] = {}
            for candidate in candidates:
                raw_values = candidate.get(field, [])
                if not isinstance(raw_values, list):
                    continue
                for value in {str(raw) for raw in raw_values}:
                    counts[value] = counts.get(value, 0) + 1
            buckets = [
                FacetBucket(
                    value=value,
                    label=facet_label(field, value),
                    count=count,
                )
                for value, count in counts.items()
            ]
            buckets.sort(key=lambda bucket: (-bucket.count, bucket.value))
            results[field] = FacetResult(
                field=field,
                buckets=tuple(buckets[:bucket_limit]),
                scope="candidate_pool",
                candidate_count=len(candidates),
            )
        return results

    def search(
        self,
        query: str,
        *,
        mode: SearchMode = "hybrid",
        filters: SearchFilters | None = None,
        topk: int = 15,
        deep: int = 200,
        num_candidates: int = 500,
        k0: int = 60,
        facet_pool: int = 1000,
        bucket_limit: int = 50,
    ) -> SearchResponse:
        if mode not in _SEARCH_MODES:
            raise ValueError(f"unsupported search mode: {mode}")
        if topk < 1:
            raise ValueError("topk must be positive")
        if deep < topk:
            raise ValueError("deep must be at least topk")
        if num_candidates < 1:
            raise ValueError("num_candidates must be positive")
        if k0 < 1:
            raise ValueError("k0 must be positive")
        if facet_pool < 1:
            raise ValueError("facet_pool must be positive")
        if bucket_limit < 1:
            raise ValueError("bucket_limit must be positive")

        active_filters = filters or SearchFilters()
        query_vector: list[float] | None = None
        if mode != "bm25":
            query_vector = _validate_query_vector(
                self._encode_query(query), self._spec
            )
        hits = self._retrieve(
            query,
            mode=mode,
            filters=active_filters,
            query_vector=query_vector,
            topk=topk,
            deep=deep,
            num_candidates=num_candidates,
            k0=k0,
        )
        facets = (
            self._candidate_facets(
                query,
                mode=mode,
                filters=active_filters,
                query_vector=query_vector,
                deep=deep,
                num_candidates=num_candidates,
                k0=k0,
                facet_pool=facet_pool,
                bucket_limit=bucket_limit,
            )
            if query.strip()
            else self._blank_facets(active_filters, bucket_limit)
        )
        provenance = SearchProvenance(
            backend="elasticsearch",
            mapping_revision=MAPPING_REVISION,
            active_model_key=self._spec.model_key,
            vector_field=self._spec.field,
            dimensions=self._spec.dimensions,
            mode=cast(SearchMode, mode),
            settings={
                "topk": topk,
                "deep": deep,
                "num_candidates": num_candidates,
                "rank_constant": k0,
                "facet_pool": facet_pool,
                "bucket_limit": bucket_limit,
            },
        )
        return SearchResponse(
            hits=cast(tuple[SearchHit, ...], tuple(hits)),
            facets=facets,
            provenance=provenance,
        )
