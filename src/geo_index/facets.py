"""Safe SQL construction and aggregation for normalized search facets."""

from __future__ import annotations

from .search_models import FACET_FIELDS, FacetField, SearchFilters


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

