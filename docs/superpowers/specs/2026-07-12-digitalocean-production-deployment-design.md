# DigitalOcean Production Deployment Design

**Status:** Approved 2026-07-12

## Goal

Deploy the complete GEOscope corpus as a public hackathon service at
`https://geoscope.kevinformatics.com`. The deployment must expose the marketing
site, browser-safe search API, and anonymous Streamable HTTP MCP endpoint while
keeping Elasticsearch private, persistent, and independently operable.

The design optimizes for low operational burden rather than high availability.
The application runs on DigitalOcean App Platform; one dedicated DigitalOcean
Droplet runs the official Elasticsearch container. Kamal and an application
Droplet are not part of the approved design.

## Architecture

```text
Internet
   |
   | HTTPS: geoscope.kevinformatics.com
   v
DigitalOcean App Platform (sfo)
one stateless service, one container, one Uvicorn worker
   |-- /                  React production build
   |-- /api/*             browser-safe FastAPI API
   |-- /mcp               anonymous FastMCP Streamable HTTP
   |-- /healthz           process liveness
   `-- /readyz            Elasticsearch/index readiness
   |
   | private VPC connection
   v
Elasticsearch Droplet (sfo3)
4 shared vCPU, 8 GiB RAM, 160 GiB SSD
official Elasticsearch 9.4.2 container, single node
```

App Platform and the Elasticsearch Droplet attach to the same `sfo3` VPC.
`ELASTICSEARCH_URL` uses the Droplet's private VPC address. Elasticsearch port
9200 is never reachable from the public internet.

## App Platform service

App Platform builds one production Dockerfile from the repository and runs one
combined ASGI application. FastAPI owns the marketing site, `/api` routes,
health routes, and static asset fallback. FastMCP is mounted as a sub-application
at `/mcp` with its lifespan combined with the FastAPI lifespan. The two surfaces
share one `McpSearchService`, Elasticsearch client, and lazy Gemini query
encoder.

The service starts on the `apps-s-1vcpu-0.5gb` plan: one shared vCPU and 512 MiB
RAM. It moves to the fixed 1 GiB plan only if App Platform metrics or an
out-of-memory restart show the smaller plan is insufficient. The container
runs one Uvicorn worker; horizontal scaling and multiple workers are out of
scope for the hackathon deployment.

The App Platform specification is committed to the repository and configures:

- the `sfo` App Platform region and the `sfo3` VPC UUID;
- the custom domain `geoscope.kevinformatics.com`;
- a redirect from the App Platform starter domain to the custom domain;
- one HTTP service receiving the full path without prefix stripping;
- `disable_edge_cache: true` for MCP and live API behavior;
- `/healthz` as the platform liveness check;
- a shutdown grace period long enough for bounded in-flight tool calls;
- encrypted run-time secrets; and
- deployment alerts for build, deployment, domain, and health failures.

`/readyz` is not the platform liveness check. An Elasticsearch outage makes the
service unready but must not cause App Platform to restart an otherwise healthy
application repeatedly.

The App Platform filesystem is treated as ephemeral. The application writes no
corpus data, indexes, credentials, or required state to local disk.

## Production Python and image boundary

The online service needs only the web/MCP framework, Elasticsearch client,
Google GenAI query client, HTTP client, validation models, and lightweight
numeric support. The production dependency set excludes:

- PyTorch, Transformers, Sentence Transformers, and Hugging Face tooling;
- Prefect and the corpus ETL pipeline;
- PostgreSQL and pgvector clients;
- local document-embedding builders and model downloads; and
- raw, canonical, or embedding-artifact corpus files.

Prefect remains an offline orchestration choice for corpus construction, not a
production serving dependency. Dense and hybrid requests create only query
embeddings through the Gemini API. The production image target is below App
Platform's recommended 1 GiB image size.

## Public API and MCP security

The MCP endpoint is intentionally anonymous for the hackathon. OAuth/JWT,
issuer, audience, invitation subject allowlisting, and authorization-server
configuration are removed from the production MCP path. Anonymous does not
mean unbounded.

The existing read-only MCP contract remains exactly:

- `search_datasets`;
- `get_dataset`; and
- `facet_values`.

The service preserves strict Pydantic inputs, closed field names, result caps,
request-body limits, timeouts, concurrency limits, masked errors, sensitive-log
filtering, and Host/Origin protection. The browser API uses similarly bounded
request and response models and never accepts Elasticsearch Query DSL, index
names, model selectors, or credentials.

Browser demo searches and all MCP requests share an initial process-wide limit
of one admitted request per second, a burst of five, and at most four concurrent
requests. The existing 256 KiB MCP body ceiling remains in force. These values
are environment-configurable but may only be raised after measuring latency,
memory, and Gemini usage. The single-process limiter is sufficient for the
fixed one-instance deployment. Provider-side Gemini quotas are also configured
at no more than 60 query-embedding requests per minute and 5,000 per day to cap
anonymous abuse; budget alerts alone are not treated as a hard spending limit.
If the app later scales to multiple instances, rate limiting must move to a
shared or edge-enforced mechanism before scaling.

Only the custom domain, App Platform health traffic, and explicitly required
MCP client origins are accepted. Normal server-to-server MCP clients may omit
`Origin`; an arbitrary supplied browser origin is not implicitly trusted.

App Platform sets `ELASTICSEARCH_USERNAME=elastic` as run-time configuration
and stores `ELASTICSEARCH_PASSWORD` and `GEMINI_API_KEY` as encrypted run-time
secrets. Neither secret is available to the React bundle or returned by health,
readiness, API, or MCP responses.

## Elasticsearch host

Elasticsearch receives a dedicated 4-vCPU, 8-GiB, 160-GiB Droplet in `sfo3`.
It runs the official pinned `docker.elastic.co/elasticsearch/elasticsearch:9.4.2`
image under Docker Compose. Docker does not impose CPU or memory limits, so the
node may use every host vCPU and the host's available memory.

The production composition differs from the local composition in these ways:

- index data is bind-mounted from `/srv/elasticsearch/data`;
- Elasticsearch listens on host loopback and the private VPC address, never on
  the public interface;
- JVM sizing uses a production `jvm.options.d` file rather than `ES_JAVA_OPTS`;
- heap is fixed initially at 4 GiB, leaving the remainder for Lucene filesystem
  cache, off-heap buffers, Docker, and the kernel;
- no Compose CPU or memory limits are set;
- swap is disabled and the required Elasticsearch host kernel settings and
  file limits are applied persistently;
- memory locking and container `memlock` limits are configured where supported;
- container logs use bounded rotation;
- security and the `elastic` password remain enabled; and
- the existing cluster and index health check remains fail-closed.

The single-node trial license is acceptable for the time-bounded hackathon and
the current native RRF implementation. License expiration is monitored because
the application must not silently lose licensed retrieval behavior. A durable
post-hackathon license or an application-side fusion replacement is a separate
decision.

## Network controls

The App Platform service connects to the `sfo3` VPC and reaches Elasticsearch
at its private address. The Droplet's DigitalOcean Cloud Firewall allows:

- TCP 9200 only from the App Platform VPC egress private IP;
- TCP 22 only from the administrator's current trusted IP range; and
- required outbound HTTPS and DNS for host maintenance and image pulls.

The host firewall mirrors the important inbound restrictions. Elasticsearch
basic authentication remains required even on the VPC. HTTP without TLS on the
VPC is accepted for the hackathon because traffic is private and credentialed;
public Elasticsearch TLS termination is unnecessary because no public
Elasticsearch listener exists.

For initial loading and emergency administration, Elasticsearch is also bound
to host loopback. An SSH local port forward reaches that loopback listener
without opening 9200 publicly.

## Full-corpus initialization

The deployment contains all 288,904 currently audited GEO series. The existing
local canonical records and aligned embedding artifacts remain the source of
truth for the first load; the 264 GiB raw workspace is not copied to the
Droplet.

Initial loading uses this flow:

1. Provision and harden the Elasticsearch Droplet and start the empty pinned
   node.
2. Open an SSH local port forward from the development machine to the Droplet's
   loopback Elasticsearch listener.
3. Run the existing idempotent Elasticsearch loader locally against the
   forwarded endpoint, streaming canonical documents and registered vectors.
4. Run the existing index audit and require exactly 288,904 documents, the
   expected mapping revision, and complete `embedding_gemini_3072` coverage.
5. Exercise BM25, dense, hybrid, exact lookup, filters, and facets through the
   private endpoint before enabling the public application.

This avoids transferring the 43 GiB canonical-record tree and 16 GiB artifact
tree to the server as standalone files. A retry replaces documents by GSE ID
and remains safe.

## Data durability and recovery

The Elasticsearch data bind mount survives container replacement and image
upgrades. DigitalOcean Droplet backups are enabled for coarse host recovery,
but they are not the only copy of the corpus. The local canonical records and
embedding artifacts remain authoritative and can reconstruct the index through
the idempotent loader.

For the hackathon, single-node downtime and a reload-based disaster recovery
objective are accepted. Elasticsearch application-consistent snapshots to
object storage, replicas, multi-node failover, and automated restore drills are
post-hackathon improvements.

## Request and failure behavior

- If Elasticsearch is unavailable, `/healthz` stays healthy, `/readyz` returns
  503, MCP calls return masked service errors, and the marketing narrative and
  static assets remain available.
- If Gemini query embedding fails, dense and hybrid requests fail safely while
  BM25, exact lookup, and blank facet browsing remain usable.
- If NCBI keyword comparison fails, GEOscope results remain visible and the
  browser marks the native comparison as unavailable.
- Invalid or oversized input is rejected before Elasticsearch, Gemini, or NCBI
  work begins.
- Logs contain request IDs and bounded operational metadata, never raw bearer
  values, secrets, full queries, filter values, or returned study text.

## Observability and routine operation

App Platform owns application builds, deploys, TLS, health checks, logs,
metrics, and deployment rollback. DigitalOcean monitoring and alerts cover
application restarts, elevated latency, deployment failures, Elasticsearch host
CPU and memory pressure, and disk usage. Disk alerts fire before Elasticsearch
watermarks can make the index read-only.

Elasticsearch operations remain deliberately separate from application
deployments. Routine application delivery is a Git push/App Platform deploy;
Elasticsearch container upgrades require an explicit maintenance action and a
verified backup or reload path.

## Verification and acceptance

Implementation is accepted only after:

- offline Python and frontend suites pass;
- production dependency inspection proves that PyTorch, Transformers,
  Sentence Transformers, Prefect, and PostgreSQL clients are absent;
- the production image builds below 1 GiB;
- the App Platform spec validates and uses the intended VPC and custom domain;
- the production Compose configuration exposes no public Elasticsearch port,
  applies persistent data and host settings, and contains no container resource
  cap;
- restarting and replacing the Elasticsearch container preserves the index;
- the live audit reports 288,904 documents and complete Gemini vector coverage;
- public `/healthz`, `/readyz`, website, browser search, and anonymous MCP tool
  calls work at `https://geoscope.kevinformatics.com`;
- a dense or hybrid request successfully reaches Gemini and private
  Elasticsearch;
- direct public access to Elasticsearch fails; and
- a controlled Elasticsearch outage produces the documented degraded behavior
  and recovers without rebuilding the App Platform service.

## Non-goals

This deployment does not add user accounts, OAuth, autoscaling, multiple app
instances, an Elasticsearch replica, Kubernetes, a managed Elastic service,
public Elasticsearch access, on-server corpus ETL, scheduled ingestion,
zero-downtime Elasticsearch upgrades, or cross-region disaster recovery.
Kamal, Caddy, and an application Droplet are intentionally excluded.
