# DigitalOcean deployment

This runbook deploys the public, anonymous hackathon service. Elasticsearch is
credentialed and reachable only on Droplet loopback and the private VPC. The
application sends a second, nonblocking copy of its runtime logs to Vector on
that VPC for cold storage in Cloudflare R2.

## Resource facts

| Resource | Value |
| --- | --- |
| Droplet public IP | `143.198.53.162` |
| Droplet private IP | `10.124.0.2` |
| Droplet SSH identity | `~/.ssh/digitalocean` |
| App region | `sfo` |
| VPC/datacenter | `default-sfo3` / `sfo3` |
| Public domain | `geoscope.kevinformatics.com` |
| Elasticsearch | `9.4.2`, index `geo-series` |
| Vector | `0.57.0`, private listener `10.124.0.2:8686` |
| Runtime archive | R2 bucket `geoscope-runtime-logs` |
| Required corpus | `288904` documents and Gemini vectors |

The Droplet keeps its provider-assigned public address because an existing
standard Droplet cannot be converted to private-only. Elasticsearch is never
published on that interface. Restrict TCP 22 to the administrator address and
TCP 9200 and 8686 only to the App Platform VPC egress private IP in the
DigitalOcean Cloud Firewall; deny every other inbound rule.

## 1. Bootstrap Elasticsearch and prepare Vector

In Cloudflare, create an R2 Standard bucket named
`geoscope-runtime-logs`. Create an S3-compatible API token with **Object Read &
Write**, scoped to that bucket only, and record its access key ID, secret
access key, and endpoint. The endpoint is normally
`https://<ACCOUNT_ID>.r2.cloudflarestorage.com`; jurisdictional buckets require
their jurisdiction-specific endpoint. See Cloudflare's
[R2 S3 setup](https://developers.cloudflare.com/r2/get-started/s3/) and
[token permissions](https://developers.cloudflare.com/r2/api/tokens/).

Generate the Elasticsearch credential locally without displaying it, append
the safe R2 placeholders, and fill the four R2 values in an editor so secrets
do not enter shell history:

```bash
umask 077
ELASTICSEARCH_PASSWORD="$(openssl rand -hex 32)"
printf 'ELASTICSEARCH_USERNAME=elastic\nELASTICSEARCH_PASSWORD=%s\n' \
  "$ELASTICSEARCH_PASSWORD" >.env.elasticsearch.production
unset ELASTICSEARCH_PASSWORD
sed -n '2,$p' deploy/elasticsearch/elasticsearch.env.example \
  >>.env.elasticsearch.production
chmod 600 .env.elasticsearch.production
${EDITOR:-vi} .env.elasticsearch.production
if grep -q 'replace-with-' .env.elasticsearch.production; then
  echo 'replace every R2 placeholder before deployment' >&2
  exit 1
fi
```

Copy the committed host files and ignored credential, then bootstrap Ubuntu:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'install -d -m 0700 /opt/geoscope'
scp -i ~/.ssh/digitalocean -r deploy/elasticsearch \
  root@143.198.53.162:/opt/geoscope/
scp -i ~/.ssh/digitalocean .env.elasticsearch.production \
  root@143.198.53.162:/opt/geoscope/elasticsearch/.env
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'chmod 600 /opt/geoscope/elasticsearch/.env && /opt/geoscope/elasticsearch/bootstrap-ubuntu.sh'
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'cd /opt/geoscope/elasticsearch && docker compose --env-file .env -f docker-compose.production.yml up -d elasticsearch && docker compose --env-file .env -f docker-compose.production.yml ps elasticsearch'
```

Verify host invariants and authenticated loopback health without printing the
password:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'sysctl vm.max_map_count; swapon --show; ss -lntp | grep :9200; cd /opt/geoscope/elasticsearch && set -a && . ./.env && set +a && curl --fail --silent --user elastic:"$ELASTICSEARCH_PASSWORD" http://127.0.0.1:9200/_cluster/health'
```

Expected listeners are exactly `127.0.0.1:9200` and `10.124.0.2:9200`; swap
output is empty. Confirm `curl http://143.198.53.162:9200` fails from another
machine.

## 2. Load and audit the full corpus

Keep this tunnel running in a separate terminal:

```bash
ssh -i ~/.ssh/digitalocean -N \
  -L 127.0.0.1:19200:127.0.0.1:9200 root@143.198.53.162
```

In the repository, use the local canonical records and Gemini artifact. The
loader is idempotent and streams documents; it does not copy the source trees
to the Droplet.

```bash
set -a
. ./.env.elasticsearch.production
set +a
export ELASTICSEARCH_URL=http://127.0.0.1:19200
export ELASTICSEARCH_ACTIVE_MODEL=gemini_embedding_2_3072_v1
uv run geo-elasticsearch-load \
  --records-root data/processed/series_records \
  --artifacts-root data/processed/embedding_artifacts \
  --model-key gemini_embedding_2_3072_v1 \
  --report data/processed/elasticsearch_load_report.production.json
jq -e '
  .server_version == "9.4.2" and
  .mapping_revision == "geo-series-v1" and
  .document_count == 288904 and
  .failures == [] and
  .vector_coverage.embedding_gemini_3072 == 288904
' data/processed/elasticsearch_load_report.production.json
```

Exercise each retrieval mode before exposing the application:

```bash
uv run geo-search 'single cell lung cancer' --mode bm25 --topk 5
uv run geo-search 'single cell lung cancer' --mode dense --topk 5
uv run geo-search 'single cell lung cancer' --mode hybrid --topk 5
uv run geo-search 'breast cancer' --mode hybrid --organism-id NCBITaxon:9606 --topk 5
```

## 3. R2 runtime log archive

Vector runs beside Elasticsearch on the existing Droplet, but does not depend
on Elasticsearch. It accepts only `POST /events`, keeps a bounded disk buffer
at `/srv/vector/data`, and uploads gzip JSONL objects to R2 through the
S3-compatible HTTPS API. The collector has no public listener, API, dashboard,
TLS, or application authentication; the VPC-only bind and source-IP firewall
rule are the access boundary for these non-sensitive logs.

For an existing Droplet, copy the updated deployment directory and ignored env
file again, rerun the idempotent bootstrap, and keep the remote env mode 0600:

```bash
scp -i ~/.ssh/digitalocean -r deploy/elasticsearch \
  root@143.198.53.162:/opt/geoscope/
scp -i ~/.ssh/digitalocean .env.elasticsearch.production \
  root@143.198.53.162:/opt/geoscope/elasticsearch/.env
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'chmod 600 /opt/geoscope/elasticsearch/.env && /opt/geoscope/elasticsearch/bootstrap-ubuntu.sh'
```

In the App Platform **Networking** tab, copy the app's **App Platform VPC
egress private IP**. Add a DigitalOcean Cloud Firewall inbound rule for TCP
8686 whose only source is that private IP as a `/32`. Do not add a public
source range or `0.0.0.0/0`. Keep the app attached to `default-sfo3`; DigitalOcean
documents this private path in its
[App Platform VPC guide](https://docs.digitalocean.com/products/app-platform/how-to/enable-vpc/).

Check host headroom before adding the collector. Elasticsearch remains the
priority workload:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'free -h; df -h /srv/elasticsearch /srv/vector; docker stats --no-stream'
```

Validate the exact mounted configuration with
`vector validate --skip-healthchecks`, start only Vector, and inspect the
listener:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'cd /opt/geoscope/elasticsearch && docker compose --env-file .env -f docker-compose.production.yml run --rm --no-deps --entrypoint vector vector validate --skip-healthchecks /etc/vector/vector.yaml && docker compose --env-file .env -f docker-compose.production.yml up -d vector && docker compose --env-file .env -f docker-compose.production.yml ps vector && ss -lntp | grep :8686'
```

The only listener must be `10.124.0.2:8686`, never `0.0.0.0:8686`,
`127.0.0.1:8686`, or `143.198.53.162:8686`. Confirm a connection to the public
address fails from another machine.

Before updating the App Platform spec, open the existing app's console and
send a unique newline-terminated marker over the VPC. Record the printed value
for archive verification:

```bash
export MARKER="pre-deploy-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
python - <<'PY'
import json
import os
import urllib.request

marker = os.environ["MARKER"]
payload = json.dumps(
    {
        "event": "deployment.marker",
        "event_id": marker,
        "marker": marker,
        "phase": "pre-deploy",
    },
    separators=(",", ":"),
).encode() + b"\n"
request = urllib.request.Request(
    "http://10.124.0.2:8686/events",
    data=payload,
    headers={"Content-Type": "application/x-ndjson"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(f"unexpected Vector status: {response.status}")
print(marker)
PY
```

Production batches flush after one hour or 128 MiB, whichever happens first,
so allow up to 60 minutes for a low-traffic marker to appear. From an admin
machine with the AWS CLI, load the ignored credentials, list the hourly prefix,
copy the object whose timestamp covers the marker, and inspect it:

```bash
set -a
. ./.env.elasticsearch.production
set +a
export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION=auto
aws --endpoint-url "$R2_ENDPOINT" s3 ls \
  "s3://$R2_BUCKET/runtime/app=geoscope/" --recursive
export OBJECT_KEY='copy-the-matching-runtime/.../*.jsonl.gz-key-from-the-list'
aws --endpoint-url "$R2_ENDPOINT" s3 cp \
  "s3://$R2_BUCKET/$OBJECT_KEY" /tmp/geoscope-runtime.jsonl.gz
gzip -dc /tmp/geoscope-runtime.jsonl.gz | grep -F "$MARKER"
```

Repeat this check for the post-deploy marker in the next section. A successful
result proves the private App-to-Vector path, persistent batching, R2 upload,
compression, and object recovery. App Platform deployments cannot wipe
Vector's accepted queue because the collector and `/srv/vector/data` live on
the Droplet. The app still uses its 120-second termination grace period to
flush its small in-process queue before an old instance exits.

### Vector or R2 outage and recovery

During an R2 outage, Vector retries from its persistent buffer. Its configured
disk cap is 512 MiB and its container is limited to 256 MiB RAM and 0.25 CPU.
Check buffer growth, container usage, and Elasticsearch health without
restarting Elasticsearch:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'cd /opt/geoscope/elasticsearch && du -sh /srv/vector/data && docker stats --no-stream geo-vector geo-elasticsearch && set -a && . ./.env && set +a && curl --fail --silent --user elastic:"$ELASTICSEARCH_PASSWORD" http://127.0.0.1:9200/_cluster/health'
```

The buffer must remain within its 512 MiB cap and Elasticsearch health must
remain yellow or green. If the buffer fills, Vector applies backpressure; the
application's network worker times out and its bounded memory queue eventually
drops only the remote archive copies while request handling and DigitalOcean
runtime logging continue.

After correcting R2 credentials or connectivity, restart/recreate only Vector
and follow its logs until uploads resume:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'cd /opt/geoscope/elasticsearch && docker compose --env-file .env -f docker-compose.production.yml up -d --force-recreate vector && docker compose --env-file .env -f docker-compose.production.yml logs --tail 100 -f vector'
```

Never delete `/srv/vector/data` until queued records have drained and their R2
objects have been verified. Recreating the Vector container is safe because
that directory is a host bind mount; replacing the Droplet is not safe unless
the buffer has already drained or the directory is copied first.

## 4. Render and apply the App Platform spec

Install and authenticate current `doctl`, then create the ignored environment
file from `deploy/app-platform.env.example`. Copy the Elasticsearch password
from `.env.elasticsearch.production`; do not paste either secret into the
committed template.

```bash
cp deploy/app-platform.env.example deploy/app-platform.env
${EDITOR:-vi} deploy/app-platform.env
set -a
. ./deploy/app-platform.env
set +a
umask 077
envsubst '${DO_VPC_ID} ${DO_GITHUB_REPO} ${DO_GITHUB_BRANCH} ${ELASTICSEARCH_PASSWORD} ${GEMINI_API_KEY} ${ANTHROPIC_API_KEY} ${GEO_RERANK_ENABLED} ${GEO_RERANK_MODEL} ${GEO_RERANK_THINKING} ${GEO_RERANK_CANDIDATE_LIMIT} ${GEO_RERANK_TIMEOUT_SECONDS} ${GEO_NCBI_TIMEOUT_SECONDS}' \
  <.do/app.yaml.tmpl >.do/app.yaml
doctl apps spec validate .do/app.yaml
doctl apps list --format ID,Spec.Name,DefaultIngress
export DO_APP_ID=replace-with-existing-geoscope-app-id
doctl apps update "$DO_APP_ID" --spec .do/app.yaml --update-sources --wait \
  --format ID,DefaultIngress,ActiveDeployment.ID
doctl apps logs "$DO_APP_ID" geoscope --type run --follow
```

After the new deployment is active, open its App Platform console and emit a
post-deploy marker through the same `LogExporter` used by the Uvicorn process:

```bash
export MARKER="post-deploy-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
python - <<'PY'
import json
import logging
import os

from geo_index.log_export import LogExporter, LogExportSettings

marker = os.environ["MARKER"]
settings = LogExportSettings.from_env(os.environ)
if settings is None:
    raise SystemExit("runtime log export is not enabled")
exporter = LogExporter(settings)
exporter.start()
try:
    logging.getLogger("geo_index.deployment").warning(
        json.dumps(
            {
                "event": "deployment.marker",
                "event_id": marker,
                "marker": marker,
                "phase": "post-deploy",
            },
            separators=(",", ":"),
        )
    )
finally:
    exporter.stop()
print(marker)
PY
```

Use the R2 listing/download commands in the preceding section to recover this
marker after the hourly flush. Both the pre-deploy and post-deploy markers must
be present before treating the log archive rollout as complete.

App Platform must report one `apps-s-1vcpu-0.5gb` instance in `sfo`, attached
to the `default-sfo3` VPC, with edge caching disabled and `/healthz` liveness.
Set Google Gemini project quotas to at most 60 embedding requests/minute and
5,000/day.

The anonymous demo uses one process-wide token bucket at 100 requests/second
with burst 100, plus a 20-request concurrency cap. These are global safeguards,
not per-user quotas. MCP request bodies remain capped at 256 KB.

### Safe unified-search rollout

Deploy first with `GEO_RERANK_ENABLED=false`. The shared search layer still
retrieves up to 100 Elasticsearch and 100 NCBI candidates, merges them, and
falls back to deterministic Elasticsearch-first ordering without calling the
reranker. NCBI-only results are partial live records from E-utilities, not
online-ingested canonical Elasticsearch documents; unavailable metadata stays
marked unavailable.

When reranking is enabled, the shared service returns 10 results by default,
while callers may request from 1 through 50. Elasticsearch admits up to 100
candidates, NCBI retrieves up to its configured page maximum of 100, and the
deduplicated union of up to 200 candidates reaches the reranker before the
caller-selected result slice is returned.

At enabled runtime, an NCBI timeout or failure degrades to Elasticsearch-only
candidate generation. An Anthropic timeout, refusal, truncation, malformed
response, or invalid output fails open to deterministic pre-rerank
Elasticsearch-first union ordering. Elasticsearch failure remains fatal.
Provider response text and API keys are never exposed through MCP, HTTP, logs,
or evaluation reports.

Startup validates the complete search-quality configuration. Enabling
reranking requires `ANTHROPIC_API_KEY`, `GEO_RERANK_MODEL=claude-haiku-4-5`, and
`GEO_RERANK_THINKING=disabled`; an absent key or an invalid model, thinking mode,
candidate bound, or timeout prevents
startup. Keep the Anthropic key in App Platform as a secret at runtime. The
`envsubst` step writes it into the ignored local `.do/app.yaml`; never commit
the generated spec or include the key in reports.

The shared reranker request timeout defaults to 30 seconds via
`GEO_RERANK_TIMEOUT_SECONDS=30`; keep the environment override available for
operational tuning.

With live Elasticsearch and NCBI access configured, explicitly opt in to the
provider smoke and then record baseline versus Haiku metrics. Supply current
prices at run time rather than committing a price assumption:

```bash
GEO_TEST_ANTHROPIC=1 uv run pytest \
  tests/test_reranker_live.py -m provider_integration -q

GEO_RERANK_ENABLED=true uv run geo-search-eval \
  eval/unified_search_queries.jsonl \
  --output eval/unified_search_report.json \
  --compare-baseline \
  --input-cost-per-million "$CURRENT_HAIKU_INPUT_COST_PER_MILLION" \
  --output-cost-per-million "$CURRENT_HAIKU_OUTPUT_COST_PER_MILLION"
```

Keep `eval/unified_search_report.json` uncommitted until its values are reviewed.
Enable reranking only after the baseline versus Haiku Recall@40, nDCG@10, MRR,
constraint violations, NCBI-only recovery, p50/p95 latency, fallback rate,
token use, and estimated cost are recorded. Improve candidate generation when
relevant records are absent; tune reranking when they are present but
misordered; propose query understanding only when unmodified NCBI recall or
explicit constraint handling remains inadequate after that evaluation.

Inspect the shared MCP/Elasticsearch search behavior for each required smoke
query in the versioned evaluation corpus:

1. `mouse skeletal muscle gene expression after endurance exercise in insulin resistance`
2. `human breast cancer transcriptomics before and after neoadjuvant chemotherapy with treatment response data`
3. `GSE310900`

Claude Haiku 4.5 must be attempted and applied for the two natural-language
queries with no organism constraint violation. Exact `GSE310900` must return
the accession without a rerank attempt. The integration follows the official
[Claude Haiku 4.5 model documentation](https://platform.claude.com/docs/en/about-claude/models/overview),
[Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
and the [Anthropic Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python).

A production source deploy is incomplete until public provenance shows Haiku
applied with model `claude-haiku-4-5` and thinking `disabled` for
both natural-language smoke queries. A successful source push or healthy
process alone is not completion evidence.

## 5. DNS and public verification

If DNS is not managed by DigitalOcean, point the `geoscope` CNAME at the
`DefaultIngress` hostname from `doctl apps get "$DO_APP_ID"`. Add the custom
domain in the app spec before changing DNS so certificate validation can begin.

```bash
dig +short geoscope.kevinformatics.com
curl --fail --silent https://geoscope.kevinformatics.com/healthz
curl --fail --silent https://geoscope.kevinformatics.com/readyz
curl --fail --silent https://geoscope.kevinformatics.com/ >/dev/null
curl --fail --silent \
  'https://geoscope.kevinformatics.com/api/demo/search?q=single%20cell&limit=3' \
  >/dev/null
```

Initialize anonymous MCP without an Authorization header:

```bash
curl --fail --silent https://geoscope.kevinformatics.com/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl-smoke","version":"1"}}}'
```

## Operations

Redeploy the current source and inspect recent deployments:

```bash
doctl apps create-deployment "$DO_APP_ID" --force-rebuild
doctl apps list-deployments "$DO_APP_ID" --format ID,Phase,Cause,Created
```

App Platform has no `doctl` rollback subcommand. Roll back reproducibly by
reverting the bad source commit and pushing the configured branch:

```bash
git revert BAD_COMMIT
git push origin "$DO_GITHUB_BRANCH"
```

Restart or replace the Elasticsearch container without losing the bind-mounted
index:

```bash
ssh -i ~/.ssh/digitalocean root@143.198.53.162 \
  'cd /opt/geoscope/elasticsearch && docker compose --env-file .env -f docker-compose.production.yml pull elasticsearch && docker compose --env-file .env -f docker-compose.production.yml up -d --force-recreate elasticsearch'
```

To rotate the Elasticsearch password, first keep the SSH tunnel open. Generate
a new value, change it through the authenticated API, update the ignored local
file and remote `.env`, render/apply the App spec with the new secret, and only
then discard the old value:

```bash
set -a; . ./.env.elasticsearch.production; set +a
OLD_ELASTICSEARCH_PASSWORD="$ELASTICSEARCH_PASSWORD"
NEW_ELASTICSEARCH_PASSWORD="$(openssl rand -hex 32)"
jq -n --arg password "$NEW_ELASTICSEARCH_PASSWORD" '{password:$password}' |
  curl --fail --silent --user elastic:"$OLD_ELASTICSEARCH_PASSWORD" \
    -H 'Content-Type: application/json' -X POST \
    http://127.0.0.1:19200/_security/user/elastic/_password --data-binary @-
printf 'ELASTICSEARCH_USERNAME=elastic\nELASTICSEARCH_PASSWORD=%s\n' \
  "$NEW_ELASTICSEARCH_PASSWORD" >.env.elasticsearch.production
chmod 600 .env.elasticsearch.production
scp -i ~/.ssh/digitalocean .env.elasticsearch.production \
  root@143.198.53.162:/opt/geoscope/elasticsearch/.env
unset OLD_ELASTICSEARCH_PASSWORD NEW_ELASTICSEARCH_PASSWORD ELASTICSEARCH_PASSWORD
```

If the Droplet or index is lost, provision a same-region replacement, update
the private IP in Compose/App Platform, rerun bootstrap, and repeat the
idempotent full-corpus load and audit. The canonical records and embedding
artifacts—not the Elasticsearch data directory—are the recovery source of
truth.
