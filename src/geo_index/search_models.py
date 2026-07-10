"""Stable internal contracts for normalized search filters and facets."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Mapping, Sequence, TypeAlias


FacetField = Literal[
    "organism_ids", "sex_ids", "assay_categories", "assay_labels"
]
FACET_FIELDS: tuple[FacetField, ...] = (
    "organism_ids",
    "sex_ids",
    "assay_categories",
    "assay_labels",
)
SearchHit: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class SearchFilters:
    """Normalized filters with OR-within and AND-across query semantics."""

    organism_ids: tuple[str, ...] = ()
    sex_ids: tuple[str, ...] = ()
    assay_categories: tuple[str, ...] = ()
    assay_labels: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Sequence[str]] | None
    ) -> SearchFilters:
        source = values or {}
        unknown = sorted(set(source) - set(FACET_FIELDS))
        if unknown:
            raise ValueError(f"unknown filter field: {', '.join(unknown)}")
        normalized: dict[str, tuple[str, ...]] = {}
        for facet in FACET_FIELDS:
            raw_values = source.get(facet, ())
            if isinstance(raw_values, (str, bytes)):
                raise ValueError(f"{facet} must be a sequence of values")
            cleaned: list[str] = []
            for raw in raw_values:
                value = str(raw).strip()
                if not value:
                    raise ValueError(f"{facet} contains a blank value")
                if value not in cleaned:
                    cleaned.append(value)
            normalized[facet] = tuple(cleaned)
        return cls(**normalized)

    def without(self, facet: FacetField) -> SearchFilters:
        if facet not in FACET_FIELDS:
            raise ValueError(f"unknown facet field: {facet}")
        return replace(self, **{facet: ()})

    def as_dict(self) -> dict[str, list[str]]:
        return {facet: list(getattr(self, facet)) for facet in FACET_FIELDS}


@dataclass(frozen=True)
class FacetBucket:
    value: str
    label: str
    count: int


@dataclass(frozen=True)
class FacetResult:
    field: FacetField
    buckets: tuple[FacetBucket, ...]
    scope: Literal["all_matches", "candidate_pool"]
    candidate_count: int | None


@dataclass(frozen=True)
class SearchResponse:
    hits: tuple[SearchHit, ...]
    facets: dict[FacetField, FacetResult] = field(default_factory=dict)
