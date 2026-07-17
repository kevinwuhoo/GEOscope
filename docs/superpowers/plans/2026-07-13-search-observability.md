# Search Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit structured request and shared-search timing events that identify slow GEO search stages for both the marketing API and MCP transport.

**Architecture:** A dependency-free observability module will own request context, a thread-safe monotonic stage timer, and JSON event emission. The production ASGI app establishes client metadata and HTTP completion logging; shared MCP/Elasticsearch services populate the detailed terminal search event.

**Tech Stack:** Python 3.11+, FastAPI/Starlette ASGI, standard-library `contextvars`, `logging`, `json`, `datetime`, `threading`, pytest, pytest caplog.

## Global Constraints

- Search instrumentation belongs in the shared MCP/Elasticsearch layer, never solely in the marketing site.
- Every event has an explicit UTC RFC 3339 `timestamp`; latency uses a monotonic `time.perf_counter`-compatible clock.
- Retain verbatim normalized queries, normalized filters, raw forwarded client address, direct peer address, and user-agent.
- Never log authorization/cookie headers, API keys, complete MCP bodies, provider response text, or result contents.
- Preserve the existing `SearchLatencyOutput` response schema and its three coarse timing fields; the detailed breakdown is log-only.
- Add no runtime dependency or metrics exporter. Python JSON log lines must be visible in DigitalOcean App Platform Runtime Logs.
- Preserve exact-accession behavior, source concurrency, fallback ordering, reranking semantics, and FastMCP sensitive-log filtering.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/geo_index/observability.py` | Request context, thread-safe timing, ISO-UTC JSON events, and ASGI completion middleware. |
| `src/geo_index/elasticsearch_search.py` | Optional timing for query embedding, primary retrieval, and facet generation. |
| `src/geo_index/mcp_search_service.py` | Per-search timer ownership; hydration, NCBI, merge, rerank, formatting timing; terminal event. |
| `src/geo_index/production_app.py` | Outer request-observability middleware around API and MCP routes. |
| `tests/test_observability.py` | Unit coverage for JSON fields, timestamp, metadata, timing, and redaction. |
| `tests/test_elasticsearch_search.py` | Elasticsearch sub-stage timing coverage without changed query behavior. |
| `tests/test_mcp_search_service.py` | Natural, exact, and degraded shared-search event coverage. |
| `tests/test_production_app.py` | Marketing and mounted MCP request-context/completion coverage. |
| `docs/deployment/digitalocean.md` | Runtime log inspection instructions and stage field guide. |

### Task 1: Create observability primitives

**Files:**

- Create: `src/geo_index/observability.py`
- Create: `tests/test_observability.py`

**Interfaces:**

- Produces: `RequestLogContext`, `SearchTiming`, `current_request_context()`, `use_request_context(context)`, `emit_json_event(event, payload)`, and `RequestObservabilityMiddleware`.
- Consumed by: `McpSearchService`, `ElasticsearchSearchService`, and `production_app.create_app`.

- [ ] **Step 1: Write the failing unit tests.**

```python
def test_search_timing_accumulates_stage_samples() -> None:
    timing = SearchTiming(clock=StepClock(0.0, 0.0, 0.012, 0.012, 0.020, 0.020))
    with timing.measure("elasticsearch_retrieval"):
        pass
    with timing.measure("elasticsearch_retrieval"):
        pass
    assert timing.as_ms()["elasticsearch_retrieval"] == 20
    assert timing.as_ms()["total"] == 20


class StepClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def test_json_event_has_utc_timestamp_and_safe_context(caplog) -> None:
    caplog.set_level(logging.INFO, logger="geo_index.observability")
    context = RequestLogContext.from_scope(_scope(
        headers={b"x-forwarded-for": b"198.51.100.7", b"user-agent": b"test"}
    ))
    with use_request_context(context):
        emit_json_event("search.completed", {"query": "immune atlas"})
    event = json.loads(caplog.records[-1].message)
    assert event["timestamp"].endswith("Z")
    assert event["client_ip"] == "198.51.100.7"
    assert event["peer_ip"] == "127.0.0.1"
    assert event["user_agent"] == "test"
    assert "authorization" not in event
    assert "cookie" not in event
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `uv run pytest tests/test_observability.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'geo_index.observability'`.

- [ ] **Step 3: Implement the dependency-free core.**

```python
SEARCH_STAGE_NAMES = (
    "validation", "query_embedding", "elasticsearch_lookup",
    "elasticsearch_retrieval", "facet_generation", "document_hydration",
    "ncbi_search", "candidate_merge", "reranker", "response_formatting",
)


@dataclass
class RequestLogContext:
    request_id: str
    method: str
    route: str
    client_ip: str | None
    forwarded_for: str | None
    peer_ip: str | None
    user_agent: str | None
    referer: str | None
    accept_language: str | None
    search_event_emitted: bool = False


class SearchTiming:
    def __init__(self, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started = clock()
        self._values = {stage: 0.0 for stage in SEARCH_STAGE_NAMES}
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        started = self._clock()
        try:
            yield
        finally:
            with self._lock:
                self._values[stage] += self._clock() - started

    def as_ms(self) -> dict[str, int]:
        with self._lock:
            values = {name: max(0, round(value * 1000)) for name, value in self._values.items()}
        values["total"] = max(0, round((self._clock() - self._started) * 1000))
        return values


def emit_json_event(event: str, payload: Mapping[str, object]) -> None:
    """Log one allowlisted JSON object with event, timestamp, and request context."""


class RequestObservabilityMiddleware:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        context = RequestLogContext.from_scope(scope)
        started = self.clock()
        status_code = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        with use_request_context(context):
            try:
                await self.app(scope, receive, send_with_status)
            finally:
                if not context.search_event_emitted:
                    emit_json_event("request.completed", {"status_code": status_code,
                        "total_ms": max(0, round((self.clock() - started) * 1000))})
```

Implement `RequestLogContext.from_scope` to retain `X-Forwarded-For` exactly in `forwarded_for`, use its first comma-separated address as `client_ip`, and retain `scope["client"]` as `peer_ip`. Emit only allowlisted metadata; do not serialize arbitrary headers. `SearchTiming` must lock its accumulator because Elasticsearch and NCBI run on different threads. The middleware must capture the status from `http.response.start`, create a UUID request ID, and emit `request.completed` only if no terminal search event marked the context.

- [ ] **Step 4: Run the module tests to verify they pass.**

Run: `uv run pytest tests/test_observability.py -q`

Expected: PASS; events have a `Z` timestamp, request ID, raw forwarded/peer address, fixed stage map, and no disallowed fields.

- [ ] **Step 5: Commit the primitive.**

```bash
git add src/geo_index/observability.py tests/test_observability.py
git commit -m "feat: add structured search observability primitives"
```

### Task 2: Time Elasticsearch sub-stages

**Files:**

- Modify: `src/geo_index/elasticsearch_search.py: ElasticsearchSearchService.search`
- Modify: `tests/test_elasticsearch_search.py`

**Interfaces:**

- Consumes: `SearchTiming` from `geo_index.observability`.
- Produces: an optional keyword-only `timing: SearchTiming | None = None` parameter on `ElasticsearchSearchService.search`.
- Consumed by: `McpSearchService._local_candidates`; callers which omit the optional keyword keep existing behavior.

- [ ] **Step 1: Write the failing hybrid timing test.**

```python
def test_hybrid_search_records_embedding_retrieval_and_facets() -> None:
    client = _Client(search_responses=[
        _response(("GSE2", 1.0, {})),
        *[_response() for _ in FACET_FIELDS],
    ])
    timing = SearchTiming()
    _service(client).search("immune", mode="hybrid", timing=timing)
    assert set(("query_embedding", "elasticsearch_retrieval", "facet_generation")) <= set(timing.as_ms())
    assert len(client.search_calls) == 5
```

- [ ] **Step 2: Run the focused test to verify it fails.**

Run: `uv run pytest tests/test_elasticsearch_search.py::test_hybrid_search_records_embedding_retrieval_and_facets -q`

Expected: FAIL with `TypeError` because `search()` does not yet accept `timing`.

- [ ] **Step 3: Add behavior-preserving optional instrumentation.**

```python
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
    timing: SearchTiming | None = None,
) -> SearchResponse:
    active_filters = filters or SearchFilters()
    query_vector: list[float] | None = None
    if mode != "bm25":
        with _timed(timing, "query_embedding"):
            query_vector = _validate_query_vector(self._encode_query(query), self._spec)
    with _timed(timing, "elasticsearch_retrieval"):
        hits = self._retrieve(query, mode=mode, filters=active_filters,
                              query_vector=query_vector, topk=topk, deep=deep,
                              num_candidates=num_candidates, k0=k0)
    with _timed(timing, "facet_generation"):
        facets = (
            self._candidate_facets(query, mode=mode, filters=active_filters,
                query_vector=query_vector, deep=deep, num_candidates=num_candidates,
                k0=k0, facet_pool=facet_pool, bucket_limit=bucket_limit)
            if query.strip() else self._blank_facets(active_filters, bucket_limit)
        )
```

Implement `_timed` with `nullcontext()` when timing is absent. Do not time an embedding for BM25, change the number/order of Elasticsearch calls, or change search results.

- [ ] **Step 4: Run focused and complete Elasticsearch tests.**

Run: `uv run pytest tests/test_elasticsearch_search.py::test_hybrid_search_records_embedding_retrieval_and_facets -q && uv run pytest tests/test_elasticsearch_search.py -q`

Expected: PASS; no pre-existing request/mapping behavior changes.

- [ ] **Step 5: Commit the Elasticsearch stage timing.**

```bash
git add src/geo_index/elasticsearch_search.py tests/test_elasticsearch_search.py
git commit -m "feat: time Elasticsearch search stages"
```

### Task 3: Emit terminal events from the shared search pipeline

**Files:**

- Modify: `src/geo_index/mcp_search_service.py: _local_candidates, _hydrate_documents, _exact_execution, _search_execution`
- Modify: `tests/test_mcp_search_service.py`

**Interfaces:**

- Consumes: `SearchTiming`, `current_request_context`, and `emit_json_event`.
- Consumes: the optional `timing=timing` keyword on `ElasticsearchSearchService.search`.
- Produces: one `search.completed` JSON event containing all safe outcome data and the detailed `latency_ms` map.

- [ ] **Step 1: Write failing tests for natural, exact, and degraded search events.**

```python
def test_natural_search_logs_query_outcome_and_pipeline_stages(caplog) -> None:
    service, *_ = _service(native=FakeNativeSource(), reranker=FakeReranker())
    service.open()
    with use_request_context(_request_context()):
        service.search_datasets(query="mouse exercise", filters=SearchFilters(), limit=10)
    event = _search_event(caplog)
    assert event["query"] == "mouse exercise"
    assert event["candidate_counts"] == {"elasticsearch": 40, "ncbi": 0, "merged": 40}
    assert set(SEARCH_STAGE_NAMES) <= set(event["latency_ms"])
    assert event["latency_ms"]["total"] >= event["latency_ms"]["reranker"]


def test_exact_fallback_logs_lookup_and_safe_degradation(caplog) -> None:
    service, *_ = _service(
        exact_document=None, native=FakeNativeSource(error=TimeoutError("private"))
    )
    service.open()
    with use_request_context(_request_context()):
        service.search_datasets(query="GSE310900", filters=SearchFilters(), limit=10)
    event = _search_event(caplog)
    assert event["exact_accession"] is True
    assert event["degradation"] == ["ncbi_timeout"]
    assert event["latency_ms"]["elasticsearch_lookup"] >= 0
    assert "private" not in caplog.text
```

- [ ] **Step 2: Run the new tests to verify they fail.**

Run: `uv run pytest tests/test_mcp_search_service.py -k 'logs_query_outcome or logs_lookup' -q`

Expected: FAIL because no `search.completed` JSON log record exists.

- [ ] **Step 3: Pass one timing object through the shared search execution and emit allowlisted data.**

```python
def _local_candidates(
    self, client: object, search: DomainSearch, *, query: str,
    filters: SearchFilters, topk: int, timing: SearchTiming,
) -> tuple[tuple[SearchCandidate, ...], SearchResponse]:
    response = search.search(query, filters=filters, mode="hybrid", topk=topk,
                             bucket_limit=50, timing=timing)
    with timing.measure("document_hydration"):
        documents = self._hydrate_documents(client, tuple(response.hits[:topk]))
    hits = tuple(response.hits[:topk])
    candidates = tuple(
        self._candidate_from_document(document, original_rank=rank,
            retrieval_score=float(hit["score"]) if hit.get("score") is not None else None)
        for rank, (hit, document) in enumerate(zip(hits, documents, strict=True), 1)
    )
    return candidates, response


def _emit_search_completed(
    self, output: SearchDatasetsOutput, timing: SearchTiming
) -> None:
    provenance = output.provenance
    emit_json_event("search.completed", {
        "query": output.query,
        "filters": output.filters.model_dump(mode="json"),
        "limit": output.limit,
        "result_count": len(output.results),
        "exact_accession": provenance.exact_accession,
        "retrieval_version": output.retrieval_version,
        "embedding_variant": output.embedding_variant,
        "candidate_counts": {
            "elasticsearch": provenance.elasticsearch_candidates,
            "ncbi": provenance.ncbi_candidates,
            "merged": provenance.merged_candidates,
        },
        "rerank": {
            "attempted": provenance.rerank_attempted,
            "applied": provenance.rerank_applied,
            "model": provenance.rerank_model,
            "input_tokens": provenance.rerank_input_tokens,
            "output_tokens": provenance.rerank_output_tokens,
        },
        "degradation": provenance.degradation,
        "latency_ms": timing.as_ms(),
    })
```

Create the timer before validation. Time `search.get_dataset` as `elasticsearch_lookup`, each NCBI search/lookup as `ncbi_search`, `merge_candidates` as `candidate_merge`, the reranker as `reranker`, and summary/facet construction as `response_formatting`. Continue setting the existing coarse provenance values from `_TimedCall`; do not replace response timing with new values. Emit only after valid `SearchDatasetsOutput` creation, including every failed-open/degradation result.

- [ ] **Step 4: Run the complete shared-service test module.**

Run: `uv run pytest tests/test_mcp_search_service.py -q`

Expected: PASS; natural and exact paths emit exactly one event, degraded event text remains safe, and existing retrieval/reranking assertions pass.

- [ ] **Step 5: Commit shared event emission.**

```bash
git add src/geo_index/mcp_search_service.py tests/test_mcp_search_service.py
git commit -m "feat: log shared search pipeline timings"
```

### Task 4: Attach client context to production API and MCP requests

**Files:**

- Modify: `src/geo_index/production_app.py: create_app`
- Modify: `tests/test_production_app.py`

**Interfaces:**

- Consumes: `RequestObservabilityMiddleware`.
- Produces: request context for all production HTTP requests and `request.completed` events for non-search requests, including admission 429 responses.
- Consumed by: marketing API, FastMCP mount, health/readiness routes, and frontend routes.

- [ ] **Step 1: Write failing API and MCP context tests.**

```python
def test_production_logs_forwarded_client_metadata(caplog) -> None:
    app = create_app(settings=_settings(), service=FakeService())
    with TestClient(app, base_url="https://geoscope.kevinformatics.com") as client:
        response = client.get("/api/demo/search", params={"q": "mouse"}, headers={
            "X-Forwarded-For": "198.51.100.9, 10.0.0.4",
            "User-Agent": "GEOscope-test/1.0",
            "Authorization": "Bearer must-not-appear",
        })
    event = _request_event(caplog, "/api/demo/search")
    assert response.status_code == 200
    assert event["client_ip"] == "198.51.100.9"
    assert event["forwarded_for"] == "198.51.100.9, 10.0.0.4"
    assert event["user_agent"] == "GEOscope-test/1.0"
    assert "must-not-appear" not in caplog.text


def test_production_logs_mcp_completion_without_tool_body(caplog) -> None:
    app = create_app(settings=_settings(), service=FakeService())
    body = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "get_dataset", "arguments": {"gse": "GSE310900"}},
    }
    with TestClient(app, base_url="https://geoscope.kevinformatics.com") as client:
        response = client.post("/mcp", json=body, headers=_mcp_headers())
    event = _request_event(caplog, "/mcp")
    assert response.status_code == 200
    assert event["status_code"] == 200
    assert "GSE310900" not in json.dumps(event)
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `uv run pytest tests/test_production_app.py -k 'logs_forwarded or logs_mcp_completion' -q`

Expected: FAIL because `request.completed` records are absent.

- [ ] **Step 3: Install the middleware as the outermost production middleware.**

```python
app.add_middleware(
    HttpAdmissionMiddleware,
    gate=admission_gate,
    admitted_paths=("/api/demo/search",),
)
app.add_middleware(RequestObservabilityMiddleware)
```

Register observability after admission: Starlette therefore runs it outermost and it records success, errors, and 429 admission responses. It must not read request bodies, change the scope/path given to `_McpRootEndpoint`, or affect the shared rate budget.

- [ ] **Step 4: Run production and MCP HTTP regressions.**

Run: `uv run pytest tests/test_production_app.py tests/test_mcp_http.py -q`

Expected: PASS; API and MCP requests get a request ID/context, generic request events exclude authorization and MCP bodies, and existing FastMCP redaction remains green.

- [ ] **Step 5: Commit production context logging.**

```bash
git add src/geo_index/production_app.py tests/test_production_app.py
git commit -m "feat: log production request context"
```

### Task 5: Document Runtime Log usage and verify the repository

**Files:**

- Modify: `docs/deployment/digitalocean.md: Operations`
- Modify: `tests/test_primary_path_docs.py`
- Test: `tests/test_mcp_packaging.py`

**Interfaces:**

- Consumes: stable `search.completed` and `request.completed` JSON fields.
- Produces: a follow command and a stage-by-stage interpretation guide for App Platform operators.

- [ ] **Step 1: Write the failing runbook documentation assertion.**

```python
def test_deployment_runbook_documents_search_observability() -> None:
    runbook = (ROOT / "docs" / "deployment" / "digitalocean.md").read_text()
    assert "search.completed" in runbook
    assert "request.completed" in runbook
    assert 'doctl apps logs "$DO_APP_ID" geoscope --type run --follow' in runbook
```

- [ ] **Step 2: Run the assertion to verify it fails.**

Run: `uv run pytest tests/test_primary_path_docs.py::test_deployment_runbook_documents_search_observability -q`

Expected: FAIL because the runbook has no observability section.

- [ ] **Step 3: Document immediate log inspection and durable-retention guidance.**

````markdown
### Search observability

Follow the App Platform runtime stream:

```bash
doctl apps logs "$DO_APP_ID" geoscope --type run --follow
```

Each `search.completed` event includes an explicit UTC `timestamp`, `request_id`,
verbatim query/client metadata, and `latency_ms`. Compare
`query_embedding`, `elasticsearch_retrieval`, `facet_generation`,
`document_hydration`, `ncbi_search`, and `reranker` to identify the
limiting stage. `request.completed` records non-search traffic without request
bodies. Forward runtime logs to a log provider when durable retention is needed.
````

Do not add an environment variable, a metrics dependency, or provider setup.

- [ ] **Step 4: Run documentation, focused observability, and complete offline regressions.**

Run: `uv run pytest tests/test_observability.py tests/test_elasticsearch_search.py tests/test_mcp_search_service.py tests/test_production_app.py tests/test_mcp_http.py tests/test_primary_path_docs.py tests/test_mcp_packaging.py -q && uv run pytest -q`

Expected: PASS; neither command enables live Elasticsearch, NCBI, Gemini, nor Anthropic integration markers.

- [ ] **Step 5: Inspect the change and commit the runbook.**

```bash
git diff --check
git status --short
git add docs/deployment/digitalocean.md tests/test_primary_path_docs.py
git commit -m "docs: explain search observability logs"
```
