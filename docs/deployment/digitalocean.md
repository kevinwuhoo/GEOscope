# DigitalOcean deployment

This runbook deploys the public, anonymous hackathon service. Elasticsearch is
credentialed and reachable only on Droplet loopback and the private VPC.

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
| Required corpus | `288904` documents and Gemini vectors |

The Droplet keeps its provider-assigned public address because an existing
standard Droplet cannot be converted to private-only. Elasticsearch is never
published on that interface. Restrict TCP 22 to the administrator address and
TCP 9200 to the App Platform VPC source in the DigitalOcean Cloud Firewall;
deny every other inbound rule.

## 1. Bootstrap Elasticsearch

Generate the credential locally without displaying it:

```bash
umask 077
ELASTICSEARCH_PASSWORD="$(openssl rand -hex 32)"
printf 'ELASTICSEARCH_USERNAME=elastic\nELASTICSEARCH_PASSWORD=%s\n' \
  "$ELASTICSEARCH_PASSWORD" >.env.elasticsearch.production
unset ELASTICSEARCH_PASSWORD
chmod 600 .env.elasticsearch.production
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
  'cd /opt/geoscope/elasticsearch && docker compose --env-file .env -f docker-compose.production.yml up -d && docker compose --env-file .env -f docker-compose.production.yml ps'
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

## 3. Render and apply the App Platform spec

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
envsubst '${DO_VPC_ID} ${DO_GITHUB_REPO} ${DO_GITHUB_BRANCH} ${ELASTICSEARCH_PASSWORD} ${GEMINI_API_KEY}' \
  <.do/app.yaml.tmpl >.do/app.yaml
doctl apps spec validate .do/app.yaml
doctl apps list --format ID,Spec.Name,DefaultIngress
export DO_APP_ID=replace-with-existing-geoscope-app-id
doctl apps update "$DO_APP_ID" --spec .do/app.yaml --update-sources --wait \
  --format ID,DefaultIngress,ActiveDeployment.ID
doctl apps logs "$DO_APP_ID" geoscope --type run --follow
```

App Platform must report one `apps-s-1vcpu-0.5gb` instance in `sfo`, attached
to the `default-sfo3` VPC, with edge caching disabled and `/healthz` liveness.
Set Google Gemini project quotas to at most 60 embedding requests/minute and
5,000/day.

The anonymous demo uses one process-wide token bucket at 100 requests/second
with burst 100, plus a 20-request concurrency cap. These are global safeguards,
not per-user quotas. MCP request bodies remain capped at 256 KB.

## 4. DNS and public verification

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
  'cd /opt/geoscope/elasticsearch && docker compose --env-file .env -f docker-compose.production.yml pull && docker compose --env-file .env -f docker-compose.production.yml up -d --force-recreate'
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
