from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from io import StringIO
from uuid import UUID

import httpx
import pytest

from geo_index.log_export import (
    BoundedEventQueue,
    DropReporter,
    HttpBatchSender,
    LogExporter,
    LogExportSettings,
    serialize_record,
)


_EVENT_ID = UUID("00000000-0000-0000-0000-000000000001")


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        "geo_index.test",
        logging.ERROR,
        __file__,
        1,
        message,
        args,
        None,
    )


def _serialize(record: logging.LogRecord) -> bytes | None:
    return serialize_record(
        record,
        now=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
        event_id_factory=lambda: _EVENT_ID,
    )


def test_disabled_export_requires_no_url() -> None:
    assert LogExportSettings.from_env({}) is None
    assert LogExportSettings.from_env(
        {"GEO_LOG_EXPORT_ENABLED": "false"}
    ) is None


def test_enabled_export_requires_an_http_url() -> None:
    with pytest.raises(ValueError, match="GEO_LOG_EXPORT_URL"):
        LogExportSettings.from_env({"GEO_LOG_EXPORT_ENABLED": "true"})

    with pytest.raises(ValueError, match="GEO_LOG_EXPORT_URL"):
        LogExportSettings.from_env(
            {
                "GEO_LOG_EXPORT_ENABLED": "true",
                "GEO_LOG_EXPORT_URL": "ftp://collector.test/events",
            }
        )


def test_plain_record_becomes_redacted_json_with_stable_event_id() -> None:
    payload = _serialize(_record("password=%s failed", "secret-value"))

    assert payload is not None
    assert json.loads(payload) == {
        "event": "python.log",
        "event_id": str(_EVENT_ID),
        "timestamp": "2026-07-15T00:00:00Z",
        "level": "error",
        "logger": "geo_index.test",
        "message": "password=[REDACTED] failed",
    }


def test_structured_record_preserves_safe_fields_and_redacts_nested_secrets() -> None:
    payload = _serialize(
        _record(
            json.dumps(
                {
                    "event": "search.completed",
                    "query": "mouse atlas",
                    "request": {
                        "Authorization": "Bearer secret",
                        "input_tokens": 42,
                    },
                }
            )
        )
    )

    assert payload is not None
    event = json.loads(payload)
    assert event["event"] == "search.completed"
    assert event["query"] == "mouse atlas"
    assert event["request"] == {
        "Authorization": "[REDACTED]",
        "input_tokens": 42,
    }
    assert event["event_id"] == str(_EVENT_ID)
    assert event["timestamp"] == "2026-07-15T00:00:00Z"


def test_successful_health_event_is_omitted_but_failure_is_retained() -> None:
    successful = _record(
        json.dumps(
            {
                "event": "request.completed",
                "method": "GET",
                "route": "/healthz",
                "status_code": 200,
            }
        )
    )
    failed = _record(
        json.dumps(
            {
                "event": "request.completed",
                "method": "GET",
                "route": "/healthz",
                "status_code": 503,
            }
        )
    )

    assert _serialize(successful) is None
    failed_payload = _serialize(failed)
    assert failed_payload is not None
    assert json.loads(failed_payload)["status_code"] == 503


def test_queue_enforces_event_and_byte_limits_without_blocking() -> None:
    queue = BoundedEventQueue(max_events=2, max_bytes=5)

    assert queue.put_nowait(b"aa") is True
    assert queue.put_nowait(b"bbb") is True
    assert queue.put_nowait(b"x") is False
    assert queue.get(timeout=0) == b"aa"
    assert queue.put_nowait(b"x") is True
    assert queue.get(timeout=0) == b"bbb"
    assert queue.get(timeout=0) == b"x"
    assert queue.empty()


class _FailingOnceSender:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.thread_ids: list[int] = []
        self.sent = threading.Event()
        self.closed = False

    def send(self, payload: bytes) -> None:
        self.payloads.append(payload)
        self.thread_ids.append(threading.get_ident())
        if len(self.payloads) == 1:
            raise OSError("collector unavailable")
        self.sent.set()

    def close(self) -> None:
        self.closed = True


def test_exporter_retries_the_same_batch_only_on_its_worker_thread() -> None:
    sender = _FailingOnceSender()
    settings = LogExportSettings(
        url="http://collector.test/events",
        flush_interval_seconds=0.01,
    )
    exporter = LogExporter(
        settings,
        sender=sender,
        retry_delays=(0.01,),
    )
    main_thread = threading.get_ident()

    exporter.start()
    try:
        logging.getLogger("geo_index.archive_test").warning("archive me")
        assert sender.sent.wait(timeout=2)
    finally:
        exporter.stop()

    assert len(sender.payloads) == 2
    first = json.loads(sender.payloads[0].splitlines()[0])
    second = json.loads(sender.payloads[1].splitlines()[0])
    assert first["event_id"] == second["event_id"]
    assert sender.thread_ids[0] == sender.thread_ids[1]
    assert sender.thread_ids[0] != main_thread
    assert sender.closed is True


class _RecordingSender:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.sent = threading.Event()

    def send(self, payload: bytes) -> None:
        self.payloads.append(payload)
        self.sent.set()

    def close(self) -> None:
        pass


def test_exporter_batches_multiple_records_into_ndjson() -> None:
    sender = _RecordingSender()
    settings = LogExportSettings(
        url="http://collector.test/events",
        flush_interval_seconds=1,
        max_batch_events=2,
    )
    exporter = LogExporter(settings, sender=sender)

    exporter.start()
    try:
        logger = logging.getLogger("geo_index.batch_test")
        logger.warning("first")
        logger.warning("second")
        assert sender.sent.wait(timeout=2)
    finally:
        exporter.stop()

    events = [json.loads(line) for line in sender.payloads[0].splitlines()]
    assert [event["message"] for event in events] == ["first", "second"]


def test_drop_reporter_rate_limits_and_accumulates_warnings() -> None:
    stream = StringIO()
    times = iter((0.0, 30.0, 61.0))
    reporter = DropReporter(
        interval_seconds=60,
        clock=lambda: next(times),
        stream=stream,
    )

    reporter.record_drop()
    reporter.record_drop()
    reporter.record_drop()

    assert stream.getvalue().splitlines() == [
        "WARNING geo_index.log_export dropped 1 remote log record(s); "
        "stdout logging continues",
        "WARNING geo_index.log_export dropped 2 remote log record(s); "
        "stdout logging continues",
    ]


def test_http_sender_posts_ndjson_with_the_expected_content_type() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    sender = HttpBatchSender(
        "http://collector.test/events",
        timeout=5,
        client=client,
    )

    sender.send(b'{"event":"test"}\n')
    sender.close()

    assert len(requests) == 1
    assert requests[0].url == "http://collector.test/events"
    assert requests[0].headers["content-type"] == "application/x-ndjson"
    assert requests[0].content == b'{"event":"test"}\n'


class _AlwaysFailingSender:
    def __init__(self) -> None:
        self.attempted = threading.Event()
        self.attempts = 0
        self.closed = False

    def send(self, payload: bytes) -> None:
        self.attempts += 1
        self.attempted.set()
        raise OSError("collector unavailable")

    def close(self) -> None:
        self.closed = True


def test_stop_interrupts_retry_delay_and_makes_one_final_attempt() -> None:
    sender = _AlwaysFailingSender()
    exporter = LogExporter(
        LogExportSettings(
            url="http://collector.test/events",
            flush_interval_seconds=0.01,
        ),
        sender=sender,
        retry_delays=(30,),
    )

    exporter.start()
    logging.getLogger("geo_index.shutdown_test").warning("flush me")
    assert sender.attempted.wait(timeout=2)
    exporter.stop(timeout=1)

    assert sender.attempts == 2
    assert sender.closed is True
