# Runtime log archive design

## Purpose

Archive GEOscope's application and Uvicorn runtime logs in Cloudflare R2
without operating OpenSearch solely for log retention. Log export must not add
latency to search requests, must survive ordinary App Platform deployments,
and must reuse the existing Elasticsearch Droplet instead of adding another
monthly compute charge.

This design extends the structured search observability design. The existing
JSON events remain the canonical application log format; this design adds a
durable export path for them.

## Decision

The App Platform process sends newline-delimited JSON batches directly to an
authenticated HTTPS collector. Vector runs as a separate service in the
existing production Docker Compose stack beside Elasticsearch. Vector buffers
accepted events on the Droplet's persistent host disk and writes hourly gzip
archives to R2 through its S3-compatible API.

DigitalOcean App Platform log forwarding is not used. The collector therefore
does not need to emulate the OpenSearch bulk API, and the app never points a
log drain back at itself.

```text
App Platform
  Python logging / Uvicorn
       | stdout (immediate DigitalOcean view)
       ` HTTPS NDJSON batches
                 |
                 v
Existing Elasticsearch Droplet
  Caddy :443 -> Vector HTTP source -> bounded disk buffer -> R2 S3 sink
  Elasticsearch :9200 remains loopback/VPC-only
```

## Application logging

Python continues to write every selected record to stdout. Remote export is a
second handler, so collector or R2 failures never remove DigitalOcean's
immediate runtime stream.

The exporter uses the standard-library logging queue pattern:

- a non-blocking handler serializes an allowlisted event and places it in a
  bounded in-memory queue;
- a background worker sends NDJSON batches over HTTPS;
- a batch is sent after at most one second, 100 events, or 1 MiB of serialized
  data, whichever happens first; and
- graceful shutdown stops accepting new records, sends the remaining batch,
  waits for the collector response within the App Platform termination grace
  period, and then exits.

The one-second boundary limits the normal abrupt-crash exposure. It does not
control R2 object size; Vector independently combines these network batches
into archive objects.

The queue is bounded by both 1,000 events and 8 MiB of serialized data. When it
is full, remote copies are dropped rather than blocking application requests.
The stdout copy remains available, and one rate-limited local warning reports
the drop count without recursively entering the remote exporter.

Every exported event has a stable UUID `event_id`. Retries reuse the same ID,
so the archive is explicitly at-least-once and future readers can deduplicate
without relying on message text or timestamps.

Production configuration uses:

- `GEO_LOG_EXPORT_ENABLED=true`;
- `GEO_LOG_EXPORT_URL=https://logs.geoscope.kevinformatics.com/events`;
- `GEO_LOG_EXPORT_USERNAME` as a secret; and
- `GEO_LOG_EXPORT_PASSWORD` as a secret.

Enabling export without a complete URL and credential pair is a startup
configuration error. A correctly configured but unreachable collector is a
runtime degradation: the application remains healthy and continues logging to
stdout.

## Selected records and health noise

The structured `search.completed` and `request.completed` events are exported.
Python and Uvicorn warning, error, startup, and shutdown records are wrapped in
the same JSON envelope and exported after the existing secret-redaction rules
are applied.

Uvicorn's generic access log is disabled in production because the application
middleware already emits the more useful structured completion event. A
successful `GET /healthz` completion is omitted from both the structured
archive and generic access output. Failed readiness checks, non-2xx responses,
searches, MCP calls, and ordinary user requests remain visible.

This prevents the ten-second App Platform liveness probe from producing about
259,200 low-value archive events per 30-day month.

## Collector deployment

`deploy/elasticsearch/docker-compose.production.yml` remains the production
stack and gains two independent services:

- `vector` receives NDJSON on the private Compose network, filters and
  normalizes records, buffers them, and writes R2 objects; and
- `caddy` terminates public TLS for
  `logs.geoscope.kevinformatics.com`, authenticates the application sender,
  permits only `POST /events`, enforces the 1 MiB request-size limit, and
  proxies to Vector.

Neither service depends on Elasticsearch at runtime. All three containers use
`restart: unless-stopped`. Vector and Caddy images are pinned to reviewed patch
releases rather than floating tags.

Vector has no host-published ingestion or administration port. Caddy publishes
TCP 80 and 443 for certificate issuance and HTTPS ingestion. Elasticsearch
keeps its existing loopback and VPC-only port 9200 bindings. The DigitalOcean
Cloud Firewall opens 80 and 443 publicly while retaining the existing SSH and
private Elasticsearch restrictions.

Caddy's certificate and configuration state are bind-mounted from
`/srv/caddy/data` and `/srv/caddy/config`, so a Caddy container replacement
does not discard its ACME account or issued certificate state.

The Vector data directory is bind-mounted from `/srv/vector/data`. Its S3 sink
uses a 512 MiB disk buffer with blocking backpressure at the collector boundary.
The cap prevents an R2 outage from consuming enough of the Droplet's 160 GiB
disk to threaten Elasticsearch. The application exporter has its own bounded
queue and therefore never propagates that backpressure into a search request.

Vector and Caddy write their own operational logs only to locally rotated
Docker JSON logs. They are not fed back into Vector, which prevents a collector
logging loop.

## R2 archive format

Vector uses R2 Standard storage and the S3-compatible endpoint with a
bucket-scoped access key. Credentials stay in the Droplet's root-readable
deployment environment file and are never committed or passed to App
Platform.

Objects contain gzip-compressed newline-delimited JSON and use partitioned
keys:

```text
runtime/app=geoscope/year=YYYY/month=MM/day=DD/hour=HH/<timestamp>-<uuid>.jsonl.gz
```

The S3 sink flushes after one hour or 128 MiB of uncompressed events, whichever
comes first. The hourly timer provides predictable archive partitions at low
traffic; the byte cap only bounds upload and retry size during an unusual
burst. Quiet hours produce no object.

The initial bucket has no automatic deletion lifecycle. This avoids deleting
the archive before its useful retention period is understood. Operators review
bucket growth monthly and add a lifecycle rule only as a separate, explicit
retention decision.

## Delivery and failure semantics

The archive is at-least-once, not exactly-once.

- During a normal App Platform deployment, the old instance flushes its queue
  during the configured 120-second termination grace period. App deployments
  never restart Vector or remove its disk buffer.
- An abrupt app OOM, `SIGKILL`, or host replacement can lose only records that
  have not yet reached Vector, normally the current one-second network batch.
- After Vector accepts a record, its bounded Droplet disk buffer survives
  Vector container restarts and Droplet reboots.
- R2 request failures are retried by Vector. A full Vector buffer applies
  backpressure to the background exporter; it does not block application
  requests.
- Retried batches may create duplicate events. Consumers use `event_id` when
  deduplication matters.
- Destroying or reimaging the Elasticsearch Droplet can lose records still in
  Vector's local buffer. Objects already written to R2 are unaffected.

Application shutdown waits only within the existing App Platform grace period.
If the collector remains unavailable, shutdown completes rather than delaying
a deployment indefinitely; the stdout copy remains in DigitalOcean's runtime
log stream.

## Security

The collector accepts HTTPS only. Caddy uses a long random credential stored as
an App Platform secret, rejects unauthenticated requests, rejects methods and
paths other than `POST /events`, and limits request bodies to the exporter batch
bound. Vector is reachable only from Caddy on the private Compose network.

Exported JSON follows the observability allowlist. It excludes authorization
and cookie headers, API keys, complete MCP bodies, provider response text, and
result contents. R2 credentials exist only on the Droplet and grant access only
to the log bucket.

## Verification

Automated verification covers:

1. logging a record never performs network I/O on the request thread;
2. event serialization, `event_id` stability across retries, batching, queue
   limits, and rate-limited drop reporting;
3. exclusion of successful `/healthz` events and retention of failures;
4. clean graceful-shutdown flushing and bounded shutdown when the collector is
   unavailable;
5. application health while the collector is stopped;
6. Compose validation, private Vector ports, persistent bind mounts, buffer
   caps, and unchanged Elasticsearch bindings; and
7. secret and request-body redaction in exported payloads.

A local integration test sends NDJSON through Caddy and Vector to an
S3-compatible test bucket, then decompresses the object and verifies its event
IDs and partition key. Production smoke verification emits a unique marker,
confirms it appears in R2, performs an App Platform deployment, and confirms a
pre-shutdown marker and a post-startup marker both arrive.

## Operational boundaries

This path archives logs emitted by the Python/Uvicorn container. DigitalOcean
build logs, deployment logs, and platform crash metadata remain in
DigitalOcean's own interfaces and retention windows; the application cannot
export messages produced before its process starts or after it is killed.

The design does not add OpenSearch, dashboards, log querying, alerts derived
from R2, or a general observability platform. R2 is a cold archive. Immediate
debugging continues to use App Platform Runtime Logs.

## Success criteria

- Search and MCP request behavior is unchanged when export succeeds, fails, or
  is disabled.
- App Platform deployments do not remove Vector's accepted queue.
- Successful liveness probes do not enter the archive.
- Vector and Caddy reuse the existing 8 GiB Elasticsearch Droplet without a
  new compute service.
- Elasticsearch remains private and has enough disk protected from collector
  growth by the fixed Vector buffer cap.
- A production marker is recoverable from a gzip NDJSON object in R2 after an
  application deployment.
