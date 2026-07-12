# Elasticsearch MCP Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the complete private hosted GEO MCP service on current `main` with Elasticsearch as its only online datastore and retrieval backend.

**Architecture:** Selectively port the proven MCP wire models, authentication, HTTP admission, and packaging from `codex/remote-mcp-first-draft`. Add a focused `McpSearchService` that wraps the backend-neutral `ElasticsearchSearchService`, owns Elasticsearch/query-encoder lifecycle, validates readiness and facet vocabularies, and converts domain search results into bounded MCP outputs without coupling FastMCP to the core search module.

**Tech Stack:** Python 3.11+, FastMCP 3, Pydantic 2, official Elasticsearch 9 client, Google GenAI Gemini embeddings, Starlette ASGI, Uvicorn, pytest, pytest-asyncio.

## Global Constraints

- Expose exactly `search_datasets`, `get_dataset`, and `facet_values`.
- Serve stateless Streamable HTTP at `/mcp` and run one Uvicorn worker.
- Preserve JWT/JWKS validation, `geo:read`, stable-subject allowlisting, Host/Origin checks, bounded request bodies, rate limiting, safe logging, health, and readiness behavior.
- Use Elasticsearch as the only MCP datastore; no MCP runtime path may require `GEO_PG_DSN`, psycopg pooling, pgvector registration, or PostgreSQL SQL.
- Default `ELASTICSEARCH_ACTIVE_MODEL` to `gemini_embedding_2_3072_v1`; callers never select a model.
- Perform no Elasticsearch, model, provider, or credential-discovery I/O at module import.
- Never accept raw Elasticsearch Query DSL, index names, vector fields, or dynamic facet fields from clients.
- Bound every input and response and never log bearer tokens, credentials, raw queries, filter values, or returned study text.
- The full offline suite must pass without live Elasticsearch, PostgreSQL, model downloads, or provider calls.
- Preserve user-owned changes in the original `main` checkout.

---

### Task 1: Port bounded MCP settings, models, and authentication

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/geo_index/mcp_settings.py`
- Create: `src/geo_index/mcp_models.py`
- Create: `src/geo_index/mcp_auth.py`
- Create: `tests/test_mcp_settings.py`
- Create: `tests/test_mcp_models.py`
- Create: `tests/test_mcp_auth.py`

**Interfaces:**
- Consumes: `ElasticsearchSettings.from_env(environ)` and the existing `SearchFilters`/facet contracts.
- Produces: `McpSettings.from_env(environ)`, strict input/output models, `create_auth(settings)`, and `require_invited_subject(allowed_subjects)`.

- [ ] **Step 1: Add failing settings/model/auth tests**

Port the retained branch tests first, then change their fixture environment to use:

```python
{
    "ELASTICSEARCH_URL": "https://elastic.internal:9200",
    "ELASTICSEARCH_API_KEY": "secret-api-key",
    "ELASTICSEARCH_ACTIVE_MODEL": "gemini_embedding_2_3072_v1",
    "GEO_MCP_PUBLIC_BASE_URL": "https://geo.example.org",
    "GEO_MCP_JWKS_URI": "https://auth.example.org/.well-known/jwks.json",
    "GEO_MCP_ISSUER": "https://auth.example.org/",
    "GEO_MCP_AUDIENCE": "geo-mcp",
    "GEO_MCP_AUTHORIZATION_SERVER": "https://auth.example.org",
    "GEO_MCP_ALLOWED_SUBJECTS": "user-1,user-2",
    "GEO_MCP_ALLOWED_HOSTS": "geo.example.org",
}
```

Assert `repr(settings)` omits the API key, `settings.elasticsearch.active_model_key` is Gemini by default, `GEO_PG_DSN` is neither required nor represented, input bounds remain unchanged, and auth rejects missing scope/uninvited subjects.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest tests/test_mcp_settings.py tests/test_mcp_models.py tests/test_mcp_auth.py -q
```

Expected: collection fails because the MCP modules do not exist.

- [ ] **Step 3: Implement the settings, models, auth, and dependencies**

Port the bounded models and auth implementation without PostgreSQL edits. Define settings as:

```python
@dataclass(frozen=True)
class McpSettings:
    elasticsearch: ElasticsearchSettings = field(repr=False)
    public_base_url: str
    jwks_uri: str
    issuer: str
    audience: str
    authorization_server: str
    allowed_subjects: frozenset[str] = field(repr=False)
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    rate_per_second: float = 5.0
    burst_capacity: int = 10
    required_scope: str = "geo:read"
```

`from_env()` must call `ElasticsearchSettings.from_env(env)`, retain existing HTTPS/host/origin/rate validation, and never copy credentials into top-level repr-visible fields. Add `fastmcp>=3.4.4,<4`, `uvicorn[standard]>=0.35,<1`, and `pytest-asyncio>=1,<2`, then regenerate the lockfile.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 command. Expected: all focused tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add pyproject.toml uv.lock src/geo_index/mcp_settings.py src/geo_index/mcp_models.py src/geo_index/mcp_auth.py tests/test_mcp_settings.py tests/test_mcp_models.py tests/test_mcp_auth.py
git commit -m "feat: define Elasticsearch MCP contracts"
```

### Task 2: Build the Elasticsearch-backed MCP adapter

**Files:**
- Create: `src/geo_index/mcp_search_service.py`
- Create: `tests/test_mcp_search_service.py`

**Interfaces:**
- Consumes: `ElasticsearchSearchService`, `index_readiness()`, `create_client()`, the registered Gemini variant, `SearchFilters`, and MCP models.
- Produces: `McpSearchService.from_settings(settings)`, `open()`, `close()`, `ping()`, `search_datasets()`, `get_dataset()`, and `facet_values()` plus `UnknownFilterValueError`.

- [ ] **Step 1: Add failing adapter and primary-model tests**

Use fake Elasticsearch clients and fake encoders to assert:

```python
service = McpSearchService(
    elasticsearch=settings,
    client_factory=lambda _: client,
    query_encoder_factory=lambda key: encoder,
)
service.open()
assert service.is_open
assert client.readiness_calls == 1
service.search_datasets(
    query="lung cancer",
    filters=SearchFilters(),
    mode="bm25",
    limit=15,
)
assert encoder.create_calls == 0
service.close()
assert client.closed
```

Also assert the MCP-owned Gemini encoder returns a finite 3,072-vector through
an injected fake Google client and that `McpSettings` supplies the Gemini key by
default without changing the shared Elasticsearch default used by the concurrent
primary-app migration.

- [ ] **Step 2: Run the tests and verify RED**

```bash
uv run pytest tests/test_mcp_search_service.py -q
```

Expected: failure because `McpSearchService` and primary Gemini query encoding are missing.

- [ ] **Step 3: Implement lazy resource composition**

`open()` creates the client, calls `index_readiness(client, active_model_key)`, loads fixed facet vocabularies with `size=0` and code-owned aggregation field names, and creates `ElasticsearchSearchService` with a callable that lazily constructs the query encoder. On any startup failure, close every created resource and leave `is_open` false. `close()` is idempotent and closes the encoder before the client. `ping()` reruns `index_readiness()` and errors while closed.

Add an MCP-owned Gemini query encoder using `google.genai.Client.models.embed_content`
with the registered `gemini-embedding-2` model and
`output_dimensionality=3072`. It must construct the provider client lazily from
`GEMINI_API_KEY`, validate one finite vector with the configured dimensions,
and close idempotently. BM25 must not construct it. Keep this composition out of
`elasticsearch_query_embeddings.py` so the concurrent primary-app migration can
finish that shared boundary without a merge conflict.

- [ ] **Step 4: Implement bounded output conversion**

Map domain hits and exact documents into `DatasetSummary`/`DatasetDetail` with the existing caps: title 500, snippet 1,000, summary/design 8,000, arrays 100, values 256, at most 50 results and buckets. Convert the first sole positive `pubmed_ids` value to `pubmed_id`; otherwise return `None`. Derive retrieval version as:

```python
f"{p.mapping_revision}:{p.active_model_key}:{p.vector_field}:{p.mode}"
```

For `facet_values`, call search with the requested fixed field and return only that bounded facet. Reject filter values absent from the startup vocabulary using `UnknownFilterValueError` before search.

- [ ] **Step 5: Run adapter tests and verify GREEN**

Run the Step 2 command. Expected: all adapter and query-encoder tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/geo_index/mcp_search_service.py tests/test_mcp_search_service.py
git commit -m "feat: adapt Elasticsearch search for MCP"
```

### Task 3: Restore the protected FastMCP server

**Files:**
- Create: `src/geo_index/mcp_server.py`
- Create: `tests/test_mcp_server.py`
- Create: `tests/test_mcp_http.py`

**Interfaces:**
- Consumes: `McpSettings`, `McpSearchService`, the MCP models/auth helpers, and an injectable service protocol.
- Produces: `create_mcp(settings, service, auth_provider=None)` and ASGI factory `create_app(settings=None, service=None, auth_provider=None)`.

- [ ] **Step 1: Port server and HTTP tests before implementation**

Keep the retained branch assertions for the exact tool list, strict schemas,
delegation, masked errors, sensitive-log filtering, body/Host/Origin rejection,
rate/concurrency admission, `/healthz`, and `/readyz`. Replace DSN fixtures and
concrete `SearchService` references with `McpSearchService` or a `McpService`
protocol fake. Add an assertion that `create_app()` constructs
`McpSearchService.from_settings(settings)` and never imports `pg_hybrid`.

- [ ] **Step 2: Run server tests and verify RED**

```bash
uv run pytest tests/test_mcp_server.py tests/test_mcp_http.py -q
```

Expected: collection fails because `mcp_server.py` does not exist.

- [ ] **Step 3: Port and adapt the server**

Retain the hardened middleware, safe logging, three tool functions, FastMCP
configuration, and health routes. Define a structural protocol containing the
seven adapter methods/properties used by the server. The lifespan opens the
service, yields it to tool contexts, and always closes it. `create_app()` uses
`McpSearchService.from_settings(settings)` when no service is injected.

- [ ] **Step 4: Run server tests and verify GREEN**

Run the Step 2 command. Expected: all server and HTTP tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/geo_index/mcp_server.py tests/test_mcp_server.py tests/test_mcp_http.py
git commit -m "feat: restore protected Elasticsearch MCP server"
```

### Task 4: Package, document, and smoke-test the service

**Files:**
- Create: `.dockerignore`
- Create: `Dockerfile`
- Create: `deploy/geo-mcp.env.example`
- Modify: `README.md`
- Modify: `wiki/27-MCP-Interface.md`
- Create: `tests/test_mcp_packaging.py`
- Create: `tests/test_mcp_elasticsearch_smoke.py`

**Interfaces:**
- Consumes: `geo_index.mcp_server:create_app` and existing Elasticsearch environment settings.
- Produces: a one-worker container command, safe deployment template, operator runbook, and opt-in live three-tool smoke.

- [ ] **Step 1: Add failing packaging and smoke-contract tests**

Assert the Docker command is:

```dockerfile
CMD ["uvicorn", "geo_index.mcp_server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

Assert the environment example includes Elasticsearch/MCP variables, excludes
`GEO_PG_DSN`, and contains no real secrets. The live test must be skipped unless
`GEO_TEST_ELASTIC=1`; when enabled it builds real settings/client/encoder and
calls all three tools through `fastmcp.Client`.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest tests/test_mcp_packaging.py tests/test_mcp_elasticsearch_smoke.py -q
```

Expected: packaging test fails because files are absent; live test skips by default.

- [ ] **Step 3: Add packaging and documentation**

Selectively port the hardened Docker assets, replace PostgreSQL environment and
network copy with Elasticsearch connection variables, and document local start,
required OAuth variables, health/readiness checks, and client URL. Update the MCP
wiki page to name Elasticsearch/Gemini and the implemented service files.

- [ ] **Step 4: Run packaging tests and verify GREEN**

Run the Step 2 command. Expected: packaging passes and live smoke skips unless explicitly enabled.

- [ ] **Step 5: Commit Task 4**

```bash
git add .dockerignore Dockerfile deploy/geo-mcp.env.example README.md wiki/27-MCP-Interface.md tests/test_mcp_packaging.py tests/test_mcp_elasticsearch_smoke.py
git commit -m "docs: package Elasticsearch MCP service"
```

### Task 5: Verify the migration and audit completion

**Files:**
- Modify only files required to fix verification failures attributable to this branch.

**Interfaces:**
- Consumes: all prior task deliverables.
- Produces: fresh focused/full-suite evidence and a requirement-by-requirement audit.

- [ ] **Step 1: Run all focused MCP tests**

```bash
uv run pytest tests/test_mcp_*.py -q
```

Expected: all offline MCP tests pass; the opt-in live smoke skips unless configured.

- [ ] **Step 2: Run the complete offline suite**

```bash
uv run pytest -q
```

Expected: zero failures; only explicitly opt-in PostgreSQL/Elasticsearch live tests skip.

- [ ] **Step 3: Check imports, diffs, and PostgreSQL leakage**

```bash
uv run python -c "import geo_index.mcp_server; print('mcp import ok')"
git diff --check main...HEAD
rg -n "GEO_PG_DSN|psycopg|pgvector|pg_hybrid" src/geo_index/mcp_*.py deploy/geo-mcp.env.example Dockerfile tests/test_mcp_*.py
```

Expected: import succeeds, diff check is clean, and the leakage search returns no matches except deliberate negative packaging assertions.

- [ ] **Step 4: Run a live MCP smoke when configured**

If `GEO_TEST_ELASTIC=1` and all required OAuth/Elasticsearch/model credentials are present:

```bash
uv run pytest tests/test_mcp_elasticsearch_smoke.py -q
```

Expected: all three MCP tools return schema-valid results. Otherwise report the exact missing live precondition and rely only on the clearly identified offline evidence.

- [ ] **Step 5: Inspect branch state and commit any verification fixes**

```bash
git status --short
git log --oneline main..HEAD
```

Expected: no uncommitted implementation changes and a coherent migration commit series.
