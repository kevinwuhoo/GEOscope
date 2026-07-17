# Runtime Log Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive GEOscope Python and Uvicorn runtime records in hourly gzip JSONL objects in Cloudflare R2 without blocking requests or exposing a public collector.

**Architecture:** A dependency-free logging handler serializes and queues records in the App Platform process while a background worker posts bounded NDJSON batches over the DigitalOcean VPC. One resource-limited Vector container on the Elasticsearch Droplet accepts those batches, persists them in a bounded disk buffer, and uploads them to R2 using its S3-compatible HTTPS endpoint.

**Tech Stack:** Python 3.11 standard-library logging/threading/queue primitives, `httpx`, FastAPI lifespan, Uvicorn, Vector 0.57.0, Docker Compose, Cloudflare R2 S3 API, pytest.

## Global Constraints

- Network I/O must never execute on an application request thread.
- Continue writing selected records to DigitalOcean Runtime Logs even when remote export is enabled.
- Bound the app queue at 1,000 events and 8 MiB; bound one event and one request batch at 1 MiB.
- Flush a network batch after one second, 100 events, or 1 MiB, whichever occurs first.
- Reuse each event's UUID across transport retries; delivery is at-least-once.
- A full app queue drops only the remote copy and emits a rate-limited local warning.
- Omit successful `GET /healthz` structured events and disable Uvicorn's generic access log.
- Vector listens only at `10.124.0.2:8686`, has no public/API port, and accepts only `POST /events`.
- Vector is limited to 256 MiB memory, 0.25 CPU, and a 512 MiB persistent disk buffer.
- R2 objects are gzip JSONL under hourly partition prefixes and flush after one hour or 128 MiB.
- The existing structured-search observability plan remains a separate change; this exporter preserves future JSON log objects without requiring that plan first.
- Do not modify or stage unrelated workspace changes.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/geo_index/log_export.py` | Settings, safe serialization, bounded queue, retrying worker, logging handler installation, and shutdown. |
| `tests/test_log_export.py` | Deterministic unit tests for settings, redaction, queue limits, health filtering, batching, retries, and threading. |
| `src/geo_index/production_app.py` | Start and stop the exporter with the production ASGI lifespan. |
| `tests/test_production_app.py` | Verify exporter lifecycle is independent of the shared search service lifecycle. |
| `Dockerfile` | Disable the noisy generic Uvicorn access logger. |
| `.do/app.yaml.tmpl` | Enable private log export in App Platform. |
| `deploy/geo-mcp.env.example` | Document disabled-by-default local exporter variables. |
| `tests/test_app_platform_config.py` | Assert the private collector URL and enabled flag. |
| `tests/test_mcp_packaging.py` | Keep the exact production command contract current. |
| `tests/test_production_packaging.py` | Assert Uvicorn access logs are disabled. |
| `deploy/elasticsearch/vector.yaml` | Private HTTP source, R2 sink, gzip/hourly object batching, and disk buffer. |
| `deploy/elasticsearch/docker-compose.production.yml` | Run Vector with private binding, persistence, resource bounds, credentials, and rotated local logs. |
| `deploy/elasticsearch/elasticsearch.env.example` | Document R2 bucket-scoped credential variables. |
| `deploy/elasticsearch/bootstrap-ubuntu.sh` | Create the persistent Vector data directory. |
| `tests/test_production_elasticsearch_config.py` | Assert collector security, resources, persistence, and R2 configuration. |
| `tests/vector_r2_integration.sh` | Exercise the production Vector pipeline against a disposable S3-compatible bucket. |
| `docs/deployment/digitalocean.md` | R2, firewall, rollout, smoke, recovery, and object verification runbook. |

### Task 1: Build the bounded nonblocking application exporter

**Files:**

- Create: `src/geo_index/log_export.py`
- Create: `tests/test_log_export.py`

**Interfaces:**

- Produces: `LogExportSettings.from_env(environ) -> LogExportSettings | None`.
- Produces: `serialize_record(record, *, now, event_id_factory) -> bytes | None`.
- Produces: `BoundedEventQueue.put_nowait(payload) -> bool`, `get(timeout) -> bytes`, and `empty()`.
- Produces: `LogExporter(settings, *, sender=None)` with `start()` and `stop(timeout=10.0)`.
- Consumes: `BatchSender.send(payload: bytes) -> None`; the default implementation posts `application/x-ndjson` with a five-second HTTP timeout.

- [ ] **Step 1: Write failing settings and serialization tests.**

```python
def test_enabled_export_requires_an_http_url() -> None:
    with pytest.raises(ValueError, match="GEO_LOG_EXPORT_URL"):
        LogExportSettings.from_env({"GEO_LOG_EXPORT_ENABLED": "true"})


def test_plain_record_becomes_redacted_json_with_stable_event_id() -> None:
    record = logging.LogRecord(
        "geo_index.test", logging.ERROR, __file__, 1,
        "password=%s failed", ("secret-value",), None,
    )
    payload = serialize_record(
        record,
        now=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
        event_id_factory=lambda: UUID("00000000-0000-0000-0000-000000000001"),
    )
    event = json.loads(payload)
    assert event == {
        "event": "python.log",
        "event_id": "00000000-0000-0000-0000-000000000001",
        "timestamp": "2026-07-15T00:00:00Z",
        "level": "error",
        "logger": "geo_index.test",
        "message": "password=[REDACTED] failed",
    }


def test_future_structured_event_is_preserved_and_successful_health_is_omitted() -> None:
    assert serialize_record(_json_record({
        "event": "request.completed", "method": "GET", "route": "/healthz",
        "status_code": 200,
    })) is None
    failed = json.loads(serialize_record(_json_record({
        "event": "request.completed", "method": "GET", "route": "/healthz",
        "status_code": 503,
    })))
    assert failed["status_code"] == 503
    assert failed["event_id"]
```

- [ ] **Step 2: Run the focused tests and verify they fail.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_log_export.py -k 'requires_an_http_url or redacted_json or successful_health' -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'geo_index.log_export'`.

- [ ] **Step 3: Implement settings, recursive redaction, stable JSON serialization, and health filtering.**

```python
@dataclass(frozen=True)
class LogExportSettings:
    url: str
    request_timeout_seconds: float = 5.0
    flush_interval_seconds: float = 1.0
    max_batch_events: int = 100
    max_batch_bytes: int = 1024 * 1024
    max_queue_events: int = 1000
    max_queue_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> LogExportSettings | None:
        enabled = environ.get("GEO_LOG_EXPORT_ENABLED", "false").strip().lower()
        if enabled not in {"true", "false"}:
            raise ValueError("GEO_LOG_EXPORT_ENABLED must be true or false")
        if enabled == "false":
            return None
        url = environ.get("GEO_LOG_EXPORT_URL", "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("GEO_LOG_EXPORT_URL must be an absolute HTTP URL")
        return cls(url=url)


def serialize_record(
    record: logging.LogRecord,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    event_id_factory: Callable[[], UUID] = uuid4,
) -> bytes | None:
    message = record.getMessage()
    try:
        parsed = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        event = _redact(parsed)
        if _successful_health_event(event):
            return None
    else:
        event = {
            "event": "python.log",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": _redact_text(message),
        }
    event.setdefault("event_id", str(event_id_factory()))
    event.setdefault("timestamp", now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    event.setdefault("level", record.levelname.lower())
    event.setdefault("logger", record.name)
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

Redact values recursively when a JSON key case-folds to `authorization`, `cookie`, `set-cookie`, `password`, `api_key`, `access_key_id`, or `secret_access_key`. Redact matching `name=value` and `name: value` fragments in plain messages. Do not serialize `record.args`, arbitrary extras, request bodies, or exception objects.

- [ ] **Step 4: Write failing queue, worker, retry, and drop-warning tests.**

```python
def test_queue_enforces_event_and_byte_limits() -> None:
    queue = BoundedEventQueue(max_events=2, max_bytes=5)
    assert queue.put_nowait(b"aa") is True
    assert queue.put_nowait(b"bbb") is True
    assert queue.put_nowait(b"x") is False
    assert queue.get(timeout=0) == b"aa"
    assert queue.put_nowait(b"x") is True


def test_exporter_sends_on_worker_thread_and_reuses_serialized_ids() -> None:
    sender = FailingOnceSender()
    exporter = LogExporter(_settings(), sender=sender)
    exporter.start()
    logging.getLogger("geo_index.archive-test").warning("archive me")
    assert sender.sent.wait(timeout=2)
    exporter.stop()
    assert sender.thread_ids == [sender.thread_ids[0], sender.thread_ids[0]]
    first, second = (json.loads(body.splitlines()[0]) for body in sender.payloads)
    assert first["event_id"] == second["event_id"]
    assert sender.thread_ids[0] != threading.get_ident()
```

- [ ] **Step 5: Run the worker tests and verify they fail.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_log_export.py -k 'queue_enforces or worker_thread or drop_warning' -q`

Expected: FAIL because the queue and exporter classes are not implemented.

- [ ] **Step 6: Implement the queue, HTTP sender, logging handler, batching worker, and lifecycle.**

```python
class HttpBatchSender:
    def __init__(self, url: str, timeout: float) -> None:
        self._client = httpx.Client(timeout=timeout)
        self._url = url

    def send(self, payload: bytes) -> None:
        response = self._client.post(
            self._url,
            content=payload,
            headers={"Content-Type": "application/x-ndjson"},
        )
        response.raise_for_status()

    def close(self) -> None:
        self._client.close()


class LogExportHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("geo_index.log_export", "httpx", "httpcore")):
            return
        try:
            payload = serialize_record(record)
            if payload is not None and len(payload) <= self.max_event_bytes:
                if self.queue.put_nowait(payload):
                    return
            self.drop_reporter.record_drop()
        except Exception:
            self.handleError(record)


class LogExporter:
    def start(self) -> None:
        self._attach_handler(logging.getLogger())
        self._attach_handler(logging.getLogger("uvicorn"))
        self._install_application_stdout_handler()
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stopping.set()
        self._thread.join(timeout=timeout)
        self._detach_handlers()
        self._sender.close()
```

The worker retains a failed serialized batch in memory and retries it with interruptible exponential delays of 1, 2, 4, 8, 16, then 30 seconds. The stop event interrupts a delay and allows one final send attempt before the worker exits. Batch construction starts its one-second deadline when the first event is removed from the queue. `DropReporter` writes at most one accumulated warning per 60 seconds directly to `sys.stderr`, bypassing logging and preventing recursion. Installation adds an INFO stderr handler to the `geo_index` logger so future structured application records remain visible in DigitalOcean as well as the archive; restore logger state on stop.

- [ ] **Step 7: Run the exporter module tests.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_log_export.py -q`

Expected: PASS; requests occur only on the worker, retries preserve serialized IDs, health success is filtered, failures remain, and queue limits never block the caller.

- [ ] **Step 8: Commit the exporter core.**

```bash
git add src/geo_index/log_export.py tests/test_log_export.py
git commit -m "feat: add nonblocking runtime log exporter"
```

### Task 2: Wire exporter lifecycle and App Platform configuration

**Files:**

- Modify: `src/geo_index/production_app.py`
- Modify: `tests/test_production_app.py`
- Modify: `Dockerfile`
- Modify: `.do/app.yaml.tmpl`
- Modify: `deploy/geo-mcp.env.example`
- Modify: `tests/test_app_platform_config.py`
- Modify: `tests/test_mcp_packaging.py`
- Modify: `tests/test_production_packaging.py`

**Interfaces:**

- Consumes: `LogExportSettings.from_env`, `LogExporter.start`, and `LogExporter.stop`.
- Produces: optional `log_exporter` injection in `create_app` for deterministic lifecycle tests.
- Produces: production environment `GEO_LOG_EXPORT_ENABLED=true` and `GEO_LOG_EXPORT_URL=http://10.124.0.2:8686/events`.

- [ ] **Step 1: Write failing application lifecycle and deployment-contract tests.**

```python
def test_log_exporter_wraps_the_service_lifespan() -> None:
    exporter = FakeLogExporter()
    service = FakeService()
    app = create_app(settings=_settings(), service=service, log_exporter=exporter)
    with TestClient(app, base_url="https://geoscope.kevinformatics.com"):
        assert exporter.calls == ["start"]
        assert service.is_open
    assert exporter.calls == ["start", "stop"]


def test_app_platform_template_exports_logs_over_the_vpc() -> None:
    text = Path(".do/app.yaml.tmpl").read_text()
    assert "GEO_LOG_EXPORT_ENABLED" in text
    assert 'value: "true"' in text
    assert "http://10.124.0.2:8686/events" in text


def test_production_disables_generic_uvicorn_access_logs() -> None:
    assert '"--no-access-log"' in Path("Dockerfile").read_text()
```

- [ ] **Step 2: Run focused tests and verify they fail.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_production_app.py::test_log_exporter_wraps_the_service_lifespan tests/test_app_platform_config.py tests/test_production_packaging.py tests/test_mcp_packaging.py -q`

Expected: FAIL because `create_app` has no exporter injection and the deployment files have no log-export settings.

- [ ] **Step 3: Start and stop export around the existing MCP lifespan.**

```python
def create_app(
    settings: McpSettings | None = None,
    service: McpService | None = None,
    static_dir: Path | None = None,
    log_exporter: LogExporter | None = None,
) -> FastAPI:
    # existing settings/service/mount construction
    exporter = log_exporter
    if exporter is None:
        export_settings = LogExportSettings.from_env(os.environ)
        exporter = LogExporter(export_settings) if export_settings else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if exporter is not None:
            exporter.start()
        try:
            async with mcp_mount.lifespan(app):
                yield
        finally:
            if exporter is not None:
                exporter.stop()
```

Start export before the shared service opens so service startup records can enter the queue. Stop it after service close so shutdown records are flushed. Exporter startup/configuration errors remain startup-fatal only when export is enabled; collector connection failures happen on the worker and never affect health.

- [ ] **Step 4: Add App Platform variables, the disabled local example, and `--no-access-log`.**

```yaml
      - key: GEO_LOG_EXPORT_ENABLED
        value: "true"
        scope: RUN_TIME
        type: GENERAL
      - key: GEO_LOG_EXPORT_URL
        value: http://10.124.0.2:8686/events
        scope: RUN_TIME
        type: GENERAL
```

```dotenv
GEO_LOG_EXPORT_ENABLED=false
GEO_LOG_EXPORT_URL=
```

Append `"--no-access-log"` to the existing JSON-form Uvicorn `CMD`, then update exact-command assertions in both packaging test modules.

- [ ] **Step 5: Run lifecycle and deployment tests.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_production_app.py tests/test_app_platform_config.py tests/test_production_packaging.py tests/test_mcp_packaging.py -q`

Expected: PASS with existing production routing, shared service lifecycle, and packaging behavior unchanged.

- [ ] **Step 6: Commit application integration.**

```bash
git add src/geo_index/production_app.py tests/test_production_app.py Dockerfile .do/app.yaml.tmpl deploy/geo-mcp.env.example tests/test_app_platform_config.py tests/test_mcp_packaging.py tests/test_production_packaging.py
git commit -m "feat: export App Platform runtime logs"
```

### Task 3: Add the private Vector-to-R2 collector

**Files:**

- Create: `deploy/elasticsearch/vector.yaml`
- Modify: `deploy/elasticsearch/docker-compose.production.yml`
- Modify: `deploy/elasticsearch/elasticsearch.env.example`
- Modify: `deploy/elasticsearch/bootstrap-ubuntu.sh`
- Modify: `tests/test_production_elasticsearch_config.py`
- Create: `tests/vector_r2_integration.sh`

**Interfaces:**

- Consumes: NDJSON `POST /events` at the host's private `10.124.0.2:8686` binding.
- Consumes: `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` from the ignored Compose env file.
- Consumes: `VECTOR_BATCH_TIMEOUT_SECONDS=3600` from Compose; the integration test overrides it to one second without duplicating the production pipeline.
- Produces: gzip JSONL R2 keys under `runtime/app=geoscope/year=%Y/month=%m/day=%d/hour=%H/`.

- [ ] **Step 1: Write failing static deployment tests.**

```python
def test_vector_is_private_persistent_and_resource_bounded() -> None:
    compose = Path("deploy/elasticsearch/docker-compose.production.yml").read_text()
    assert "timberio/vector:0.57.0-debian" in compose
    assert '10.124.0.2:8686:8686' in compose
    assert '0.0.0.0:8686' not in compose
    assert "/srv/vector/data:/var/lib/vector" in compose
    assert "mem_limit: 256m" in compose
    assert 'cpus: "0.25"' in compose


def test_vector_archives_hourly_gzip_jsonl_to_r2() -> None:
    config = Path("deploy/elasticsearch/vector.yaml").read_text()
    for fragment in (
        "type: http_server", "path: /events", "method: POST",
        "type: aws_s3", "region: auto", "force_path_style: true",
        "compression: gzip", "filename_extension: jsonl",
        "timeout_secs: ${VECTOR_BATCH_TIMEOUT_SECONDS}",
        "max_bytes: 134217728",
        "type: disk", "max_size: 536870912", "when_full: block",
    ):
        assert fragment in config
```

- [ ] **Step 2: Run the deployment test and verify it fails.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_production_elasticsearch_config.py -q`

Expected: FAIL because no Vector service or configuration exists.

- [ ] **Step 3: Add the exact Vector configuration.**

```yaml
data_dir: /var/lib/vector

sources:
  app_http:
    type: http_server
    address: 0.0.0.0:8686
    path: /events
    strict_path: true
    method: POST
    decoding:
      codec: json
    framing:
      method: newline_delimited

sinks:
  r2:
    type: aws_s3
    inputs: [app_http]
    endpoint: ${R2_ENDPOINT}
    bucket: ${R2_BUCKET}
    region: auto
    force_path_style: true
    key_prefix: runtime/app=geoscope/year=%Y/month=%m/day=%d/hour=%H/
    filename_extension: jsonl
    compression: gzip
    encoding:
      codec: json
    batch:
      max_bytes: 134217728
      timeout_secs: ${VECTOR_BATCH_TIMEOUT_SECONDS}
    buffer:
      type: disk
      max_size: 536870912
      when_full: block
```

- [ ] **Step 4: Add the resource-limited private Compose service and persistent directory.**

```yaml
  vector:
    image: timberio/vector:0.57.0-debian
    container_name: geo-vector
    environment:
      AWS_ACCESS_KEY_ID: ${R2_ACCESS_KEY_ID:?set R2_ACCESS_KEY_ID}
      AWS_SECRET_ACCESS_KEY: ${R2_SECRET_ACCESS_KEY:?set R2_SECRET_ACCESS_KEY}
      R2_ENDPOINT: ${R2_ENDPOINT:?set R2_ENDPOINT}
      R2_BUCKET: ${R2_BUCKET:?set R2_BUCKET}
      VECTOR_BATCH_TIMEOUT_SECONDS: "3600"
    ports:
      - "10.124.0.2:8686:8686"
    volumes:
      - /srv/vector/data:/var/lib/vector
      - ./vector.yaml:/etc/vector/vector.yaml:ro
    mem_limit: 256m
    cpus: "0.25"
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"
```

Append the four R2 variables with safe placeholders to `elasticsearch.env.example`. Update bootstrap to run `install -d -m 0750 /srv/vector/data` without changing Elasticsearch ownership or host kernel settings.

- [ ] **Step 5: Add the disposable S3 integration script.**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK="geoscope-vector-test-$$"
MINIO="geoscope-minio-test-$$"
VECTOR="geoscope-vector-test-$$"
WORK="$(mktemp -d)"
ACCESS_KEY="integration-access"
SECRET_KEY="integration-secret-key"
BUCKET="runtime-logs"

cleanup() {
  docker rm -f "$VECTOR" "$MINIO" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

docker network create "$NETWORK" >/dev/null
docker run -d --name "$MINIO" --network "$NETWORK" \
  -e MINIO_ROOT_USER="$ACCESS_KEY" -e MINIO_ROOT_PASSWORD="$SECRET_KEY" \
  quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z \
  server /data >/dev/null

for _ in $(seq 1 30); do
  if docker run --rm --network "$NETWORK" --entrypoint /bin/sh \
    minio/mc:RELEASE.2025-08-13T08-35-41Z -c \
    "mc alias set local http://$MINIO:9000 $ACCESS_KEY $SECRET_KEY >/dev/null && mc mb --ignore-existing local/$BUCKET >/dev/null"; then
    break
  fi
  sleep 1
done

mkdir -p "$WORK/vector" "$WORK/objects"
docker run -d --name "$VECTOR" --network "$NETWORK" -p 127.0.0.1::8686 \
  -e AWS_ACCESS_KEY_ID="$ACCESS_KEY" -e AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
  -e R2_ENDPOINT="http://$MINIO:9000" -e R2_BUCKET="$BUCKET" \
  -e VECTOR_BATCH_TIMEOUT_SECONDS=1 \
  -v "$ROOT/deploy/elasticsearch/vector.yaml:/etc/vector/vector.yaml:ro" \
  -v "$WORK/vector:/var/lib/vector" \
  timberio/vector:0.57.0-debian >/dev/null

PORT="$(docker port "$VECTOR" 8686/tcp | awk -F: '{print $NF}')"
MARKER="00000000-0000-0000-0000-000000000001"
for _ in $(seq 1 30); do
  if curl --fail --silent -X POST "http://127.0.0.1:$PORT/events" \
    -H 'Content-Type: application/x-ndjson' \
    --data-binary "{\"event\":\"integration.marker\",\"event_id\":\"$MARKER\"}"; then
    break
  fi
  sleep 1
done

for _ in $(seq 1 30); do
  docker run --rm --network "$NETWORK" -v "$WORK/objects:/out" \
    --entrypoint /bin/sh minio/mc:RELEASE.2025-08-13T08-35-41Z -c \
    "mc alias set local http://$MINIO:9000 $ACCESS_KEY $SECRET_KEY >/dev/null && mc cp --recursive local/$BUCKET/ /out/ >/dev/null" || true
  ARCHIVE="$(find "$WORK/objects" -type f -name '*.jsonl.gz' -print -quit)"
  if [[ -n "$ARCHIVE" ]] && gzip -dc "$ARCHIVE" | grep -Fq "$MARKER"; then
    case "$ARCHIVE" in
      *"/runtime/app=geoscope/year="*"/month="*"/day="*"/hour="*) exit 0 ;;
    esac
  fi
  sleep 1
done

docker logs "$VECTOR"
exit 1
```

Mark the script executable. It must clean up all containers, networks, and temporary files through its trap whether it passes or fails.

- [ ] **Step 6: Validate static contracts, Vector syntax, and end-to-end S3 output.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_production_elasticsearch_config.py -q`

Expected: PASS.

Run when Docker is available: `docker run --rm -v "$PWD/deploy/elasticsearch/vector.yaml:/etc/vector/vector.yaml:ro" -e R2_ENDPOINT=https://example.r2.cloudflarestorage.com -e R2_BUCKET=geoscope-runtime-logs timberio/vector:0.57.0-debian validate --skip-healthchecks /etc/vector/vector.yaml`

Expected: Vector reports that the configuration is valid without contacting R2.

Run when Docker is available: `tests/vector_r2_integration.sh`

Expected: exit 0 after finding the stable marker in a gzip JSONL object under the hourly partition prefix.

- [ ] **Step 7: Commit collector deployment.**

```bash
git add deploy/elasticsearch/vector.yaml deploy/elasticsearch/docker-compose.production.yml deploy/elasticsearch/elasticsearch.env.example deploy/elasticsearch/bootstrap-ubuntu.sh tests/test_production_elasticsearch_config.py tests/vector_r2_integration.sh
git commit -m "feat: archive runtime logs to R2 with Vector"
```

### Task 4: Document secure rollout and verify the complete feature

**Files:**

- Modify: `docs/deployment/digitalocean.md`
- Modify: `tests/test_production_elasticsearch_config.py`

**Interfaces:**

- Produces: operator commands for bucket/token setup, private firewall rule, headroom check, collector rollout, App Platform rollout, R2 verification, and recovery.

- [ ] **Step 1: Write a failing runbook-contract test.**

```python
def test_runbook_covers_log_archive_rollout_and_recovery() -> None:
    text = Path("docs/deployment/digitalocean.md").read_text()
    for phrase in (
        "R2 runtime log archive", "R2_ENDPOINT", "10.124.0.2:8686",
        "App Platform VPC egress private IP", "docker stats",
        "vector validate --skip-healthchecks", "application/x-ndjson",
        "gzip -dc", "/srv/vector/data", "512 MiB",
    ):
        assert phrase in text
```

- [ ] **Step 2: Run the focused test and verify it fails.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_production_elasticsearch_config.py::test_runbook_covers_log_archive_rollout_and_recovery -q`

Expected: FAIL because the runbook has no R2 log archive section.

- [ ] **Step 3: Add the operator runbook section.**

Document these exact gates and commands:

1. Create an R2 Standard bucket named `geoscope-runtime-logs` and a bucket-scoped object read/write token; store only its S3 access key ID and secret in the ignored `.env` copied from `elasticsearch.env.example`.
2. Set `R2_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, `R2_BUCKET=geoscope-runtime-logs`, and region `auto` through Vector config.
3. Allow inbound TCP 8686 on the Droplet Cloud Firewall only from the App Platform VPC egress private IP; do not add a public source range.
4. Check `free -h`, `df -h /srv/elasticsearch /srv/vector`, and `docker stats --no-stream` before rollout.
5. Validate config, start only Vector, and confirm `ss -lntp` shows `10.124.0.2:8686` but no public/any-address listener.
6. Send a unique NDJSON marker from the App Platform console, then list/download the R2 object and inspect it with `gzip -dc`.
7. Apply the App Platform spec and confirm pre-deploy and post-deploy markers both arrive.
8. During an R2 outage, verify `/srv/vector/data` remains below the 512 MiB cap and Elasticsearch health remains yellow or green.
9. Recover by restarting/recreating only Vector; never delete `/srv/vector/data` until queued records have drained.

- [ ] **Step 4: Run focused and full automated verification.**

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest tests/test_log_export.py tests/test_production_app.py tests/test_app_platform_config.py tests/test_production_packaging.py tests/test_production_elasticsearch_config.py tests/test_mcp_packaging.py -q`

Expected: PASS.

Run: `UV_CACHE_DIR=/tmp/geo-metadata-index-uv-cache uv run pytest -q`

Expected: PASS, with live Elasticsearch/provider tests skipped unless their explicit opt-in environment variables are set.

- [ ] **Step 5: Run final hygiene checks.**

Run: `git diff --check`

Expected: no output.

Run: `rg -n "R2_SECRET_ACCESS_KEY=[^r]|R2_ACCESS_KEY_ID=[^r]" deploy .do docs`

Expected: no real credential value; only safe `replace-with-...` placeholders appear.

- [ ] **Step 6: Commit the runbook and verification contract.**

```bash
git add docs/deployment/digitalocean.md tests/test_production_elasticsearch_config.py
git commit -m "docs: add runtime log archive runbook"
```
