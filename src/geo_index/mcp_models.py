"""Strict and bounded transport models for the hosted MCP interface."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .search_candidates import MAX_MERGED_CANDIDATES, MAX_SOURCE_CANDIDATES
from .search_models import FACET_FIELDS, SearchFilters


ResultSource = Literal["elasticsearch", "ncbi", "both"]
DegradationCategory = Literal[
    "ncbi_timeout",
    "ncbi_error",
    "rerank_timeout",
    "rerank_refusal",
    "rerank_invalid",
    "rerank_error",
]
FacetFieldName = Literal[
    "organism_ids", "sex_ids", "assay_categories", "assay_labels"
]
FacetScope = Literal["all_matches", "candidate_pool"]

_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
_ORGANISM_RE = re.compile(r"^NCBITaxon:[1-9][0-9]*$")
_SEX_RE = re.compile(r"^PATO:[0-9]{7}$")

BoundedValue = Annotated[str, Field(min_length=1, max_length=256)]
BoundedStatus = Annotated[str, Field(min_length=1, max_length=256)]
BoundedVersion = Annotated[str, Field(min_length=1, max_length=256)]
Gse = Annotated[str, Field(pattern=r"^GSE[1-9][0-9]*$")]


class _StrictInputModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class SearchLatencyOutput(_StrictOutputModel):
    elasticsearch_ms: int = Field(ge=0)
    ncbi_ms: int = Field(ge=0)
    reranker_ms: int = Field(ge=0)


class SearchProvenanceOutput(_StrictOutputModel):
    exact_accession: bool
    elasticsearch_candidates: int = Field(ge=0, le=MAX_SOURCE_CANDIDATES)
    ncbi_candidates: int = Field(ge=0, le=MAX_SOURCE_CANDIDATES)
    merged_candidates: int = Field(ge=0, le=MAX_MERGED_CANDIDATES)
    rerank_attempted: bool
    rerank_applied: bool
    rerank_model: BoundedValue | None
    rerank_reasoning_effort: Literal["low"] | None
    rerank_thinking: Literal["disabled"] | None
    rerank_input_tokens: int = Field(ge=0)
    rerank_output_tokens: int = Field(ge=0)
    latency: SearchLatencyOutput
    degradation: list[DegradationCategory] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def _validate_reranker_state(self) -> Self:
        if self.rerank_applied and not self.rerank_attempted:
            raise ValueError("applied reranking requires an attempted rerank")
        if self.rerank_attempted != (self.rerank_model is not None):
            raise ValueError("rerank model must agree with attempted state")
        if self.rerank_attempted != (self.rerank_reasoning_effort is not None):
            raise ValueError("reasoning effort must agree with attempted state")
        if self.rerank_attempted != (self.rerank_thinking is not None):
            raise ValueError("thinking must agree with attempted state")
        return self


class SearchFiltersInput(_StrictInputModel):
    organism_ids: list[str] = Field(default_factory=list, max_length=20)
    sex_ids: list[str] = Field(default_factory=list, max_length=20)
    assay_categories: list[str] = Field(default_factory=list, max_length=20)
    assay_labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator(*FACET_FIELDS, mode="after")
    @classmethod
    def _normalize_values(
        cls, values: list[str], info: ValidationInfo
    ) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value:
                raise ValueError(f"{info.field_name} contains a blank value")
            if len(value) > 256:
                raise ValueError(f"{info.field_name} values are limited to 256 characters")
            if info.field_name == "organism_ids" and not _ORGANISM_RE.fullmatch(value):
                raise ValueError("organism_ids must contain NCBITaxon identifiers")
            if info.field_name == "sex_ids" and not _SEX_RE.fullmatch(value):
                raise ValueError("sex_ids must contain seven-digit PATO identifiers")
            if value not in normalized:
                normalized.append(value)
        if len(normalized) > 20:
            raise ValueError(f"{info.field_name} is limited to 20 unique values")
        return normalized

    def to_domain(self) -> SearchFilters:
        return SearchFilters(**{field: tuple(getattr(self, field)) for field in FACET_FIELDS})


def _normalized_query(value: str) -> str:
    query = value.strip()
    if not 1 <= len(query) <= 1000:
        raise ValueError("query must contain between 1 and 1,000 characters")
    return query


class SearchDatasetsInput(_StrictInputModel):
    query: str
    filters: SearchFiltersInput = Field(default_factory=SearchFiltersInput)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("query", mode="after")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        return _normalized_query(value)


class GetDatasetInput(_StrictInputModel):
    gse: str

    @field_validator("gse", mode="after")
    @classmethod
    def _normalize_gse(cls, value: str) -> str:
        gse = value.strip().upper()
        if not _GSE_RE.fullmatch(gse):
            raise ValueError("gse must be a valid GSE accession")
        return gse


class FacetValuesInput(_StrictInputModel):
    field: FacetFieldName
    query: str | None = None
    filters: SearchFiltersInput = Field(default_factory=SearchFiltersInput)
    limit: int = Field(default=50, ge=1, le=50)

    @field_validator("query", mode="after")
    @classmethod
    def _normalize_optional_query(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _normalized_query(value)


class _DatasetMetadata(_StrictOutputModel):
    gse: Gse
    title: Annotated[str, Field(max_length=500)] | None
    study_type: Annotated[str, Field(max_length=200)] | None
    n_samples: Annotated[int, Field(ge=0)] | None
    pubmed_id: Annotated[int, Field(ge=1)] | None
    organism_ids: list[BoundedValue] = Field(max_length=100)
    organism_labels: list[BoundedValue] = Field(max_length=100)
    organism_status: BoundedStatus | None
    sex_ids: list[BoundedValue] = Field(max_length=100)
    sex_status: BoundedStatus | None
    assay_categories: list[BoundedValue] = Field(max_length=100)
    assay_labels: list[BoundedValue] = Field(max_length=100)
    assay_status: BoundedStatus | None
    truncated_fields: list[BoundedValue] = Field(default_factory=list)

    @field_validator("truncated_fields", mode="after")
    @classmethod
    def _sort_truncated_fields(cls, values: list[str]) -> list[str]:
        return sorted(set(values))


class DatasetSummary(_DatasetMetadata):
    rank: int = Field(ge=1, le=50)
    score: float | None
    source: ResultSource
    retrieval_score: float | None
    original_rank: int | None = Field(default=None, ge=1, le=100)
    snippet: Annotated[str, Field(max_length=1000)] | None


class DatasetDetail(_DatasetMetadata):
    summary: Annotated[str, Field(max_length=8000)] | None
    overall_design: Annotated[str, Field(max_length=8000)] | None
    geo_url: AnyHttpUrl
    pubmed_url: AnyHttpUrl | None


class FacetBucketOutput(_StrictOutputModel):
    value: BoundedValue
    label: BoundedValue
    count: int = Field(ge=0)


class FacetResultOutput(_StrictOutputModel):
    field: FacetFieldName
    buckets: list[FacetBucketOutput] = Field(max_length=50)
    scope: FacetScope
    candidate_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_scope_count(self) -> Self:
        if self.scope == "all_matches" and self.candidate_count is not None:
            raise ValueError("all_matches facets must not have a candidate count")
        if self.scope == "candidate_pool" and self.candidate_count is None:
            raise ValueError("candidate_pool facets require a candidate count")
        return self


class SearchDatasetsOutput(_StrictOutputModel):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    filters: SearchFiltersInput
    limit: int = Field(ge=1, le=50)
    retrieval_version: BoundedVersion
    embedding_variant: BoundedValue | None
    results: list[DatasetSummary] = Field(max_length=50)
    facets: dict[FacetFieldName, FacetResultOutput]
    provenance: SearchProvenanceOutput

    @model_validator(mode="after")
    def _validate_facets(self) -> Self:
        if set(self.facets) != set(FACET_FIELDS):
            raise ValueError("search output must contain all four v1 facets")
        if any(name != result.field for name, result in self.facets.items()):
            raise ValueError("facet map keys must match their result fields")
        return self


class GetDatasetOutput(_StrictOutputModel):
    found: bool
    dataset: DatasetDetail | None

    @model_validator(mode="after")
    def _validate_found_dataset(self) -> Self:
        if self.found != (self.dataset is not None):
            raise ValueError("found must agree with dataset presence")
        return self


class FacetValuesOutput(_StrictOutputModel):
    field: FacetFieldName
    buckets: list[FacetBucketOutput] = Field(max_length=50)
    scope: FacetScope
    candidate_count: int | None = Field(default=None, ge=0)
    retrieval_version: BoundedVersion
    embedding_variant: BoundedValue | None

    @model_validator(mode="after")
    def _validate_scope_count(self) -> Self:
        if self.scope == "all_matches" and self.candidate_count is not None:
            raise ValueError("all_matches facets must not have a candidate count")
        if self.scope == "candidate_pool" and self.candidate_count is None:
            raise ValueError("candidate_pool facets require a candidate count")
        return self
