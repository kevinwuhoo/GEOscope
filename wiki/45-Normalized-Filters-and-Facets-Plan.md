---
title: Normalized Filters and Facets Plan
tags: [search, filters, facets, postgres, plan, v1]
status: implementation-plan
created: 2026-07-10
---

# 45 · Normalized Filters and Facets Implementation Plan

← [[Home]] · implements [[24-Faceted-Search]] · follows
[[44-Normalization-Tests-and-Assay-Hardening-Plan]] · enables
[[46-Retrieval-Evaluation-Plan]] and [[47-MCP-Server-Plan]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the already-populated organism, sex, and assay arrays into safe
filters and useful facet counts across the Postgres search path, HTTP demo, and
future MCP service.

**Architecture:** Keep normalization separate from querying. A small typed filter
contract validates callers, a whitelist-only SQL builder translates it into array
predicates, and `pg_hybrid.py` applies those predicates inside every retrieval
branch. Facet counts use disjunctive semantics and make their retrieval scope
explicit rather than presenting approximate semantic counts as corpus totals.

**Tech Stack:** Python 3.11+, dataclasses, psycopg 3, PostgreSQL `TEXT[]` + GIN,
pg_search BM25, pgvector HNSW, pytest.

## Global Constraints

- Reuse the populated organism/sex arrays and Track 1's targeted assay refresh;
  do not reload raw metadata or recompute embeddings.
- Support exactly four v1 facet fields: `organism_ids`, `sex_ids`,
  `assay_categories`, and `assay_labels`.
- Use OR within one field and AND across fields.
- Apply filters before BM25/dense branch limits.
- Use native Postgres aggregation as the correctness reference.
- Label semantic facet counts as a bounded 1,000-candidate scope.
- Keep the default test suite offline and nondestructive.

---

## First: what is already done?

Yes—the normalized database values already exist. A read-only check against the
local database on 2026-07-10 found:

| Evidence | Live value |
|---|---:|
| `series` rows | 222,961 |
| rows with mapped organism IDs | 201,174 |
| rows with mapped sex IDs | 41,503 |
| rows with an assay category or detailed label | 221,318 |
| rows with detailed assay labels | 48,119 |
| human series (`NCBITaxon:9606`) | 97,114 |
| mouse series (`NCBITaxon:10090`) | 71,204 |

The table already has `organism_ids`, `sex_ids`, `assay_categories`,
`assay_labels`, and their status columns. What is missing is the serving layer:

- `search_rows()` cannot accept normalized filters.
- The array columns have no GIN indexes.
- No facet-count function or disjunctive-count behavior exists.
- The HTTP endpoint and future MCP tools cannot expose these fields.

This plan therefore **does not reload raw data or recompute organism/sex**. It
adds query behavior over the data already present. Complete Track 1's targeted
`geo-normalize assay-refresh` before presenting assay labels as facets, because
the current detailed-assay data contains a known broad `10x|chromium`
false-positive rule.

## v1 contract and semantics

The v1 query contract exposes four physical facet fields:

| API field | Database column | Value kind |
|---|---|---|
| `organism_ids` | `series.organism_ids` | NCBITaxon IDs |
| `sex_ids` | `series.sex_ids` | PATO IDs |
| `assay_categories` | `series.assay_categories` | controlled coarse labels |
| `assay_labels` | `series.assay_labels` | controlled detailed labels |

Important boundaries:

- Values within one field are ORed with PostgreSQL array overlap (`&&`).
- Different fields are ANDed.
- A facet count drops its own selected values but keeps every other filter. This
  lets a caller select human and still see the count for mouse.
- Assay category and assay label are deliberately separate facets. The prototype
  labels are controlled strings, **not EFO IDs**; EFO grounding is later work.
- Filters apply to the GSE series aggregate. `female + human` means the series
  contains each value somewhere, not necessarily on the same GSM sample. Preserve
  the warning in [[24-Faceted-Search#What a facet is here]].
- With a text query, facet counts describe the top `facet_pool=1000` candidates
  from the selected retrieval mode. They are labeled `candidate_pool`, not total
  corpus counts. With no text query, counts are exact over all matching rows.

## File structure

| Path | Responsibility |
|---|---|
| `src/geo_index/search_models.py` | Typed filters, results, facet buckets, response metadata |
| `src/geo_index/facets.py` | Filter SQL builder, label resolution, disjunctive facet counts |
| `src/geo_index/pg_hybrid.py` | Filtered BM25/dense/hybrid retrieval and array indexes |
| `src/geo_index/web.py` | HTTP request parsing and response serialization |
| `src/geo_index/web_ui.html` | Minimal multi-select filter and facet display |
| `tests/test_search_models.py` | Filter validation and semantics |
| `tests/test_facets.py` | SQL-builder and facet-count unit tests |
| `tests/test_pg_hybrid.py` | Query-construction and opt-in Postgres integration tests |
| `tests/test_web.py` | HTTP parameter parsing tests |
| `pyproject.toml` | Integration marker registration |

### Task 1: Define one internal search/filter contract

**Files:**
- Create: `src/geo_index/search_models.py`
- Create: `tests/test_search_models.py`

**Interfaces:**
- Produces: `FacetField`, `SearchFilters`, `FacetBucket`, `FacetResult`, and
  `SearchResponse`.
- Consumers: Postgres search, web endpoint, retrieval evaluation, and MCP layer.

- [ ] **Step 1: Write filter-normalization tests**

Cover these exact behaviors in `tests/test_search_models.py`:

```python
import pytest

from geo_index.search_models import SearchFilters


def test_filters_deduplicate_without_reordering() -> None:
    filters = SearchFilters.from_mapping(
        {"organism_ids": ["NCBITaxon:9606", "NCBITaxon:9606", "NCBITaxon:10090"]}
    )
    assert filters.organism_ids == ("NCBITaxon:9606", "NCBITaxon:10090")


def test_without_removes_only_the_requested_facet() -> None:
    filters = SearchFilters(
        organism_ids=("NCBITaxon:9606",),
        sex_ids=("PATO:0000383",),
    )
    assert filters.without("organism_ids").organism_ids == ()
    assert filters.without("organism_ids").sex_ids == ("PATO:0000383",)


def test_unknown_filter_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown filter field"):
        SearchFilters.from_mapping({"tissue_ids": ["UBERON:0000955"]})
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

```bash
uv run pytest tests/test_search_models.py -v
```

Expected: import/collection fails because `search_models.py` does not exist.

- [ ] **Step 3: Implement immutable dataclasses**

Use this public shape in `src/geo_index/search_models.py`:

```python
from dataclasses import dataclass, field, replace
from typing import Literal, Mapping, Sequence, TypeAlias


FacetField = Literal[
    "organism_ids", "sex_ids", "assay_categories", "assay_labels"
]
FACET_FIELDS: tuple[FacetField, ...] = (
    "organism_ids", "sex_ids", "assay_categories", "assay_labels"
)
SearchHit: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class SearchFilters:
    organism_ids: tuple[str, ...] = ()
    sex_ids: tuple[str, ...] = ()
    assay_categories: tuple[str, ...] = ()
    assay_labels: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Sequence[str]] | None
    ) -> "SearchFilters":
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

    def without(self, facet: FacetField) -> "SearchFilters":
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
```

Do not validate IDs by network call; strict field names and nonblank values are
enough internally. Track 4 adds the external Pydantic validation boundary.

- [ ] **Step 4: Run the focused tests**

```bash
uv run pytest tests/test_search_models.py -v
```

Expected: all contract tests pass.

- [ ] **Step 5: Commit the contract**

```bash
git add src/geo_index/search_models.py tests/test_search_models.py
git commit -m "feat: define normalized search filters"
```

### Task 2: Build filter predicates from a closed column whitelist

**Files:**
- Create: `src/geo_index/facets.py`
- Create: `tests/test_facets.py`

**Interfaces:**
- Produces: `build_filter_clause(filters, *, exclude=None, alias="s")`.
- Returns: `(sql_fragment, parameters)`; callers append the fragment to an
  existing `WHERE` clause.

- [ ] **Step 1: Write SQL-builder tests**

Test all of the following:

```python
from geo_index.facets import build_filter_clause
from geo_index.search_models import SearchFilters


def test_filter_sql_ors_within_and_ands_across_fields() -> None:
    sql, params = build_filter_clause(
        SearchFilters(
            organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
            sex_ids=("PATO:0000383",),
        ),
        alias="s",
    )
    assert "s.organism_ids && %(filter_organism_ids)s::text[]" in sql
    assert "s.sex_ids && %(filter_sex_ids)s::text[]" in sql
    assert " AND " in sql
    assert params["filter_organism_ids"] == ["NCBITaxon:9606", "NCBITaxon:10090"]


def test_filter_sql_can_exclude_its_own_facet() -> None:
    sql, params = build_filter_clause(
        SearchFilters(
            organism_ids=("NCBITaxon:9606",),
            sex_ids=("PATO:0000383",),
        ),
        exclude="organism_ids",
    )
    assert "organism_ids" not in sql
    assert "filter_organism_ids" not in params
    assert "sex_ids" in sql
```

- [ ] **Step 2: Confirm the tests fail, then implement the builder**

Add this implementation to `src/geo_index/facets.py`:

```python
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
```

The only interpolated names come from the two closed constants; all user values
remain psycopg parameters. An empty filter returns `"TRUE"`.

```bash
uv run pytest tests/test_facets.py -v
```

Expected after implementation: all SQL-builder tests pass.

- [ ] **Step 3: Commit the safe predicate builder**

```bash
git add src/geo_index/facets.py tests/test_facets.py
git commit -m "feat: build normalized array filter predicates"
```

### Task 3: Make fresh schemas and the existing live table index-ready

**Files:**
- Modify: `src/geo_index/pg_hybrid.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_pg_hybrid.py`

**Interfaces:**
- Produces: `build_filter_indexes() -> int`.
- Produces CLI command: `python -m geo_index.pg_hybrid filter-index`.
- Preserves: `geo-normalize migrate` as the idempotent migration path.

- [ ] **Step 1: Register opt-in integration tests**

Add this pytest marker in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
markers = ["integration: requires the local GEO Postgres database"]
```

Integration tests must skip unless `GEO_TEST_PG=1`; the default suite remains
fast, offline, and nondestructive.

- [ ] **Step 2: Keep `init()` consistent with normalization**

Add nullable columns for the four v1 arrays and their status fields to the table
created by `pg_hybrid.init()`:

```sql
organism_ids TEXT[],
organism_status TEXT,
sex_ids TEXT[],
sex_status TEXT,
assay_categories TEXT[],
assay_labels TEXT[],
assay_status TEXT
```

Keep `normalize.migrate()` unchanged and idempotent for older databases. Do not
run `init()` on the populated database: it intentionally drops `series`.

- [ ] **Step 3: Add a separately runnable array-index function**

`build_filter_indexes()` creates these indexes with `IF NOT EXISTS`:

```sql
CREATE INDEX IF NOT EXISTS series_organism_ids_gin
    ON series USING gin (organism_ids);
CREATE INDEX IF NOT EXISTS series_sex_ids_gin
    ON series USING gin (sex_ids);
CREATE INDEX IF NOT EXISTS series_assay_categories_gin
    ON series USING gin (assay_categories);
CREATE INDEX IF NOT EXISTS series_assay_labels_gin
    ON series USING gin (assay_labels);
ANALYZE series;
```

Implement it as a separately committed transaction:

```python
def build_filter_indexes() -> int:
    statements = (
        "CREATE INDEX IF NOT EXISTS series_organism_ids_gin "
        "ON series USING gin (organism_ids)",
        "CREATE INDEX IF NOT EXISTS series_sex_ids_gin "
        "ON series USING gin (sex_ids)",
        "CREATE INDEX IF NOT EXISTS series_assay_categories_gin "
        "ON series USING gin (assay_categories)",
        "CREATE INDEX IF NOT EXISTS series_assay_labels_gin "
        "ON series USING gin (assay_labels)",
    )
    with _connect() as conn, conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
        cur.execute("ANALYZE series")
        conn.commit()
    print("ensured four normalized-array GIN indexes", flush=True)
    return 0
```

After the BM25/HNSW connection block closes, have `build_indexes()` return
`build_filter_indexes()`. Register and dispatch the safe upgrade command:

```python
    sub.add_parser("filter-index")
```

```python
    if a.cmd == "filter-index":
        return build_filter_indexes()
```

Have `build_indexes()` call `build_filter_indexes()` on fresh builds, but expose
`filter-index` separately so adding these indexes to the live corpus does not
rebuild BM25 or HNSW.

- [ ] **Step 4: Add an opt-in schema/index integration test**

The test opens the configured database read-only and asserts the four columns and
four named indexes exist. It must never call `init()`, `normalize.run()`, or any
DDL itself.

- [ ] **Step 5: Run unit tests, then add only the missing live indexes**

```bash
uv run pytest -m "not integration" -v
uv run python -m geo_index.pg_hybrid filter-index
GEO_TEST_PG=1 uv run pytest tests/test_pg_hybrid.py -m integration -v
```

Expected: four GIN indexes are present; row counts and normalized values are
unchanged.

- [ ] **Step 6: Commit schema/index support**

```bash
git add pyproject.toml src/geo_index/pg_hybrid.py tests/test_pg_hybrid.py
git commit -m "feat: index normalized search arrays"
```

### Task 4: Apply filters inside every retrieval branch

**Files:**
- Modify: `src/geo_index/pg_hybrid.py`
- Modify: `tests/test_pg_hybrid.py`

**Interfaces:**
- Extends: `search_rows(conn, query: str, *, model=None,
  qv: np.ndarray | None = None, topk: int = 15, deep: int = 200,
  mode: str = "hybrid", k0: int = 60,
  filters: SearchFilters | None = None) -> list[dict]`.
- Preserves: callers that omit filters and all existing rank fields.

- [ ] **Step 1: Add query-construction tests before changing retrieval**

Write tests for `_search_statement(mode, predicate)` without a database. Assert:

1. BM25 includes the filter in its `WHERE` clause.
2. Dense includes the filter before `ORDER BY embedding <=>`.
3. Both CTEs in hybrid include the same filter.
4. An empty `SearchFilters()` leaves behavior unchanged.
5. Parameters—not string formatting—carry all requested values.

- [ ] **Step 2: Extend `search_rows()`**

Add this complete statement helper above `search_rows()`:

```python
from .facets import build_filter_clause
from .search_models import SearchFilters


def _search_statement(mode: str, predicate: str) -> str:
    if mode == "bm25":
        return f"""
            SELECT s.gse, s.title, s.type, paradedb.score(s.id) AS score,
                   NULL::bigint AS bm25_rank, NULL::bigint AS dense_rank
            FROM series AS s
            WHERE s.search_text @@@ %(q)s AND ({predicate})
            ORDER BY score DESC
            LIMIT %(topk)s
        """
    if mode == "dense":
        return f"""
            SELECT s.gse, s.title, s.type,
                   1 - (s.embedding <=> %(qv)s) AS score,
                   NULL::bigint AS bm25_rank, NULL::bigint AS dense_rank
            FROM series AS s
            WHERE {predicate}
            ORDER BY s.embedding <=> %(qv)s
            LIMIT %(topk)s
        """
    if mode == "hybrid":
        return f"""
            WITH bm25 AS (
                SELECT s.id,
                       RANK() OVER (ORDER BY paradedb.score(s.id) DESC) AS rank
                FROM series AS s
                WHERE s.search_text @@@ %(q)s AND ({predicate})
                ORDER BY paradedb.score(s.id) DESC
                LIMIT %(deep)s
            ),
            dense AS (
                SELECT s.id,
                       RANK() OVER (ORDER BY s.embedding <=> %(qv)s) AS rank
                FROM series AS s
                WHERE {predicate}
                ORDER BY s.embedding <=> %(qv)s
                LIMIT %(deep)s
            ),
            fused AS (
                SELECT COALESCE(b.id, d.id) AS id,
                       COALESCE(1.0 / (%(k0)s + b.rank), 0) +
                       COALESCE(1.0 / (%(k0)s + d.rank), 0) AS rrf,
                       b.rank AS bm25_rank,
                       d.rank AS dense_rank
                FROM bm25 AS b
                FULL OUTER JOIN dense AS d USING (id)
            )
            SELECT s.gse, s.title, s.type, f.rrf AS score,
                   f.bm25_rank, f.dense_rank
            FROM fused AS f
            JOIN series AS s USING (id)
            ORDER BY f.rrf DESC
            LIMIT %(topk)s
        """
    raise ValueError(f"unsupported search mode: {mode}")
```

Replace `search_rows()` with:

```python
def search_rows(
    conn,
    query: str,
    *,
    model=None,
    qv: np.ndarray | None = None,
    topk: int = 15,
    deep: int = 200,
    mode: str = "hybrid",
    k0: int = 60,
    filters: SearchFilters | None = None,
) -> list[dict]:
    if mode not in {"bm25", "dense", "hybrid"}:
        raise ValueError(f"unsupported search mode: {mode}")
    if topk < 1 or deep < topk or k0 < 1:
        raise ValueError("require topk >= 1, deep >= topk, and k0 >= 1")
    active_filters = filters or SearchFilters()
    if mode != "bm25" and qv is None:
        if model is None:
            model = load_model()
        qv = embed_query(model, query)
    predicate, filter_params = build_filter_clause(active_filters, alias="s")
    params: dict[str, object] = {
        "q": query,
        "qv": qv,
        "topk": topk,
        "deep": deep,
        "k0": k0,
        **filter_params,
    }
    with conn.cursor() as cur:
        if mode in {"dense", "hybrid"}:
            cur.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        cur.execute(_search_statement(mode, predicate), params)
        return [
            {
                "gse": gse,
                "title": title,
                "type": study_type,
                "score": float(score) if score is not None else None,
                "bm25_rank": int(bm25_rank) if bm25_rank is not None else None,
                "dense_rank": int(dense_rank) if dense_rank is not None else None,
            }
            for gse, title, study_type, score, bm25_rank, dense_rank
            in cur.fetchall()
        ]
```

The fragment is inside each branch before its limit. Do not retrieve globally and
filter the returned rows afterward; that loses relevant filtered hits.

For dense and hybrid searches, reuse the existing precomputed `qv` parameter and
run this before the SELECT:

```python
if mode in ("dense", "hybrid"):
    cur.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
```

The live database has pgvector 0.8.2, which supports
[iterative filtered index scans](https://github.com/pgvector/pgvector#iterative-index-scans).
Add an integration assertion that a matching rare filter fills `topk` when at
least `topk` matching rows exist.

- [ ] **Step 3: Add live behavior tests**

With `GEO_TEST_PG=1`, verify:

- a human-only search returns only rows containing `NCBITaxon:9606`;
- a mouse-only search returns only rows containing `NCBITaxon:10090`;
- `female OR male` is one overlap predicate, not two AND predicates;
- an impossible value returns `[]` rather than falling back to unfiltered search.

The test should inspect the normalized arrays for returned GSEs directly instead
of relying on titles.

- [ ] **Step 4: Run unit and integration tests**

```bash
uv run pytest tests/test_pg_hybrid.py -m "not integration" -v
GEO_TEST_PG=1 uv run pytest tests/test_pg_hybrid.py -m integration -v
```

- [ ] **Step 5: Commit filtered retrieval**

```bash
git add src/geo_index/pg_hybrid.py tests/test_pg_hybrid.py
git commit -m "feat: filter hybrid retrieval by normalized fields"
```

### Task 5: Add disjunctive facet counts with an explicit scope

**Files:**
- Modify: `src/geo_index/facets.py`
- Modify: `src/geo_index/pg_hybrid.py`
- Modify: `tests/test_facets.py`
- Modify: `tests/test_pg_hybrid.py`

**Interfaces:**
- Produces: `facet_counts(conn, *, query: str, mode: str, qv: np.ndarray | None,
  filters: SearchFilters, retrieve: Retriever, deep: int = 200, k0: int = 60,
  fields: tuple[FacetField, ...] = FACET_FIELDS, facet_pool: int = 1000,
  bucket_limit: int = 50) -> dict[FacetField, FacetResult]`.
- Produces: `search_with_facets(conn, query: str, *, filters: SearchFilters,
  model=None, qv: np.ndarray | None = None, topk: int = 15, deep: int = 200,
  mode: str = "hybrid", k0: int = 60, facet_pool: int = 1000) -> SearchResponse`.

- [ ] **Step 1: Write deterministic facet tests against a temporary table**

Use a tiny fixture with these conceptual rows:

| Row | Organism | Sex | Assay label |
|---|---|---|---|
| A | human | female | scRNA-seq |
| B | human | male | ChIP-seq |
| C | mouse | female | scRNA-seq |

Assert:

- no filters: human=2, mouse=1;
- human selected: organism still shows human=2 and mouse=1;
- human + female selected: organism shows human=1 and mouse=1 because its own
  filter is dropped while female remains;
- human selected: assay shows scRNA-seq=1 and ChIP-seq=1;
- counts use `COUNT(DISTINCT series.id)`.

The unit version can use a fake cursor with captured statements; the opt-in
integration version may create a transaction-scoped temporary table and roll it
back.

- [ ] **Step 2: Implement the two count scopes**

For each requested facet field:

1. Call `filters.without(field)`.
2. If `query` is blank, aggregate directly over every row matching the other
   filters and return `scope="all_matches"`.
3. If `query` is nonblank, obtain up to `facet_pool` ranked IDs in the requested
   BM25/dense/hybrid mode using the other filters, aggregate over those GSEs, and
   return `scope="candidate_pool"` plus the actual candidate count.
4. Unnest only the whitelisted facet column, group by value, order by
   `count DESC, value ASC`, and cap at `bucket_limit`.

For one request, embed the query once and pass the same `qv` to result retrieval
and every facet candidate query. In hybrid mode use
`deep=max(deep, facet_pool)` for the facet candidate calls. Build the aggregation
with this exact shape after substituting the column from `FACET_COLUMNS`:

```sql
SELECT value, count(*)::int
FROM (
    SELECT DISTINCT s.id, u.value
    FROM series AS s
    CROSS JOIN LATERAL unnest(s.assay_labels) AS u(value)
    WHERE s.gse = ANY(%(candidate_gses)s::text[])
) AS values_per_series
GROUP BY value
ORDER BY count(*) DESC, value ASC
LIMIT %(bucket_limit)s
```

For a blank query, replace the candidate-GSE predicate with the filter fragment
produced from `filters.without(field)`. The `DISTINCT (id, value)` prevents a
malformed duplicate array entry from double-counting a series.

Add these helpers to `src/geo_index/facets.py` below the filter builder:

```python
from collections.abc import Callable

import numpy as np

from .normalize import (
    NCBITAXON,
    PATO_FEMALE,
    PATO_HERMAPHRODITE,
    PATO_MALE,
)
from .search_models import FacetBucket, FacetResult


_ORGANISM_LABELS = {value: key for key, value in NCBITAXON.items()}
_SEX_LABELS = {
    PATO_FEMALE: "female",
    PATO_MALE: "male",
    PATO_HERMAPHRODITE: "hermaphrodite",
}


def facet_label(field: FacetField, value: str) -> str:
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
    if field not in FACET_COLUMNS:
        raise ValueError(f"unknown facet field: {field}")
    if limit < 1:
        raise ValueError("facet limit must be positive")
    predicate, filter_params = build_filter_clause(
        filters, exclude=field, alias="s"
    )
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
```

- [ ] **Step 3: Resolve compact display labels without an API call**

In `facets.py`, derive organism labels by reversing `normalize.NCBITAXON`; map the
three supported sex IDs from `PATO_MALE`, `PATO_FEMALE`, and
`PATO_HERMAPHRODITE`; use assay category/label values as their own labels. If an
unknown ID is already present in the database, return the ID as its label rather
than hiding the bucket.

- [ ] **Step 4: Add `search_with_facets()`**

Add to `src/geo_index/pg_hybrid.py`:

```python
from .facets import facet_counts
from .search_models import SearchFilters, SearchResponse


def search_with_facets(
    conn,
    query: str,
    *,
    filters: SearchFilters | None = None,
    model=None,
    qv: np.ndarray | None = None,
    topk: int = 15,
    deep: int = 200,
    mode: str = "hybrid",
    k0: int = 60,
    facet_pool: int = 1000,
) -> SearchResponse:
    active_filters = filters or SearchFilters()
    if mode != "bm25" and qv is None:
        if model is None:
            model = load_model()
        qv = embed_query(model, query)
    hits = search_rows(
        conn,
        query,
        qv=qv,
        topk=topk,
        deep=deep,
        mode=mode,
        k0=k0,
        filters=active_filters,
    )
    facets = facet_counts(
        conn,
        query=query,
        mode=mode,
        qv=qv,
        filters=active_filters,
        retrieve=search_rows,
        deep=deep,
        k0=k0,
        facet_pool=facet_pool,
    )
    return SearchResponse(hits=tuple(hits), facets=facets)
```

Keep `search_rows()` available for callers that do not need counts, including
the retrieval evaluator.

- [ ] **Step 5: Run focused and full tests**

```bash
uv run pytest tests/test_facets.py tests/test_pg_hybrid.py -v
uv run pytest -v
```

- [ ] **Step 6: Commit facets**

```bash
git add src/geo_index/facets.py src/geo_index/pg_hybrid.py tests/test_facets.py tests/test_pg_hybrid.py
git commit -m "feat: return disjunctive normalized facets"
```

### Task 6: Expose filters and facets in the local HTTP JSON API

**Files:**
- Modify: `src/geo_index/web.py`
- Create: `tests/test_web.py`

**Interfaces:**
- Extends `/api/search` query parameters with repeatable `organism_id`, `sex_id`,
  `assay_category`, and `assay_label`.
- Adds response keys: `filters` and `facets`.

- [ ] **Step 1: Extract and test request parsing**

Add this pure helper and test repeated values, invalid-mode fallback, the `topk`
range `1..50`, and blank-filter rejection without starting a server:

```python
from dataclasses import asdict

from .search_models import SearchFilters, SearchResponse


_WEB_FILTERS = {
    "organism_id": "organism_ids",
    "sex_id": "sex_ids",
    "assay_category": "assay_categories",
    "assay_label": "assay_labels",
}


def _parse_search_request(
    query_string: dict[str, list[str]],
) -> tuple[str, str, int, SearchFilters]:
    query = query_string.get("q", [""])[0].strip()
    mode = query_string.get("mode", ["hybrid"])[0]
    if mode not in {"hybrid", "dense", "bm25"}:
        mode = "hybrid"
    try:
        topk = max(1, min(50, int(query_string.get("topk", ["15"])[0])))
    except ValueError:
        topk = 15
    values = {
        internal: query_string.get(external, [])
        for external, internal in _WEB_FILTERS.items()
    }
    return query, mode, topk, SearchFilters.from_mapping(values)


def _serialize_search(
    response: SearchResponse, filters: SearchFilters
) -> dict[str, object]:
    return {
        "ours": list(response.hits),
        "filters": filters.as_dict(),
        "facets": {
            field: asdict(result)
            for field, result in response.facets.items()
        },
    }
```

Example request:

```text
/api/search?q=immune+cells&organism_id=NCBITaxon%3A9606&assay_label=scRNA-seq
```

- [ ] **Step 2: Switch the endpoint to `search_with_facets()`**

Change `_our_search()` to return a `SearchResponse`:

```python
def _our_search(
    query: str,
    mode: str,
    topk: int,
    filters: SearchFilters,
) -> SearchResponse:
    global _model
    qv = None
    if mode != "bm25":
        with _model_lock:
            if _model is None:
                _model = pg_hybrid.load_model()
            qv = pg_hybrid.embed_query(_model, query)
    conn = pg_hybrid._connect()
    try:
        return pg_hybrid.search_with_facets(
            conn,
            query,
            qv=qv,
            mode=mode,
            topk=topk,
            filters=filters,
        )
    finally:
        conn.close()
```

In the handler, call `_parse_search_request(qs)`, reject an empty query with the
existing 400 response, and build the local payload exactly as follows before
adding the unchanged NCBI comparison:

```python
response = _our_search(query, mode, topk, filters)
ours = list(response.hits)
payload = _serialize_search(response, filters)
payload.update(
    {
        "query": query,
        "mode": mode,
        "geo": _geo_keyword_search(query, topk),
    }
)
```

Run `_geo_membership()` against `ours` as before. Facet serialization includes
`scope` and `candidate_count`, so a caller cannot mistake top-1,000 semantic
counts for complete corpus totals.

- [ ] **Step 3: Run tests and a manual API smoke search**

```bash
uv run pytest tests/test_web.py -v
uv run python -m geo_index.web
```

Request `single cell RNA` with human, then human + mouse, using the repeatable
query parameters shown above. Both values remain visible in the organism facet
and every returned hit has at least one selected organism ID.

- [ ] **Step 4: Commit HTTP exposure**

```bash
git add src/geo_index/web.py tests/test_web.py
git commit -m "feat: expose normalized filters and facets"
```

### Task 7: Record the live-data smoke baseline

**Files:**
- Modify: `wiki/42-Build-Log.md`

- [ ] **Step 1: Run read-only corpus checks**

Record total/filter counts and the top ten buckets for all four facets. Verify at
minimum that current counts remain 97,114 human series, 71,204 mouse series,
24,719 series containing female, and 28,934 containing male unless the corpus has
intentionally changed since 2026-07-10.

- [ ] **Step 2: Time representative calls**

Run blank-query facets, hybrid `single cell RNA` facets, and hybrid `chromatin
accessibility` with human + assay filters. Record cold and warm timings without
turning a prototype timing into a hard CI threshold.

- [ ] **Step 3: Update the build log and commit**

```bash
git add wiki/42-Build-Log.md
git commit -m "docs: record normalized facet smoke results"
```

## Definition of done

- Existing organism/sex rows are reused; Track 1 performs only the required
  targeted assay refresh.
- BM25, dense, and hybrid modes accept the same four normalized filter fields.
- OR-within/AND-across behavior is covered by tests.
- Filters are applied before branch-level top-k selection.
- Each facet excludes its own filter and reports either exact-all-match or bounded
  candidate-pool scope.
- Four GIN array indexes exist without rebuilding the other live indexes.
- The HTTP JSON API accepts organism, sex, assay category, and assay label filters
  and returns scoped counts.
- The default test suite is offline; live Postgres tests are explicit and
  read-only except for rolled-back temporary fixtures.

## Explicitly deferred

- Tissue, cell type, disease, developmental-stage, and ethnicity filters.
- EFO grounding and hierarchical assay ancestor arrays.
- Ontology-DAG rollups; these follow the tissue decision gate.
- Sample-level conjunction semantics.
- Performance work beyond measured prototype queries.
- Visual facet controls in the local comparison page; MCP is the primary v1
  interactive surface.

## Sources

- pgvector iterative index scans — https://github.com/pgvector/pgvector#iterative-index-scans
