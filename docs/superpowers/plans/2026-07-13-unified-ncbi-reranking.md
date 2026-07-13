# Unified NCBI Fallback and LLM Reranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every GEOscope consumer use exact GSE routing, concurrent Elasticsearch and NCBI candidate generation, and GPT-5.6 Luna reranking that returns ten results by default.

**Architecture:** `McpSearchService` remains the shared lifecycle and orchestration boundary beneath MCP and the marketing API. Focused modules own the canonical candidate model, NCBI E-utilities adaptation, and OpenAI Responses API reranking; the marketing API consumes the same `SearchExecution` as MCP and performs no independent NCBI request.

**Tech Stack:** Python 3.11, Elasticsearch 9.x, FastMCP 3.4.4, FastAPI, `httpx`, OpenAI Python SDK and Responses API Structured Outputs, Pydantic v2, React 19, TypeScript, Zod, Vitest, pytest.

## Global Constraints

- This repository is a prototype for a hackathon.
- Do not use `claude` or `codex` as prefixes for branch names.
- Search correctness and relevance behavior must live in the shared MCP/Elasticsearch layer; do not implement a marketing-only search path.
- Use model ID `gpt-5.6-luna` with `reasoning.effort="low"`.
- Return 10 results by default while preserving the explicit `limit` range of 1 through 50.
- Retrieve a local reranking pool with floor 40, target four times the requested limit, and hard cap 100.
- Query up to 20 NCBI GEO Series candidates for the unmodified user query.
- NCBI-only records are eligible for the final result set and must report `source="ncbi"` with partial metadata marked `unavailable`.
- Exact full-string `GSE[1-9][0-9]*` queries bypass embeddings and reranking.
- Elasticsearch failure fails the request; NCBI or OpenAI failure falls back to deterministic Elasticsearch-first ordering.
- Do not add query rewriting, automatic filter extraction, synchronous SOFT ingestion, or online Elasticsearch writes.
- Preserve unrelated working-tree changes, especially the existing frontend and wiki edits.
- Follow TDD: observe each focused test fail before adding its implementation.

---

## File Structure

- Create `src/geo_index/search_candidates.py`: bounded internal candidate types, filter proof, deduplication, candidate-pool sizing, and deterministic fallback order.
- Create `src/geo_index/ncbi_search.py`: shared E-utilities candidate source and exact-accession fallback.
- Create `src/geo_index/reranker.py`: GPT-5.6 Luna Structured Output request, response validation, usage accounting, and ranked ordering.
- Modify `src/geo_index/mcp_settings.py`: search-quality configuration shared by hosted MCP and the standalone marketing process.
- Modify `src/geo_index/mcp_models.py`: result-source and search-provenance transport contracts.
- Modify `src/geo_index/mcp_search_service.py`: exact routing, concurrent retrieval, merging, reranking, lifecycle, and `SearchExecution`.
- Modify `src/geo_index/marketing_api.py`: thin adapter over `SearchExecution`; remove marketing-owned E-utilities work.
- Modify `src/geo_index/production_app.py`: one shared service lifecycle with no separate GEO comparison object.
- Modify `frontend/src/api.ts` and `frontend/src/components/LiveComparison.tsx`: parse provenance, request ten, and explain NCBI-only/local-only results accurately.
- Modify `frontend/src/styles.css`: source badge treatment, preserving current user styling.
- Create `src/geo_index/search_eval.py` and `eval/unified_search_queries.jsonl`: repeatable retrieval-quality and latency evaluation.
- Modify `pyproject.toml`, `uv.lock`, deployment examples, deployment docs, README, and focused tests.

---

### Task 1: Add search-quality settings and bounded transport contracts

**Files:**
- Modify: `pyproject.toml:8-17`
- Modify: `uv.lock`
- Modify: `src/geo_index/mcp_settings.py:11-154`
- Modify: `src/geo_index/mcp_models.py:20-198`
- Modify: `src/geo_index/mcp_server.py:314-324`
- Modify: `tests/test_mcp_settings.py:8-106`
- Modify: `tests/test_mcp_models.py:24-206`
- Modify: `tests/test_mcp_server.py:49-118`

**Interfaces:**
- Produces: `SearchQualitySettings.from_env(env) -> SearchQualitySettings`.
- Produces: `SearchQualitySettings.disabled() -> SearchQualitySettings` for isolated tests.
- Produces: `ResultSource`, `SearchLatencyOutput`, and `SearchProvenanceOutput` Pydantic output types.
- Extends: `DatasetSummary` with `source`, `retrieval_score`, and `original_rank`.
- Extends: `SearchDatasetsOutput` with `provenance`.

- [ ] **Step 1: Write failing settings and output-contract tests**

Add these focused assertions to `tests/test_mcp_settings.py`:

```python
from geo_index.mcp_settings import SearchQualitySettings


def test_search_quality_defaults_are_bounded_and_disabled() -> None:
    quality = SearchQualitySettings.from_env({})

    assert quality.rerank_enabled is False
    assert quality.openai_api_key is None
    assert quality.rerank_model == "gpt-5.6-luna"
    assert quality.reasoning_effort == "low"
    assert quality.candidate_limit == 40
    assert quality.rerank_timeout_seconds == 8.0
    assert quality.ncbi_timeout_seconds == 5.0


def test_enabled_reranker_requires_openai_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        SearchQualitySettings.from_env({"GEO_RERANK_ENABLED": "true"})

    quality = SearchQualitySettings.from_env(
        {"GEO_RERANK_ENABLED": "true", "OPENAI_API_KEY": " secret "}
    )
    assert quality.rerank_enabled is True
    assert quality.openai_api_key == "secret"
    assert "secret" not in repr(quality)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GEO_RERANK_ENABLED", "yes"),
        ("GEO_RERANK_MODEL", "gpt-5.6-sol"),
        ("GEO_RERANK_REASONING_EFFORT", "medium"),
        ("GEO_RERANK_CANDIDATE_LIMIT", "9"),
        ("GEO_RERANK_CANDIDATE_LIMIT", "101"),
        ("GEO_RERANK_TIMEOUT_SECONDS", "0"),
        ("GEO_NCBI_TIMEOUT_SECONDS", "nan"),
    ],
)
def test_search_quality_settings_fail_closed(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        SearchQualitySettings.from_env({key: value})
```

Update `_summary()` in `tests/test_mcp_models.py` with:

```python
"source": "elasticsearch",
"retrieval_score": 0.75,
"original_rank": 1,
```

Add a provenance fixture and update the top-level contract assertion:

```python
from geo_index.mcp_models import SearchLatencyOutput, SearchProvenanceOutput


def _provenance() -> SearchProvenanceOutput:
    return SearchProvenanceOutput(
        exact_accession=False,
        elasticsearch_candidates=40,
        ncbi_candidates=20,
        merged_candidates=55,
        rerank_attempted=True,
        rerank_applied=True,
        rerank_model="gpt-5.6-luna",
        rerank_reasoning_effort="low",
        rerank_input_tokens=1200,
        rerank_output_tokens=400,
        latency=SearchLatencyOutput(
            elasticsearch_ms=120,
            ncbi_ms=80,
            reranker_ms=200,
        ),
        degradation=[],
    )
```

Pass `provenance=_provenance()` to every `SearchDatasetsOutput` fixture, assert
`SearchDatasetsInput(query="x").limit == 10`, and expect this exact top-level set:

```python
assert set(search.model_dump(mode="json")) == {
    "query",
    "filters",
    "mode",
    "limit",
    "retrieval_version",
    "embedding_variant",
    "results",
    "facets",
    "provenance",
}
```

- [ ] **Step 2: Run focused tests and confirm the new contracts fail**

Run:

```bash
uv run pytest tests/test_mcp_settings.py tests/test_mcp_models.py -q
```

Expected: FAIL because `SearchQualitySettings`, the provenance models, and the new result fields do not exist and the search default is still 15.

- [ ] **Step 3: Implement strict settings parsing**

Add these helpers and type to `src/geo_index/mcp_settings.py`:

```python
def _positive_float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        value = float(env.get(key, str(default)))
    except ValueError as exc:
        raise ValueError(f"{key} must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _strict_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "true" if default else "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{key} must be true or false")
    return raw == "true"


@dataclass(frozen=True)
class SearchQualitySettings:
    openai_api_key: str | None = field(default=None, repr=False)
    rerank_enabled: bool = False
    rerank_model: str = "gpt-5.6-luna"
    reasoning_effort: str = "low"
    candidate_limit: int = 40
    rerank_timeout_seconds: float = 8.0
    ncbi_timeout_seconds: float = 5.0

    @classmethod
    def disabled(cls) -> "SearchQualitySettings":
        return cls()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "SearchQualitySettings":
        enabled = _strict_bool(env, "GEO_RERANK_ENABLED", False)
        api_key = env.get("OPENAI_API_KEY", "").strip() or None
        if enabled and api_key is None:
            raise ValueError("OPENAI_API_KEY is required when reranking is enabled")
        model = env.get("GEO_RERANK_MODEL", "gpt-5.6-luna").strip()
        if model != "gpt-5.6-luna":
            raise ValueError("GEO_RERANK_MODEL must be gpt-5.6-luna")
        effort = env.get("GEO_RERANK_REASONING_EFFORT", "low").strip().lower()
        if effort != "low":
            raise ValueError("GEO_RERANK_REASONING_EFFORT must be low")
        candidate_limit = _positive_int(env, "GEO_RERANK_CANDIDATE_LIMIT", 40)
        if not 10 <= candidate_limit <= 100:
            raise ValueError("GEO_RERANK_CANDIDATE_LIMIT must be between 10 and 100")
        return cls(
            openai_api_key=api_key,
            rerank_enabled=enabled,
            rerank_model=model,
            reasoning_effort=effort,
            candidate_limit=candidate_limit,
            rerank_timeout_seconds=_positive_float(
                env, "GEO_RERANK_TIMEOUT_SECONDS", 8.0
            ),
            ncbi_timeout_seconds=_positive_float(
                env, "GEO_NCBI_TIMEOUT_SECONDS", 5.0
            ),
        )
```

Add this field to `McpSettings` and populate it in `from_env`:

```python
search_quality: SearchQualitySettings = field(
    default_factory=SearchQualitySettings.disabled,
    repr=False,
)
```

```python
search_quality=SearchQualitySettings.from_env(env),
```

- [ ] **Step 4: Implement result and provenance contracts**

Add these aliases and models to `src/geo_index/mcp_models.py`:

```python
ResultSource = Literal["elasticsearch", "ncbi", "both"]
DegradationCategory = Literal[
    "ncbi_timeout",
    "ncbi_error",
    "rerank_timeout",
    "rerank_refusal",
    "rerank_invalid",
    "rerank_error",
]


class SearchLatencyOutput(_StrictOutputModel):
    elasticsearch_ms: int = Field(ge=0)
    ncbi_ms: int = Field(ge=0)
    reranker_ms: int = Field(ge=0)


class SearchProvenanceOutput(_StrictOutputModel):
    exact_accession: bool
    elasticsearch_candidates: int = Field(ge=0, le=100)
    ncbi_candidates: int = Field(ge=0, le=20)
    merged_candidates: int = Field(ge=0, le=120)
    rerank_attempted: bool
    rerank_applied: bool
    rerank_model: BoundedValue | None
    rerank_reasoning_effort: Literal["low"] | None
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
        return self
```

Add these fields to `DatasetSummary`:

```python
source: ResultSource
retrieval_score: float | None
original_rank: int | None = Field(default=None, ge=1, le=100)
```

Change `SearchDatasetsInput.limit` to default 10 and add this required field to
`SearchDatasetsOutput`:

```python
provenance: SearchProvenanceOutput
```

Change the `search_datasets` default in `src/geo_index/mcp_server.py` from 15 to
10. Update MCP test fixtures with the new summary fields and provenance fixture.

- [ ] **Step 5: Add the OpenAI SDK dependency and regenerate the lock**

Add to the default dependencies in `pyproject.toml`:

```toml
"openai>=2,<3",
```

Run:

```bash
uv lock
```

Expected: `uv.lock` adds the official OpenAI Python package without changing the
project's Python floor.

- [ ] **Step 6: Run focused contract tests**

Run:

```bash
uv run pytest tests/test_mcp_settings.py tests/test_mcp_models.py tests/test_mcp_server.py tests/test_production_packaging.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit the contracts**

```bash
git add pyproject.toml uv.lock src/geo_index/mcp_settings.py src/geo_index/mcp_models.py src/geo_index/mcp_server.py tests/test_mcp_settings.py tests/test_mcp_models.py tests/test_mcp_server.py
git commit -m "feat: define unified search contracts"
```

---

### Task 2: Add the canonical candidate and merge policy

**Files:**
- Create: `src/geo_index/search_candidates.py`
- Create: `tests/test_search_candidates.py`

**Interfaces:**
- Produces: immutable `SearchCandidate`.
- Produces: `candidate_pool_limit(requested_limit, configured_floor) -> int`.
- Produces: `candidate_matches_filters(candidate, filters) -> bool`.
- Produces: `merge_candidates(elasticsearch, ncbi, filters) -> tuple[SearchCandidate, ...]`.
- Produces: `fallback_order(candidates) -> tuple[SearchCandidate, ...]`.

- [ ] **Step 1: Write failing merge-policy tests**

Create `tests/test_search_candidates.py`:

```python
from geo_index.search_candidates import (
    SearchCandidate,
    candidate_matches_filters,
    candidate_pool_limit,
    fallback_order,
    merge_candidates,
)
from geo_index.search_models import SearchFilters


def candidate(gse: str, source: str, rank: int, **overrides: object) -> SearchCandidate:
    values: dict[str, object] = {
        "gse": gse,
        "title": f"Title {gse}",
        "snippet": f"Summary {gse}",
        "study_type": "Expression profiling by array",
        "n_samples": None,
        "pubmed_id": None,
        "organism_ids": ("NCBITaxon:10090",),
        "organism_status": "mapped",
        "sex_ids": (),
        "sex_status": "unavailable" if source == "ncbi" else "absent",
        "assay_categories": ("expression (array)",),
        "assay_labels": (),
        "assay_status": "category",
        "source": source,
        "retrieval_score": 0.25 if source == "elasticsearch" else None,
        "original_rank": rank if source == "elasticsearch" else None,
        "native_rank": rank if source == "ncbi" else None,
        "taxon": "Mus musculus",
    }
    values.update(overrides)
    return SearchCandidate(**values)


def test_candidate_pool_floor_target_and_cap() -> None:
    assert candidate_pool_limit(5, 40) == 40
    assert candidate_pool_limit(20, 40) == 80
    assert candidate_pool_limit(50, 40) == 100


def test_merge_prefers_local_metadata_and_marks_both_sources() -> None:
    local = candidate("GSE1", "elasticsearch", 1, title="Indexed title")
    native = candidate("GSE1", "ncbi", 3, title="Native title")

    merged = merge_candidates((local,), (native,), SearchFilters())

    assert len(merged) == 1
    assert merged[0].source == "both"
    assert merged[0].title == "Indexed title"
    assert merged[0].original_rank == 1
    assert merged[0].native_rank == 3


def test_ncbi_only_candidate_must_prove_every_active_filter() -> None:
    native = candidate("GSE2", "ncbi", 1)

    assert candidate_matches_filters(
        native, SearchFilters(organism_ids=("NCBITaxon:10090",))
    )
    assert not candidate_matches_filters(
        native, SearchFilters(sex_ids=("PATO:0000384",))
    )


def test_fallback_keeps_elasticsearch_order_then_ncbi_only_order() -> None:
    candidates = merge_candidates(
        (
            candidate("GSE2", "elasticsearch", 2),
            candidate("GSE1", "elasticsearch", 1),
        ),
        (
            candidate("GSE3", "ncbi", 2),
            candidate("GSE4", "ncbi", 1),
        ),
        SearchFilters(),
    )

    assert [item.gse for item in fallback_order(candidates)] == [
        "GSE1",
        "GSE2",
        "GSE4",
        "GSE3",
    ]
```

- [ ] **Step 2: Run the candidate tests and confirm import failure**

Run:

```bash
uv run pytest tests/test_search_candidates.py -q
```

Expected: FAIL because `geo_index.search_candidates` does not exist.

- [ ] **Step 3: Implement the candidate model and policies**

Create `src/geo_index/search_candidates.py`:

```python
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
    return min(100, max(configured_floor, requested_limit * 4))


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
    for native in ncbi:
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
```

- [ ] **Step 4: Run candidate tests**

Run:

```bash
uv run pytest tests/test_search_candidates.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the candidate boundary**

```bash
git add src/geo_index/search_candidates.py tests/test_search_candidates.py
git commit -m "feat: add unified search candidates"
```

---

### Task 3: Move NCBI retrieval into a shared candidate source

**Files:**
- Create: `src/geo_index/ncbi_search.py`
- Create: `tests/test_ncbi_search.py`
- Reuse: `src/geo_index/eutils.py:32-193`
- Reuse: `src/geo_index/normalize.py:271-283,480-493`

**Interfaces:**
- Consumes: `SearchCandidate` from Task 2.
- Produces: `NativeSearchResult(count, candidates, error)`.
- Produces: `NcbiCandidateSource.search(query, limit=20) -> NativeSearchResult`.
- Produces: `NcbiCandidateSource.lookup(gse) -> SearchCandidate | None`.

- [ ] **Step 1: Write failing NCBI adaptation tests**

Create `tests/test_ncbi_search.py` with a fake E-utilities client. Assert:

```python
from types import SimpleNamespace

from geo_index.ncbi_search import NcbiCandidateSource


class Eutils:
    def __init__(self) -> None:
        self.terms: list[str] = []
        self.closed = False

    def esearch(self, db: str, term: str) -> SimpleNamespace:
        assert db == "gds"
        self.terms.append(term)
        return SimpleNamespace(count=2)

    def esummary_page(
        self, db: str, search: object, retstart: int, retmax: int
    ) -> dict[str, object]:
        assert (db, retstart) == ("gds", 0)
        return {
            "uids": ["1", "2"],
            "1": {
                "entrytype": "GSE",
                "accession": "GSE11803",
                "title": "Mouse exercise",
                "gdstype": "Expression profiling by array",
                "taxon": "Mus musculus",
                "summary": "Skeletal muscle after endurance exercise.",
            },
            "2": {"entrytype": "GPL", "accession": "GPL1"},
        }

    def close(self) -> None:
        self.closed = True


def test_search_returns_normalized_series_candidates() -> None:
    eutils = Eutils()
    source = NcbiCandidateSource(eutils)

    result = source.search("mouse exercise", limit=20)

    assert eutils.terms == ["(mouse exercise) AND gse[ETYP]"]
    assert result.count == 2
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.gse == "GSE11803"
    assert candidate.source == "ncbi"
    assert candidate.organism_ids == ("NCBITaxon:10090",)
    assert candidate.sex_status == "unavailable"
    assert candidate.assay_categories == ("expression (array)",)


def test_exact_lookup_requires_the_requested_accession() -> None:
    eutils = Eutils()
    source = NcbiCandidateSource(eutils)

    found = source.lookup("GSE11803")

    assert eutils.terms == ["GSE11803[ACCN] AND gse[ETYP]"]
    assert found is not None
    assert found.gse == "GSE11803"


def test_close_owns_the_eutils_client() -> None:
    eutils = Eutils()
    source = NcbiCandidateSource(eutils)
    source.close()
    assert eutils.closed
```

- [ ] **Step 2: Run the NCBI tests and confirm import failure**

Run:

```bash
uv run pytest tests/test_ncbi_search.py -q
```

Expected: FAIL because `geo_index.ncbi_search` does not exist.

- [ ] **Step 3: Implement the bounded NCBI source**

Create `src/geo_index/ncbi_search.py` with these concrete rules:

```python
"""Live NCBI GEO candidate retrieval shared by every search transport."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Literal, Protocol

from .eutils import EutilsClient, SearchResult
from .normalize import map_assay, map_organisms
from .search_candidates import SearchCandidate


_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
NativeError = Literal["ncbi_timeout", "ncbi_error"]


class EutilsProtocol(Protocol):
    def esearch(self, db: str, term: str) -> SearchResult:
        raise NotImplementedError

    def esummary_page(
        self, db: str, search: SearchResult, retstart: int, retmax: int
    ) -> dict[str, object]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class NativeSearchResult:
    count: int | None
    candidates: tuple[SearchCandidate, ...]
    error: NativeError | None = None

    @classmethod
    def unavailable(cls, error: NativeError) -> "NativeSearchResult":
        return cls(count=None, candidates=(), error=error)


class NcbiCandidateSource:
    def __init__(self, client: EutilsProtocol | None = None) -> None:
        self._client = client or EutilsClient()
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _candidate(raw: object, rank: int) -> SearchCandidate | None:
        if not isinstance(raw, dict):
            return None
        if str(raw.get("entrytype", "")).upper() != "GSE":
            return None
        gse = str(raw.get("accession") or "").upper()
        if not _GSE_RE.fullmatch(gse):
            return None
        title = str(raw.get("title") or "")[:500] or None
        summary = str(raw.get("summary") or "")[:1000] or None
        study_type = str(raw.get("gdstype") or "")[:200] or None
        taxon = str(raw.get("taxon") or "")[:256] or None
        organism_ids, organism_status = map_organisms(taxon)
        categories, labels, assay_status = map_assay(
            study_type or "",
            " ".join(value for value in (study_type, title, summary) if value),
        )
        return SearchCandidate(
            gse=gse,
            title=title,
            snippet=summary,
            study_type=study_type,
            n_samples=None,
            pubmed_id=None,
            organism_ids=tuple(organism_ids),
            organism_status=organism_status,
            sex_ids=(),
            sex_status="unavailable",
            assay_categories=tuple(categories),
            assay_labels=tuple(labels),
            assay_status=assay_status if categories or labels else "unavailable",
            source="ncbi",
            retrieval_score=None,
            original_rank=None,
            native_rank=rank,
            taxon=taxon,
        )

    def _search_term(self, term: str, limit: int) -> NativeSearchResult:
        with self._lock:
            search = self._client.esearch("gds", term)
            if search.count == 0:
                return NativeSearchResult(count=0, candidates=())
            page = self._client.esummary_page(
                "gds", search, 0, min(max(limit * 3, limit), 100)
            )
        candidates: list[SearchCandidate] = []
        for uid in page.get("uids", []):
            candidate = self._candidate(page.get(str(uid)), len(candidates) + 1)
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return NativeSearchResult(count=search.count, candidates=tuple(candidates))

    def search(self, query: str, limit: int = 20) -> NativeSearchResult:
        if not 1 <= limit <= 20:
            raise ValueError("NCBI candidate limit must be between 1 and 20")
        return self._search_term(f"({query}) AND gse[ETYP]", limit)

    def lookup(self, gse: str) -> SearchCandidate | None:
        if not _GSE_RE.fullmatch(gse):
            raise ValueError("lookup requires a normalized GSE accession")
        result = self._search_term(f"{gse}[ACCN] AND gse[ETYP]", 1)
        return next(
            (candidate for candidate in result.candidates if candidate.gse == gse),
            None,
        )
```

- [ ] **Step 4: Run NCBI and normalizer tests**

Run:

```bash
uv run pytest tests/test_ncbi_search.py tests/test_normalize.py tests/test_assay_rules.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the NCBI source**

```bash
git add src/geo_index/ncbi_search.py tests/test_ncbi_search.py
git commit -m "feat: add shared NCBI candidates"
```

---

### Task 4: Add the GPT-5.6 Luna Structured Output reranker

**Files:**
- Create: `src/geo_index/reranker.py`
- Create: `tests/test_reranker.py`

**Interfaces:**
- Consumes: `SearchCandidate` from Task 2.
- Produces: `RerankResult(scores, input_tokens, output_tokens)`.
- Produces: `OpenAIReranker.rerank(query, candidates, limit) -> RerankResult`.
- Produces: `rank_candidates(candidates, result) -> tuple[SearchCandidate, ...]`.
- Raises: `RerankRefusalError` or `InvalidRerankOutputError` for fail-open handling by Task 5.

- [ ] **Step 1: Write failing request, validation, and tie-order tests**

Create `tests/test_reranker.py` using a fake `responses.create` client. Cover:

```python
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from geo_index.reranker import (
    InvalidRerankOutputError,
    OpenAIReranker,
    RerankResult,
    rank_candidates,
)
from geo_index.search_candidates import SearchCandidate


def candidate(gse: str, original_rank: int) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title=f"Title {gse}",
        snippet="Mouse skeletal muscle after endurance exercise.",
        study_type="Expression profiling by array",
        n_samples=10,
        pubmed_id=None,
        organism_ids=("NCBITaxon:10090",),
        organism_status="mapped",
        sex_ids=(),
        sex_status="absent",
        assay_categories=("expression (array)",),
        assay_labels=(),
        assay_status="category",
        source="elasticsearch",
        retrieval_score=0.2,
        original_rank=original_rank,
        native_rank=None,
        taxon="Mus musculus",
    )


class Responses:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text=json.dumps(self.output),
            output=[],
            usage=SimpleNamespace(input_tokens=120, output_tokens=30),
        )


class Client:
    def __init__(self, output: dict[str, object]) -> None:
        self.responses = Responses(output)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_reranker_uses_luna_low_reasoning_and_strict_schema() -> None:
    client = Client(
        {
            "rankings": [
                {"gse": "GSE2", "relevance_score": 95},
                {"gse": "GSE1", "relevance_score": 80},
            ]
        }
    )
    reranker = OpenAIReranker(
        api_key="secret",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=8,
        client=client,
    )

    result = reranker.rerank(
        "mouse exercise",
        (candidate("GSE1", 1), candidate("GSE2", 2)),
        limit=10,
    )

    assert result.scores == {"GSE2": 95, "GSE1": 80}
    assert (result.input_tokens, result.output_tokens) == (120, 30)
    assert client.responses.kwargs is not None
    assert client.responses.kwargs["model"] == "gpt-5.6-luna"
    assert client.responses.kwargs["reasoning"] == {"effort": "low"}
    assert client.responses.kwargs["store"] is False
    text = client.responses.kwargs["text"]
    assert isinstance(text, dict)
    assert text["format"]["strict"] is True
    payload = json.loads(client.responses.kwargs["input"])
    assert payload["requested_results"] == 10
    assert [item["gse"] for item in payload["candidates"]] == ["GSE1", "GSE2"]


def test_reranker_rejects_missing_duplicate_or_unknown_ids() -> None:
    for rankings in (
        [{"gse": "GSE1", "relevance_score": 90}],
        [
            {"gse": "GSE1", "relevance_score": 90},
            {"gse": "GSE1", "relevance_score": 80},
        ],
        [
            {"gse": "GSE1", "relevance_score": 90},
            {"gse": "GSE9", "relevance_score": 80},
        ],
    ):
        reranker = OpenAIReranker(
            api_key="secret",
            model="gpt-5.6-luna",
            reasoning_effort="low",
            timeout_seconds=8,
            client=Client({"rankings": rankings}),
        )
        with pytest.raises(InvalidRerankOutputError):
            reranker.rerank(
                "query",
                (candidate("GSE1", 1), candidate("GSE2", 2)),
                limit=10,
            )


def test_reranker_bounds_candidate_text_before_the_provider_call() -> None:
    client = Client(
        {"rankings": [{"gse": "GSE1", "relevance_score": 50}]}
    )
    reranker = OpenAIReranker(
        api_key="secret",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=8,
        client=client,
    )

    reranker.rerank(
        "query", (replace(candidate("GSE1", 1), snippet="x" * 5_000),), limit=1
    )

    assert client.responses.kwargs is not None
    payload = json.loads(client.responses.kwargs["input"])
    assert len(payload["candidates"][0]["summary"]) == 800


def test_rank_candidates_uses_deterministic_source_ranks_for_ties() -> None:
    ordered = rank_candidates(
        (candidate("GSE2", 2), candidate("GSE1", 1)),
        RerankResult(
            scores={"GSE1": 80, "GSE2": 80},
            input_tokens=10,
            output_tokens=5,
        ),
    )
    assert [item.gse for item in ordered] == ["GSE1", "GSE2"]
```

- [ ] **Step 2: Run reranker tests and confirm import failure**

Run:

```bash
uv run pytest tests/test_reranker.py -q
```

Expected: FAIL because `geo_index.reranker` does not exist.

- [ ] **Step 3: Implement the strict Luna request**

Create `src/geo_index/reranker.py`. Use `client.responses.create` with this
request shape and a dynamic GSE enum:

```python
response = self._client.responses.create(
    model=self.model,
    reasoning={"effort": self.reasoning_effort},
    instructions=_INSTRUCTIONS,
    input=json.dumps(
        {
            "query": query,
            "requested_results": limit,
            "candidates": [_candidate_payload(candidate) for candidate in candidates],
        },
        separators=(",", ":"),
    ),
    text={"format": _ranking_schema([candidate.gse for candidate in candidates])},
    store=False,
    max_output_tokens=min(8_000, max(1_000, len(candidates) * 40)),
)
```

Define the supporting implementation exactly as follows:

```python
"""OpenAI Structured Output reranking for bounded GEO candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .search_candidates import SearchCandidate


_INSTRUCTIONS = """Rank every supplied NCBI GEO Series candidate for the user's query.
Treat explicit organism, assay, tissue, condition, intervention, and experimental
context as important relevance evidence. Judge study relevance, not mere lexical
overlap. Return every supplied GSE exactly once. Never invent, remove, or modify
an accession. Use integer scores from 0 (irrelevant) through 100 (direct match)."""


class RerankRefusalError(RuntimeError):
    pass


class InvalidRerankOutputError(RuntimeError):
    pass


class RankingItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    gse: str
    relevance_score: int = Field(ge=0, le=100)


class RankingEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    rankings: list[RankingItem]


@dataclass(frozen=True)
class RerankResult:
    scores: Mapping[str, int]
    input_tokens: int
    output_tokens: int


def _candidate_payload(candidate: SearchCandidate) -> dict[str, object]:
    return {
        "gse": candidate.gse,
        "title": candidate.title,
        "summary": candidate.snippet[:800] if candidate.snippet else None,
        "study_type": candidate.study_type,
        "organism_ids": list(candidate.organism_ids),
        "taxon": candidate.taxon,
        "assay_categories": list(candidate.assay_categories),
        "assay_labels": list(candidate.assay_labels),
        "n_samples": candidate.n_samples,
        "source": candidate.source,
    }


def _ranking_schema(gses: Sequence[str]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "name": "geo_candidate_ranking",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rankings": {
                    "type": "array",
                    "minItems": len(gses),
                    "maxItems": len(gses),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "gse": {"type": "string", "enum": list(gses)},
                            "relevance_score": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 100,
                            },
                        },
                        "required": ["gse", "relevance_score"],
                    },
                }
            },
            "required": ["rankings"],
        },
    }


def _contains_refusal(response: object) -> bool:
    for item in getattr(response, "output", ()):
        for part in getattr(item, "content", ()):
            if getattr(part, "type", None) == "refusal":
                return True
    return False


class OpenAIReranker:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._client = client or OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )

    def close(self) -> None:
        self._client.close()

    def rerank(
        self, query: str, candidates: Sequence[SearchCandidate], *, limit: int
    ) -> RerankResult:
        if not candidates:
            return RerankResult(scores={}, input_tokens=0, output_tokens=0)
        if not 1 <= limit <= 50:
            raise ValueError("rerank result limit must be between 1 and 50")
        response = self._client.responses.create(
            model=self.model,
            reasoning={"effort": self.reasoning_effort},
            instructions=_INSTRUCTIONS,
            input=json.dumps(
                {
                    "query": query,
                    "requested_results": limit,
                    "candidates": [
                        _candidate_payload(candidate) for candidate in candidates
                    ],
                },
                separators=(",", ":"),
            ),
            text={
                "format": _ranking_schema(
                    [candidate.gse for candidate in candidates]
                )
            },
            store=False,
            max_output_tokens=min(8_000, max(1_000, len(candidates) * 40)),
        )
        if _contains_refusal(response):
            raise RerankRefusalError("reranker refused the request")
        try:
            parsed = RankingEnvelope.model_validate_json(response.output_text)
        except (ValidationError, ValueError, TypeError) as exc:
            raise InvalidRerankOutputError("reranker returned invalid JSON") from exc
        received = [item.gse for item in parsed.rankings]
        expected = [candidate.gse for candidate in candidates]
        if len(received) != len(set(received)) or set(received) != set(expected):
            raise InvalidRerankOutputError(
                "reranker candidate identifiers do not match the request"
            )
        usage = getattr(response, "usage", None)
        return RerankResult(
            scores={item.gse: item.relevance_score for item in parsed.rankings},
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )


def rank_candidates(
    candidates: Sequence[SearchCandidate], result: RerankResult
) -> tuple[SearchCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -result.scores[candidate.gse],
                candidate.original_rank or 10_000,
                candidate.native_rank or 10_000,
                candidate.gse,
            ),
        )
    )
```

- [ ] **Step 4: Run reranker tests**

Run:

```bash
uv run pytest tests/test_reranker.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the reranker**

```bash
git add src/geo_index/reranker.py tests/test_reranker.py
git commit -m "feat: add Luna candidate reranking"
```

---

### Task 5: Orchestrate exact lookup, concurrent source retrieval, and fail-open ranking

**Files:**
- Modify: `src/geo_index/mcp_search_service.py:35-455`
- Modify: `tests/test_mcp_search_service.py:1-332`
- Modify: `tests/test_mcp_elasticsearch_smoke.py:1-80`

**Interfaces:**
- Consumes: `SearchQualitySettings`, `SearchCandidate`, `NativeSearchResult`, `NcbiCandidateSource`, `OpenAIReranker`, and `RerankResult`.
- Produces: `SearchExecution(output, native, candidates)`.
- Produces: `McpSearchService.search_execution(...) -> SearchExecution`.
- Preserves: `McpSearchService.search_datasets(...) -> SearchDatasetsOutput` by returning `search_execution(...).output`.

- [ ] **Step 1: Extend service fakes and write failing exact-routing tests**

Add injected `FakeNativeSource` and `FakeReranker` test doubles to
`tests/test_mcp_search_service.py`. Add tests that assert:

```python
def test_exact_indexed_gse_bypasses_embedding_search_ncbi_and_reranking() -> None:
    service, client, domain, _, _ = _service(
        exact_document=_document("GSE310900"),
        native=FakeNativeSource(),
        reranker=FakeReranker(),
    )
    service.open()

    output = service.search_datasets(
        query="  gse310900 ", filters=SearchFilters(), mode="hybrid", limit=10
    )

    assert [result.gse for result in output.results] == ["GSE310900"]
    assert output.results[0].source == "elasticsearch"
    assert output.provenance.exact_accession is True
    assert domain.search_calls == []
    assert domain.encode_calls == []
    assert service._ncbi_source.lookup_calls == []
    assert service._reranker.rerank_calls == []


def test_exact_gse_missing_locally_uses_ncbi_without_reranking() -> None:
    native = FakeNativeSource(exact=_native_candidate("GSE310900", 1))
    service, _, domain, _, _ = _service(
        exact_document=None,
        native=native,
        reranker=FakeReranker(),
    )
    service.open()

    output = service.search_datasets(
        query="GSE310900", filters=SearchFilters(), mode="hybrid", limit=10
    )

    assert output.results[0].source == "ncbi"
    assert output.results[0].gse == "GSE310900"
    assert native.lookup_calls == ["GSE310900"]
    assert domain.search_calls == []
    assert service._reranker.rerank_calls == []


def test_exact_ncbi_record_that_cannot_prove_filter_returns_no_results() -> None:
    native = FakeNativeSource(exact=_native_candidate("GSE310900", 1))
    service, _, domain, _, _ = _service(
        exact_document=None,
        native=native,
        reranker=FakeReranker(),
        facet_values={"sex_ids": ("PATO:0000384",)},
    )
    service.open()

    output = service.search_datasets(
        query="GSE310900",
        filters=SearchFilters(sex_ids=("PATO:0000384",)),
        mode="hybrid",
        limit=10,
    )

    assert output.results == []
    assert output.provenance.exact_accession is True
    assert output.provenance.merged_candidates == 0
    assert native.lookup_calls == ["GSE310900"]
    assert domain.search_calls == []
```

- [ ] **Step 2: Write failing natural-query orchestration tests**

Add tests proving:

```python
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
        mode="hybrid",
        limit=10,
    )

    assert domain.search_calls[0]["topk"] == 40
    assert native.search_calls == [("mouse exercise", 20)]
    assert len(execution.output.results) == 10
    assert execution.output.results[0].gse == "GSE999999"
    assert execution.output.results[0].source == "ncbi"
    assert execution.output.provenance.rerank_applied is True


def test_ncbi_and_reranker_failures_keep_elasticsearch_order() -> None:
    service, _, _, _, _ = _service(
        native=FakeNativeSource(error=TimeoutError("NCBI timeout")),
        reranker=FakeReranker(error=RuntimeError("OpenAI unavailable")),
    )
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), mode="hybrid", limit=10
    )

    assert [result.original_rank for result in output.results] == list(range(1, 11))
    assert output.provenance.rerank_applied is False
    assert output.provenance.degradation == ["ncbi_timeout", "rerank_error"]


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
        query="immune", filters=SearchFilters(), mode="hybrid", limit=10
    )

    assert [result.original_rank for result in output.results] == list(range(1, 11))
    assert output.provenance.rerank_applied is False
    assert output.provenance.degradation == [category]
```

Give both fakes an optional `on_search` callback and add this concurrency test;
if either retrieval starts only after the other returns, the barrier breaks:

```python
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
        query="immune", filters=SearchFilters(), mode="hybrid", limit=10
    )

    assert native.search_calls == [("immune", 20)]
```

- [ ] **Step 3: Run the focused service tests and observe failures**

Run:

```bash
uv run pytest tests/test_mcp_search_service.py -q
```

Expected: FAIL because `search_execution`, exact routing, candidate merging, and
the injected shared resources do not exist.

- [ ] **Step 4: Add shared execution and resource protocols**

Add these service-level types near the existing protocols in
`src/geo_index/mcp_search_service.py`:

```python
@dataclass(frozen=True)
class SearchExecution:
    output: SearchDatasetsOutput
    native: NativeSearchResult
    candidates: tuple[SearchCandidate, ...]


class NativeSource(Protocol):
    def search(self, query: str, limit: int = 20) -> NativeSearchResult:
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
```

Import `Generic`, `TypeVar`, `httpx`, `APITimeoutError`, `SearchResponse`, the
two reranker validation exceptions, and the bounded error aliases. Add the
timing and categorization helpers below so failure paths retain latency without
ever serializing provider exception text:

```python
T = TypeVar("T")


@dataclass(frozen=True)
class _TimedCall(Generic[T]):
    value: T | None
    error: Exception | None
    elapsed_ms: int


def _capture_timed(
    clock: Callable[[], float], function: Callable[..., T], *args: object, **kwargs: object
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


def _has_cause(exc: BaseException, kinds: tuple[type[BaseException], ...]) -> bool:
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
```

Extend `McpSearchService.__init__` with:

```python
quality: SearchQualitySettings | None = None,
ncbi_source_factory: Callable[[float], NativeSource] | None = None,
reranker_factory: Callable[[SearchQualitySettings], CandidateReranker] | None = None,
clock: Callable[[], float] = time.perf_counter,
```

Store the quality settings and factories, and initialize `_ncbi_source` and
`_reranker` to `None`. The defaults are:

```python
self.quality = quality or SearchQualitySettings.disabled()
self._ncbi_source_factory = ncbi_source_factory or (
    lambda timeout: NcbiCandidateSource(
        EutilsClient(timeout=timeout, max_retries=1)
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
```

Update `from_settings` to pass `quality=settings.search_quality`. During `open`,
create the NCBI source after Elasticsearch readiness succeeds and create the
reranker only when enabled. If either factory fails, close all resources already
created and leave the service closed. During `close`, close and clear the
reranker and NCBI source before closing the encoder and Elasticsearch client.

- [ ] **Step 5: Add local candidate and bounded output helpers**

Implement private helpers with these signatures:

```python
def _local_candidates(
    self,
    client: object,
    search: DomainSearch,
    *,
    query: str,
    filters: SearchFilters,
    mode: str,
    topk: int,
) -> tuple[tuple[SearchCandidate, ...], SearchResponse]:
```

It calls `search.search(..., topk=topk, bucket_limit=50)`, hydrates the returned
hits, and creates one `SearchCandidate(source="elasticsearch")` per hit with
`original_rank` starting at one and `retrieval_score` copied from the hit.
Factor document conversion into
`_candidate_from_document(document, *, original_rank, retrieval_score)` so the
same bounded mapping is used for local exact results.

Implement:

```python
def _summary_from_candidate(
    self,
    candidate: SearchCandidate,
    *,
    rank: int,
    final_score: float | None,
) -> DatasetSummary:
```

It applies the existing `_cap_text` and `_cap_array` bounds, sets `source`,
`retrieval_score`, and `original_rank`, and uses the candidate's partial metadata
without claiming unavailable NCBI fields are absent.

Implement exact-result facets:

```python
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
                for value in values
            ],
            scope="candidate_pool",
            candidate_count=candidate_count,
        )
    return results
```

- [ ] **Step 6: Implement exact-accession execution**

Implement private signature
`_exact_execution(self, gse: str, *, filters: SearchFilters, mode: str,
limit: int, search: DomainSearch, native_source: NativeSource) ->
SearchExecution`. Capture the local direct lookup
with `_capture_timed`. If it returns a document, convert it with
`_candidate_from_document` and do not call NCBI. If it returns `None`, capture
exactly one `native_source.lookup(gse)` call; categorize an exception and
continue with no result. Apply `candidate_matches_filters` to the resolved
candidate and set the eligible candidate to `None` when any active filter is
unproved or contradicted.

Build exact provenance with these values:

```python
SearchProvenanceOutput(
    exact_accession=True,
    elasticsearch_candidates=1 if local_document is not None else 0,
    ncbi_candidates=len(native.candidates),
    merged_candidates=1 if eligible is not None else 0,
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
)
```

Return zero or one summary and `_exact_facets(eligible)`. Use retrieval version
`geo-series-v1:exact-accession` when the accession resolved locally,
`ncbi-gds:exact-accession-v1` when it resolved only through NCBI, and
`geo-series-v1:exact-accession-miss` when neither source found it. A resolved
record keeps its source retrieval version even when filters exclude it. Always set
`embedding_variant=None`; never call the query encoder or reranker.

Use `NativeSearchResult(count=None, candidates=())` when NCBI was bypassed. For
the fallback lookup, use `count=1` with the one returned candidate, `count=0`
when it returns `None`, or `NativeSearchResult.unavailable(category)` on an
exception; append only that bounded category to exact-search degradation.

- [ ] **Step 7: Implement concurrent natural-language execution**

Replace the current `search_datasets` body with:

```python
def search_datasets(
    self, *, query: str, filters: SearchFilters, mode: str, limit: int
) -> SearchDatasetsOutput:
    return self.search_execution(
        query=query,
        filters=filters,
        mode=mode,
        limit=limit,
    ).output
```

Implement `search_execution` with this sequence:

```python
query = self._validate_search_request(query, filters, mode, limit)
self._require_filters(filters)
client, search = self._require_open()
if self._ncbi_source is None:
    raise RuntimeError("McpSearchService NCBI source is not open")
native_source = self._ncbi_source
if _GSE_RE.fullmatch(query.upper()):
    return self._exact_execution(
        query.upper(),
        filters=filters,
        mode=mode,
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
        mode=mode,
        topk=pool_size,
    )
    ncbi_future = executor.submit(
        _capture_timed, self._clock, native_source.search, query, 20
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

merged = merge_candidates(local_candidates, native.candidates, filters)
ordered = fallback_order(merged)
rerank_result = RerankResult(scores={}, input_tokens=0, output_tokens=0)
rerank_attempted = self._reranker is not None and len(merged) > 1
rerank_applied = False
if rerank_attempted:
    rerank_call = _capture_timed(
        self._clock, self._reranker.rerank, query, merged, limit=limit
    )
    reranker_ms = rerank_call.elapsed_ms
    if rerank_call.error is None and rerank_call.value is not None:
        rerank_result = rerank_call.value
        ordered = rank_candidates(merged, rerank_result)
        rerank_applied = True
    elif rerank_call.error is not None:
        degradation.append(_rerank_error_category(rerank_call.error))
```

Initialize `reranker_ms = 0` before the optional call. Set final `score` from the
validated reranker score only when `rerank_applied`; otherwise use the local
retrieval score when present. Slice `ordered[:limit]`, preserve facets and
retrieval version from the Elasticsearch response, and populate provenance with
`local_call.elapsed_ms`, `native_call.elapsed_ms`, `reranker_ms`, source counts,
usage tokens, model/effort only when attempted, and the bounded degradation
list. Return `SearchExecution(output=output, native=native, candidates=merged)`.
If NCBI degrades and reranking succeeds on local candidates, report both facts
without discarding the reranked local order.

- [ ] **Step 8: Run service and smoke tests**

Run:

```bash
uv run pytest tests/test_mcp_search_service.py tests/test_mcp_elasticsearch_smoke.py tests/test_mcp_server.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit shared orchestration**

```bash
git add src/geo_index/mcp_search_service.py tests/test_mcp_search_service.py tests/test_mcp_elasticsearch_smoke.py tests/test_mcp_server.py
git commit -m "feat: unify GEO search orchestration"
```

---

### Task 6: Make marketing and frontend consume the shared execution

**Files:**
- Modify: `src/geo_index/marketing_api.py:1-227`
- Modify: `src/geo_index/production_app.py:5-89`
- Modify: `tests/test_marketing_api.py:1-241`
- Modify: `tests/test_production_app.py:12-104`
- Modify: `frontend/src/api.ts:1-67`
- Modify: `frontend/src/components/LiveComparison.tsx:1-202`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/App.test.tsx:8-189`

**Interfaces:**
- Consumes: `SearchExecution` from Task 5.
- Removes: marketing-owned `GeoComparison`, `EutilsGeoComparison`, `geo_factory`, and membership ESearch.
- Preserves: `/api/demo/search` response keys `query`, `mode`, `geo`, `geoscope`, and `membership`.

- [ ] **Step 1: Rewrite failing marketing tests around one shared execution**

Replace the fake marketing service's `search_datasets` method with
`search_execution`, returning:

```python
return SearchExecution(
    output=_search_output(),
    native=NativeSearchResult(
        count=1,
        candidates=(_native_candidate("GSE999", 1),),
    ),
    candidates=(_local_candidate("GSE123", 1), _native_candidate("GSE999", 1)),
)
```

Update `test_demo_search_uses_shared_mcp_service_and_returns_comparison` to
assert one service call, ten as the default limit, native results from the same
execution, and membership derived from source provenance. Assert
`payload["geoscope"] == execution.output.model_dump(mode="json")` and compare
its ordered GSE list with the ordered GSE list returned by the in-memory MCP
`search_datasets` tool fixture. This is the transport parity assertion: neither
adapter is allowed to sort or filter the shared final results. Delete the unit
test for `EutilsGeoComparison`; its behavior now belongs to
`tests/test_ncbi_search.py`.

Update `tests/test_production_app.py` so `create_app` receives only `settings`,
`service`, and `static_dir`; delete `FakeGeo`.

- [ ] **Step 2: Run backend adapter tests and confirm old ownership fails**

Run:

```bash
uv run pytest tests/test_marketing_api.py tests/test_production_app.py -q
```

Expected: FAIL because marketing still owns and calls its separate GEO object.

- [ ] **Step 3: Remove marketing-owned NCBI lifecycle and serialize shared native results**

In `src/geo_index/marketing_api.py`:

- Delete `GeoComparison`, `EutilsGeoComparison`, `geo_factory`, `app.state.geo`,
  and their close logic.
- Extend `SearchService` with `search_execution`.
- Change the route declaration to `Query(default=10, ge=1, le=50)` so its
  explicit range matches MCP.
- Call `service.search_execution` once in `asyncio.to_thread`.
- Serialize `execution.native.candidates` into the existing native-card fields.
- Map `execution.native.error` to the existing user-safe unavailable message.
- Build membership with this exact rule, preserving `None` when the shared NCBI
  call degraded so an outage is never presented as a negative match:

```python
membership = (
    None
    if execution.native.error is not None or execution.native.count is None
    else {
        result.gse: result.source in {"ncbi", "both"}
        for result in execution.output.results
    }
)
```

The membership value now means “present in the displayed NCBI top 20,” not
“present anywhere in all native matches.”

In `src/geo_index/production_app.py`, remove the separate `geo` argument,
construction, state, and close path. `McpSearchService` owns Elasticsearch,
NCBI, and OpenAI for both transports.

Update the standalone marketing `_default_service_factory` as well; it cannot
silently omit the shared quality configuration:

```python
return McpSearchService(
    elasticsearch=ElasticsearchSettings.from_env(os.environ),
    quality=SearchQualitySettings.from_env(os.environ),
)
```

Add a production test that sets reranking environment values, invokes this
factory through `create_app`, and asserts the constructed shared service sees
the enabled Luna settings. This keeps the combined App Platform process and the
standalone marketing entrypoint on the same orchestration behavior.

- [ ] **Step 4: Run backend adapter tests**

Run:

```bash
uv run pytest tests/test_marketing_api.py tests/test_production_app.py -q
```

Expected: PASS.

- [ ] **Step 5: Write failing frontend tests for ten results and source labels**

Update `demoResponse` in `frontend/src/App.test.tsx` with provenance and result
source fields. Add one NCBI-only unified result and assert:

```typescript
expect(fetchMock.mock.calls[0]?.[0]).toEqual(
  expect.stringContaining("limit=10"),
);
expect(
  screen.getByText(/live ncbi result · not yet indexed/i),
).toBeInTheDocument();
expect(
  screen.getByText(/not in the displayed ncbi top 20/i),
).toBeInTheDocument();
```

Run:

```bash
pnpm --dir frontend exec vitest run src/App.test.tsx -t "live GEO comparison"
```

Expected: FAIL because the client still requests eight and the UI has no source
labels.

- [ ] **Step 6: Update the TypeScript contract and presentation**

In `frontend/src/api.ts`, add to `geoscopeResultSchema`:

```typescript
source: z.enum(["elasticsearch", "ncbi", "both"]),
retrieval_score: z.number().nullable(),
original_rank: z.number().int().positive().nullable(),
```

Add the bounded provenance object to the GEOscope response schema, and change:

```typescript
const params = new URLSearchParams({ q: query, mode, limit: "10" });
```

In `LiveComparison.tsx`:

- Render `Live NCBI result · not yet indexed` when `source === "ncbi"`.
- Render `Found by both GEOscope and displayed NCBI results` when
  `source === "both"`.
- Replace the old miss label with `Not in the displayed NCBI top 20` when
  membership is false.
- Keep the final reranked `rank` as the displayed ordinal.
- Update the success count to expect ten requested GEOscope results.

Add source-badge CSS using existing color variables; do not overwrite unrelated
style edits.

- [ ] **Step 7: Run frontend and backend tests**

Run:

```bash
pnpm --dir frontend test
pnpm --dir frontend build
uv run pytest tests/test_marketing_api.py tests/test_production_app.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit the shared adapters**

```bash
git add src/geo_index/marketing_api.py src/geo_index/production_app.py tests/test_marketing_api.py tests/test_production_app.py frontend/src/api.ts frontend/src/components/LiveComparison.tsx frontend/src/styles.css frontend/src/App.test.tsx
git commit -m "feat: expose unified search everywhere"
```

---

### Task 7: Add evaluation, deployment configuration, and end-to-end verification

**Files:**
- Create: `src/geo_index/search_eval.py`
- Create: `tests/test_search_eval.py`
- Create: `tests/test_reranker_live.py`
- Create: `eval/unified_search_queries.jsonl`
- Modify: `pyproject.toml:27-45`
- Modify: `deploy/geo-mcp.env.example`
- Modify: `deploy/app-platform.env.example`
- Modify: `docs/deployment/digitalocean.md`
- Modify: `README.md`
- Modify: `tests/test_primary_path_docs.py`

**Interfaces:**
- Consumes: `McpSearchService.search_execution` and reranker usage provenance.
- Produces: `geo-search-eval` JSON report with Recall@40, nDCG@10, MRR, constraint violations, NCBI-only recovery, latency, fallback rate, token use, and caller-supplied cost estimate.

- [ ] **Step 1: Write failing metric tests**

Create `tests/test_search_eval.py`:

```python
from geo_index.search_eval import estimated_cost, ndcg_at, recall_at, reciprocal_rank


def test_retrieval_metrics_are_bounded_and_deterministic() -> None:
    judgments = {"GSE1": 3, "GSE2": 2, "GSE3": 1}
    assert recall_at(["GSE1", "GSE9", "GSE2"], judgments, 3) == 2 / 3
    assert reciprocal_rank(["GSE9", "GSE2"], judgments) == 0.5
    assert ndcg_at(["GSE1", "GSE2", "GSE3"], judgments, 10) == 1.0


def test_cost_uses_explicit_current_prices_not_hard_coded_prices() -> None:
    assert estimated_cost(
        input_tokens=1_000_000,
        output_tokens=500_000,
        input_cost_per_million=0.25,
        output_cost_per_million=2.0,
    ) == 1.25
```

- [ ] **Step 2: Run metric tests and confirm import failure**

Run:

```bash
uv run pytest tests/test_search_eval.py -q
```

Expected: FAIL because `geo_index.search_eval` does not exist.

- [ ] **Step 3: Implement pure metrics and the live evaluation CLI**

In `src/geo_index/search_eval.py`, implement:

```python
def recall_at(ranked: list[str], judgments: dict[str, int], k: int) -> float:
    relevant = {gse for gse, grade in judgments.items() if grade > 0}
    if not relevant:
        return 0.0
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def reciprocal_rank(ranked: list[str], judgments: dict[str, int]) -> float:
    for rank, gse in enumerate(ranked, 1):
        if judgments.get(gse, 0) > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at(ranked: list[str], judgments: dict[str, int], k: int) -> float:
    import math

    def dcg(grades: list[int]) -> float:
        return sum(
            (2**grade - 1) / math.log2(index + 2)
            for index, grade in enumerate(grades)
        )

    actual = dcg([judgments.get(gse, 0) for gse in ranked[:k]])
    ideal = dcg(sorted(judgments.values(), reverse=True)[:k])
    return actual / ideal if ideal else 0.0


def estimated_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    input_cost_per_million: float,
    output_cost_per_million: float,
) -> float:
    return (
        input_tokens * input_cost_per_million
        + output_tokens * output_cost_per_million
    ) / 1_000_000
```

The CLI must:

1. Parse a JSONL path, output path, `--input-cost-per-million`, and
   `--output-cost-per-million`.
2. Construct `McpSearchService` from `ElasticsearchSettings.from_env` and
   `SearchQualitySettings.from_env`.
3. With `--compare-baseline`, construct two services. The baseline uses
   `dataclasses.replace(quality, rerank_enabled=False, openai_api_key=None)` and
   an injected `EmptyNativeSource` whose `search` returns
   `NativeSearchResult(count=0, candidates=())`; the comparison service uses
   the configured NCBI source and Luna reranker. Open both once, run the same
   normalized cases through each, and close both in `finally`.
4. Use `execution.candidates` for Recall@40 and final output for nDCG@10/MRR.
5. Count result candidates that violate declared organism, assay-category, or
   assay-label constraints.
6. Count final results with `source="ncbi"`.
7. Record native count and flag a case-level mismatch when an optional
   `expected_ncbi_count` is present.
8. Aggregate p50/p95 latency, degradation rate, reranker token usage, and
   caller-priced estimated cost.
9. Atomically write a bounded JSON report without provider exception strings.

Add CLI tests using injected baseline and comparison service factories. Assert
that a two-case JSONL file produces separate `baseline` and `luna` aggregates,
that candidate IDs feed Recall@40 while final IDs feed nDCG@10/MRR, and that the
temporary output file is replaced only after a complete report is serialized.

Add this script entry to `pyproject.toml`:

```toml
geo-search-eval = "geo_index.search_eval:main"
```

- [ ] **Step 4: Add the versioned evaluation cases**

Create `eval/unified_search_queries.jsonl` with one JSON object per line. Include
these required cases with explicit judgments or constraints:

```json
{"query_id":"exact_gse_310900","query":"GSE310900","filters":{},"judgments":{"GSE310900":3},"constraints":{}}
{"query_id":"mouse_endurance_insulin","query":"mouse skeletal muscle gene expression after endurance exercise in insulin resistance","filters":{},"judgments":{"GSE11803":3,"GSE302911":2,"GSE126001":2,"GSE178262":2},"constraints":{"organism_ids":["NCBITaxon:10090"]}}
{"query_id":"human_breast_neoadjuvant","query":"human breast cancer transcriptomics before and after neoadjuvant chemotherapy with treatment response data","filters":{},"judgments":{"GSE310900":3},"constraints":{"organism_ids":["NCBITaxon:9606"]}}
{"query_id":"control_childhood_malaria","query":"whole blood transcriptomics of children with severe malaria","filters":{},"judgments":{"GSE1124":3},"constraints":{}}
{"query_id":"human_tumor_exhausted_t_cells","query":"single-cell RNA sequencing of exhausted CD8 T cells in human solid tumors","filters":{"organism_ids":["NCBITaxon:9606"],"assay_labels":["scRNA-seq"]},"judgments":{"GSE244433":3,"GSE335452":3},"constraints":{"organism_ids":["NCBITaxon:9606"],"assay_labels":["scRNA-seq"]}}
{"query_id":"mouse_brain_spatial_injury","query":"spatial transcriptomics of mouse hippocampus after traumatic brain injury","filters":{"organism_ids":["NCBITaxon:10090"]},"judgments":{"GSE282909":3,"GSE230253":2,"GSE101901":2},"constraints":{"organism_ids":["NCBITaxon:10090"]}}
{"query_id":"crispr_interferon_t_cells","query":"CRISPR knockout screen for regulators of interferon response in T cells","filters":{},"judgments":{"GSE140717":3,"GSE144142":2,"GSE199813":2},"constraints":{}}
{"query_id":"ncbi_zero_control","query":"zzqxjv nonlexical geoscope retrieval sentinel","filters":{},"judgments":{},"constraints":{},"expected_ncbi_count":0}
```

- [ ] **Step 5: Run metric and parser tests**

Run:

```bash
uv run pytest tests/test_search_eval.py -q
```

Expected: PASS.

- [ ] **Step 6: Document production configuration and safe rollout**

Add these keys with explanatory comments to both deployment environment examples:

```dotenv
OPENAI_API_KEY=
GEO_RERANK_ENABLED=false
GEO_RERANK_MODEL=gpt-5.6-luna
GEO_RERANK_REASONING_EFFORT=low
GEO_RERANK_CANDIDATE_LIMIT=40
GEO_RERANK_TIMEOUT_SECONDS=8
GEO_NCBI_TIMEOUT_SECONDS=5
```

Update `docs/deployment/digitalocean.md` and `README.md` with:

- the new required secret when reranking is enabled;
- startup validation behavior;
- the initial deploy sequence with `GEO_RERANK_ENABLED=false`;
- the provider-gated smoke/evaluation command;
- enabling reranking only after baseline versus Luna metrics are recorded;
- the fact that NCBI-only results are partial live records, not online-ingested
  canonical documents;
- the staged decision rule: improve candidate generation when relevant records
  are absent, tune reranking when candidates are present but misordered, and
  propose query understanding only when unmodified NCBI recall or explicit
  constraint handling remains inadequate after reranker evaluation;
- the current official model and Structured Outputs links from the design.

- [ ] **Step 7: Add the explicitly gated live Luna schema smoke test**

Create `tests/test_reranker_live.py`:

```python
from __future__ import annotations

import os

import pytest

from geo_index.reranker import OpenAIReranker
from geo_index.search_candidates import SearchCandidate


def _candidate(gse: str, rank: int, taxon: str, organism_id: str) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title=f"Provider schema smoke {gse}",
        snippet="Skeletal muscle gene expression after endurance exercise.",
        study_type="Expression profiling by high throughput sequencing",
        n_samples=10,
        pubmed_id=None,
        organism_ids=(organism_id,),
        organism_status="mapped",
        sex_ids=(),
        sex_status="absent",
        assay_categories=("expression (seq)",),
        assay_labels=(),
        assay_status="category",
        source="elasticsearch",
        retrieval_score=1.0 / rank,
        original_rank=rank,
        native_rank=None,
        taxon=taxon,
    )


@pytest.mark.provider_integration
def test_live_luna_accepts_the_strict_complete_ranking_schema() -> None:
    if os.environ.get("GEO_TEST_OPENAI") != "1":
        pytest.skip("set GEO_TEST_OPENAI=1 to permit the live provider call")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY is not configured")
    reranker = OpenAIReranker(
        api_key=api_key,
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=30,
    )
    candidates = (
        _candidate("GSE11803", 1, "Mus musculus", "NCBITaxon:10090"),
        _candidate("GSE310900", 2, "Homo sapiens", "NCBITaxon:9606"),
    )
    try:
        result = reranker.rerank(
            "mouse skeletal muscle after endurance exercise",
            candidates,
            limit=2,
        )
    finally:
        reranker.close()

    assert set(result.scores) == {"GSE11803", "GSE310900"}
```

Add the marker to `pyproject.toml`:

```toml
"provider_integration: requires GEO_TEST_OPENAI=1 and live provider credentials",
```

- [ ] **Step 8: Run the complete offline verification suite**

Run:

```bash
uv run pytest -q
pnpm --dir frontend test
pnpm --dir frontend build
git diff --check
```

Expected: all pytest and Vitest tests PASS, the frontend build succeeds, and
`git diff --check` prints no errors.

- [ ] **Step 9: Run the provider-gated smoke and evaluation**

With `OPENAI_API_KEY`, NCBI connectivity, and the deployed Elasticsearch
credentials explicitly configured, run:

```bash
GEO_TEST_OPENAI=1 uv run pytest tests/test_reranker_live.py -m provider_integration -q

GEO_RERANK_ENABLED=true uv run geo-search-eval \
  eval/unified_search_queries.jsonl \
  --output eval/unified_search_report.json \
  --compare-baseline \
  --input-cost-per-million "$CURRENT_LUNA_INPUT_COST_PER_MILLION" \
  --output-cost-per-million "$CURRENT_LUNA_OUTPUT_COST_PER_MILLION"
```

Expected: the report contains all eight query IDs; `exact_gse_310900` returns
`GSE310900`; the mouse query has no more-relevant human record ahead of the
judged mouse records; NCBI-only recovery, fallback rate, p50/p95 latency, token
usage, and estimated cost are present; `ncbi_zero_control` reports a native
count of zero. Keep the generated report uncommitted
unless the values have been reviewed and intentionally accepted as a baseline.

- [ ] **Step 10: Commit evaluation and rollout documentation**

```bash
git add src/geo_index/search_eval.py tests/test_search_eval.py tests/test_reranker_live.py eval/unified_search_queries.jsonl pyproject.toml deploy/geo-mcp.env.example deploy/app-platform.env.example docs/deployment/digitalocean.md README.md tests/test_primary_path_docs.py
git commit -m "test: add unified search evaluation"
```

---

## Final Review Checklist

- [ ] `GSE310900` takes the exact path and never creates a query embedding.
- [ ] A locally absent exact GSE can be returned from NCBI with `source="ncbi"`.
- [ ] Natural search starts Elasticsearch and NCBI retrieval concurrently.
- [ ] The merged candidate set deduplicates by normalized GSE and applies every active filter to NCBI-only candidates.
- [ ] GPT-5.6 Luna receives every candidate exactly once and uses low reasoning effort.
- [ ] Invalid, refused, or timed-out reranking discards the complete model order and falls back deterministically.
- [ ] MCP and marketing expose the same final top-ten ranking.
- [ ] Marketing makes no second E-utilities request.
- [ ] Facets retain local Elasticsearch candidate-pool semantics.
- [ ] Provider errors are categorized without exposing exception text.
- [ ] The OpenAI key never appears in object repr, logs, fixtures, or committed files.
- [ ] Existing user changes remain intact and every task commit contains only its listed files.

## Official API References

- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Responses API create](https://developers.openai.com/api/reference/resources/responses/methods/create)
