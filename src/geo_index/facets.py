"""Safe SQL construction and aggregation for normalized search facets."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .normalize import NCBITAXON, PATO_FEMALE, PATO_HERMAPHRODITE, PATO_MALE
from .search_models import (
    FACET_FIELDS,
    FacetBucket,
    FacetField,
    FacetResult,
    SearchFilters,
)


FACET_COLUMNS: dict[FacetField, str] = {
    "organism_ids": "organism_ids",
    "sex_ids": "sex_ids",
    "assay_categories": "assay_categories",
    "assay_labels": "assay_labels",
}
_SQL_ALIASES = {"s", "series"}


def build_filter_clause(
    filters: SearchFilters,
    *,
    exclude: FacetField | None = None,
    alias: str = "s",
) -> tuple[str, dict[str, list[str]]]:
    """Build whitelist-only array-overlap predicates for normalized filters."""

    if alias not in _SQL_ALIASES:
        raise ValueError(f"unsupported SQL alias: {alias}")
    if exclude is not None and exclude not in FACET_FIELDS:
        raise ValueError(f"unknown facet field: {exclude}")
    clauses: list[str] = []
    params: dict[str, list[str]] = {}
    for facet in FACET_FIELDS:
        values = getattr(filters, facet)
        if facet == exclude or not values:
            continue
        param = f"filter_{facet}"
        clauses.append(f"{alias}.{FACET_COLUMNS[facet]} && %({param})s::text[]")
        params[param] = list(values)
    return (" AND ".join(clauses) or "TRUE"), params


_ORGANISM_LABELS = {value: key for key, value in NCBITAXON.items()}
_SEX_LABELS = {
    PATO_FEMALE: "female",
    PATO_MALE: "male",
    PATO_HERMAPHRODITE: "hermaphrodite",
}


def facet_label(field: FacetField, value: str) -> str:
    """Resolve compact display labels without a network dependency."""

    if field == "organism_ids":
        label = _ORGANISM_LABELS.get(value)
        return label.capitalize() if label is not None else value
    if field == "sex_ids":
        return _SEX_LABELS.get(value, value)
    return value


def facet_buckets(
    conn,
    field: FacetField,
    *,
    filters: SearchFilters,
    candidate_gses: list[str] | None,
    limit: int,
) -> tuple[FacetBucket, ...]:
    """Aggregate one disjunctive facet from exact rows or ranked candidates."""

    if field not in FACET_COLUMNS:
        raise ValueError(f"unknown facet field: {field}")
    if limit < 1:
        raise ValueError("facet limit must be positive")
    predicate, filter_params = build_filter_clause(filters, exclude=field, alias="s")
    params: dict[str, object] = {**filter_params}
    if candidate_gses is not None:
        if not candidate_gses:
            return ()
        predicate = f"({predicate}) AND s.gse = ANY(%(candidate_gses)s::text[])"
        params["candidate_gses"] = candidate_gses
    params["bucket_limit"] = limit
    column = FACET_COLUMNS[field]
    statement = f"""
        SELECT value, count(*)::int
        FROM (
            SELECT DISTINCT s.id, u.value
            FROM series AS s
            CROSS JOIN LATERAL unnest(s.{column}) AS u(value)
            WHERE {predicate}
        ) AS values_per_series
        GROUP BY value
        ORDER BY count(*) DESC, value ASC
        LIMIT %(bucket_limit)s
    """
    with conn.cursor() as cur:
        cur.execute(statement, params)
        return tuple(
            FacetBucket(value=value, label=facet_label(field, value), count=count)
            for value, count in cur.fetchall()
        )


Retriever = Callable[..., list[dict]]


def facet_counts(
    conn,
    *,
    query: str,
    mode: str,
    qv: np.ndarray | None,
    filters: SearchFilters,
    retrieve: Retriever,
    deep: int = 200,
    k0: int = 60,
    fields: tuple[FacetField, ...] = FACET_FIELDS,
    facet_pool: int = 1000,
    bucket_limit: int = 50,
) -> dict[FacetField, FacetResult]:
    """Return own-filter-omitting counts with an explicit retrieval scope."""

    results: dict[FacetField, FacetResult] = {}
    for field in fields:
        candidates: list[str] | None = None
        if query.strip():
            rows = retrieve(
                conn,
                query,
                qv=qv,
                topk=facet_pool,
                deep=max(deep, facet_pool),
                mode=mode,
                k0=k0,
                filters=filters.without(field),
            )
            candidates = [str(row["gse"]) for row in rows]
        buckets = facet_buckets(
            conn,
            field,
            filters=filters,
            candidate_gses=candidates,
            limit=bucket_limit,
        )
        results[field] = FacetResult(
            field=field,
            buckets=buckets,
            scope="candidate_pool" if candidates is not None else "all_matches",
            candidate_count=len(candidates) if candidates is not None else None,
        )
    return results
