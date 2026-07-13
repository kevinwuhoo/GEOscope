# Public Search Contract and Demo Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove caller-selected retrieval modes from every public GEOscope search surface, always use hybrid retrieval for nonblank searches, and raise the anonymous demo's global admission limits to 100 requests/second, burst 100, and concurrency 20.

**Architecture:** `McpSearchService` remains the single production retrieval-policy boundary shared by MCP and the marketing API. MCP, REST, and frontend contracts stop carrying `mode`; the shared service selects hybrid for nonblank searches and uses the existing filter-only aggregation for blank facet browsing, while low-level Elasticsearch evaluation interfaces retain explicit modes. HTTP admission remains process-wide and configurable, with higher hackathon defaults and the existing request-body bound.

**Tech Stack:** Python 3.11, FastMCP 3.4.4, Pydantic 2, FastAPI, Elasticsearch 9, pytest, React 19, TypeScript, Zod, Vitest, DigitalOcean App Platform.

## Global Constraints

- Search correctness and relevance behavior must be implemented in the shared MCP/Elasticsearch layer, not only in the marketing site.
- Every nonblank public dataset or query-scoped facet search uses hybrid retrieval.
- Blank facet browsing is an unranked filter-only aggregation and must not initialize or call the query encoder.
- Public MCP, REST, response, and frontend contracts contain no caller-selected `mode`.
- Internal Elasticsearch, evaluation, comparison, and CLI interfaces retain BM25, dense, and hybrid modes.
- Admission controls are global to the single anonymous Uvicorn worker, not per user.
- Defaults and deployment values are 100 requests/second, burst 100, concurrency 20, request body 256 KB, and request-body timeout 10 seconds.
- Preserve all unrelated existing working-tree changes, especially the current frontend edits; do not stage them without explicit user authorization.

---

## File Structure

- `src/geo_index/mcp_models.py`: public MCP transport inputs and outputs.
- `src/geo_index/mcp_server.py`: MCP tool registration and HTTP admission middleware wiring.
- `src/geo_index/mcp_search_service.py`: shared production retrieval policy and Elasticsearch adaptation.
- `src/geo_index/marketing_api.py`: public browser API layered on `McpSearchService`.
- `src/geo_index/mcp_settings.py`: configurable admission defaults.
- `frontend/src/api.ts`: browser request and response contract.
- `frontend/src/components/LiveComparison.tsx`: live-demo caller and URL state.
- `tests/test_mcp_models.py`, `tests/test_mcp_server.py`, `tests/test_mcp_search_service.py`, `tests/test_mcp_http.py`, `tests/test_mcp_elasticsearch_smoke.py`: MCP contract and shared-policy coverage.
- `tests/test_marketing_api.py`, `frontend/src/App.test.tsx`: REST/frontend contract coverage.
- `tests/test_mcp_settings.py`, `tests/test_mcp_packaging.py`: admission configuration and packaging coverage.
- `deploy/geo-mcp.env.example`, `.do/app.yaml.tmpl`, `docs/deployment/digitalocean.md`, `README.md`, `wiki/27-MCP-Interface.md`: deployable defaults and user-facing reference.

### Task 1: Make the shared service own production retrieval mode

**Files:**
- Modify: `tests/test_mcp_search_service.py`
- Modify: `src/geo_index/mcp_search_service.py`

**Interfaces:**
- Consumes: `ElasticsearchSearchService.search(query, *, mode, filters, topk, bucket_limit)`.
- Produces: `McpSearchService.search_datasets(*, query: str, filters: SearchFilters, limit: int) -> SearchDatasetsOutput`.
- Produces: `McpSearchService.facet_values(*, field: FacetField, query: str | None, filters: SearchFilters, limit: int) -> FacetValuesOutput`.

- [ ] **Step 1: Write failing shared-policy tests**

Replace public service calls that pass `mode` and add exact internal-routing assertions:

```python
def test_public_search_always_uses_hybrid_retrieval() -> None:
    service, _, _, _, domain = _service(responses=[_response(mode="hybrid")])
    service.open()

    output = service.search_datasets(
        query="immune", filters=SearchFilters(), limit=5
    )

    assert domain().calls[0]["mode"] == "hybrid"
    assert output.embedding_variant == "gemini_embedding_2_3072_v1"


def test_facet_values_use_filter_only_for_blank_and_hybrid_for_query() -> None:
    blank = SearchResponse(hits=(), facets=_facets("all_matches"), provenance=None)
    service, _, _, _, domain = _service(
        responses=[blank, _response(mode="hybrid")]
    )
    service.open()

    all_values = service.facet_values(
        field="organism_ids", query=None, filters=SearchFilters(), limit=10
    )
    candidates = service.facet_values(
        field="organism_ids", query="immune", filters=SearchFilters(), limit=10
    )

    assert domain().calls[0]["query"] == ""
    assert domain().calls[0]["mode"] == "bm25"
    assert domain().calls[1]["mode"] == "hybrid"
    assert all_values.retrieval_version == "facet-all-matches-v1"
    assert all_values.embedding_variant is None
    assert candidates.embedding_variant == "gemini_embedding_2_3072_v1"
```

Update unknown-filter and hydration calls to omit `mode`. Remove the old test that calls public BM25 and dense service modes; low-level mode behavior remains covered by `tests/test_elasticsearch_search.py`.

For `test_search_hydrates_ranked_hits_maps_provenance_and_bounds_output`, pass
`responses=[_response(mode="hybrid")]`, expect the retrieval version suffix
`:hybrid`, and expect `embedding_variant == "gemini_embedding_2_3072_v1"`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest tests/test_mcp_search_service.py -q
```

Expected: FAIL with `TypeError` because `mode` is still required by `search_datasets` and `facet_values`.

- [ ] **Step 3: Implement fixed shared routing**

Change validation and public methods to remove `mode`:

```python
@staticmethod
def _validate_search_request(
    query: str, filters: SearchFilters, limit: int
) -> str:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    normalized = query.strip()
    if not 1 <= len(normalized) <= 1000:
        raise ValueError("query must contain between 1 and 1,000 characters")
    if not isinstance(filters, SearchFilters):
        raise TypeError("filters must be SearchFilters")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    return normalized
```

```python
def search_datasets(
    self, *, query: str, filters: SearchFilters, limit: int
) -> SearchDatasetsOutput:
    query = self._validate_search_request(query, filters, limit)
    self._require_filters(filters)
    client, search = self._require_open()
    response = search.search(
        query,
        filters=filters,
        mode="hybrid",
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
                snippet=_cap_text(
                    document.get("summary"), 1000, "snippet", truncated
                ),
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
        mode="hybrid",
        limit=limit,
        retrieval_version=_retrieval_version(response.provenance),
        embedding_variant=self.elasticsearch.active_model_key,
        results=summaries,
        facets=facets,
    )
```

Remove the `mode` parameter from `facet_values` and select only internally:

```python
effective_mode = "hybrid" if normalized_query else "bm25"
response = search.search(
    normalized_query,
    filters=filters,
    mode=effective_mode,
    topk=1,
    bucket_limit=limit,
)
```

Set search `embedding_variant` from the fixed hybrid behavior. Keep blank facet provenance as `facet-all-matches-v1`; query-scoped facets report the configured embedding variant.

- [ ] **Step 4: Run the shared-policy tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_mcp_search_service.py tests/test_elasticsearch_search.py -q
```

Expected: PASS, including low-level explicit-mode coverage.

- [ ] **Step 5: Commit the clean backend policy change**

```bash
git add src/geo_index/mcp_search_service.py tests/test_mcp_search_service.py
git commit -m "feat: centralize public hybrid search policy"
```

### Task 2: Remove mode from the MCP wire contract

**Files:**
- Modify: `tests/test_mcp_models.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_mcp_http.py`
- Modify: `tests/test_mcp_elasticsearch_smoke.py`
- Modify: `tests/test_marketing_api.py` (output fixture only; route changes remain in Task 3)
- Modify: `src/geo_index/mcp_models.py`
- Modify: `src/geo_index/mcp_server.py`

**Interfaces:**
- Consumes: mode-free `McpSearchService.search_datasets` and `facet_values` from Task 1.
- Produces: MCP `search_datasets(query, filters=None, limit=15)`.
- Produces: MCP `facet_values(field, query=None, filters=None, limit=50)`.
- Produces: `SearchDatasetsOutput` without a `mode` property.

- [ ] **Step 1: Write failing MCP schema and strict-validation tests**

Update model construction to omit mode and assert it is forbidden:

```python
with pytest.raises(ValidationError):
    SearchDatasetsInput(query="x", mode="hybrid")
with pytest.raises(ValidationError):
    FacetValuesInput(field="organism_ids", mode="hybrid")

assert set(search.model_dump(mode="json")) == {
    "query", "filters", "limit", "retrieval_version",
    "embedding_variant", "results", "facets",
}
```

Strengthen the runtime schema assertion and invalid-call matrix:

```python
for tool in tools:
    assert "mode" not in tool.inputSchema.get("properties", {})
    assert "mode" not in (tool.outputSchema or {}).get("properties", {})

@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "x", "mode": "hybrid"},
        {"query": "x", "limit": "5"},
        {"query": "x", "filters": {"invented": ["x"]}},
        {"query": " ", "limit": 5},
    ],
)
async def test_validation_fails_before_service(
    mcp, fake_service: FakeService, arguments: dict[str, object]
) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_datasets", arguments, raise_on_error=False
        )
    assert result.is_error is True
    assert fake_service.search_calls == []
```

Update fake outputs and delegation assertions so no service call includes `mode`.
Remove the `mode` argument from the `SearchDatasetsOutput` fixture in
`tests/test_marketing_api.py` so the model change does not leave an unrelated
suite broken.

Update the live smoke to issue one mode-free search and require the active
embedding variant. While touching that opt-in smoke, align its `_settings()`
with the current anonymous `McpSettings` fields by removing `jwks_uri`,
`issuer`, `audience`, `authorization_server`, and `allowed_subjects`; remove the
`AccessToken` import and authorization monkeypatch; and supply
`max_concurrent_requests=100` with the existing high test-only rate values.

- [ ] **Step 2: Run the MCP tests and verify RED**

Run:

```bash
uv run pytest tests/test_mcp_models.py tests/test_mcp_server.py tests/test_mcp_http.py -q
```

Expected: FAIL because generated tool schemas and transport models still expose `mode`.

- [ ] **Step 3: Remove mode from models and tool functions**

Delete the MCP-only `SearchMode` alias and the mode fields:

```python
class SearchDatasetsInput(_StrictInputModel):
    query: str
    filters: SearchFiltersInput = Field(default_factory=SearchFiltersInput)
    limit: int = Field(default=15, ge=1, le=50)


class FacetValuesInput(_StrictInputModel):
    field: FacetFieldName
    query: str | None = None
    filters: SearchFiltersInput = Field(default_factory=SearchFiltersInput)
    limit: int = Field(default=50, ge=1, le=50)


class SearchDatasetsOutput(_StrictOutputModel):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    filters: SearchFiltersInput
    limit: int = Field(ge=1, le=50)
    retrieval_version: BoundedVersion
    embedding_variant: BoundedValue | None
    results: list[DatasetSummary] = Field(max_length=50)
    facets: dict[FacetFieldName, FacetResultOutput]
```

Remove `mode` from both `@mcp.tool` function signatures, input construction, and service delegation. Keep timeouts, read-only annotations, strict validation, and masked errors unchanged.

- [ ] **Step 4: Run MCP contract tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_mcp_models.py tests/test_mcp_server.py tests/test_mcp_http.py tests/test_mcp_elasticsearch_smoke.py -q
```

Expected: all offline tests PASS; the opt-in live smoke SKIPS unless `GEO_TEST_ELASTIC=1`.

- [ ] **Step 5: Commit the MCP contract change**

```bash
git add src/geo_index/mcp_models.py src/geo_index/mcp_server.py src/geo_index/mcp_search_service.py tests/test_mcp_models.py tests/test_mcp_server.py tests/test_mcp_http.py tests/test_mcp_elasticsearch_smoke.py tests/test_marketing_api.py
git commit -m "feat: hide retrieval mode from MCP"
```

### Task 3: Remove mode from the marketing API and frontend client

**Files:**
- Modify: `tests/test_marketing_api.py`
- Modify: `src/geo_index/marketing_api.py`
- Modify: `frontend/src/App.test.tsx` (already dirty; preserve unrelated hunks)
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/LiveComparison.tsx` (already dirty; preserve unrelated hunks)

**Interfaces:**
- Consumes: mode-free `McpSearchService.search_datasets` from Task 1.
- Produces: `GET /api/demo/search?q=<query>&limit=<1..20>`.
- Produces: `searchDemo(query: string, signal?: AbortSignal) -> Promise<DemoResponse>`.

- [ ] **Step 1: Write failing REST and frontend contract tests**

Change the marketing API test to omit `mode` and assert the shared service call:

```python
response = client.get(
    "/api/demo/search",
    params={"q": " transcriptomes of individual cells ", "limit": "5"},
)

assert "mode" not in response.json()
assert "mode" not in response.json()["geoscope"]
assert service.calls == [
    {
        "query": "transcriptomes of individual cells",
        "filters": SearchFilters(),
        "limit": 5,
    }
]
```

Add a compatibility assertion proving a legacy query parameter has no effect:

```python
def test_demo_search_ignores_legacy_mode_query_parameter() -> None:
    service = _DemoService()
    app = create_app(service_factory=lambda: service, geo_factory=_Geo)

    with TestClient(app) as client:
        response = client.get(
            "/api/demo/search",
            params={"q": "immune cells", "mode": "bm25", "limit": "5"},
        )

    assert response.status_code == 200
    assert "mode" not in response.json()
    assert service.calls[0] == {
        "query": "immune cells",
        "filters": SearchFilters(),
        "limit": 5,
    }
```

Remove both fixture `mode` properties in `frontend/src/App.test.tsx` and replace the request assertion:

```typescript
const requestUrl = new URL(String(fetchMock.mock.calls[0]?.[0]), window.location.origin);
expect(requestUrl.searchParams.get("q")).toBe("transcriptomes of individual cells");
expect(requestUrl.searchParams.get("limit")).toBe("8");
expect(requestUrl.searchParams.has("mode")).toBe(false);
```

- [ ] **Step 2: Run REST and frontend tests and verify RED**

Run:

```bash
uv run pytest tests/test_marketing_api.py -q
pnpm --dir frontend test -- --run
```

Expected: backend FAIL because the response and delegate still include mode; frontend FAIL because `searchDemo` still serializes `mode`.

- [ ] **Step 3: Remove mode from the marketing API**

Remove the unused `Literal` import, update the protocol, and simplify the route:

```python
class SearchService(Protocol):
    def search_datasets(
        self, *, query: str, filters: SearchFilters, limit: int
    ) -> SearchDatasetsOutput:
        pass
```

```python
@app.get("/api/demo/search")
async def demo_search(
    q: str = Query(min_length=1, max_length=1000),
    limit: int = Query(default=8, ge=1, le=20),
) -> dict[str, object]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="query must not be blank")
    service: SearchService = app.state.search_service
    geoscope = await asyncio.to_thread(
        service.search_datasets,
        query=query,
        filters=SearchFilters(),
        limit=limit,
    )
    geo: GeoComparison | None = app.state.geo
    if geo is None:
        native: dict[str, object] = {
            "count": None,
            "results": [],
            "error": "Native GEO comparison is not configured.",
        }
        membership = None
    else:
        try:
            native = await asyncio.to_thread(geo.keyword_search, query, limit)
            membership = await asyncio.to_thread(
                geo.membership,
                query,
                [result.gse for result in geoscope.results],
            )
        except Exception:
            native = {
                "count": None,
                "results": [],
                "error": "Native GEO search is temporarily unavailable.",
            }
            membership = None
    return {
        "query": query,
        "geo": native,
        "geoscope": geoscope.model_dump(mode="json"),
        "membership": membership,
    }
```

Return `query`, `geo`, `geoscope`, and `membership`, with no top-level mode.

- [ ] **Step 4: Remove mode from the frontend API and caller**

Update the Zod contract and function:

```typescript
const demoResponseSchema = z.object({
  query: z.string(),
  geo: z.object({
    count: z.number().nullable(),
    results: z.array(nativeResultSchema),
    error: z.string().optional(),
  }),
  geoscope: z.object({
    query: z.string(),
    retrieval_version: z.string(),
    embedding_variant: z.string().nullable(),
    results: z.array(geoscopeResultSchema),
    facets: z.record(z.string(), z.unknown()),
  }).passthrough(),
  membership: z.record(z.string(), z.boolean()).nullable(),
});

export async function searchDemo(
  query: string,
  signal?: AbortSignal,
): Promise<DemoResponse> {
  const params = new URLSearchParams({ q: query, limit: "8" });
  const response = await fetch(`/api/demo/search?${params}`, { signal });
  if (!response.ok) {
    throw new Error(
      response.status === 422
        ? "Enter a specific study, mechanism, assay, or perturbation."
        : "The live comparison could not be loaded. Check the backend and try again.",
    );
  }
  return demoResponseSchema.parse(await response.json());
}
```

In `LiveComparison`, call `searchDemo(normalized, controller.signal)` and stop writing `mode` into `window.location.search`. Preserve all existing NCBI link and marketing-copy edits.

- [ ] **Step 5: Run REST and frontend tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_marketing_api.py tests/test_production_app.py -q
pnpm --dir frontend test -- --run
pnpm --dir frontend build
```

Expected: all tests PASS and TypeScript production build succeeds.

- [ ] **Step 6: Record the frontend overlap checkpoint without staging user work**

Run:

```bash
git diff -- src/geo_index/marketing_api.py tests/test_marketing_api.py frontend/src/api.ts frontend/src/components/LiveComparison.tsx frontend/src/App.test.tsx
```

Expected: only mode-removal hunks plus the user's pre-existing frontend changes. Do not stage or commit the already-dirty frontend files without explicit user authorization. Leave this task uncommitted and report the verified files in the final handoff.

### Task 4: Raise admission defaults and deployment values

**Files:**
- Modify: `tests/test_mcp_settings.py`
- Modify: `tests/test_mcp_packaging.py`
- Modify: `src/geo_index/mcp_settings.py`
- Modify: `deploy/geo-mcp.env.example`
- Modify: `.do/app.yaml.tmpl`

**Interfaces:**
- Produces: `McpSettings.rate_per_second == 100.0` by default.
- Produces: `McpSettings.burst_capacity == 100` by default.
- Produces: `McpSettings.max_concurrent_requests == 20` by default.
- Preserves: `MAX_REQUEST_BODY_BYTES == 256 * 1024` and `BODY_READ_TIMEOUT_SECONDS == 10.0`.

- [ ] **Step 1: Write failing settings and packaging assertions**

Change the default assertions:

```python
assert settings.rate_per_second == 100.0
assert settings.burst_capacity == 100
assert settings.max_concurrent_requests == 20
```

Add exact example-value assertions:

```python
assert "GEO_MCP_RATE_PER_SECOND=100" in example
assert "GEO_MCP_BURST_CAPACITY=100" in example
assert "GEO_MCP_MAX_CONCURRENT_REQUESTS=20" in example
```

Add `.do/app.yaml.tmpl` assertions to `tests/test_mcp_packaging.py`:

```python
app_spec = (ROOT / ".do" / "app.yaml.tmpl").read_text()
assert 'key: GEO_MCP_RATE_PER_SECOND\n        value: "100"' in app_spec
assert 'key: GEO_MCP_BURST_CAPACITY\n        value: "100"' in app_spec
assert 'key: GEO_MCP_MAX_CONCURRENT_REQUESTS\n        value: "20"' in app_spec
```

- [ ] **Step 2: Run settings and packaging tests and verify RED**

Run:

```bash
uv run pytest tests/test_mcp_settings.py tests/test_mcp_packaging.py -q
```

Expected: FAIL with current values `1.0`, `5`, and `4`.

- [ ] **Step 3: Raise configurable defaults and deployment values**

Update the dataclass and environment fallbacks:

```python
rate_per_second: float = 100.0
burst_capacity: int = 100
max_concurrent_requests: int = 20
```

```python
rate_per_second = float(env.get("GEO_MCP_RATE_PER_SECOND", "100"))
burst_capacity=_positive_int(env, "GEO_MCP_BURST_CAPACITY", 100)
max_concurrent_requests=_positive_int(
    env, "GEO_MCP_MAX_CONCURRENT_REQUESTS", 20
)
```

Set `deploy/geo-mcp.env.example` and `.do/app.yaml.tmpl` to `100`, `100`, and `20`. Do not change the admission middleware algorithm, body-size constant, timeout, or global semaphore behavior.

- [ ] **Step 4: Run settings, packaging, and HTTP admission tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_mcp_settings.py tests/test_mcp_packaging.py tests/test_mcp_http.py tests/test_production_app.py -q
```

Expected: PASS, including existing 429, body-size, timeout, host/origin, and concurrency behavior.

- [ ] **Step 5: Commit the clean admission configuration change**

```bash
git add src/geo_index/mcp_settings.py deploy/geo-mcp.env.example .do/app.yaml.tmpl tests/test_mcp_settings.py tests/test_mcp_packaging.py
git commit -m "chore: raise demo admission limits"
```

### Task 5: Update public documentation and run complete verification

**Files:**
- Modify: `docs/deployment/digitalocean.md`
- Modify: `README.md`
- Modify: `wiki/27-MCP-Interface.md`
- Test: repository and frontend suites

**Interfaces:**
- Consumes: final MCP schema, REST contract, and deployment values from Tasks 1-4.
- Produces: human-readable docs matching the runtime-discoverable contract.

- [ ] **Step 1: Update current documentation**

In `wiki/27-MCP-Interface.md`, make the v1 table exact:

```markdown
| Tool | Input | Returns | Notes |
|---|---|---|---|
| `search_datasets` | `query`, `filters{}`, `limit` | query, filters, limit, retrieval provenance, ranked results, and facets | always uses the production hybrid strategy |
| `get_dataset` | `gse` | `{found, dataset}` with bounded indexed metadata and GEO/PubMed links | exact drill-in |
| `facet_values` | `field`, `query?`, `filters?`, `limit?` | field, value/label/count buckets, scope, candidate count, and retrieval provenance | hybrid when query-scoped; filter-only when blank |
```

Update README's hosted MCP section to state that callers do not select a retrieval mode and that production search policy is shared with the marketing API. In `docs/deployment/digitalocean.md`, remove `mode=hybrid` from the public API smoke URL and document the process-wide `100/100/20` admission values. Keep the internal `geo-search --mode bm25`, `--mode dense`, and `--mode hybrid` deployment checks because they intentionally verify low-level evaluation modes.

- [ ] **Step 2: Run a repository-wide stale-contract scan**

Run:

```bash
rg -n 'search_datasets.*mode|facet_values.*mode|api/demo/search.*mode|GEO_MCP_RATE_PER_SECOND=1|GEO_MCP_BURST_CAPACITY=5|GEO_MCP_MAX_CONCURRENT_REQUESTS=4' README.md wiki/27-MCP-Interface.md docs/deployment deploy .do src/geo_index frontend/src tests
```

Expected: no public-contract or deployment-default matches. Explicit low-level `geo-search --mode` examples and internal Elasticsearch tests may remain.

- [ ] **Step 3: Run focused backend and frontend verification**

Run:

```bash
uv run pytest tests/test_mcp_models.py tests/test_mcp_settings.py tests/test_mcp_server.py tests/test_mcp_http.py tests/test_mcp_search_service.py tests/test_mcp_packaging.py tests/test_marketing_api.py tests/test_production_app.py -q
pnpm --dir frontend test -- --run
pnpm --dir frontend build
```

Expected: all focused tests PASS and frontend build succeeds.

- [ ] **Step 4: Run the complete offline repository suite**

Run:

```bash
uv run pytest -q
```

Expected: all offline tests PASS; explicitly opt-in live tests SKIP when their environment flags are absent.

- [ ] **Step 5: Inspect the generated MCP schema in-process**

Use the existing fake-service `fastmcp.Client` path in `tests/test_mcp_server.py`, or run the focused schema test verbosely:

```bash
uv run pytest tests/test_mcp_server.py::test_exact_tool_list_annotations_and_stable_schema -vv
```

Expected: PASS, with `search_datasets(query, filters, limit)` and `facet_values(field, query, filters, limit)` containing no `mode` property.

- [ ] **Step 6: Review the final diff without committing user-owned frontend work**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Expected: no whitespace errors. Confirm pre-existing frontend changes remain present. Do not create a final implementation commit unless the user explicitly authorizes staging the overlapping frontend files.
