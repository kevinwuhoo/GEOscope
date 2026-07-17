# Search observability design

## Purpose

Make the latency and outcome of every online GEO search observable in the
shared search runtime. The same structured events must cover the public
marketing API and the MCP transport, so the data identifies slow pipeline
stages without creating transport-specific search behavior.

## Event model

The service will write one JSON `search.completed` event when a
`search_datasets` operation completes, including degraded and failed-open
paths. Every event has an explicit UTC RFC 3339 `timestamp`, a server-generated
`request_id`, and `total_ms`; it does not rely on the App Platform viewer's
display timestamp.

Search events contain:

- request context: transport, method, route, raw forwarded client IP, direct
  peer IP, user-agent, referer, and accept-language;
- search input: the normalized query verbatim, normalized filters, and limit;
- outcome: result count, candidate counts, exact-accession state, retrieval
  version/model, reranker state/model/token use, and degradation categories;
- stage timings in milliseconds: validation, query embedding, Elasticsearch
  retrieval, facet generation, document hydration, NCBI search, candidate
  merge, reranking, result formatting, and total elapsed time.

Exact GSE lookups log their applicable local lookup and NCBI fallback timings
instead of stages that the deterministic exact route does not perform.

The production ASGI application will additionally log lightweight
`request.completed` events for requests that do not invoke a search. They have
the request context, status, and total duration but never include request or
response bodies.

## Privacy and trust boundaries

The request context intentionally retains raw client-address data for debugging
and analytics. When an `X-Forwarded-For` header is present, the event records
the forwarded value and the direct ASGI peer separately, making the header's
provenance explicit rather than silently treating it as equivalent to the
socket peer.

Events must exclude cookies, authorization and other secret-bearing headers,
API keys, complete MCP request payloads, provider response text, and result
contents. Only the search query and normalized search filters are retained from
the request body.

## Implementation boundaries

A small shared timing collector records stage durations with a monotonic clock
and is passed through the shared MCP/Elasticsearch search path. The existing
coarse `SearchLatencyOutput` response contract remains compatible; detailed
breakdowns are emitted only to logs.

An ASGI middleware establishes request context and logs HTTP completion. The
shared `McpSearchService` emits the search event, so all consumers inherit the
same timing and outcome fields. The Elasticsearch service exposes optional
instrumentation around query embedding, primary retrieval, and facet work;
the search adapter instruments hydration, source union, reranking, and result
formatting. Tests use deterministic clocks and captured log records to verify
stage coverage, field redaction, and behavior for marketing, MCP, natural
language, exact-accession, and degraded paths.

## Operations

App Platform Runtime Logs can be used immediately to inspect the JSON events.
The explicit stable fields permit later forwarding to Managed OpenSearch or
another log provider for retention, aggregate queries, and latency percentiles
without changing the application event format.
