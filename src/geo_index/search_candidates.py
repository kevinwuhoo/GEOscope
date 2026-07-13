"""Shared candidate model and deterministic source-union policies."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal, Sequence

from .search_models import FACET_FIELDS, SearchFilters


ResultSource = Literal["elasticsearch", "ncbi", "both"]
_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")


@dataclass(frozen=True)
class SearchCandidate:
    gse: str
    title: str | None
    snippet: str | None
    study_type: str | None
    n_samples: int | None
    pubmed_id: int | None
    organism_ids: tuple[str, ...]
    organism_status: str | None
    sex_ids: tuple[str, ...]
    sex_status: str | None
    assay_categories: tuple[str, ...]
    assay_labels: tuple[str, ...]
    assay_status: str | None
    source: ResultSource
    retrieval_score: float | None
    original_rank: int | None
    native_rank: int | None
    taxon: str | None = None
    truncated_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _GSE_RE.fullmatch(self.gse):
            raise ValueError(f"invalid GSE candidate {self.gse!r}")
        if self.source == "elasticsearch" and self.original_rank is None:
            raise ValueError("Elasticsearch candidates require original_rank")
        if self.source == "ncbi" and self.native_rank is None:
            raise ValueError("NCBI candidates require native_rank")
        for rank in (self.original_rank, self.native_rank):
            if rank is not None and rank < 1:
                raise ValueError("candidate ranks must be positive")


def candidate_pool_limit(requested_limit: int, configured_floor: int) -> int:
    return min(100, max(40, configured_floor, requested_limit * 4))


def candidate_matches_filters(
    candidate: SearchCandidate, filters: SearchFilters
) -> bool:
    for field in FACET_FIELDS:
        requested = set(getattr(filters, field))
        available = set(getattr(candidate, field))
        if requested and requested.isdisjoint(available):
            return False
    return True


def merge_candidates(
    elasticsearch: Sequence[SearchCandidate],
    ncbi: Sequence[SearchCandidate],
    filters: SearchFilters,
) -> tuple[SearchCandidate, ...]:
    merged: dict[str, SearchCandidate] = {
        candidate.gse: candidate for candidate in elasticsearch
    }
    seen_ncbi: set[str] = set()
    for native in ncbi:
        if native.gse in seen_ncbi:
            continue
        seen_ncbi.add(native.gse)
        local = merged.get(native.gse)
        if local is not None:
            merged[native.gse] = replace(
                local,
                title=local.title or native.title,
                snippet=local.snippet or native.snippet,
                study_type=local.study_type or native.study_type,
                taxon=local.taxon or native.taxon,
                source="both",
                native_rank=native.native_rank,
            )
        elif candidate_matches_filters(native, filters):
            merged[native.gse] = native
    return tuple(merged.values())


def fallback_order(
    candidates: Sequence[SearchCandidate],
) -> tuple[SearchCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                0 if candidate.original_rank is not None else 1,
                candidate.original_rank or candidate.native_rank or 10_000,
                candidate.gse,
            ),
        )
    )
