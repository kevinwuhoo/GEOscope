---
title: Private Remote MCP Server Plan
tags: [mcp, fastmcp, search, api, elasticsearch, oauth, plan, v1]
status: implemented-code-deployment-pending
created: 2026-07-10
updated: 2026-07-12
---

# 47 · Private Remote FastMCP Server Implementation Plan

← [[Home]] · implements [[27-MCP-Interface]] · depends on
[[45-Normalized-Filters-and-Facets-Plan]] · deploys independently with BGE, then
is extended by [[49-Alternate-Embedding-Bakeoff-Implementation-Plan]] to expose
active-variant provenance without exposing a model selector

> **Datastore update (2026-07-10):** The authentication, transport, three-tool
> contract, and bounded wire models remain the intended design. Its PostgreSQL
> `SearchService` implementation steps are superseded by the Elasticsearch
> adapter in [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]]. Reuse the
> remote MCP first draft selectively; do not merge its Postgres composition root
> as-is.

> **Implementation status (2026-07-12):** The selective migration is complete
> and merged. The live service now uses `McpSearchService` over Elasticsearch,
> defaults to `gemini_embedding_2_3072_v1`, and retains this plan's three-tool,
> authentication, bounded-model, stateless-HTTP, and admission-control design.
> The authoritative as-built design and plan are
> `docs/superpowers/specs/2026-07-12-elasticsearch-mcp-migration-design.md` and
> `docs/superpowers/plans/2026-07-12-elasticsearch-mcp-migration.md`. The task
> bodies below preserve the original PostgreSQL plan as history; do not execute
> its DSN, SQL, psycopg, or Postgres smoke steps. Production hosting remains.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host the three stable, read-only GEO tools at an invite-only remote MCP
endpoint that coworkers can connect to without running this repository locally.

**Architecture:** Keep search and database behavior in an injectable
`SearchService`, keep Pydantic wire models independent of transport, and expose a
standalone FastMCP 3 ASGI application over Streamable HTTP. An external OAuth
authorization server issues JWTs; this service validates them through JWKS and
then applies a stable-subject invite allowlist. The v1 deployment is stateless at
the MCP transport layer and runs one application worker behind an HTTPS edge.

**As-built stack:** Python 3.11+, standalone `fastmcp>=3.4.4,<4`, Streamable
HTTP, Pydantic, Uvicorn, official Elasticsearch 9 client, Gemini query
embeddings, pytest + pytest-asyncio. The original PostgreSQL stack remains only
inside the historical task bodies below.

## Global Constraints

- Expose exactly `search_datasets`, `get_dataset`, and `facet_values`.
- Use Streamable HTTP at `/mcp`; stdio is not the hosted product transport.
- Enable `stateless_http=True` and run one Uvicorn worker in v1.
- Pass `host_origin_protection=True` explicitly when constructing the HTTP app;
  FastMCP 3.4.4 does not enable that guard by default. The reverse proxy must
  preserve the configured public `Host` value
  ([3.4.4 change](https://github.com/PrefectHQ/fastmcp/pull/4472)).
- Require a signed JWT with the configured issuer, audience, and `geo:read`
  scope, then require its stable `sub` claim to appear in the invite allowlist.
- Use `RemoteAuthProvider` so clients can discover the external authorization
  server; the chosen authorization server must support the client-registration
  flow used by the intended MCP clients
  ([remote OAuth guide](https://gofastmcp.com/servers/auth/remote-oauth)).
- Never use `StaticTokenVerifier` or `DebugTokenVerifier` in a hosted
  environment; FastMCP documents static tokens as development-only
  ([token-verification guide](https://gofastmcp.com/servers/auth/token-verification)).
- Terminate TLS at the hosting edge and reject unexpected Host and Origin values.
- Perform no database, network, or model I/O during Python module import.
- Use a database role with `SELECT` only and set transactions read-only.
- Parameterize all user values. Only fixed code registries may supply SQL
  identifiers.
- Bound every input and response; never return raw SOFT, full sample blobs, or an
  unbounded summary.
- Do not log bearer tokens, DSNs, raw queries, filters, or returned study text.
- Do not add a server-side LLM, query-expansion model, reranker, or public
  embedding-model selector.
- The active embedding model is deployment configuration. MCP outputs report a
  retrieval-version string so runs can be reproduced.

Track 4 pins the baseline query encoder to
`BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
([pinned snapshot](https://huggingface.co/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a)).
Its legacy document-artifact revision is still unknown, so Track 4 reports
`bm25-v1` or `bge_small_v15:5c38ec7c405e:{dense|hybrid}-v1` honestly as query-
pipeline provenance. Track 49 later replaces this compatibility version with a
database-manifest vector hash; it must not pretend the old document revision is
known.

---

## Decisions and boundaries

FastMCP recommends Streamable HTTP for network deployments and supports ASGI
applications through `mcp.http_app()`
([running servers](https://gofastmcp.com/deployment/running-server),
[HTTP deployment](https://gofastmcp.com/deployment/http)). Hosted authentication
uses a resource-server split:

1. The external authorization server authenticates a user and issues a JWT.
2. `JWTVerifier` validates signature, expiry, issuer, audience, and `geo:read`
   against a JWKS endpoint.
3. `RemoteAuthProvider` publishes protected-resource discovery metadata.
4. `AuthMiddleware` checks the verified `sub` against
   `GEO_MCP_ALLOWED_SUBJECTS`.

FastMCP's authorization checks receive the verified token claims and can be
applied server-wide
([authorization guide](https://gofastmcp.com/servers/authorization)). The
allowlist is therefore an authorization rule after cryptographic validation,
not a substitute for OAuth.

The deployment remains provider-neutral. `GEO_MCP_AUTHORIZATION_SERVER` may
point at any compatible issuer. If the eventual identity provider cannot support
the intended MCP clients' registration flow, replace `RemoteAuthProvider` with
FastMCP's provider-specific/OAuth-proxy adapter in a deployment follow-up; do not
ship both auth stacks in this prototype.

Public anonymous access, self-service invitations, admin tools, per-user roles,
multi-tenant quotas, and multiple application replicas are **(v2+)**.

## Stable v1 tool contract

### `search_datasets`

Inputs:

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

- `query` is stripped and 1–1,000 characters.
- `mode` is exactly `hybrid`, `bm25`, or `dense`.
- `limit` is 1–50.
- Each filter list contains at most 20 unique nonblank values.
- Organism values match `^NCBITaxon:[1-9][0-9]*$` and sex values match
  `^PATO:[0-9]{7}$`. Assay category/detail values are 1–256-character closed
  labels returned by `facet_values`.
- Unknown fields are rejected.
- Retrieval tuning (`deep`, `k0`, HNSW parameters, and model choice) stays
  private. The response's `limit` remains a request field; all hidden tuning is
  bound to a versioned server profile.

The exact top-level output keys are `query`, `filters`, `mode`, `limit`,
`retrieval_version`, `embedding_variant`, `results`, and `facets`. `results` is
the ranked GSE summaries; `facets` maps each of the four field names to
`{field, buckets, scope, candidate_count}`. Each study includes rank, accession,
title, bounded summary snippet, study type, sample count, PubMed ID, normalized
arrays, score, and sorted `truncated_fields`. Titles are capped at 500
characters, snippets at 1,000, type at 200, normalized arrays at 100 values,
and every normalized value at 256 characters.

`DatasetSummary` uses exactly `rank`, `gse`, `score`, `title`, `snippet`,
`study_type`, `n_samples`, `pubmed_id`, the four normalized arrays and their
available status fields, and `truncated_fields`. A facet bucket uses exactly
`value`, `label`, and `count`.

### `get_dataset`

Normalize `gse` with `strip().upper()` and accept only
`^GSE[1-9][0-9]*$`. A valid but absent accession returns
`{"found": false, "dataset": null}` rather than a protocol error.

The detail record contains indexed GSE metadata, normalized arrays/statuses, and
derived GEO/PubMed URLs. Apply the summary-record bounds above; cap summary and
overall design at 8,000 characters each and include sorted `truncated_fields`
when a scalar or array cap applies.
The exact output is `{found, dataset}`; `dataset` is null when `found=false`.

### `facet_values`

`field` is exactly `organism_ids`, `sex_ids`, `assay_categories`, or
`assay_labels`. Optional `query`, filters, and mode are accepted; `limit` is
1–50. Prefix/autocomplete matching is not a v1 input because Track 2 does not
implement it. Output contains `{value, label, count}` buckets plus Track 2's
exact `all_matches` or `candidate_pool` scope and candidate count. Values and
labels are capped at 256 characters, and Track 2's disjunctive rule applies.
Normalize an omitted/null/whitespace-only `query` to no-query browsing; a
nonblank query is stripped and limited to 1–1,000 characters. Mode is ignored
for retrieval provenance when no query is present.
The exact output keys are `field`, `buckets`, `scope`, `candidate_count`,
`retrieval_version`, and `embedding_variant`.

## Runtime configuration

`src/geo_index/mcp_settings.py` owns these variables:

| Variable | Rule |
|---|---|
| `GEO_PG_DSN` | required; never printed |
| `GEO_MCP_PUBLIC_BASE_URL` | required HTTPS origin, for example `https://geo.example.org` |
| `GEO_MCP_JWKS_URI` | required HTTPS JWKS URL |
| `GEO_MCP_ISSUER` | required exact issuer |
| `GEO_MCP_AUDIENCE` | required exact JWT audience |
| `GEO_MCP_AUTHORIZATION_SERVER` | required HTTPS authorization-server origin |
| `GEO_MCP_ALLOWED_SUBJECTS` | required comma-separated, nonempty stable subject IDs |
| `GEO_MCP_ALLOWED_HOSTS` | required comma-separated public hostnames |
| `GEO_MCP_ALLOWED_ORIGINS` | optional comma-separated browser origins; empty means none |
| `GEO_EMBEDDING_VARIANT` | optional; defaults to `bge_small_v15` |
| `GEO_MCP_RATE_PER_SECOND` | optional; defaults to `5` |
| `GEO_MCP_BURST_CAPACITY` | optional; defaults to `10` |

Production settings fail closed for a non-HTTPS base/JWKS/auth URL, a public
base URL containing userinfo, a path other than `/`, a query, or a fragment, an
empty subject/host list, wildcard Host or Origin, nonpositive rate
settings, or an unknown embedding variant.

Client registration and redirect-URI policy belong to the external
authorization server. `RemoteAuthProvider` is only the protected resource and
does not accept or enforce a redirect allowlist; do not invent a resource-server
environment variable for it.

## File structure

| Path | Responsibility |
|---|---|
| `src/geo_index/mcp_settings.py` | Parse and fail-closed validate hosted settings |
| `src/geo_index/mcp_auth.py` | JWT/JWKS provider and invite authorization check |
| `src/geo_index/mcp_models.py` | Strict transport input/output models |
| `src/geo_index/search_service.py` | Read-only DB/model lifecycle and retrieval facade |
| `src/geo_index/mcp_server.py` | FastMCP factory, three tools, health route, ASGI app |
| `tests/test_mcp_settings.py` | Environment parsing/fail-closed tests |
| `tests/test_mcp_auth.py` | Offline signed-token and invite-policy tests |
| `tests/test_mcp_models.py` | Wire validation and payload-bound tests |
| `tests/test_search_service.py` | Pool, lazy encoder, and service delegation tests |
| `tests/test_mcp_server.py` | In-memory tool contract tests |
| `tests/test_mcp_http.py` | ASGI Streamable HTTP and authentication boundary |
| `tests/test_mcp_db_smoke.py` | Opt-in live read-only database smoke |
| `Dockerfile`, `.dockerignore` | Reproducible non-root, single-worker image |
| `deploy/geo-mcp.env.example` | Variable names and safe example values only |
| `README.md` | Local test, deployment, invite, revoke, and client instructions |

### Task 1: Add FastMCP and fail-closed hosted settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/geo_index/mcp_settings.py`
- Create: `tests/test_mcp_settings.py`

**Interfaces:**
- Produces: `McpSettings.from_env(env: Mapping[str, str]) -> McpSettings`.
- Produces: the shared route constant `MCP_PATH = "/mcp"`.
- Produces: `McpSettings.mcp_url -> str`.

- [ ] **Step 1: Add bounded runtime and test dependencies**

```bash
uv add "fastmcp>=3.4.4,<4" "uvicorn[standard]>=0.35,<1" "psycopg[binary,pool]>=3.3.4"
uv add --dev "pytest-asyncio>=1,<2"
```

Expected: `uv.lock` resolves standalone FastMCP 3, not
`mcp.server.fastmcp.FastMCP`. FastMCP 3.4.4 was the published stable package on
2026-07-09 ([PyPI](https://pypi.org/project/fastmcp/)); the lockfile, rather than
a floating production install, fixes the exact build. Retain the repository's
existing `pytest>=9.1.1` dependency and add `asyncio_mode = "auto"` under
`[tool.pytest.ini_options]` so the async examples below are collected.

- [ ] **Step 2: Write failing settings tests**

Create `tests/test_mcp_settings.py`:

```python
import pytest

from geo_index.mcp_settings import McpSettings


VALID = {
    "GEO_PG_DSN": "postgresql://reader:secret@db:5432/geo",
    "GEO_MCP_PUBLIC_BASE_URL": "https://geo.example.org",
    "GEO_MCP_JWKS_URI": "https://login.example.org/.well-known/jwks.json",
    "GEO_MCP_ISSUER": "https://login.example.org/",
    "GEO_MCP_AUDIENCE": "geo-mcp",
    "GEO_MCP_AUTHORIZATION_SERVER": "https://login.example.org/",
    "GEO_MCP_ALLOWED_SUBJECTS": "user-1,user-2,user-1",
    "GEO_MCP_ALLOWED_HOSTS": "geo.example.org",
}


def test_settings_normalize_stable_subjects():
    settings = McpSettings.from_env(VALID)
    assert settings.allowed_subjects == frozenset({"user-1", "user-2"})
    assert settings.mcp_url == "https://geo.example.org/mcp"
    assert settings.embedding_variant == "bge_small_v15"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GEO_MCP_PUBLIC_BASE_URL", "http://geo.example.org"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://geo.example.org/prefix"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://user@geo.example.org"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://geo.example.org?tenant=1"),
        ("GEO_MCP_PUBLIC_BASE_URL", "https://geo.example.org/#fragment"),
        ("GEO_MCP_JWKS_URI", "http://login.example.org/jwks"),
        ("GEO_MCP_ALLOWED_SUBJECTS", ""),
        ("GEO_MCP_ALLOWED_HOSTS", "*"),
        ("GEO_MCP_ALLOWED_ORIGINS", "*"),
        ("GEO_MCP_RATE_PER_SECOND", "0"),
    ],
)
def test_settings_fail_closed(key, value):
    env = VALID | {key: value}
    with pytest.raises(ValueError):
        McpSettings.from_env(env)
```

- [ ] **Step 3: Implement the immutable settings object**

Use a frozen dataclass, `urllib.parse.urlparse`, and one helper that splits
comma-separated values, strips whitespace, removes duplicates, and rejects
blank entries. Require `public_base_url` to be an origin: `https`, a hostname,
no username/password, no query/fragment, and either an empty path or `/`.
Normalize that value to `https://<authority>` without a trailing slash. Derive
`mcp_url` by appending the shared `MCP_PATH` constant. Never include `pg_dsn` or
invited subject IDs in `repr`; declare both with `field(repr=False)`.

```python
MCP_PATH = "/mcp"


@dataclass(frozen=True)
class McpSettings:
    pg_dsn: str = field(repr=False)
    public_base_url: str
    jwks_uri: str
    issuer: str
    audience: str
    authorization_server: str
    allowed_subjects: frozenset[str] = field(repr=False)
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    embedding_variant: str
    rate_per_second: float = 5.0
    burst_capacity: int = 10
    required_scope: str = "geo:read"

    @property
    def mcp_url(self) -> str:
        return f"{self.public_base_url}{MCP_PATH}"
```

`McpSettings.from_env` must call the embedding registry's
`get_variant(key)` when that registry exists. Until Track 49 lands, accept only
`bge_small_v15` through a one-entry local constant; remove that compatibility
constant during Track 49's MCP integration task.
It must also require the hostname parsed from `public_base_url` to appear in
`allowed_hosts` (ignoring an optional port); otherwise OAuth discovery would
advertise a resource that the request guard rejects. Add a failure test for that
mismatch.

- [ ] **Step 4: Run and commit**

```bash
uv run pytest tests/test_mcp_settings.py -v
git add pyproject.toml uv.lock src/geo_index/mcp_settings.py tests/test_mcp_settings.py
git commit -m "feat: define hosted MCP settings"
```

Expected: all settings tests pass and neither DSN nor invited subject appears in
`repr(settings)` or assertion output.

### Task 2: Add JWT discovery and invite authorization

**Files:**
- Create: `src/geo_index/mcp_auth.py`
- Create: `tests/test_mcp_auth.py`

**Interfaces:**
- Consumes: `McpSettings`.
- Produces: `create_auth(settings) -> RemoteAuthProvider`.
- Produces: `require_invited_subject(subjects) -> AuthCheck`.

- [ ] **Step 1: Write authorization tests before provider code**

```python
from types import SimpleNamespace

from geo_index.mcp_auth import require_invited_subject


def _ctx(subject=None):
    token = None if subject is None else SimpleNamespace(claims={"sub": subject})
    return SimpleNamespace(token=token)


def test_invited_subject_is_allowed():
    check = require_invited_subject(frozenset({"user-1"}))
    assert check(_ctx("user-1")) is True


def test_missing_or_uninvited_subject_is_denied():
    check = require_invited_subject(frozenset({"user-1"}))
    assert check(_ctx()) is False
    assert check(_ctx("user-2")) is False


def test_non_string_subject_is_denied():
    check = require_invited_subject(frozenset({"123"}))
    assert check(_ctx(123)) is False
```

Also monkeypatch `JWTVerifier` and `RemoteAuthProvider` constructors and assert
that `create_auth` forwards the exact JWKS URI, issuer, audience,
`required_scopes=["geo:read"]`, authorization server, and base URL—and no
unsupported redirect-policy keyword.

- [ ] **Step 2: Implement provider and policy**

```python
from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthContext, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier


def require_invited_subject(subjects: frozenset[str]):
    def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        subject = ctx.token.claims.get("sub")
        return (
            isinstance(subject, str)
            and bool(subject.strip())
            and subject.strip() in subjects
        )

    return check


def create_auth(settings: McpSettings) -> RemoteAuthProvider:
    verifier = JWTVerifier(
        jwks_uri=settings.jwks_uri,
        issuer=settings.issuer,
        audience=settings.audience,
        required_scopes=[settings.required_scope],
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(settings.authorization_server)],
        base_url=settings.public_base_url,
        scopes_supported=[settings.required_scope],
    )
```

JWKS enables public-key rotation without distributing signing keys to this
service ([FastMCP JWT/JWKS guidance](https://gofastmcp.com/servers/auth/token-verification)).
Do not accept symmetric shared secrets in hosted configuration.

- [ ] **Step 3: Run and commit**

```bash
uv run pytest tests/test_mcp_auth.py -v
git add src/geo_index/mcp_auth.py tests/test_mcp_auth.py
git commit -m "feat: require invited MCP identities"
```

### Task 3: Define bounded wire models and the read-only service

**Files:**
- Create: `src/geo_index/mcp_models.py`
- Create: `src/geo_index/search_service.py`
- Create: `tests/test_mcp_models.py`
- Create: `tests/test_search_service.py`

**Interfaces:**
- Produces: `SearchMode`, `FacetFieldName`, `SearchFiltersInput`,
  `SearchDatasetsInput`, `GetDatasetInput`, `FacetValuesInput`,
  `DatasetSummary`, `DatasetDetail`, `SearchDatasetsOutput`,
  `GetDatasetOutput`, and `FacetValuesOutput`.
- Produces: `SearchService.open()`, `close()`, `search_datasets(...)`,
  `get_dataset(gse)`, `facet_values(...)`, `ping()`, and
  `retrieval_version_for(mode)`.
- Produces this immutable private profile:

  ```python
  from types import MappingProxyType


  RETRIEVAL_PROFILE_V1 = MappingProxyType({
      "fusion": "rrf-v1",
      "distance": "cosine",
      "deep": 200,
      "k0": 60,
      "facet_pool": 1000,
      "hnsw_m": 16,
      "hnsw_ef_construction": 64,
      "hnsw_ef_search": 100,
      "hnsw_iterative_scan": "relaxed_order",
  })
  ```
- This is a Track 4 compatibility definition over the existing Track 2 SQL.
  Embedding-plan Task 1 creates the canonical core profile, including a stable
  secondary-order policy that embedding-plan Task 4 implements; embedding-plan
  Task 6 replaces this local definition with that shared import after both
  tracks land.
- Produces: `SearchService.from_settings(settings)` without opening the pool or
  loading a model.

The service boundary is domain-only:

```python
def search_datasets(
    self, *, query: str, filters: SearchFilters, mode: str, limit: int
) -> SearchDatasetsOutput: ...

def facet_values(
    self,
    *,
    field: FacetField,
    query: str | None,
    filters: SearchFilters,
    mode: str,
    limit: int,
) -> FacetValuesOutput: ...
```

Pydantic input models are owned by the tool handlers, which call
`SearchFiltersInput.to_domain()` before invoking either method.

- [ ] **Step 1: Write strict model tests**

Tests must instantiate the actual Pydantic models and assert:

```python
import pytest
from pydantic import ValidationError

from geo_index.mcp_models import SearchDatasetsInput, SearchFiltersInput


def test_search_input_bounds_and_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query=" ", limit=15)
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query="x", limit=51)
    with pytest.raises(ValidationError):
        SearchFiltersInput(organism_ids=[], invented=["x"])
    with pytest.raises(ValidationError):
        SearchDatasetsInput(query="x", limit="5")


def test_filters_deduplicate_without_reordering():
    filters = SearchFiltersInput(
        organism_ids=["NCBITaxon:9606", "NCBITaxon:10090", "NCBITaxon:9606"]
    )
    assert filters.organism_ids == ["NCBITaxon:9606", "NCBITaxon:10090"]
```

Add cases for malformed ontology IDs, more than 20 filter values, invalid mode,
invalid GSE, numeric-string coercion, 8,001-character detail fields, 101-value
arrays, 257-character values, and stable `truncated_fields`.

- [ ] **Step 2: Implement the models**

Every input model sets `ConfigDict(strict=True, extra="forbid")`. Define mode
and facet names with `Literal[...]` so valid JSON strings remain valid under
strict mode. Use field validators to strip queries, normalize GSEs, deduplicate
filters, and validate NCBITaxon/PATO syntax. Implement
`SearchFiltersInput.to_domain() -> SearchFilters` as the only transport-to-Track
2 conversion. Output models must use concrete bounded fields rather than
`dict[str, Any]`.
`SearchDatasetsOutput` and `FacetValuesOutput` include:

```python
retrieval_version: str
embedding_variant: str | None
```

`embedding_variant` is `None` for BM25 and the configured key for dense/hybrid.
It is output-only.

Set `allow_inf_nan=False` on output models. `FacetValuesOutput` always carries
provenance: a blank query uses `retrieval_version="facet-all-matches-v1"` and
`embedding_variant=None`; a nonblank query follows BM25/dense/hybrid provenance.

- [ ] **Step 3: Write service lifecycle tests with fakes**

```python
from geo_index.search_models import SearchFilters, SearchResponse


def test_bm25_does_not_load_query_encoder(fake_pool, encoder_loader):
    def empty_search(conn, query, **kwargs):
        return SearchResponse(hits=(), facets={})

    service = SearchService(
        pg_dsn="postgresql://unused",
        embedding_variant="bge_small_v15",
        pool_factory=lambda **_: fake_pool,
        load_query_encoder=encoder_loader,
        search_with_facets=empty_search,
    )
    service.open()
    service.search_datasets(
        query="GSE1", filters=SearchFilters(), mode="bm25", limit=5
    )
    encoder_loader.assert_not_called()
    service.close()
```

Add tests proving: `from_settings` and the constructor perform no I/O; `open`
and `close` are idempotent; `open` loads the four closed facet vocabularies once;
unknown requested values fail before retrieval with an instruction to call
`facet_values`; a connection returns to the pool after success and failure;
every transaction executes
`SET LOCAL transaction_read_only = on` and
`SET LOCAL statement_timeout = '30s'`;
dense/hybrid load the active query encoder once; encoder calls are serialized by
a lock; `get_dataset` uses a SQL parameter; and `ping` executes only `SELECT 1`.
For a nonempty search, assert exactly one injected Track 2 retrieval call and
one batched hydration `SELECT`; an empty ranked list performs no hydration
query. Assert hydration preserves ranked order and never issues per-GSE SQL.
Assert `open()` verifies the baseline HNSW index uses cosine with effective
`m=16`/`ef_construction=64` (including absent options that mean pgvector
defaults), and dense/hybrid transactions execute `SET LOCAL hnsw.ef_search =
100`; Track 2 continues setting `iterative_scan=relaxed_order`.
Call `search_datasets` with `filters: SearchFilters` and `facet_values` with the
same domain type; neither service method accepts dicts or Pydantic models. Assert
baseline provenance is exactly `bm25-v1`,
`bge_small_v15:5c38ec7c405e:dense-v1`, or
`bge_small_v15:5c38ec7c405e:hybrid-v1`, while blank-query facets use
`facet-all-matches-v1` and null `embedding_variant`.
Assert every hidden retrieval argument comes from `RETRIEVAL_PROFILE_V1` and
that its effective HNSW settings match the SQL path. Treat that mapping as
immutable: a ranking- or facet-changing algorithm/tuning change introduces a
new profile/version rather than silently mutating v1.

- [ ] **Step 4: Implement `SearchService`**

The constructor performs no I/O. `open()` creates a bounded
`psycopg_pool.ConnectionPool(min_size=1, max_size=6, timeout=5,
max_waiting=24, open=True)`, calls `pool.wait(timeout=10)`,
then reads the distinct scalar values from the four array columns named by
Track 2's fixed `FACET_COLUMNS` registry into an immutable process cache. For
each registry entry, compose the identifier with `psycopg.sql.Identifier` and
execute the equivalent of:

```sql
SELECT DISTINCT item.value
FROM series AS s
CROSS JOIN LATERAL unnest(s.<whitelisted_array_column>) AS item(value)
WHERE item.value IS NOT NULL
```

The identifier always comes from `FACET_COLUMNS`, never from request data.
Add a fixture containing multi-value arrays and assert the cache contains
individual strings rather than array objects. `close()` closes the pool.
Validate every requested filter against that cache and raise a typed,
nonrevealing unknown-value error before retrieval. Dense/hybrid requests lazily
load one active query encoder behind a load lock and execute its `encode_query`
behind an inference lock. BM25 and blank-query facet browsing never touch the
encoder.

The explicit acquire/startup bounds follow psycopg's pool semantics: opening is
asynchronous with respect to filling `min_size` unless `wait()` is called, and
`timeout`/`max_waiting` bound request pressure
([psycopg pool API](https://www.psycopg.org/psycopg3/docs/api/pool.html)).

Until Track 49 replaces the compatibility path, the encoder loader must pass
`revision=BASELINE_QUERY_REVISION` for the pinned 40-character SHA above and
`local_files_only=True` in the hosted image. It must never resolve `main` at
runtime.

All service methods acquire one pool connection, begin a read-only transaction,
apply the profile's HNSW query settings for dense/hybrid,
delegate to Track 2, and convert domain results to the MCP output dictionaries.
The injected retrieval callable must match Track 2's existing keyword-only
contract exactly:

```python
class SearchWithFacets(Protocol):
    def __call__(
        self,
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
    ) -> SearchResponse: ...
```

This is the current Track 2/BGE contract. Track 49 Tasks 4 and 6 later replace
the legacy `model` keyword with `query_encoder`, add the keyword-only cached
`ready: ReadyEmbeddingVariant`, and bind that same ready object through nested
facet retrieval. Track 4 must not invent that storage/routing change early.

`search_datasets` calls it once with the domain `filters` argument supplied by
the tool handler, `qv` supplied for dense/hybrid and null for BM25, and
the versioned server-profile tuning constants. Then hydrate all ranked GSEs with one
parameterized query:

```sql
SELECT gse, title, summary, type, n_samples, pubmed_id,
       organism_ids, organism_status, sex_ids, sex_status,
       assay_categories, assay_labels, assay_status
FROM series
WHERE gse = ANY(%s::text[])
```

Reorder the returned mapping to the ranked GSE list in Python. For
`get_dataset` use one parameterized query that additionally selects
`overall_design`. For `facet_values`, call Track 2's existing `facet_counts`
with `fields=(field,)`, `bucket_limit=limit`, `retrieve=search_rows`, and the
same encoded `qv` policy; do not reimplement count SQL or prefix filtering.

Cap titles, snippets, details, arrays, facet values, and labels before Pydantic
validation. Never return `search_text`, an embedding vector, component ranks,
or full characteristics. Tests must prove a maximum of 50 summaries, 50 buckets
per facet, 100 normalized values per array, and deterministic sorted
`truncated_fields`.

- [ ] **Step 5: Run and commit**

```bash
uv run pytest tests/test_mcp_models.py tests/test_search_service.py -v
git add src/geo_index/mcp_models.py src/geo_index/search_service.py tests/test_mcp_models.py tests/test_search_service.py
git commit -m "feat: add bounded GEO MCP service"
```

### Task 4: Register the three tools and ASGI application

**Files:**
- Create: `src/geo_index/mcp_server.py`
- Create: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `create_mcp(settings, service, auth_provider=None) -> FastMCP`.
- Produces: `create_app(settings=None, service=None, auth_provider=None) -> ASGIApp`.
- Uvicorn calls `create_app` as an application factory; importing the module
  performs no I/O or required-environment validation.

- [ ] **Step 1: Write in-memory protocol tests**

Use FastMCP's client transport, which the framework documents for server tests
([testing guide](https://gofastmcp.com/servers/testing)):

```python
from fastmcp import Client


async def test_exact_tool_list(mcp):
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert [tool.name for tool in tools] == [
        "facet_values",
        "get_dataset",
        "search_datasets",
    ]


async def test_search_delegates_to_fake_service(mcp, fake_service):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_datasets",
            {
                "query": "single cell RNA",
                "filters": {"organism_ids": ["NCBITaxon:9606"]},
                "mode": "hybrid",
                "limit": 5,
            },
        )
    assert result.data["results"][0]["gse"] == "GSE123"
    assert result.data["retrieval_version"] == (
        "bge_small_v15:5c38ec7c405e:hybrid-v1"
    )
```

Also call `get_dataset` and `facet_values`, verify validation fails before the
fake service, and assert server instructions include the series-aggregation
caveat.

- [ ] **Step 2: Build FastMCP with lifespan-managed service**

FastMCP lifespans run setup/cleanup around the server lifecycle, and its
rate-limiting middleware uses a token bucket with configurable rate and burst
capacity
([lifespans](https://gofastmcp.com/servers/lifespan),
[middleware](https://gofastmcp.com/servers/middleware)).

```python
from fastmcp import Context, FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.lifespan import lifespan
from fastmcp.server.middleware import AuthMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware


def create_mcp(
    settings: McpSettings,
    service: SearchService,
    auth_provider: AuthProvider | None = None,
) -> FastMCP:
    @lifespan
    async def app_lifespan(server):
        service.open()
        try:
            yield {"service": service}
        finally:
            service.close()

    auth = auth_provider or create_auth(settings)
    mcp = FastMCP(
        "GEO Metadata Index",
        instructions=INSTRUCTIONS,
        auth=auth,
        lifespan=app_lifespan,
        middleware=[
            AuthMiddleware(
                auth=require_invited_subject(settings.allowed_subjects)
            ),
            RateLimitingMiddleware(
                max_requests_per_second=settings.rate_per_second,
                burst_capacity=settings.burst_capacity,
            ),
        ],
        strict_input_validation=True,
        mask_error_details=True,
        on_duplicate="error",
    )
```

Register three synchronous tool functions with the stable arguments in this
plan. Decorate `search_datasets` and `facet_values` with
`@mcp.tool(timeout=60.0)` and `get_dataset` with
`@mcp.tool(timeout=15.0)`. Give every tool
`ToolAnnotations(readOnlyHint=True, idempotentHint=True,
openWorldHint=False)`. Each function constructs its strict input model,
calls the service from `ctx.lifespan_context["service"]`, and validates the
returned output model. Convert known invalid-filter errors to concise
`ToolError` messages. Never catch and expose unexpected exception text. Add a
protocol test proving `limit="5"` is rejected rather than coerced. After
registering the tools, call `_register_health_routes(mcp, service)` and
`return mcp`.
FastMCP documents strict input validation, explicit per-tool timeouts, structured
Pydantic output, error masking, and read-only annotations in its
[tool guide](https://gofastmcp.com/servers/tools). FastMCP v3 consolidated the
old per-component duplicate settings into the constructor's `on_duplicate`
argument; retain a version-pinned constructor test so an API change fails during
CI rather than deployment
([v3 upgrade guide](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-2)).

The handlers are the sole transport/domain boundary: `search_datasets` builds
`SearchDatasetsInput` and passes `request.filters.to_domain()`;
`facet_values` does the same with `FacetValuesInput`; `get_dataset` passes only
the normalized GSE from `GetDatasetInput`. Protocol tests assert the fake
service receives `SearchFilters`, never a dict or Pydantic object, and assert the
exact top-level output keys fixed in the v1 contract.

- [ ] **Step 3: Add a nonrevealing health route and ASGI factory**

```python
import os

import asyncio

from starlette.responses import JSONResponse


def _register_health_routes(mcp: FastMCP, service: SearchService) -> None:
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def readyz(request):
        try:
            await asyncio.to_thread(service.ping)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})


def create_app(settings=None, service=None, auth_provider=None):
    settings = settings or McpSettings.from_env(os.environ)
    service = service or SearchService.from_settings(settings)
    mcp = create_mcp(settings, service, auth_provider)
    return mcp.http_app(
        path=MCP_PATH,
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )

```

Run Uvicorn with `--factory` so `create_app` is called only when the hosted
process starts. The factory may parse configuration and instantiate unopened
objects, but must not connect to Postgres, fetch JWKS, or load a model before
FastMCP's lifespan starts. Uvicorn documents `--factory` as the application-
factory mode ([deployment CLI](https://www.uvicorn.org/deployment/)).

- [ ] **Step 4: Run and commit**

```bash
uv run pytest tests/test_mcp_server.py -v
git add src/geo_index/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: expose GEO tools with FastMCP"
```

### Task 5: Test the real HTTP authentication boundary

**Files:**
- Create: `tests/test_mcp_http.py`

**Interfaces:**
- Consumes: `create_app` with a fake service and a production-shaped
  `RemoteAuthProvider` wrapping an offline test `JWTVerifier`.
- Proves: Streamable HTTP, protected-resource discovery, token validation,
  invitation, Host/Origin protection, and structured tool calls work together.

- [ ] **Step 1: Create offline RSA tokens**

Use FastMCP's `RSAKeyPair` test helper
([token-verification guide](https://gofastmcp.com/servers/auth/token-verification)):

```python
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

ISSUER = "https://issuer.test/"
AUDIENCE = "geo-mcp-test"


def token(
    key_pair,
    subject,
    *,
    issuer=ISSUER,
    audience=AUDIENCE,
    scopes=("geo:read",),
    expires_in_seconds=300,
):
    return key_pair.create_token(
        subject=subject,
        issuer=issuer,
        audience=audience,
        scopes=list(scopes),
        expires_in_seconds=expires_in_seconds,
    )
```

Build the test app with a `RemoteAuthProvider` whose `token_verifier` is
`JWTVerifier(public_key=key_pair.public_key, issuer=ISSUER,
audience=AUDIENCE, required_scopes=["geo:read"])`, and use
`allowed_subjects={"invited-user"}`. Give it the same test authorization-server
URL and public base URL as settings. This exercises the production provider
shape and discovery routes without making network or database calls.

- [ ] **Step 2: Exercise ASGI Streamable HTTP**

Use `with TestClient(app) as client` for raw ASGI boundary checks so the real
factory's lifespan runs. Assert:

- no bearer token returns 401;
- a token signed by another key returns 401;
- wrong issuer, audience, expiry, or missing `geo:read` returns 401/403;
- `GET /.well-known/oauth-protected-resource` returns resource metadata that
  has exact `resource == settings.mcp_url`, the configured
  `authorization_servers`, and `scopes_supported == ["geo:read"]`; assert
  `settings.mcp_url` is the actual `MCP_PATH` route served by the test client,
  not a prefixed alias; also verify
  the path-scoped `/.well-known/oauth-protected-resource/mcp` alias or the exact
  metadata URL advertised by the 401 `WWW-Authenticate` header;
- the exact configured `Host` and browser `Origin` are accepted;
- `Host: attacker.example` is rejected;
- `Origin: https://attacker.example` is rejected;
- `GET /healthz` returns only `{"status":"ok"}`;
- `GET /readyz` returns only `{"status":"ready"}` after fake `ping` and a
  nonrevealing 503 `{"status":"unavailable"}` when `ping` fails;
- captured logs contain neither token strings, `GEO_PG_DSN`, nor the sentinel
  raw query `SENTINEL-RAW-QUERY-9f7c`.

For actual MCP protocol behavior, run the exact `create_app(...)` result under
an in-process `uvicorn.Server` on pytest-asyncio's `unused_tcp_port`. Await
`server.started` with a bounded timeout and always set `server.should_exit` and
await its task in `finally`. Connect `StreamableHttpTransport` with bearer auth
and the exact allowed `Host` header. This path exercises
`stateless_http=True` and Host/Origin configuration from the ASGI factory rather
than FastMCP's stateful test-server defaults. With a valid invited token,
initialize, list exactly three tools, call
`get_dataset`, and issue the sentinel search. Assert a numeric string such as
`limit="5"` fails strict validation before the fake service and that an
unexpected fake-service exception is masked. Repeat one request with an
uninvited token: authentication/initialization may succeed, but the invite
middleware must hide all three tools and reject a direct tool call before the
fake service. Prove two separately initialized clients can call without sharing
an MCP session ID, and do not turn off `host_origin_protection` to make the test
pass.

Keep exact expected status codes aligned with FastMCP 3.4.4 during implementation;
the semantic assertion is authentication failure before any service call.

- [ ] **Step 3: Run and commit**

```bash
uv run pytest tests/test_mcp_http.py -v
git add tests/test_mcp_http.py
git commit -m "test: cover remote MCP authentication"
```

### Task 6: Package a provider-neutral single-worker deployment

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `deploy/geo-mcp.env.example`
- Modify: `README.md`

- [ ] **Step 1: Add a non-root application image**

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/opt/huggingface

# Track 4 is independently deployable with the current baseline. Track 49
# replaces this fixed prefetch with the selected registry variant.
ARG BGE_QUERY_REVISION=5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
RUN mkdir -p "$HF_HOME" \
    && /app/.venv/bin/python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5', revision=os.environ.get('BGE_QUERY_REVISION', '5c38ec7c405ec4b44b94cc5a9bb96e735b38267a'), cache_folder=os.environ['HF_HOME'])" \
    && chmod -R a=rX "$HF_HOME"

LABEL org.geo-metadata-index.embedding-variant="bge_small_v15" \
      org.geo-metadata-index.query-revision="$BGE_QUERY_REVISION"

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "geo_index.mcp_server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

The application image contains the public baseline model cache so dense/hybrid
requests never require a writable home directory or runtime model download. It
does not contain database data, OAuth client secrets, tokens, DSNs, or a mutable
`.env` file. The host provides those at runtime. Track 49 replaces the baseline
prefetch only after a candidate is promoted.
Use the virtual environment's Python directly during the image build so
`uv run` cannot resynchronize the default development dependency group
([uv dependency groups](https://docs.astral.sh/uv/concepts/projects/dependencies/)).

- [ ] **Step 2: Add safe build exclusions and environment example**

`.dockerignore` excludes `.git`, `.env*`, `data`, `eval/results`, Python caches,
test caches, Obsidian state, and local model caches.

`deploy/geo-mcp.env.example` contains all names from the configuration table with
non-secret example URLs and IDs. It must use
`GEO_PG_DSN=postgresql://geo_reader:replace-me@postgres:5432/geo` and must not
contain a real hostname, subject, token, or password.

- [ ] **Step 3: Document hosting and invitations**

Document:

1. create a database role limited to `CONNECT`, schema `USAGE`, and `SELECT` on
   `series` and normalization lookup tables;
2. configure `default_transaction_read_only=on` for that role;
3. configure an OAuth audience and a JWKS-capable authorization server; register
   supported MCP clients and their redirect URIs there;
4. set the resource server's exact public HTTPS base URL, allowed host, and
   invited `sub` values;
5. deploy one container behind an HTTPS reverse proxy or managed TLS ingress;
6. keep PostgreSQL private and expose only HTTPS;
7. invite by adding a stable subject and restarting the service; revoke by
   removing it and restarting;
8. connect interactively with
   `fastmcp list https://geo.example.org/mcp --auth oauth`, or pass an
   independently obtained bearer token for script clients
   ([FastMCP bearer client](https://gofastmcp.com/clients/auth/bearer)).

Do not select a cloud, DNS provider, TLS proxy, or identity vendor in this plan.

- [ ] **Step 4: Build and inspect the image**

```bash
docker build -t geo-mcp:local .
docker image inspect geo-mcp:local --format '{{json .Config.User}}'
```

Expected: build succeeds and the configured user is `65532:65532`.
Run the image with a disposable test configuration and prove the non-root user
can read the model cache. Against the opt-in test database, exercise both BM25
and dense/hybrid requests with outbound Hugging Face access blocked; neither may
attempt a download.

- [ ] **Step 5: Commit packaging**

```bash
git add Dockerfile .dockerignore deploy/geo-mcp.env.example README.md
git commit -m "docs: package private remote MCP service"
```

### Task 7: Run live read-only and remote smoke tests

**Files:**
- Create: `tests/test_mcp_db_smoke.py`
- Modify: `wiki/27-MCP-Interface.md`
- Modify: `wiki/42-Build-Log.md` only after a real deployment is exercised
- Modify: `wiki/99-Sources.md`

- [ ] **Step 1: Add an opt-in database protocol smoke**

Gate on `GEO_TEST_PG=1`. With a real `SearchService` and in-memory FastMCP client:

1. call `search_datasets` in BM25 mode;
2. retrieve the first accession through `get_dataset`;
3. call `facet_values(field="organism_ids")`;
4. assert the GSEs agree and at least one facet bucket is returned.

Do not assert exact corpus counts and do not download an embedding model.

- [ ] **Step 2: Verify offline and live suites**

```bash
uv run pytest tests/test_mcp_settings.py tests/test_mcp_auth.py tests/test_mcp_models.py tests/test_search_service.py tests/test_mcp_server.py tests/test_mcp_http.py -v
uv run pytest -v
GEO_TEST_PG=1 uv run pytest tests/test_mcp_db_smoke.py -v
```

Expected: offline tests pass without Postgres, network, or model downloads; the
opt-in smoke uses the read-only hosted-role contract.

- [ ] **Step 3: Verify the hosted endpoint**

From outside the host:

```bash
curl --fail https://geo.example.org/healthz
curl --fail https://geo.example.org/readyz
fastmcp list https://geo.example.org/mcp --auth oauth
fastmcp call https://geo.example.org/mcp get_dataset gse=GSE1 --auth oauth
```

Repeat the MCP command with an uninvited identity and confirm denial. Revoke the
invited subject, restart, and confirm its still-valid token is denied on the next
request. Confirm the MCP endpoint is HTTPS-only and PostgreSQL is unreachable
from the public network.

- [ ] **Step 4: Record only measured deployment facts**

After the real smoke, add the deployed FastMCP version, endpoint hostname class
(not secrets), active retrieval version, database/index checks, and observed
latency to [[42-Build-Log]]. Do not record tokens, user subjects, DSNs, or raw
queries.

- [ ] **Step 5: Commit smoke coverage and documentation**

```bash
git add tests/test_mcp_db_smoke.py README.md wiki/27-MCP-Interface.md wiki/42-Build-Log.md wiki/99-Sources.md
git commit -m "test: verify private remote MCP service"
```

## Definition of done

- A coworker can connect to the HTTPS Streamable HTTP endpoint and discover
  exactly three tools.
- Missing, invalid, insufficient-scope, and valid-but-uninvited tokens fail
  before a search service call.
- Invitation is based on a verified stable `sub` claim and can be revoked by
  configuration.
- The server publishes OAuth protected-resource discovery through
  `RemoteAuthProvider` and validates JWTs through JWKS.
- The ASGI app is stateless, single-worker, bounded, rate-limited, and
  Host/Origin protected.
- Pool startup/acquisition is bounded; `/healthz` is liveness-only and
  `/readyz` verifies a nonrevealing database ping.
- The runtime database role is read-only; all user values are parameterized.
- BM25 never loads an embedding model; dense/hybrid load only the configured
  model once and report the retrieval version.
- Offline tests need no database, model download, authorization server, or
  network.
- The image runs as a non-root user and contains no corpus or credentials.
- README and [[27-MCP-Interface]] describe the deployed behavior accurately.

## Explicitly deferred

- Public/anonymous access and self-service invitations.
- Multiple application replicas, shared distributed rate limits, and shared
  OAuth-proxy token storage.
- An in-repository OAuth authorization server or support for two auth stacks.
- Admin/write tools, per-role permissions, usage billing, and a public API.
- `expand_terms`, `resolve_ontology`, non-GSE lookup, raw SOFT/GSM/SRA access,
  hierarchy traversal, and tissue filters.
- Public model selection, reranking, server-side LLM calls, and automatic model
  promotion.

## Sources

- FastMCP published package — https://pypi.org/project/fastmcp/
- FastMCP v3 duplicate-handling constructor migration — https://gofastmcp.com/getting-started/upgrading/from-fastmcp-2
- Pinned BGE baseline query-model snapshot — https://huggingface.co/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
- Running FastMCP and Streamable HTTP — https://gofastmcp.com/deployment/running-server
- HTTP/ASGI deployment, Host/Origin protection, and stateless mode — https://gofastmcp.com/deployment/http
- FastMCP 3.4.4 Host/Origin guard default and explicit opt-in — https://github.com/PrefectHQ/fastmcp/pull/4472
- JWT/JWKS and development-only static tokens — https://gofastmcp.com/servers/auth/token-verification
- Remote OAuth discovery — https://gofastmcp.com/servers/auth/remote-oauth
- Server-wide authorization checks — https://gofastmcp.com/servers/authorization
- Lifespan-managed resources — https://gofastmcp.com/servers/lifespan
- Rate-limiting middleware — https://gofastmcp.com/servers/middleware
- Strict validation, structured output, timeouts, and tool annotations — https://gofastmcp.com/servers/tools
- Psycopg connection-pool startup, acquisition timeout, and waiting bounds — https://www.psycopg.org/psycopg3/docs/api/pool.html
- uv project and dependency-group behavior — https://docs.astral.sh/uv/concepts/projects/dependencies/
- Uvicorn application-factory and deployment options — https://www.uvicorn.org/deployment/
- FastMCP server testing — https://gofastmcp.com/servers/testing
- Bearer-token clients — https://gofastmcp.com/clients/auth/bearer
