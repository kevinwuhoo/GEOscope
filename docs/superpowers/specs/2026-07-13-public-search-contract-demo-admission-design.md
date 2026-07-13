# Public Search Contract and Demo Admission Design

**Date:** 2026-07-13
**Status:** Approved

## Goal

Give hackathon judges one simple production search behavior: callers describe
the GEO studies they want, and GEOscope chooses the retrieval strategy. Remove
the public retrieval-mode selector from MCP, the marketing API, and the
frontend while retaining internal modes for evaluation and debugging. Relax
the shared anonymous demo admission limits enough for judging without making
the service unbounded.

## Public contract

`search_datasets` accepts `query`, optional `filters`, and optional `limit`.
`facet_values` accepts `field`, optional `query`, optional `filters`, and
optional `limit`. Neither MCP tool advertises or accepts `mode` because FastMCP
strict input validation rejects undeclared arguments.

`GET /api/demo/search` accepts `q` and `limit`. Its OpenAPI contract and the
frontend client do not send or describe `mode`. FastAPI may ignore a legacy
`mode` query parameter, but it has no effect.

The public search output removes the standalone `mode` property. Existing
`retrieval_version` and `embedding_variant` provenance remain so operators can
identify the implementation that produced a result without turning it into a
caller-controlled option.

## Retrieval behavior

`McpSearchService` is the shared policy boundary used by MCP and the marketing
API. Every nonblank dataset search uses hybrid retrieval. Every query-scoped
facet request also uses hybrid retrieval.

A blank `facet_values` request is not a ranked text search: it requests counts
from the matching filtered corpus. It therefore uses the existing unranked,
filter-only facet aggregation and does not create a query embedding. The
Elasticsearch implementation may continue to route that operation through its
internal BM25 branch, but no public request or response exposes that detail.

Low-level Elasticsearch, evaluation, comparison, and command-line interfaces
retain explicit BM25, dense, and hybrid modes. They are diagnostic and research
surfaces, not public consumer contracts.

## Admission controls

The service keeps its process-wide token bucket and concurrency semaphore. The
hackathon defaults and deployment configuration become:

- sustained rate: 100 requests per second;
- burst capacity: 100 requests;
- maximum concurrent requests: 20;
- maximum JSON-RPC request body: 256 KB;
- request-body read timeout: 10 seconds.

These controls are global to the single Uvicorn worker, not per user, because
the public demo is anonymous. They provide coarse abuse and overload bounds;
they do not provide identity-based quotas. The 256 KB cap remains useful even
though a query is limited to 1,000 characters because it bounds the entire
JSON-RPC envelope, filters, and any malformed or adversarial payload.

The settings remain configurable through environment variables so production
operators can lower the values without code changes. Environment examples,
DigitalOcean configuration, and current deployment documentation use the new
hackathon values.

## Compatibility and errors

- An MCP client sending `mode` receives a normal invalid-arguments tool error.
- A legacy marketing URL containing `mode` continues to work, but the parameter
  is ignored and hybrid retrieval is used.
- Invalid filters, query bounds, limit bounds, masked service errors, request
  body limits, and concurrency rejection retain their existing behavior.
- Search results remain structured and bounded; only the public `mode` field is
  removed.

## Implementation boundaries

The change belongs in the shared MCP/Elasticsearch orchestration layer so MCP
and the marketing demo cannot diverge. Expected touch points are:

- MCP input/output models and tool registration;
- `McpSearchService` method signatures and fixed production routing;
- marketing API contract and frontend client types/calls;
- MCP settings and HTTP admission defaults;
- deployment environment templates and user-facing documentation.

Unrelated frontend work already present in the working tree must be preserved.

## Verification

Implementation will proceed test-first. Focused coverage will prove:

1. MCP `tools/list` schemas contain no `mode` input or output property.
2. MCP calls with `mode` fail strict validation before reaching the service.
3. Dataset and query-scoped facet searches invoke hybrid retrieval internally.
4. Blank facet browsing performs filter-only aggregation without initializing
   or calling the query encoder.
5. `/api/demo/search` exposes no mode selector and delegates to the shared
   service contract.
6. The frontend request and response schemas contain no `mode` field or query
   parameter.
7. Settings and deployment manifests use 100 requests/second, burst 100, and
   concurrency 20 while preserving the 256 KB body cap.
8. Existing MCP HTTP safety, search-service, production-app, marketing API, and
   frontend suites remain green.

