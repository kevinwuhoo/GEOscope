"""Nonblocking runtime log export for the production ASGI process."""

from __future__ import annotations

import json
import logging
import queue
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, TextIO
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx


_SENSITIVE_KEYS = {
    "access_key_id",
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret_access_key",
    "set_cookie",
}
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|password|api[_-]?key|"
    r"access[_-]?key(?:[_-]?id)?|secret[_-]?access[_-]?key)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


@dataclass(frozen=True)
class LogExportSettings:
    """Validated runtime settings for the remote log exporter."""

    url: str
    request_timeout_seconds: float = 5.0
    flush_interval_seconds: float = 1.0
    max_batch_events: int = 100
    max_batch_bytes: int = 1024 * 1024
    max_queue_events: int = 1000
    max_queue_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str]
    ) -> LogExportSettings | None:
        enabled = environ.get(
            "GEO_LOG_EXPORT_ENABLED", "false"
        ).strip().lower()
        if enabled not in {"true", "false"}:
            raise ValueError("GEO_LOG_EXPORT_ENABLED must be true or false")
        if enabled == "false":
            return None

        url = environ.get("GEO_LOG_EXPORT_URL", "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "GEO_LOG_EXPORT_URL must be an absolute HTTP URL"
            )
        return cls(url=url)


def _normalized_key(key: object) -> str:
    return str(key).strip().casefold().replace("-", "_")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _normalized_key(key) in _SENSITIVE_KEYS
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(message: str) -> str:
    without_bearer = _BEARER_PATTERN.sub("Bearer [REDACTED]", message)
    return _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        without_bearer,
    )


def _successful_health_event(event: Mapping[str, object]) -> bool:
    status = event.get("status_code")
    return (
        event.get("event") == "request.completed"
        and event.get("method") == "GET"
        and event.get("route") == "/healthz"
        and isinstance(status, int)
        and 200 <= status < 300
    )


def serialize_record(
    record: logging.LogRecord,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    event_id_factory: Callable[[], UUID] = uuid4,
) -> bytes | None:
    """Serialize one log record into a safe, stable JSON event."""

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
    event.setdefault(
        "timestamp",
        now()
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    )
    event.setdefault("level", record.levelname.lower())
    event.setdefault("logger", record.name)
    return json.dumps(
        event,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class BoundedEventQueue:
    """A nonblocking event queue capped by count and serialized bytes."""

    def __init__(self, *, max_events: int, max_bytes: int) -> None:
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max_events)
        self._max_bytes = max_bytes
        self._current_bytes = 0
        self._byte_lock = threading.Lock()

    def put_nowait(self, payload: bytes) -> bool:
        with self._byte_lock:
            if self._current_bytes + len(payload) > self._max_bytes:
                return False
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                return False
            self._current_bytes += len(payload)
            return True

    def get(self, timeout: float | None = None) -> bytes:
        payload = self._queue.get(timeout=timeout)
        with self._byte_lock:
            self._current_bytes -= len(payload)
        return payload

    def empty(self) -> bool:
        return self._queue.empty()


class BatchSender(Protocol):
    def send(self, payload: bytes) -> None: ...
    def close(self) -> None: ...


class HttpBatchSender:
    """Send NDJSON batches to the private Vector HTTP source."""

    def __init__(
        self,
        url: str,
        timeout: float,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._client = client or httpx.Client(timeout=timeout)

    def send(self, payload: bytes) -> None:
        response = self._client.post(
            self._url,
            content=payload,
            headers={"Content-Type": "application/x-ndjson"},
        )
        response.raise_for_status()

    def close(self) -> None:
        self._client.close()


class DropReporter:
    """Report accumulated remote-copy drops without entering logging."""

    def __init__(
        self,
        *,
        interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        stream: TextIO = sys.stderr,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._stream = stream
        self._lock = threading.Lock()
        self._last_reported = float("-inf")
        self._pending_drops = 0

    def record_drop(self) -> None:
        with self._lock:
            self._pending_drops += 1
            now = self._clock()
            if now - self._last_reported < self._interval_seconds:
                return
            count = self._pending_drops
            self._pending_drops = 0
            self._last_reported = now
        self._stream.write(
            "WARNING geo_index.log_export dropped "
            f"{count} remote log record(s); stdout logging continues\n"
        )
        self._stream.flush()


class LogExportHandler(logging.Handler):
    """Serialize and enqueue records without waiting for network I/O."""

    _IGNORED_LOGGER_PREFIXES = (
        "geo_index.log_export",
        "httpcore",
        "httpx",
    )

    def __init__(
        self,
        event_queue: BoundedEventQueue,
        *,
        max_event_bytes: int,
        drop_reporter: DropReporter,
    ) -> None:
        super().__init__(level=logging.INFO)
        self._event_queue = event_queue
        self._max_event_bytes = max_event_bytes
        self._drop_reporter = drop_reporter

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(self._IGNORED_LOGGER_PREFIXES):
            return
        try:
            payload = serialize_record(record)
        except Exception:
            self._drop_reporter.record_drop()
            return
        if payload is None:
            return
        if len(payload) > self._max_event_bytes:
            self._drop_reporter.record_drop()
            return
        if not self._event_queue.put_nowait(payload):
            self._drop_reporter.record_drop()


class LogExporter:
    """Own logging handlers and a retrying background batch worker."""

    def __init__(
        self,
        settings: LogExportSettings,
        *,
        sender: BatchSender | None = None,
        retry_delays: tuple[float, ...] = (1, 2, 4, 8, 16, 30),
    ) -> None:
        if not retry_delays:
            raise ValueError("retry_delays must not be empty")
        self._settings = settings
        self._event_queue = BoundedEventQueue(
            max_events=settings.max_queue_events,
            max_bytes=settings.max_queue_bytes,
        )
        self._sender = sender or HttpBatchSender(
            settings.url,
            settings.request_timeout_seconds,
        )
        self._retry_delays = retry_delays
        self._drop_reporter = DropReporter()
        self._handler = LogExportHandler(
            self._event_queue,
            max_event_bytes=settings.max_batch_bytes,
            drop_reporter=self._drop_reporter,
        )
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="geo-log-export",
            daemon=True,
        )
        self._started = False
        self._handler_loggers: list[logging.Logger] = []
        self._app_logger = logging.getLogger("geo_index")
        self._app_logger_previous_level = self._app_logger.level
        self._stdout_handler: logging.Handler | None = None
        self._carry: bytes | None = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._attach_handler(logging.getLogger())
        self._attach_handler(logging.getLogger("uvicorn"))
        self._install_application_stdout_handler()
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        if not self._started:
            return
        self._stopping.set()
        self._thread.join(timeout=max(0.0, timeout))
        self._detach_handlers()
        self._sender.close()
        self._started = False

    def _attach_handler(self, logger: logging.Logger) -> None:
        if self._handler not in logger.handlers:
            logger.addHandler(self._handler)
            self._handler_loggers.append(logger)

    def _install_application_stdout_handler(self) -> None:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s %(message)s")
        )
        self._app_logger.setLevel(logging.INFO)
        self._app_logger.addHandler(handler)
        self._stdout_handler = handler

    def _detach_handlers(self) -> None:
        for logger in self._handler_loggers:
            logger.removeHandler(self._handler)
        self._handler_loggers.clear()
        if self._stdout_handler is not None:
            self._app_logger.removeHandler(self._stdout_handler)
            self._stdout_handler.close()
            self._stdout_handler = None
        self._app_logger.setLevel(self._app_logger_previous_level)

    def _run(self) -> None:
        pending: bytes | None = None
        retry_index = 0
        while True:
            if pending is None:
                events = self._next_batch()
                if not events:
                    if self._stopping.is_set() and self._event_queue.empty():
                        return
                    continue
                pending = b"\n".join(events) + b"\n"
                retry_index = 0

            try:
                self._sender.send(pending)
            except Exception:
                if self._stopping.is_set():
                    return
                delay = self._retry_delays[
                    min(retry_index, len(self._retry_delays) - 1)
                ]
                retry_index += 1
                self._stopping.wait(delay)
                continue

            pending = None
            if (
                self._stopping.is_set()
                and self._carry is None
                and self._event_queue.empty()
            ):
                return

    def _next_batch(self) -> list[bytes]:
        first = self._carry
        self._carry = None
        if first is None:
            try:
                first = self._event_queue.get(timeout=0.1)
            except queue.Empty:
                return []

        batch = [first]
        batch_bytes = len(first)
        deadline = time.monotonic() + self._settings.flush_interval_seconds
        while len(batch) < self._settings.max_batch_events:
            timeout = max(0.0, deadline - time.monotonic())
            if self._stopping.is_set():
                timeout = 0.0
            if timeout <= 0:
                break
            try:
                event = self._event_queue.get(timeout=timeout)
            except queue.Empty:
                break
            projected = batch_bytes + 1 + len(event)
            if projected > self._settings.max_batch_bytes:
                self._carry = event
                break
            batch.append(event)
            batch_bytes = projected
        return batch
