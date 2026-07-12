# Elasticsearch MCP Migration Design

**Status:** Approved 2026-07-12

## Goal

Restore the private hosted GEO MCP service from the retained
`codex/remote-mcp-first-draft` branch on top of current `main`, while replacing
its PostgreSQL composition and retrieval implementation with the repository's
primary Elasticsearch stack. The service must preserve the existing bounded,
read-only three-tool contract and hosted security posture.

## Scope

The migration preserves:

- exactly `search_datasets`, `get_dataset`, and `facet_values`;
- Streamable HTTP at `/mcp`, stateless transport, and one application worker;
- JWT/JWKS verification, the `geo:read` scope, and stable-subject invitation
  allowlisting;
- Host and Origin protection, bounded request bodies, request-rate and
  concurrency controls, safe logging, health and readiness routes;
- bounded Pydantic request and response models, deterministic truncation, and
  retrieval provenance;
- Docker packaging and environment-only secrets.

The migration replaces all PostgreSQL service dependencies, settings, smoke
tests, and runbook instructions in the MCP path. Historical PostgreSQL modules
remain in the repository for evaluation and are not modified by this work.

## Architecture

The MCP transport remains independent from retrieval. A focused
`McpSearchService` adapter owns the hosted-service lifecycle and converts the
backend-neutral Elasticsearch domain results into the existing MCP wire models.
It composes:

1. `ElasticsearchSettings` and the official Elasticsearch client;
2. the fixed deployment-selected query encoder;
3. `ElasticsearchSearchService` for exact, BM25, dense, hybrid, filter, and
   facet behavior;
4. `index_readiness()` for fail-closed startup and readiness checks.

The MCP server depends on a small service protocol rather than on the concrete
adapter. Tests can therefore inject a fake service without Elasticsearch,
network access, or model downloads. The core Elasticsearch service remains
independent from FastMCP and MCP response models.

## Configuration and lifecycle

`McpSettings` combines hosted MCP settings with the existing validated
Elasticsearch connection settings. `GEO_PG_DSN` and `GEO_EMBEDDING_VARIANT`
are removed from the MCP configuration. Elasticsearch uses:

- `ELASTICSEARCH_URL`;
- exactly one of basic credentials or `ELASTICSEARCH_API_KEY`;
- `ELASTICSEARCH_ACTIVE_MODEL`, defaulting to
  `gemini_embedding_2_3072_v1` as required by the approved primary-cutover
  design;
- bounded request timeout and retry settings.

Python imports perform no database, network, credential-discovery, or model
I/O. `create_app()` parses settings and constructs resources, while the FastMCP
lifespan calls `service.open()` before serving and `service.close()` on every
shutdown path. Opening validates Elasticsearch connectivity, index existence,
mapping revision, and the configured active vector field. The query encoder is
lazy: BM25 and exact lookup do not initialize Gemini, while dense and hybrid
search initialize it on first use. Close releases both encoder and client.

## MCP search adapter

`McpSearchService.search_datasets()` validates the bounded request, validates
closed-vocabulary filter values, calls `ElasticsearchSearchService.search()`,
and maps hits and facets into `SearchDatasetsOutput`. The adapter preserves the
backend's deterministic hit order and reports a retrieval version derived from
the Elasticsearch mapping revision, active model, vector field, and mode.
BM25 outputs no embedding variant; dense and hybrid outputs report the active
model key.

`get_dataset()` uses Elasticsearch's exact GSE lookup and returns a bounded
`DatasetDetail`. `facet_values()` uses the same Elasticsearch search behavior:
blank queries return all-match disjunctive aggregations, while nonblank queries
return bounded candidate-pool facets. No endpoint accepts raw Query DSL,
dynamic field names, index names, or model selectors.

The adapter caps titles, snippets, summaries, designs, arrays, facet values,
labels, and result counts before strict Pydantic validation. Missing optional
Elasticsearch fields become `None` or empty arrays. `pubmed_ids` is converted to
the v1 singular `pubmed_id` only when it contains one valid positive integer;
the detailed record still links deterministically to GEO and PubMed.

## Filter validation and errors

At startup the adapter loads each fixed facet field's vocabulary with bounded
Elasticsearch terms aggregations and validates every value against the MCP input
contract. A requested value outside that closed vocabulary raises
`UnknownFilterValueError`; the tool layer returns the existing nonrevealing
instruction to call `facet_values`. Backend, authentication, and unexpected
errors remain masked by FastMCP. Logs never contain bearer tokens, credentials,
raw search queries, filter values, or returned study text.

Readiness returns HTTP 503 until the service is open and the live Elasticsearch
readiness contract succeeds. Health remains a process-liveness check and does
not contact Elasticsearch.

## Packaging

The MCP files, FastMCP and Uvicorn dependencies, Dockerfile, `.dockerignore`, and
safe example environment file are selectively ported from the retained branch.
The runtime image contains the application and required query-encoder runtime,
but no PostgreSQL client configuration or MCP database DSN. The example file
contains variable names and placeholder values only.

## Testing

The implementation follows red-green TDD and keeps the full offline suite free
of Elasticsearch, PostgreSQL, model downloads, and provider calls. Tests prove:

- settings fail closed, hide secrets from representations, default to the
  primary Gemini model, and reject conflicting credentials;
- the adapter opens and closes resources, validates readiness and vocabularies,
  keeps BM25 encoder-free, maps exact/search/facet responses, caps all output,
  preserves provenance, and rejects unknown filters;
- FastMCP registers exactly the three intended tools and delegates through an
  injected service protocol;
- JWT invitation checks, HTTP admission controls, safe errors, health, and
  readiness behavior remain intact;
- Docker and environment packaging contain the required files and no
  PostgreSQL MCP configuration;
- an opt-in `GEO_TEST_ELASTIC=1` smoke exercises all three MCP tools against the
  configured live `geo-series` index.

Acceptance requires the focused MCP tests and the complete offline repository
suite to pass. If a configured live Elasticsearch instance is available, the
opt-in smoke is also run and reported separately.

## Non-goals

This migration does not change the public three-tool schema, add a server-side
LLM or reranker, expose model selection to callers, provision managed Elastic,
delete PostgreSQL history, change the canonical record schema, or overlap the
separate primary app/web composition work beyond consuming its stable
Elasticsearch boundaries.
