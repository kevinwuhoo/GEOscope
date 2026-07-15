#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK="geoscope-vector-test-$$"
MINIO="geoscope-minio-test-$$"
VECTOR="geoscope-vector-test-$$"
VECTOR_DATA="geoscope-vector-data-test-$$"
WORK="$(mktemp -d)"
ACCESS_KEY="integration-access"
SECRET_KEY="integration-secret-key"
BUCKET="runtime-logs"

cleanup() {
  docker rm -f "$VECTOR" "$MINIO" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  docker volume rm "$VECTOR_DATA" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

docker network create "$NETWORK" >/dev/null
docker volume create "$VECTOR_DATA" >/dev/null
docker run -d --name "$MINIO" --network "$NETWORK" \
  -e MINIO_ROOT_USER="$ACCESS_KEY" \
  -e MINIO_ROOT_PASSWORD="$SECRET_KEY" \
  quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z \
  server /data >/dev/null

bucket_ready=false
for _ in $(seq 1 30); do
  if docker run --rm --network "$NETWORK" --entrypoint /bin/sh \
    minio/mc:RELEASE.2025-08-13T08-35-41Z -c \
    "mc alias set local http://$MINIO:9000 $ACCESS_KEY $SECRET_KEY >/dev/null && mc mb --ignore-existing local/$BUCKET >/dev/null"; then
    bucket_ready=true
    break
  fi
  sleep 1
done
if [[ "$bucket_ready" != true ]]; then
  docker logs "$MINIO"
  exit 1
fi

mkdir -p "$WORK/objects"
docker run -d --name "$VECTOR" --network "$NETWORK" \
  -p 127.0.0.1::8686 \
  -e AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
  -e AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
  -e R2_ENDPOINT="http://$MINIO:9000" \
  -e R2_BUCKET="$BUCKET" \
  -e VECTOR_BATCH_TIMEOUT_SECONDS=1 \
  -e VECTOR_DANGEROUSLY_ALLOW_ENV_VAR_INTERPOLATION=true \
  -e VECTOR_LOG=debug \
  -v "$ROOT/deploy/elasticsearch/vector.yaml:/etc/vector/vector.yaml:ro" \
  -v "$VECTOR_DATA:/var/lib/vector" \
  timberio/vector:0.57.0-debian >/dev/null

vector_ready=false
for _ in $(seq 1 30); do
  if docker logs "$VECTOR" 2>&1 | grep -Fq 'Healthcheck passed'; then
    vector_ready=true
    break
  fi
  sleep 1
done
if [[ "$vector_ready" != true ]]; then
  docker logs "$VECTOR"
  exit 1
fi

PORT="$(docker port "$VECTOR" 8686/tcp | awk -F: '{print $NF}')"
MARKER="00000000-0000-0000-0000-000000000001"
marker_sent=false
for _ in $(seq 1 30); do
  if printf '{"event":"integration.marker","event_id":"%s"}\n' "$MARKER" | \
    curl --fail --silent -X POST \
      "http://127.0.0.1:$PORT/events" \
      -H 'Content-Type: application/x-ndjson' \
      --data-binary @-; then
    marker_sent=true
    break
  fi
  sleep 1
done
if [[ "$marker_sent" != true ]]; then
  docker logs "$VECTOR"
  exit 1
fi

for _ in $(seq 1 30); do
  docker run --rm --network "$NETWORK" \
    -v "$WORK/objects:/out" \
    --entrypoint /bin/sh \
    minio/mc:RELEASE.2025-08-13T08-35-41Z -c \
    "mc alias set local http://$MINIO:9000 $ACCESS_KEY $SECRET_KEY >/dev/null && mc cp --recursive local/$BUCKET/ /out/ >/dev/null" \
    || true
  ARCHIVE="$(find "$WORK/objects" -type f -name '*.jsonl.gz' -print -quit)"
  if [[ -n "$ARCHIVE" ]] && gzip -dc "$ARCHIVE" | grep -Fq "$MARKER"; then
    case "$ARCHIVE" in
      *"/runtime/app=geoscope/year="*"/month="*"/day="*"/hour="*)
        exit 0
        ;;
    esac
  fi
  sleep 1
done

docker logs "$VECTOR"
exit 1
