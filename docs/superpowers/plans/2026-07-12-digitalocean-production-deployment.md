# DigitalOcean Production Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the complete GEOscope corpus at `https://geoscope.kevinformatics.com` through one anonymous App Platform FastAPI/FastMCP service backed by a private, dedicated Elasticsearch Droplet.

**Architecture:** One slim ASGI container serves the React build, browser API, health endpoints, and a mounted anonymous FastMCP endpoint. DigitalOcean App Platform runs that stateless container in `sfo` and reaches a pinned single-node Elasticsearch 9.4.2 container at `10.124.0.2` over the `default-sfo3` VPC. The initial 249,736-document index is streamed from the existing local canonical records and embedding artifacts through an SSH tunnel to the Droplet's loopback listener.

**Tech Stack:** Python 3.11, FastAPI, FastMCP 3, Uvicorn, React 19, Vite 8, pnpm, Elasticsearch 9.4.2, Docker Compose, DigitalOcean App Platform, Ubuntu 24.04, pytest, Vitest.

## Global Constraints

- Public origin: `https://geoscope.kevinformatics.com`; MCP endpoint: `/mcp`.
- MCP is anonymous but exposes exactly `search_datasets`, `get_dataset`, and `facet_values`.
- Preserve the 256 KiB MCP body limit, masked errors, sensitive-log filtering, strict inputs, one request/second, burst five, and four concurrent requests.
- App Platform starts on `apps-s-1vcpu-0.5gb`, one instance, one Uvicorn worker, with edge caching disabled.
- Production serving excludes PyTorch, Transformers, Sentence Transformers, Hugging Face model tooling, Prefect, psycopg, pgvector, ETL data, and embedding artifacts.
- Elasticsearch is pinned to 9.4.2 on Droplet `143.198.53.162`, private address `10.124.0.2`, with 4 GiB JVM heap and no container CPU/memory cap.
- Elasticsearch port 9200 binds only to `127.0.0.1` and `10.124.0.2`; it must never bind the public interface.
- Existing unrelated untracked frontend/API work belongs to the user: preserve and integrate it; never discard or overwrite it wholesale.
- Do not commit secrets. Local `.env*` files remain ignored; App Platform stores secrets as `SECRET` run-time variables.
- Every code task follows red-green TDD and ends in a focused commit.

---

### Task 1: Make the hosted MCP contract anonymous and resource-bounded

**Files:**
- Modify: `src/geo_index/mcp_settings.py`
- Modify: `src/geo_index/mcp_server.py`
- Modify: `tests/test_mcp_settings.py`
- Modify: `tests/test_mcp_http.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_mcp_packaging.py`
- Modify: `deploy/geo-mcp.env.example`

**Interfaces:**
- Consumes: `ElasticsearchSettings.from_env()` and the existing three MCP tools.
- Produces: `McpSettings.from_env(env) -> McpSettings`, `create_mcp(settings, service) -> FastMCP`, `create_mcp_http_mount(settings, service, *, path: str) -> McpHttpMount`, and `create_app(settings=None, service=None) -> ASGIApp`.

- [ ] **Step 1: Replace authenticated-settings tests with the public contract**

```python
VALID = {
    "ELASTICSEARCH_URL": "http://10.124.0.2:9200",
    "ELASTICSEARCH_USERNAME": "elastic",
    "ELASTICSEARCH_PASSWORD": "secret-password",
    "GEO_MCP_PUBLIC_BASE_URL": "https://geoscope.kevinformatics.com",
    "GEO_MCP_ALLOWED_HOSTS": "geoscope.kevinformatics.com",
}

def test_public_settings_apply_safe_admission_defaults() -> None:
    settings = McpSettings.from_env(VALID)
    assert settings.mcp_url == "https://geoscope.kevinformatics.com/mcp"
    assert settings.rate_per_second == 1.0
    assert settings.burst_capacity == 5
    assert settings.max_concurrent_requests == 4
    assert not hasattr(settings, "jwks_uri")
    assert not hasattr(settings, "allowed_subjects")
```

- [ ] **Step 2: Run the settings tests and verify the old authenticated model fails**

Run: `uv run pytest tests/test_mcp_settings.py -v`

Expected: FAIL because OAuth/JWKS fields are still required and the new admission defaults do not exist.

- [ ] **Step 3: Reduce `McpSettings` to online public settings**

```python
@dataclass(frozen=True)
class McpSettings:
    elasticsearch: ElasticsearchSettings = field(repr=False)
    public_base_url: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    rate_per_second: float = 1.0
    burst_capacity: int = 5
    max_concurrent_requests: int = 4

    @property
    def mcp_url(self) -> str:
        return f"{self.public_base_url}{MCP_PATH}"
```

Parse `GEO_MCP_MAX_CONCURRENT_REQUESTS` as a positive integer, keep HTTPS/public-host validation, and delete all required issuer, JWKS, audience, authorization-server, scope, and subject parsing from `from_env`.

- [ ] **Step 4: Rewrite the HTTP test to prove anonymous initialization and tool calls**

```python
with TestClient(app, base_url=settings.public_base_url) as client:
    initialized = client.post("/mcp", json=_initialize_body(), headers=_headers())
    assert initialized.status_code == 200
    assert "www-authenticate" not in initialized.headers
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/healthz", headers={"Host": "attacker.test"}).status_code == 421
```

Keep the existing oversized-body, stalled-body, readiness-failure, raw-query log-redaction, backend-error masking, rate-limit, and concurrency assertions.

In `tests/test_mcp_server.py`, remove the synthetic `AccessToken` fixture setup, construct the reduced public `McpSettings`, and update `test_create_app_uses_elasticsearch_adapter_and_http_guards` to call `create_app(settings=settings)` without `auth_provider`. In `tests/test_mcp_packaging.py`, replace the required JWKS/issuer/audience/subject keys with `ELASTICSEARCH_USERNAME`, `ELASTICSEARCH_PASSWORD`, and `GEO_MCP_MAX_CONCURRENT_REQUESTS` and assert the removed OAuth keys are absent.

- [ ] **Step 5: Run the HTTP test and verify authentication still blocks it**

Run: `uv run pytest tests/test_mcp_http.py -v`

Expected: FAIL because the current FastMCP instance still installs an auth provider and `AuthMiddleware`.

- [ ] **Step 6: Remove authentication from the MCP construction path and add a mount interface**

```python
@dataclass(frozen=True)
class McpHttpMount:
    app: ASGIApp
    lifespan: object

def create_mcp_http_mount(
    settings: McpSettings,
    service: McpService,
    *,
    path: str,
) -> McpHttpMount:
    mcp = create_mcp(settings, service)
    base = mcp.http_app(
        path=path,
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )
    bounded = HttpAdmissionMiddleware(
        RequestBodyLimitMiddleware(base, max_body_bytes=MAX_REQUEST_BODY_BYTES),
        rate_per_second=settings.rate_per_second,
        burst_capacity=settings.burst_capacity,
        max_concurrent_requests=settings.max_concurrent_requests,
    )
    return McpHttpMount(app=bounded, lifespan=base.lifespan)
```

Construct `FastMCP` without `auth=` or `AuthMiddleware`. Keep FastMCP's own tool-rate middleware only if tests prove it does not double-charge initialization and tool calls; otherwise use the outer admission limiter as the single authority.

- [ ] **Step 7: Update the safe environment example**

```dotenv
ELASTICSEARCH_URL=http://10.124.0.2:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=set-in-app-platform
ELASTICSEARCH_ACTIVE_MODEL=gemini_embedding_2_3072_v1
GEMINI_API_KEY=set-in-app-platform
GEO_MCP_PUBLIC_BASE_URL=https://geoscope.kevinformatics.com
GEO_MCP_ALLOWED_HOSTS=geoscope.kevinformatics.com
GEO_MCP_ALLOWED_ORIGINS=
GEO_MCP_RATE_PER_SECOND=1
GEO_MCP_BURST_CAPACITY=5
GEO_MCP_MAX_CONCURRENT_REQUESTS=4
```

- [ ] **Step 8: Run focused and full MCP tests**

Run: `uv run pytest tests/test_mcp_settings.py tests/test_mcp_http.py tests/test_mcp_server.py tests/test_mcp_packaging.py tests/test_mcp_auth.py -v`

Expected: PASS. `test_mcp_auth.py` may continue testing the unused auth helper as historical code, but no production factory imports or invokes it.

- [ ] **Step 9: Commit the anonymous MCP boundary**

```bash
git add src/geo_index/mcp_settings.py src/geo_index/mcp_server.py tests/test_mcp_settings.py tests/test_mcp_http.py tests/test_mcp_server.py tests/test_mcp_packaging.py deploy/geo-mcp.env.example
git commit -m "feat: expose bounded anonymous MCP service"
```

### Task 2: Compose FastAPI, FastMCP, and the static marketing build in one process

**Files:**
- Modify: `src/geo_index/marketing_api.py`
- Create: `src/geo_index/production_app.py`
- Modify: `tests/test_marketing_api.py`
- Create: `tests/test_production_app.py`

**Interfaces:**
- Consumes: `create_mcp_http_mount(settings, service, path="/")`, `McpSearchService.from_settings()`, `EutilsClient`, and `frontend/dist`.
- Produces: `install_marketing_routes(app, *, service, geo, static_dir) -> None`, `EutilsGeoComparison`, and `production_app.create_app(settings=None, service=None, geo=None, static_dir=None) -> FastAPI`.

- [ ] **Step 1: Add a production composition test**

```python
def test_one_app_serves_health_api_frontend_and_anonymous_mcp(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>GEOscope</main>")
    service = FakeService()
    app = create_app(settings=_settings(), service=service, geo=FakeGeo(), static_dir=dist)

    with TestClient(app, base_url="https://geoscope.kevinformatics.com") as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}
        assert client.get("/").text == "<main>GEOscope</main>"
        assert client.get("/api/demo/search", params={"q": "immune cells"}).status_code == 200
        assert client.post("/mcp", json=_initialize_body(), headers=_mcp_headers()).status_code == 200

    assert service.open_calls == 1
    assert service.close_calls == 1
```

- [ ] **Step 2: Run the production composition test and verify the factory is missing**

Run: `uv run pytest tests/test_production_app.py -v`

Expected: FAIL with `ModuleNotFoundError: geo_index.production_app`.

- [ ] **Step 3: Extract reusable marketing routes without duplicating lifecycle ownership**

```python
def install_marketing_routes(
    app: FastAPI,
    *,
    service: SearchService,
    geo: GeoComparison | None,
    static_dir: Path | None,
) -> None:
    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "search": "ready" if service.is_open else "unavailable",
        }

    @app.get("/api/demo/search")
    async def demo_search(
        q: str = Query(min_length=1, max_length=1000),
        mode: Literal["hybrid", "bm25", "dense"] = "hybrid",
        limit: int = Query(default=8, ge=1, le=20),
    ) -> dict[str, object]:
        query = q.strip()
        if not query:
            raise HTTPException(status_code=422, detail="query must not be blank")
        geoscope = await asyncio.to_thread(
            service.search_datasets,
            query=query,
            filters=SearchFilters(),
            mode=mode,
            limit=limit,
        )
        native: dict[str, object] = {
            "count": None,
            "results": [],
            "error": "Native GEO comparison is not configured.",
        }
        membership: dict[str, bool] | None = None
        if geo is not None:
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
        return {
            "query": query,
            "mode": mode,
            "geo": native,
            "geoscope": geoscope.model_dump(mode="json"),
            "membership": membership,
        }

    if static_dir is None:
        return
    index_path = static_dir / "index.html"
    assets_path = static_dir / "assets"
    if assets_path.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    @app.get("/", include_in_schema=False)
    async def frontend_root() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/{frontend_path:path}", include_in_schema=False)
    async def frontend_fallback(frontend_path: str) -> FileResponse:
        reserved = ("api", "mcp", "healthz", "readyz")
        if any(frontend_path == item or frontend_path.startswith(f"{item}/") for item in reserved):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(index_path)
```

Retain the existing `marketing_api.create_app(service_factory, geo_factory=None, static_dir=None)` test helper, but implement it by opening one service in its lifespan and calling `install_marketing_routes`. Ensure the history fallback rejects `api`, `mcp`, `healthz`, and `readyz` prefixes rather than shadowing server routes.

- [ ] **Step 4: Move the bounded NCBI comparison behavior into a focused class**

```python
class EutilsGeoComparison:
    def __init__(self, client: EutilsClient | None = None) -> None:
        self._client = client or EutilsClient()
        self._lock = threading.Lock()

    def keyword_search(self, query: str, limit: int) -> dict[str, object]:
        with self._lock:
            result = self._client.esearch("gds", f"{query} AND gse[ETYP]")
            if result.count == 0:
                return {"count": 0, "results": []}
            page = self._client.esummary_page(
                "gds", result, 0, min(limit * 3, 100)
            )
        rows: list[dict[str, object]] = []
        for uid in page.get("uids", []):
            record = page.get(uid, {})
            if str(record.get("entrytype", "")).upper() != "GSE":
                continue
            rows.append(
                {
                    "gse": record.get("accession") or "",
                    "title": record.get("title"),
                    "study_type": record.get("gdstype"),
                    "taxon": record.get("taxon"),
                    "summary": (record.get("summary") or "")[:240],
                }
            )
            if len(rows) >= limit:
                break
        return {"count": result.count, "results": rows}

    def membership(self, query: str, accessions: list[str]) -> dict[str, bool] | None:
        valid = [
            value for value in accessions
            if value.startswith("GSE") and value[3:].isdigit()
        ]
        if not valid:
            return {}
        term = f"({query}) AND (" + " OR ".join(
            f"{value}[ACCN]" for value in valid
        ) + ")"
        with self._lock:
            ids = set(self._client.esearch_ids("gds", term, retmax=len(valid) + 10))
        return {
            value: str(200000000 + int(value[3:])) in ids
            for value in valid
        }
```

Reuse the logic currently in `geo_index.web`; keep its GSE-only restriction, result cap, serialized client access, and nonfatal network behavior.

- [ ] **Step 5: Implement the combined production factory**

```python
def create_app(
    settings: McpSettings | None = None,
    service: McpService | None = None,
    geo: GeoComparison | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    settings = settings or McpSettings.from_env(os.environ)
    service = service or McpSearchService.from_settings(settings)
    geo = geo or EutilsGeoComparison()
    mcp_mount = create_mcp_http_mount(settings, service, path="/")
    app = FastAPI(title="GEOscope", lifespan=mcp_mount.lifespan)
    register_health_routes(app, service)
    app.mount("/mcp", mcp_mount.app)
    install_marketing_routes(
        app,
        service=service,
        geo=geo,
        static_dir=static_dir or Path("/app/frontend/dist"),
    )
    return app
```

The FastMCP lifespan is the sole owner of `service.open()` and `service.close()`.

- [ ] **Step 6: Run focused web and production tests**

Run: `uv run pytest tests/test_marketing_api.py tests/test_production_app.py tests/test_mcp_http.py -v`

Expected: PASS, including route-order and single-lifecycle assertions.

- [ ] **Step 7: Commit the combined ASGI application**

```bash
git add src/geo_index/marketing_api.py src/geo_index/production_app.py tests/test_marketing_api.py tests/test_production_app.py
git commit -m "feat: compose GEOscope production application"
```

### Task 3: Build a slim production image with the React application

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Create: `tests/test_production_packaging.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `frontend/pnpm-lock.yaml`, `frontend/dist`, and `geo_index.production_app:create_app`.
- Produces: a production image with one Uvicorn worker and no ETL/local-model/PostgreSQL distributions.

- [ ] **Step 1: Add static packaging assertions**

```python
def test_production_dockerfile_builds_frontend_and_runs_combined_app() -> None:
    text = Path("Dockerfile").read_text()
    assert "pnpm install --frozen-lockfile" in text
    assert "pnpm build" in text
    assert "COPY --from=frontend" in text
    assert "geo_index.production_app:create_app" in text
    assert '"--factory"' in text

def test_heavy_packages_are_not_default_project_dependencies() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text())
    defaults = "\n".join(data["project"]["dependencies"])
    for forbidden in ("prefect", "sentence-transformers", "psycopg", "pgvector"):
        assert forbidden not in defaults
```

- [ ] **Step 2: Run packaging tests and verify the monolithic image fails**

Run: `uv run pytest tests/test_production_packaging.py -v`

Expected: FAIL because the current Dockerfile has no frontend stage and the default dependencies contain ETL/model/PostgreSQL packages.

- [ ] **Step 3: Split online dependencies from optional offline extras**

Keep `fastapi`, `fastmcp`, `uvicorn`, `elasticsearch`, `google-genai`, `httpx`, and `numpy` in `[project].dependencies`. Move Prefect to `[project.optional-dependencies].etl`, local model packages to `.local-models`, and PostgreSQL packages to `.postgres`. Refresh the lock with:

Run: `uv lock`

Expected: the lock resolves all extras, while `uv sync --frozen --no-dev` installs only online packages.

- [ ] **Step 4: Implement the two-stage image**

```dockerfile
FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY --from=frontend /frontend/dist /app/frontend/dist
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "geo_index.production_app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

- [ ] **Step 5: Document full development synchronization**

Update setup commands to `uv sync --all-extras` for contributors who run ETL, local embedding comparisons, PostgreSQL history, and the complete test suite. Keep production at `uv sync --frozen --no-dev`.

- [ ] **Step 6: Run Python and frontend tests and build the image**

Run: `uv run pytest -q`

Expected: PASS.

Run: `pnpm --dir frontend test && pnpm --dir frontend build`

Expected: PASS and `frontend/dist/index.html` exists.

Run: `docker build -t geoscope:production .`

Expected: PASS. `docker image inspect geoscope:production --format '{{.Size}}'` reports less than 1,073,741,824 bytes.

- [ ] **Step 7: Prove forbidden distributions are absent from the image**

Run: `docker run --rm geoscope:production python -c "import importlib.util as i; assert all(i.find_spec(x) is None for x in ('torch','transformers','sentence_transformers','prefect','psycopg','pgvector'))"`

Expected: exit 0.

- [ ] **Step 8: Commit production packaging**

```bash
git add pyproject.toml uv.lock Dockerfile .dockerignore tests/test_production_packaging.py README.md
git commit -m "build: add slim GEOscope production image"
```

### Task 4: Add dedicated-host Elasticsearch production configuration

**Files:**
- Create: `deploy/elasticsearch/docker-compose.production.yml`
- Create: `deploy/elasticsearch/jvm.options.d/heap.options`
- Create: `deploy/elasticsearch/elasticsearch.env.example`
- Create: `deploy/elasticsearch/bootstrap-ubuntu.sh`
- Create: `tests/test_production_elasticsearch_config.py`

**Interfaces:**
- Consumes: Ubuntu 24.04 host, private IP `10.124.0.2`, and `ELASTICSEARCH_PASSWORD` from an uncommitted env file.
- Produces: one persistent Elasticsearch 9.4.2 node reachable at `127.0.0.1:9200` and `10.124.0.2:9200` only.

- [ ] **Step 1: Add production-host invariants**

```python
def test_production_elasticsearch_is_private_persistent_and_unlimited() -> None:
    text = Path("deploy/elasticsearch/docker-compose.production.yml").read_text()
    assert "elasticsearch:9.4.2" in text
    assert '127.0.0.1:9200:9200' in text
    assert '10.124.0.2:9200:9200' in text
    assert "0.0.0.0:9200" not in text
    assert "/srv/elasticsearch/data:/usr/share/elasticsearch/data" in text
    assert "ES_JAVA_OPTS" not in text
    assert "mem_limit" not in text
    assert "cpus:" not in text
    assert "max-size: 20m" in text

def test_heap_is_four_gibibytes() -> None:
    assert Path("deploy/elasticsearch/jvm.options.d/heap.options").read_text() == "-Xms4g\n-Xmx4g\n"
```

- [ ] **Step 2: Run the test and verify production files are missing**

Run: `uv run pytest tests/test_production_elasticsearch_config.py -v`

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Create the production Compose service**

Configure `discovery.type=single-node`, security enabled, HTTP TLS disabled on the private VPC, trial license, `bootstrap.memory_lock=true`, the password from `${ELASTICSEARCH_PASSWORD:?set ELASTICSEARCH_PASSWORD}`, both private port bindings, bind-mounted data and heap options, `memlock=-1`, `nofile=65535`, the existing auth/cluster health check, `restart: unless-stopped`, and JSON-file rotation of 20 MiB times five files. Do not add any CPU or memory resource limit.

- [ ] **Step 4: Create an idempotent Ubuntu bootstrap script**

```bash
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker
install -d -m 0750 -o 1000 -g 0 /srv/elasticsearch/data
printf '%s\n' 'vm.max_map_count=1048576' >/etc/sysctl.d/99-elasticsearch.conf
sysctl --system
swapoff -a
sed -i.bak '/\sswap\s/s/^/#/' /etc/fstab
```

Do not create or print a password in the script.

- [ ] **Step 5: Validate the production composition**

Run: `ELASTICSEARCH_PASSWORD=test-only docker compose -f deploy/elasticsearch/docker-compose.production.yml config --quiet`

Expected: exit 0.

Run: `uv run pytest tests/test_production_elasticsearch_config.py tests/test_elasticsearch_config.py -v`

Expected: PASS; the local development composition remains unchanged.

- [ ] **Step 6: Commit the Elasticsearch host configuration**

```bash
git add deploy/elasticsearch tests/test_production_elasticsearch_config.py
git commit -m "ops: add dedicated Elasticsearch host configuration"
```

### Task 5: Record App Platform and operations configuration

**Files:**
- Create: `.do/app.yaml.tmpl`
- Create: `deploy/app-platform.env.example`
- Create: `docs/deployment/digitalocean.md`
- Create: `tests/test_app_platform_config.py`

**Interfaces:**
- Consumes: the existing App Platform app, its concrete VPC UUID, GitHub repository/branch, and control-plane encrypted secrets.
- Produces: a source-controlled app-spec template and an executable operator runbook.

- [ ] **Step 1: Test the app-spec contract**

```python
def test_app_platform_template_uses_one_small_private_service() -> None:
    text = Path(".do/app.yaml.tmpl").read_text()
    assert "region: sfo" in text
    assert "instance_size_slug: apps-s-1vcpu-0.5gb" in text
    assert "instance_count: 1" in text
    assert "disable_edge_cache: true" in text
    assert "http_path: /healthz" in text
    assert "ELASTICSEARCH_URL" in text
    assert "http://10.124.0.2:9200" in text
    assert "ELASTICSEARCH_PASSWORD" in text
    assert "GEMINI_API_KEY" in text
    assert "GEO_MCP_JWKS_URI" not in text
```

- [ ] **Step 2: Run the test and verify the template is missing**

Run: `uv run pytest tests/test_app_platform_config.py -v`

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Add the renderable app spec**

Use environment substitution only for control-plane identifiers and encrypted values: `DO_VPC_ID`, `DO_GITHUB_REPO`, `DO_GITHUB_BRANCH`, `ELASTICSEARCH_PASSWORD`, and `GEMINI_API_KEY`. The rendered `.do/app.yaml` remains ignored. Define one Dockerfile-based service on port 8000, one instance, 512 MiB, `sfo`, the VPC, `disable_edge_cache: true`, `/healthz` liveness, 120-second termination grace, and all nonsecret variables from the approved design.

- [ ] **Step 4: Add an operator runbook with exact resource facts**

Document:

```text
Droplet public IP: 143.198.53.162
Droplet private IP: 10.124.0.2
Droplet SSH identity: ~/.ssh/digitalocean
App region: sfo
VPC/datacenter: default-sfo3 / sfo3
Public domain: geoscope.kevinformatics.com
```

Include exact bootstrap, copy, start, SSH tunnel, loader, audit, App Platform, DNS, rollback, password rotation, and recovery commands. Commands source ignored `.env.elasticsearch.production` rather than echoing credentials.

- [ ] **Step 5: Run configuration tests and documentation link checks**

Run: `uv run pytest tests/test_app_platform_config.py tests/test_primary_path_docs.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the operator configuration**

```bash
git add .do/app.yaml.tmpl deploy/app-platform.env.example docs/deployment/digitalocean.md tests/test_app_platform_config.py .gitignore
git commit -m "ops: document DigitalOcean application deployment"
```

### Task 6: Provision and start the Elasticsearch Droplet

**Files:**
- Use: `deploy/elasticsearch/*`
- Create locally but do not commit: `.env.elasticsearch.production`

**Interfaces:**
- Consumes: SSH access `root@143.198.53.162` with `~/.ssh/digitalocean`.
- Produces: a healthy empty Elasticsearch node on loopback and `10.124.0.2`.

- [ ] **Step 1: Generate and retain the Elasticsearch password without printing it**

Run:

```bash
umask 077
ELASTICSEARCH_PASSWORD="$(openssl rand -hex 32)"
printf 'ELASTICSEARCH_USERNAME=elastic\nELASTICSEARCH_PASSWORD=%s\n' "$ELASTICSEARCH_PASSWORD" >.env.elasticsearch.production
```

Expected: ignored file mode 600 containing two variables; terminal output contains no password.

- [ ] **Step 2: Copy configuration and bootstrap the host**

Run:

```bash
scp -i ~/.ssh/digitalocean -r deploy/elasticsearch root@143.198.53.162:/root/geoscope-elasticsearch
ssh -i ~/.ssh/digitalocean root@143.198.53.162 'bash /root/geoscope-elasticsearch/bootstrap-ubuntu.sh'
```

Expected: Docker and Compose are installed; `sysctl vm.max_map_count` reports 1048576; swap remains disabled.

- [ ] **Step 3: Transfer the secret env file without exposing it**

Run: `scp -i ~/.ssh/digitalocean .env.elasticsearch.production root@143.198.53.162:/root/geoscope-elasticsearch/.env`

Expected: remote `.env` exists with mode 600. Immediately run `ssh -i ~/.ssh/digitalocean root@143.198.53.162 'chmod 600 /root/geoscope-elasticsearch/.env'`.

- [ ] **Step 4: Start Elasticsearch and wait for health**

Run:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'cd /root/geoscope-elasticsearch && docker compose --env-file .env -f docker-compose.production.yml up -d'
```

Expected: `docker compose ps` reports `geo-elasticsearch` healthy.

- [ ] **Step 5: Verify bindings, heap, persistence path, and public isolation**

Run remote `ss -lntp`, `_security/_authenticate`, `_cluster/health`, `_nodes/_all/jvm`, and `docker inspect` checks without printing credentials. Verify listeners are only `127.0.0.1:9200` and `10.124.0.2:9200`, heap max is approximately 4 GiB, `/srv/elasticsearch/data` is mounted, and no container memory/CPU limit exists.

From the development machine, run: `nc -zvw3 143.198.53.162 9200`

Expected: connection fails.

### Task 7: Publish and configure the App Platform service

**Files:**
- Use: `.do/app.yaml.tmpl`
- Use: `.env.elasticsearch.production`

**Interfaces:**
- Consumes: committed production code, the existing DigitalOcean App Platform app, and its `default-sfo3` VPC attachment.
- Produces: a healthy App Platform deployment privately connected to Elasticsearch.

- [ ] **Step 1: Push the reviewed production commits to the app's configured GitHub branch**

Run: `git push origin main`

Expected: the GitHub repository contains every focused production commit and App Platform begins a new build from that SHA.

- [ ] **Step 2: Configure App Platform run-time variables**

Set public values:

```dotenv
ELASTICSEARCH_URL=http://10.124.0.2:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_ACTIVE_MODEL=gemini_embedding_2_3072_v1
GEO_MCP_PUBLIC_BASE_URL=https://geoscope.kevinformatics.com
GEO_MCP_ALLOWED_HOSTS=geoscope.kevinformatics.com
GEO_MCP_ALLOWED_ORIGINS=
GEO_MCP_RATE_PER_SECOND=1
GEO_MCP_BURST_CAPACITY=5
GEO_MCP_MAX_CONCURRENT_REQUESTS=4
```

Set `ELASTICSEARCH_PASSWORD` and `GEMINI_API_KEY` as encrypted run-time secrets. Do not enter them into build-time variables or commit them.

- [ ] **Step 3: Verify VPC, edge, health, and sizing controls**

Confirm `sfo`, `default-sfo3`, one 512 MiB instance, port 8000, Dockerfile build, edge cache disabled, and `/healthz` health check. Record the concrete VPC UUID in the local rendered app spec without exposing secrets.

- [ ] **Step 4: Restrict Droplet ingress**

Attach a DigitalOcean Cloud Firewall allowing TCP 22 only from the administrator's current public IP and TCP 9200 only from the App Platform VPC egress private IP. Keep the host's private binding as the second enforcement layer.

- [ ] **Step 5: Configure anonymous-spend and infrastructure safeguards**

In the Google Cloud project serving `GEMINI_API_KEY`, cap Gemini query embedding usage at 60 requests per minute and 5,000 requests per day. Enable the Droplet's weekly DigitalOcean backup, create disk alerts at 70% and 80%, and create App Platform deployment-failure and restart alerts. Record the Elasticsearch trial-license expiration returned by `GET /_license` in the operator runbook.

- [ ] **Step 6: Verify the starter domain before custom DNS**

Expected: `/healthz` returns 200, `/readyz` returns 503 while the index is empty or unready, the React shell loads, and no OAuth/JWKS configuration error appears in runtime logs.

### Task 8: Stream and audit the complete corpus

**Files:**
- Use: `.env.elasticsearch.production`
- Use: `data/processed/series_records`
- Use: `data/processed/embedding_artifacts`

**Interfaces:**
- Consumes: the local 249,736 canonical documents and four aligned registered vector artifacts.
- Produces: a live `geo-series` index with mapping revision `geo-series-v1` and complete Gemini vector coverage.

- [ ] **Step 1: Start a resilient SSH tunnel**

Run in a long-lived terminal:

```bash
ssh -i ~/.ssh/digitalocean -N \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
  -L 127.0.0.1:9201:127.0.0.1:9200 root@143.198.53.162
```

Expected: local port 9201 accepts authenticated Elasticsearch requests while public port 9200 remains closed.

- [ ] **Step 2: Load the index idempotently from the local artifacts**

Run in a separate terminal:

```bash
set -a
source .env.elasticsearch.production
set +a
ELASTICSEARCH_URL=http://127.0.0.1:9201 uv run geo-elasticsearch-load
```

Expected: loader reports `attempted=249736`, `succeeded=249736`, no failures, and index `geo-series`.

- [ ] **Step 3: Run the primary audit through the tunnel**

Run:

```bash
set -a
source .env.elasticsearch.production
set +a
ELASTICSEARCH_URL=http://127.0.0.1:9201 uv run python -c '
from geo_index.elasticsearch_config import ElasticsearchSettings, create_client
from geo_index.elasticsearch_index import index_readiness
s = ElasticsearchSettings.from_env()
c = create_client(s)
try:
    print(index_readiness(c, s.active_model_key))
    print(c.count(index="geo-series").body["count"])
    print(c.count(index="geo-series", query={"exists": {"field": "embedding_gemini_3072"}}).body["count"])
finally:
    c.close()
'
```

Expected: document count 249,736; mapping revision `geo-series-v1`; `embedding_gemini_3072` coverage 249,736; cluster yellow or green with no initializing shards.

- [ ] **Step 4: Restart Elasticsearch and prove persistence**

Run: `ssh -i ~/.ssh/digitalocean root@143.198.53.162 'cd /root/geoscope-elasticsearch && docker compose --env-file .env -f docker-compose.production.yml restart elasticsearch'`

Expected: the node returns healthy and the audit remains unchanged.

### Task 9: Attach the domain and run live acceptance checks

**Files:**
- Modify: `docs/deployment/digitalocean.md` only if live behavior differs from the runbook.

**Interfaces:**
- Consumes: the healthy App Platform deployment and populated private index.
- Produces: verified public website, API, and anonymous MCP service.

- [ ] **Step 1: Add `geoscope.kevinformatics.com` as the App Platform custom domain**

Use the exact CNAME or A record DigitalOcean supplies at the DNS provider for `kevinformatics.com`. Wait for App Platform to report the domain active and its managed certificate issued.

- [ ] **Step 2: Verify public health and readiness**

Run:

```bash
curl --fail --silent https://geoscope.kevinformatics.com/healthz
curl --fail --silent https://geoscope.kevinformatics.com/readyz
```

Expected: `{"status":"ok"}` and `{"status":"ready"}`.

- [ ] **Step 3: Verify website and browser API**

Run a representative hybrid demo query for `transcriptomes of individual cells` and verify HTTP 200, bounded GEO and GEOscope results, and no credentials/internal addresses in the payload.

- [ ] **Step 4: Verify anonymous MCP initialization and all three tools**

Use a Streamable HTTP MCP client against `https://geoscope.kevinformatics.com/mcp` with no authorization header. Call `search_datasets`, `get_dataset`, and `facet_values` and verify valid bounded responses.

- [ ] **Step 5: Verify degraded behavior**

Stop Elasticsearch for less than two minutes. Expected: website shell and `/healthz` remain 200; `/readyz` is 503; API and MCP return masked unavailable responses. Restart Elasticsearch and verify readiness recovers without redeploying App Platform.

- [ ] **Step 6: Run final local verification and commit any runbook corrections**

Run: `uv run pytest -q && pnpm --dir frontend test && pnpm --dir frontend build`

Expected: PASS.

If and only if observed live behavior required documentation corrections:

```bash
git add docs/deployment/digitalocean.md
git commit -m "docs: record verified DigitalOcean operations"
git push origin main
```
