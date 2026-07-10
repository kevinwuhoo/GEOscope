---
title: MCP Server Plan
tags: [mcp, search, api, postgres, plan, v1]
status: implementation-plan
created: 2026-07-10
---

# 47 · Local MCP Server Implementation Plan

← [[Home]] · implements [[27-MCP-Interface]] · depends on
[[45-Normalized-Filters-and-Facets-Plan]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the stable GSE search, exact lookup, and facet contracts as three
compact MCP tools that a local LLM client can call without adding another LLM to
the server.

**Architecture:** Put connection/model lifecycle in a reusable `SearchService`,
wire validation in Pydantic models, and keep the MCP module as a thin adapter. A
server factory accepts a fake service for protocol tests; importing the module
must neither connect to Postgres nor load the embedding model. The first release
uses local stdio only.

**Tech Stack:** Python 3.11+, stable MCP Python SDK v1/FastMCP,
`mcp[cli]>=1.28,<2`, Pydantic, psycopg 3, pytest + AnyIO.

## Global Constraints

- Pin the SDK to `mcp[cli]>=1.28,<2`; do not use a v2 prerelease.
- Expose exactly `search_datasets`, `get_dataset`, and `facet_values`.
- Use local stdio transport only.
- Perform no database/model I/O during module import.
- Reserve stdout exclusively for MCP protocol messages.
- Open read-only database connections and parameterize every user value.
- Return compact structured output with a GSE accession in every dataset result.
- Do not add server-side LLM calls, term expansion, or ontology resolution.

---

## Why this scope

The MCP server should expose capabilities that already have deterministic,
testable contracts. v1 therefore has exactly three tools:

1. `search_datasets` — ranked GSE results plus normalized facets.
2. `get_dataset` — exact GSE metadata lookup.
3. `facet_values` — discover valid filter values and counts.

`expand_terms` and `resolve_ontology` wait for the deterministic tissue mapper.
`lookup_accession` is redundant for GSE and cannot honestly support GSM/GPL until
those records are indexed. This keeps the MCP layer thin and prevents it from
becoming a second normalization/retrieval implementation.

The [published package history](https://pypi.org/project/mcp/) lists v1.28.1 as
the stable line as of 2026-07-10; the
[official SDK guidance](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
marks v2 prerelease and recommends a `<2` upper bound for v1 consumers. Use v1
FastMCP now and plan a separate migration after stable v2 ships.

## v1 wire contract

### `search_datasets`

Input:

```json
{
  "query": "single-cell RNA studies",
  "filters": {
    "organism_ids": ["NCBITaxon:9606"],
    "sex_ids": [],
    "assay_categories": [],
    "assay_labels": ["scRNA-seq"]
  },
  "mode": "hybrid",
  "limit": 15
}
```

Rules:

- `query`: stripped, 1–1,000 characters.
- `mode`: `hybrid`, `bm25`, or `dense`.
- `limit`: 1–50; retrieval tuning (`deep`, `k0`, `facet_pool`) is not public.
- Each filter list has at most 20 unique nonblank values.
- NCBITaxon and PATO values must match their ID syntax; assay values remain
  controlled labels in this prototype.
- Unknown object fields are rejected.

Output includes the normalized request, ranked results, the four facet groups,
and each facet's exact/candidate-pool scope. A result contains rank, GSE, title,
summary snippet, study type, sample count, PubMed ID, the four normalized arrays,
and score. BM25/dense component ranks stay internal as evaluation diagnostics.

### `get_dataset`

Input: `gse` normalized with `strip().upper()` and validated against
`^GSE[1-9][0-9]*$`.

Output:

```json
{"found": true, "dataset": {"gse": "GSE123", "title": "Indexed GEO series title"}}
```

A syntactically valid but absent GSE returns `found=false`, not a protocol error.
The detail record contains only indexed fields: title, summary, overall design,
study type, sample count, PubMed ID, raw aggregated organism names, normalized
arrays/statuses, and derived GEO/PubMed URLs. The potentially huge aggregated
sample-characteristics blob stays internal. The tool does not claim to return raw
SOFT, GSM records, SRA cross-references, or full samples.

### `facet_values`

Input:

- `field`: strict enum `organism_ids | sex_ids | assay_categories | assay_labels`.
- optional `query`, filters, retrieval mode, and case-insensitive label prefix.
- `limit`: 1–50.

Output contains `{value, label, count}` buckets plus scope, candidate count, and
candidate limit. Track 2's disjunctive rule applies: the chosen facet's own
filter is removed while its alternatives are counted.

## File structure

| Path | Responsibility |
|---|---|
| `src/geo_index/search_service.py` | Read-only DB lifecycle, lazy model, search/hydration, GSE lookup |
| `src/geo_index/mcp_models.py` | Strict Pydantic input/output models and domain conversions |
| `src/geo_index/mcp_server.py` | FastMCP factory, three tool handlers, stdio entry point |
| `tests/test_search_service.py` | Service lifecycle and delegation tests |
| `tests/test_mcp_models.py` | Wire validation tests |
| `tests/test_mcp_server.py` | In-memory protocol tests with a fake service |
| `tests/test_mcp_db_smoke.py` | Opt-in live Postgres MCP smoke test |
| `pyproject.toml`, `uv.lock` | Bounded SDK dependency and `geo-mcp` script |
| `README.md` | Local client/run instructions |
| `wiki/27-MCP-Interface.md` | Reconcile proposed tools with the implemented v1 tranche |
| `wiki/99-Sources.md` | Official SDK and test references |

### Task 1: Add the stable MCP dependency and strict wire models

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/geo_index/mcp_models.py`
- Create: `tests/test_mcp_models.py`

**Interfaces:**
- Produces: `SearchFiltersInput`, `SearchDatasetsInput`, `FacetValuesInput`,
  `DatasetSummary`, `DatasetDetail`, `SearchDatasetsOutput`,
  `GetDatasetOutput`, and `FacetValuesOutput`.

- [ ] **Step 1: Add the bounded stable SDK**

```bash
uv add "mcp[cli]>=1.28,<2"
```

Expected: `pyproject.toml` and `uv.lock` change; no v2 alpha/beta package is
selected.

- [ ] **Step 2: Write failing validation tests**

Cover:

- empty/whitespace and 1,001-character queries;
- limit 0 and 51;
- unsupported retrieval mode;
- unknown filter field;
- more than 20 values in a list;
- stable deduplication within a list;
- malformed NCBITaxon and PATO IDs;
- blank assay values;
- strict facet-field enum;
- `" gse123 "` normalizing to `GSE123`;
- malformed or zero-valued GSE IDs being rejected.

- [ ] **Step 3: Implement Pydantic models with forbidden extras**

Create `src/geo_index/mcp_models.py` with these input and shared output models:

```python
from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .search_models import SearchFilters


class SearchMode(str, Enum):
    HYBRID = "hybrid"
    BM25 = "bm25"
    DENSE = "dense"


class FacetFieldName(str, Enum):
    ORGANISM_IDS = "organism_ids"
    SEX_IDS = "sex_ids"
    ASSAY_CATEGORIES = "assay_categories"
    ASSAY_LABELS = "assay_labels"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchFiltersInput(StrictModel):
    organism_ids: list[str] = Field(default_factory=list, max_length=20)
    sex_ids: list[str] = Field(default_factory=list, max_length=20)
    assay_categories: list[str] = Field(default_factory=list, max_length=20)
    assay_labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("*", mode="before")
    @classmethod
    def normalize_values(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise ValueError("facet values cannot be blank")
            if text not in normalized:
                normalized.append(text)
        return normalized

    @field_validator("organism_ids")
    @classmethod
    def validate_organism_ids(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"NCBITaxon:[1-9][0-9]*", value) for value in values):
            raise ValueError("organism IDs must use NCBITaxon:<positive integer>")
        return values

    @field_validator("sex_ids")
    @classmethod
    def validate_sex_ids(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"PATO:[0-9]{7}", value) for value in values):
            raise ValueError("sex IDs must use PATO:<seven digits>")
        return values

    def to_domain(self) -> SearchFilters:
        return SearchFilters.from_mapping(self.model_dump())


class SearchDatasetsInput(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    filters: SearchFiltersInput = Field(default_factory=SearchFiltersInput)
    mode: SearchMode = SearchMode.HYBRID
    limit: int = Field(default=15, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query cannot be blank")
        return value


class GetDatasetInput(StrictModel):
    gse: str

    @field_validator("gse")
    @classmethod
    def normalize_gse(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"GSE[1-9][0-9]*", value):
            raise ValueError("gse must be GSE followed by a positive integer")
        return value


class FacetValuesInput(StrictModel):
    field: FacetFieldName
    query: str | None = Field(default=None, max_length=1000)
    filters: SearchFiltersInput = Field(default_factory=SearchFiltersInput)
    mode: SearchMode = SearchMode.HYBRID
    prefix: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=25, ge=1, le=50)

    @field_validator("query", "prefix")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class FacetBucketOutput(StrictModel):
    value: str
    label: str
    count: int = Field(ge=0)


class FacetGroupOutput(StrictModel):
    field: FacetFieldName
    buckets: list[FacetBucketOutput]
    scope: Literal["all_matches", "candidate_pool"]
    candidate_count: int | None = Field(default=None, ge=0)
    candidate_limit: int | None = Field(default=None, ge=1)


class DatasetSummary(StrictModel):
    rank: int = Field(ge=1)
    gse: str
    title: str
    summary_snippet: str
    study_type: str
    n_samples: int | None = Field(default=None, ge=0)
    pubmed_id: int | None = Field(default=None, ge=1)
    organism_ids: list[str]
    sex_ids: list[str]
    assay_categories: list[str]
    assay_labels: list[str]
    score: float | None = None


class SearchDatasetsOutput(StrictModel):
    query: str
    mode: SearchMode
    filters: SearchFiltersInput
    results: list[DatasetSummary]
    facets: dict[str, FacetGroupOutput]


class DatasetDetail(StrictModel):
    gse: str
    title: str
    summary: str
    overall_design: str
    study_type: str
    n_samples: int | None = Field(default=None, ge=0)
    pubmed_id: int | None = Field(default=None, ge=1)
    organisms: str
    organism_ids: list[str]
    organism_status: str | None
    sex_ids: list[str]
    sex_status: str | None
    assay_categories: list[str]
    assay_labels: list[str]
    assay_status: str | None
    geo_url: str
    pubmed_url: str | None


class GetDatasetOutput(StrictModel):
    found: bool
    dataset: DatasetDetail | None


class FacetValuesOutput(StrictModel):
    field: FacetFieldName
    buckets: list[FacetBucketOutput]
    scope: Literal["all_matches", "candidate_pool"]
    candidate_count: int | None = Field(default=None, ge=0)
    candidate_limit: int | None = Field(default=None, ge=1)
```

Every input model forbids extras. Convert `SearchFiltersInput` into Track 2's
immutable `SearchFilters`; do not pass Pydantic models into the SQL layer.

All tool return annotations are Pydantic models. This lets FastMCP publish
structured schemas and populate `structuredContent` while retaining its normal
text compatibility representation.

- [ ] **Step 4: Run model tests and commit**

```bash
uv run pytest tests/test_mcp_models.py -v
git add pyproject.toml uv.lock src/geo_index/mcp_models.py tests/test_mcp_models.py
git commit -m "feat: define MCP search schemas"
```

### Task 2: Create a read-only search service independent of MCP

**Files:**
- Create: `src/geo_index/search_service.py`
- Create: `tests/test_search_service.py`

**Interfaces:**
- Produces: `SearchService.search(query: str, filters: SearchFilters,
  mode: str, limit: int) -> dict[str, object]`.
- Produces: `SearchService.get_dataset(gse: str) -> dict[str, object]`.
- Produces: `SearchService.facet_values(field: FacetField, *, query: str | None,
  filters: SearchFilters, mode: str, prefix: str | None,
  limit: int) -> dict[str, object]`.
- Produces: `UnknownFacetValue` for a syntactically valid value outside the
  database's closed vocabulary.

- [ ] **Step 1: Write lifecycle tests with injected fakes**

Construct the service with injected `connect`, `load_model`, `embed_query`, and
retrieval callables. Assert:

- BM25 requests never load the embedding model.
- Dense/hybrid requests load it once across repeated calls.
- the lazy-model lock prevents duplicate concurrent loads;
- every database connection closes on success and exception;
- connections are marked read-only before executing application SQL;
- user values are carried as query parameters;
- missing GSE returns `found=false`;
- a value absent from the facet vocabulary raises `UnknownFacetValue` with an
  instruction to call `facet_values`.

- [ ] **Step 2: Implement lazy model and connection lifecycle**

`SearchService.__init__()` stores factories only. It does not connect or load a
model. For each tool call, open a connection, set it read-only, execute the
operation, and close it in `finally`. Cache the embedding model behind a lock and
only enter that path for dense/hybrid mode.

Cache the global valid-value set for each of the four facet fields on first use.
If a requested value is unknown, fail with a clear field/value message rather
than silently returning an empty result. Keep syntactic ID validation in the
wire layer and database-vocabulary validation here.

Create the service foundation in `src/geo_index/search_service.py`:

```python
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from . import pg_hybrid
from .facets import FACET_COLUMNS, facet_counts
from .search_models import FACET_FIELDS, FacetField, SearchFilters


class UnknownFacetValue(ValueError):
    pass


class SearchService:
    def __init__(
        self,
        *,
        connect=pg_hybrid._connect,
        load_model=pg_hybrid.load_model,
        embed_query=pg_hybrid.embed_query,
        search_with_facets=pg_hybrid.search_with_facets,
        search_rows=pg_hybrid.search_rows,
    ) -> None:
        self._connect = connect
        self._load_model = load_model
        self._embed_query = embed_query
        self._search_with_facets = search_with_facets
        self._search_rows = search_rows
        self._model = None
        self._model_lock = threading.Lock()
        self._vocabulary: dict[FacetField, set[str]] = {}
        self._vocabulary_lock = threading.Lock()

    @contextmanager
    def _connection(self) -> Iterator[object]:
        conn = self._connect()
        try:
            conn.read_only = True
            yield conn
        finally:
            conn.close()

    def _query_vector(self, mode: str, query: str):
        if mode == "bm25":
            return None
        with self._model_lock:
            if self._model is None:
                self._model = self._load_model()
            model = self._model
        return self._embed_query(model, query)

    def _values_for(self, conn, field: FacetField) -> set[str]:
        with self._vocabulary_lock:
            cached = self._vocabulary.get(field)
            if cached is not None:
                return cached
            column = FACET_COLUMNS[field]
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT DISTINCT unnest({column}) FROM series "
                    f"WHERE {column} IS NOT NULL"
                )
                values = {str(row[0]) for row in cur.fetchall()}
            self._vocabulary[field] = values
            return values

    def _validate_filters(self, conn, filters: SearchFilters) -> None:
        for field in FACET_FIELDS:
            requested = getattr(filters, field)
            if not requested:
                continue
            known = self._values_for(conn, field)
            for value in requested:
                if value not in known:
                    raise UnknownFacetValue(
                        f"unknown {field} value {value!r}; call facet_values"
                    )
```

The interpolated column is selected only from `FACET_COLUMNS`; every caller value
remains a parameter in downstream search SQL.

- [ ] **Step 3: Delegate ranked search and hydrate compact results**

Call Track 2's `search_with_facets()` once with a precomputed query vector. Then
fetch detail columns for the returned GSEs with one parameterized `ANY(%s)` query,
preserve the original ranking in Python, and make a bounded summary snippet. Do
not return `search_text`, `embedding`, raw score vectors, or full characteristics
in the search result list.

Add these methods to `SearchService`:

```python
    @staticmethod
    def _snippet(value: str | None, limit: int = 400) -> str:
        return " ".join((value or "").split())[:limit]

    def _hydrate_summaries(self, conn, hits: tuple[dict, ...]) -> list[dict]:
        gses = [str(hit["gse"]) for hit in hits]
        if not gses:
            return []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT gse, summary, n_samples, pubmed_id,
                       organism_ids, sex_ids, assay_categories, assay_labels
                FROM series
                WHERE gse = ANY(%s::text[])
                """,
                (gses,),
            )
            details = {
                gse: {
                    "summary": summary,
                    "n_samples": n_samples,
                    "pubmed_id": pubmed_id,
                    "organism_ids": organism_ids or [],
                    "sex_ids": sex_ids or [],
                    "assay_categories": assay_categories or [],
                    "assay_labels": assay_labels or [],
                }
                for (
                    gse,
                    summary,
                    n_samples,
                    pubmed_id,
                    organism_ids,
                    sex_ids,
                    assay_categories,
                    assay_labels,
                ) in cur.fetchall()
            }
        summaries: list[dict] = []
        for rank, hit in enumerate(hits, 1):
            detail = details[str(hit["gse"])]
            summaries.append(
                {
                    "rank": rank,
                    "gse": hit["gse"],
                    "title": hit.get("title") or "",
                    "summary_snippet": self._snippet(detail["summary"]),
                    "study_type": hit.get("type") or "",
                    "n_samples": detail["n_samples"],
                    "pubmed_id": detail["pubmed_id"],
                    "organism_ids": detail["organism_ids"],
                    "sex_ids": detail["sex_ids"],
                    "assay_categories": detail["assay_categories"],
                    "assay_labels": detail["assay_labels"],
                    "score": hit.get("score"),
                }
            )
        return summaries

    def search(
        self,
        query: str,
        filters: SearchFilters,
        mode: str,
        limit: int,
    ) -> dict[str, object]:
        with self._connection() as conn:
            self._validate_filters(conn, filters)
            qv = self._query_vector(mode, query)
            response = self._search_with_facets(
                conn,
                query,
                qv=qv,
                mode=mode,
                topk=limit,
                filters=filters,
            )
            results = self._hydrate_summaries(conn, response.hits)
        facet_payload = {
            field: {
                "field": result.field,
                "buckets": [
                    {
                        "value": bucket.value,
                        "label": bucket.label,
                        "count": bucket.count,
                    }
                    for bucket in result.buckets
                ],
                "scope": result.scope,
                "candidate_count": result.candidate_count,
                "candidate_limit": 1000 if result.scope == "candidate_pool" else None,
            }
            for field, result in response.facets.items()
        }
        return {"results": results, "facets": facet_payload}
```

- [ ] **Step 4: Implement exact lookup**

Select by `gse = %s` and return the indexed fields documented above. Derive:

```text
https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123
https://pubmed.ncbi.nlm.nih.gov/<pubmed_id>/
```

Omit the PubMed URL when `pubmed_id` is null.

```python
    def get_dataset(self, gse: str) -> dict[str, object]:
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT gse, title, summary, overall_design, type, n_samples,
                       pubmed_id, organisms,
                       organism_ids, organism_status, sex_ids, sex_status,
                       assay_categories, assay_labels, assay_status
                FROM series
                WHERE gse = %s
                """,
                (gse,),
            )
            row = cur.fetchone()
        if row is None:
            return {"found": False, "dataset": None}
        (
            accession, title, summary, design, study_type, n_samples,
            pubmed_id, organisms, organism_ids,
            organism_status, sex_ids, sex_status, assay_categories,
            assay_labels, assay_status,
        ) = row
        dataset = {
            "gse": accession,
            "title": title or "",
            "summary": summary or "",
            "overall_design": design or "",
            "study_type": study_type or "",
            "n_samples": n_samples,
            "pubmed_id": pubmed_id,
            "organisms": organisms or "",
            "organism_ids": organism_ids or [],
            "organism_status": organism_status,
            "sex_ids": sex_ids or [],
            "sex_status": sex_status,
            "assay_categories": assay_categories or [],
            "assay_labels": assay_labels or [],
            "assay_status": assay_status,
            "geo_url": (
                "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc="
                f"{accession}"
            ),
            "pubmed_url": (
                f"https://pubmed.ncbi.nlm.nih.gov/{pubmed_id}/"
                if pubmed_id is not None else None
            ),
        }
        return {"found": True, "dataset": dataset}
```

- [ ] **Step 5: Implement facet browsing**

Delegate to Track 2's disjunctive facet function for exactly one enum field.
Apply the optional prefix to the resolved display label after counting, then cap
to `limit`. Preserve scope/pool metadata in the response.

```python
    def facet_values(
        self,
        field: FacetField,
        *,
        query: str | None,
        filters: SearchFilters,
        mode: str,
        prefix: str | None,
        limit: int,
    ) -> dict[str, object]:
        text = query or ""
        with self._connection() as conn:
            self._validate_filters(conn, filters)
            qv = self._query_vector(mode, text) if text else None
            result = facet_counts(
                conn,
                query=text,
                mode=mode,
                qv=qv,
                filters=filters,
                retrieve=self._search_rows,
                fields=(field,),
                bucket_limit=500,
            )[field]
        buckets = [
            {
                "value": bucket.value,
                "label": bucket.label,
                "count": bucket.count,
            }
            for bucket in result.buckets
            if prefix is None or bucket.label.casefold().startswith(prefix.casefold())
        ][:limit]
        return {
            "field": field,
            "buckets": buckets,
            "scope": result.scope,
            "candidate_count": result.candidate_count,
            "candidate_limit": 1000 if result.scope == "candidate_pool" else None,
        }
```

- [ ] **Step 6: Run service tests and commit**

```bash
uv run pytest tests/test_search_service.py -v
git add src/geo_index/search_service.py tests/test_search_service.py
git commit -m "feat: add reusable GEO search service"
```

### Task 3: Register exactly three FastMCP tools

**Files:**
- Create: `src/geo_index/mcp_server.py`
- Create: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `create_server(service: SearchService) -> FastMCP`.
- Exports: module-level `mcp` for the SDK development command.
- Produces: `main() -> None`, which runs stdio transport.

- [ ] **Step 1: Write in-memory protocol tests**

Use the
[official in-memory testing helper](https://py.sdk.modelcontextprotocol.io/testing/)
directly with the FastMCP app:

```python
from collections.abc import AsyncGenerator

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client_session(fake_service) -> AsyncGenerator[ClientSession]:
    app = create_server(fake_service)
    async with create_connected_server_and_client_session(
        app, raise_exceptions=True
    ) as session:
        yield session
```

Use this minimal fake; it proves the adapter without Postgres or an embedding
model:

```python
class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def search(self, query, filters, mode, limit):
        self.calls.append(("search", query))
        return {"results": [], "facets": {}}

    def get_dataset(self, gse):
        self.calls.append(("get_dataset", gse))
        return {"found": False, "dataset": None}

    def facet_values(self, field, **kwargs):
        self.calls.append(("facet_values", field))
        return {
            "field": field,
            "buckets": [],
            "scope": "all_matches",
            "candidate_count": None,
            "candidate_limit": None,
        }


@pytest.fixture
def fake_service() -> FakeService:
    return FakeService()
```

The three protocol calls use:

```python
@pytest.mark.anyio
async def test_tools_are_exact_and_structured(client_session: ClientSession):
    listed = await client_session.list_tools()
    assert {tool.name for tool in listed.tools} == {
        "search_datasets", "get_dataset", "facet_values"
    }

    search = await client_session.call_tool(
        "search_datasets", {"query": "single-cell RNA", "mode": "bm25"}
    )
    detail = await client_session.call_tool("get_dataset", {"gse": " gse999 "})
    facets = await client_session.call_tool(
        "facet_values", {"field": "organism_ids"}
    )

    assert search.structuredContent == {
        "query": "single-cell RNA",
        "mode": "bm25",
        "filters": {
            "organism_ids": [],
            "sex_ids": [],
            "assay_categories": [],
            "assay_labels": [],
        },
        "results": [],
        "facets": {},
    }
    assert detail.structuredContent == {"found": False, "dataset": None}
    assert facets.structuredContent["field"] == "organism_ids"
```

Through `ClientSession`, assert:

- the tool list is exactly `search_datasets`, `get_dataset`, `facet_values`;
- all three tools return `structuredContent` matching their output schemas;
- invalid arguments fail before the fake service is called;
- a valid missing GSE returns `found=false`;
- service errors become concise MCP tool errors without leaking DSNs or stack
  traces;
- the server instructions explain GSE-level aggregation, strict facet values,
  bounded semantic facet counts, and citing GSE accessions.

- [ ] **Step 2: Implement the server factory**

Use the stable v1 import:

```python
from mcp.server.fastmcp import FastMCP
```

Create `src/geo_index/mcp_server.py`:

```python
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from mcp.server.fastmcp import FastMCP

from .mcp_models import (
    FacetFieldName,
    FacetValuesInput,
    FacetValuesOutput,
    GetDatasetInput,
    GetDatasetOutput,
    SearchDatasetsInput,
    SearchDatasetsOutput,
    SearchFiltersInput,
    SearchMode,
)
from .search_service import SearchService, UnknownFacetValue


LOGGER = logging.getLogger(__name__)
T = TypeVar("T")
INSTRUCTIONS = """
Searches NCBI GEO Series (GSE) records. Cite returned GSE accessions. Facet
filters describe values contained somewhere in a series and do not guarantee
same-sample co-occurrence. Use facet_values to discover valid controlled values.
With a text query, facet counts cover a bounded retrieval candidate pool rather
than the complete corpus.
""".strip()


def _safe_call(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except UnknownFacetValue as exc:
        raise ValueError(str(exc)) from None
    except Exception:
        LOGGER.exception("GEO MCP service operation failed")
        raise RuntimeError("GEO search service is unavailable") from None


def create_server(service: SearchService) -> FastMCP:
    app = FastMCP("GEO Metadata Index", instructions=INSTRUCTIONS)

    @app.tool()
    def search_datasets(
        query: str,
        filters: SearchFiltersInput | None = None,
        mode: SearchMode = SearchMode.HYBRID,
        limit: int = 15,
    ) -> SearchDatasetsOutput:
        """Search ranked GEO series and return scoped normalized facets."""
        request = SearchDatasetsInput(
            query=query,
            filters=filters or SearchFiltersInput(),
            mode=mode,
            limit=limit,
        )
        payload = _safe_call(
            lambda: service.search(
                request.query,
                request.filters.to_domain(),
                request.mode.value,
                request.limit,
            )
        )
        return SearchDatasetsOutput.model_validate(
            {
                "query": request.query,
                "mode": request.mode,
                "filters": request.filters,
                "results": payload["results"],
                "facets": payload["facets"],
            }
        )

    @app.tool()
    def get_dataset(gse: str) -> GetDatasetOutput:
        """Get one indexed GEO Series by exact GSE accession."""
        request = GetDatasetInput(gse=gse)
        payload = _safe_call(lambda: service.get_dataset(request.gse))
        return GetDatasetOutput.model_validate(payload)

    @app.tool()
    def facet_values(
        field: FacetFieldName,
        query: str | None = None,
        filters: SearchFiltersInput | None = None,
        mode: SearchMode = SearchMode.HYBRID,
        prefix: str | None = None,
        limit: int = 25,
    ) -> FacetValuesOutput:
        """List valid normalized values and disjunctive counts for one facet."""
        request = FacetValuesInput(
            field=field,
            query=query,
            filters=filters or SearchFiltersInput(),
            mode=mode,
            prefix=prefix,
            limit=limit,
        )
        payload = _safe_call(
            lambda: service.facet_values(
                request.field.value,
                query=request.query,
                filters=request.filters.to_domain(),
                mode=request.mode.value,
                prefix=request.prefix,
                limit=request.limit,
            )
        )
        return FacetValuesOutput.model_validate(payload)

    return app
```

The handlers contain no SQL, embedding, or normalization logic.

- [ ] **Step 3: Add a lazy module-level app and stdio entry point**

```python
mcp = create_server(SearchService())


def main() -> None:
    mcp.run()
```

Constructing `SearchService()` is safe because its constructor performs no I/O.
Never print logs or status to stdout: stdio reserves stdout for MCP protocol
messages. Configure any diagnostics for stderr.

- [ ] **Step 4: Run protocol tests and commit**

```bash
uv run pytest tests/test_mcp_server.py -v
git add src/geo_index/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: expose GEO search over MCP"
```

### Task 4: Add an opt-in live MCP smoke test

**Files:**
- Create: `tests/test_mcp_db_smoke.py`

- [ ] **Step 1: Gate the test on `GEO_TEST_PG=1`**

Use the same registered integration marker as Track 2. The test must stay skipped
in the default offline suite.

- [ ] **Step 2: Exercise the protocol, not service internals**

With the real `SearchService` and in-memory MCP transport:

1. Call `search_datasets` in BM25 mode to avoid model loading/downloads.
2. Take one returned GSE and call `get_dataset`.
3. Call `facet_values(field="organism_ids")`.
4. Assert the search GSE matches the detail GSE and the organism response contains
   at least human or mouse.

Use a read-only connection and do not assert exact corpus counts.

- [ ] **Step 3: Run and commit the smoke test**

```bash
GEO_TEST_PG=1 uv run pytest tests/test_mcp_db_smoke.py -v
git add tests/test_mcp_db_smoke.py
git commit -m "test: cover MCP against local GEO database"
```

### Task 5: Package and document local stdio use

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `wiki/27-MCP-Interface.md`
- Modify: `wiki/99-Sources.md`

- [ ] **Step 1: Add the console script**

```toml
[project.scripts]
geo-mcp = "geo_index.mcp_server:main"
```

- [ ] **Step 2: Document client configuration**

In `README.md`, document the working directory, `GEO_PG_DSN`, and the command:

```bash
uv run geo-mcp
```

Also document the SDK inspector workflow:

```bash
uv run mcp dev src/geo_index/mcp_server.py
```

The client config must launch through `uv` in this repository so it receives the
project environment. Do not add HTTP deployment instructions.

- [ ] **Step 3: Reconcile the design wiki with implemented scope**

Update [[27-MCP-Interface]] to mark the three v1 tools and list the deferred
tools. Correct two old assumptions: assay values are controlled labels today,
not EFO IDs, and `get_dataset` cannot yet return raw/sample/SRA content.

- [ ] **Step 4: Record official SDK sources**

Add the v1 SDK, PyPI release, and official in-memory testing pages to
[[99-Sources]].

- [ ] **Step 5: Run all verification**

```bash
uv run pytest tests/test_mcp_models.py tests/test_search_service.py tests/test_mcp_server.py -v
uv run pytest -v
GEO_TEST_PG=1 uv run pytest tests/test_mcp_db_smoke.py -v
```

Open the inspector and manually call all three tools once. Confirm that search
and facets are compact enough for an LLM context and that no non-protocol text is
written to the server's stdout.

- [ ] **Step 6: Commit packaging and docs**

```bash
git add pyproject.toml README.md wiki/27-MCP-Interface.md wiki/99-Sources.md
git commit -m "docs: add local MCP server usage"
```

## Definition of done

- The installed dependency is on stable MCP v1 with a `<2` bound.
- Importing the MCP module performs no database or model I/O.
- Exactly three typed tools are discoverable over stdio.
- Tool inputs reject unknown fields and invalid bounds/ID syntax.
- Outputs are validated structured content and include citable GSE accessions.
- BM25 requests do not load the embedding model; dense/hybrid load it once.
- Database connections are read-only and close on success and failure.
- In-memory protocol tests run offline; the live smoke test is opt-in.
- README and the design wiki match the data the server can actually return.

## Explicitly deferred

- `expand_terms`, `resolve_ontology`, and non-GSE `lookup_accession`.
- Raw SOFT/sample retrieval and SRA cross-reference enrichment.
- Ontology hierarchy traversal and tissue filters.
- Resources, prompts, pagination/cursors, and server-side LLM calls.
- Streamable HTTP, SSE, authentication, sessions, hosting, and deployment.
- Reranking and retrieval-model selection; Track 3 measures those separately.

## Sources

- Stable SDK/recommended `<2` bound — https://github.com/modelcontextprotocol/python-sdk/tree/v1.x
- Published package versions — https://pypi.org/project/mcp/
- Official in-memory server testing — https://py.sdk.modelcontextprotocol.io/testing/
