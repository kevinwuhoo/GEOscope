from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from geo_index.eutils import EutilsClient


class _ControlledSchedule:
    def __init__(self, *, delay_first_waiter: bool = True) -> None:
        self._condition = threading.Condition()
        self._now = 0.0
        self._waiters: dict[int, float] = {}
        self._delay_first_waiter = delay_first_waiter
        self._delayed_thread: int | None = None
        self._release_delayed = False
        self.starts: list[float] = []

    def clock(self) -> float:
        with self._condition:
            return self._now

    def sleep(self, seconds: float) -> None:
        ident = threading.get_ident()
        with self._condition:
            target = self._now + seconds
            self._waiters[ident] = target
            if self._delay_first_waiter and self._delayed_thread is None:
                self._delayed_thread = ident
            self._condition.notify_all()
            assert self._condition.wait_for(
                lambda: self._now >= target
                and (ident != self._delayed_thread or self._release_delayed),
                timeout=2,
            )
            del self._waiters[ident]
            self._condition.notify_all()

    def wait_for_waiters(self, count: int) -> None:
        with self._condition:
            assert self._condition.wait_for(
                lambda: len(self._waiters) == count,
                timeout=2,
            )

    def advance(self, now: float, *, release_delayed: bool = False) -> None:
        with self._condition:
            due = {
                ident
                for ident, target in self._waiters.items()
                if target <= now
                and (ident != self._delayed_thread or release_delayed)
            }
            self._now = now
            self._release_delayed = self._release_delayed or release_delayed
            self._condition.notify_all()
            assert self._condition.wait_for(
                lambda: all(self._waiters.get(ident, now + 1) > now for ident in due),
                timeout=2,
            )

    def record_start(self) -> None:
        with self._condition:
            self.starts.append(self._now)
            self._condition.notify_all()

    def wait_for_starts(self, count: int) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: len(self.starts) >= count,
                timeout=1,
            )


class _RecordingHttpClient:
    def __init__(self, schedule: _ControlledSchedule) -> None:
        self._schedule = schedule

    def get(self, path: str, **kwargs: object) -> httpx.Response:
        self._schedule.record_start()
        return httpx.Response(
            200,
            request=httpx.Request("GET", f"https://example.test{path}"),
        )

    def close(self) -> None:
        pass


class _ObservedTransportGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition()
        self._attempts = 0

    def acquire(self, *, timeout: float) -> bool:
        with self._condition:
            self._attempts += 1
            self._condition.notify_all()
        return self._lock.acquire(timeout=timeout)

    def release(self) -> None:
        self._lock.release()

    def wait_for_attempts(self, count: int) -> None:
        with self._condition:
            assert self._condition.wait_for(
                lambda: self._attempts >= count,
                timeout=2,
            )


def test_rate_gate_rechecks_the_clock_after_each_lock_free_wait() -> None:
    waits: list[float] = []
    now = 0.0

    def clock() -> float:
        return now

    def advance(seconds: float) -> None:
        nonlocal now
        waits.append(seconds)
        now += seconds

    client = EutilsClient(
        api_key=None,
        clock=clock,
        sleep=advance,
    )
    try:
        for _ in range(3):
            client._throttle(deadline=2.0)
    finally:
        client.close()

    assert waits == pytest.approx([0.4, 0.4])


def test_delayed_waiter_cannot_bunch_actual_request_starts() -> None:
    schedule = _ControlledSchedule()
    client = EutilsClient(
        api_key=None,
        max_retries=1,
        clock=schedule.clock,
        sleep=schedule.sleep,
    )
    client._client.close()
    client._client = _RecordingHttpClient(schedule)  # type: ignore[assignment]
    transport_gate = _ObservedTransportGate()
    client._transport_lock = transport_gate  # type: ignore[assignment]
    client._throttle(deadline=3.0)
    step = client._min_interval
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            requests = [
                executor.submit(client._get, "/probe", {}, deadline=3.0)
                for _ in range(4)
            ]
            transport_gate.wait_for_attempts(4)
            schedule.wait_for_waiters(1)
            schedule.advance(3 * step + 1e-9, release_delayed=True)
            assert schedule.wait_for_starts(1)
            for start_count, start_at in enumerate(range(4, 7), 2):
                schedule.wait_for_waiters(1)
                schedule.advance(start_at * step + 1e-9)
                assert schedule.wait_for_starts(start_count)
            for request in requests:
                assert request.result().status_code == 200
    finally:
        client.close()

    assert schedule.starts == pytest.approx(
        [3 * step + 1e-9, 4 * step + 1e-9, 5 * step + 1e-9, 6 * step + 1e-9]
    )
    assert all(
        sum(start <= candidate < start + 1 for candidate in schedule.starts) <= 3
        for start in schedule.starts
    )


def test_delayed_due_now_claimant_cannot_bunch_actual_request_starts() -> None:
    schedule = _ControlledSchedule(delay_first_waiter=False)
    client = EutilsClient(
        api_key=None,
        max_retries=1,
        clock=schedule.clock,
        sleep=schedule.sleep,
    )
    client._client.close()
    client._client = _RecordingHttpClient(schedule)  # type: ignore[assignment]
    transport_gate = _ObservedTransportGate()
    client._transport_lock = transport_gate  # type: ignore[assignment]
    original_request_timeout = client._request_timeout
    claim_handed_off = threading.Event()
    release_claimant = threading.Event()
    timeout_calls = 0
    timeout_calls_lock = threading.Lock()

    def delay_first_claimant(deadline: float | None) -> float:
        nonlocal timeout_calls
        with timeout_calls_lock:
            timeout_calls += 1
            is_first = timeout_calls == 1
        if is_first:
            claim_handed_off.set()
            assert release_claimant.wait(timeout=2)
        return original_request_timeout(deadline)

    client._request_timeout = delay_first_claimant  # type: ignore[method-assign]
    step = client._min_interval
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            requests = [
                executor.submit(client._get, "/probe", {}, deadline=3.0)
            ]
            assert claim_handed_off.wait(timeout=2)
            requests.extend(
                executor.submit(client._get, "/probe", {}, deadline=3.0)
                for _ in range(3)
            )
            transport_gate.wait_for_attempts(4)
            schedule.advance(3 * step + 1e-9)
            release_claimant.set()
            assert schedule.wait_for_starts(1)
            schedule.wait_for_waiters(1)
            schedule.advance(4 * step + 1e-9)
            assert schedule.wait_for_starts(2)
            schedule.wait_for_waiters(1)
            schedule.advance(5 * step + 1e-9)
            assert schedule.wait_for_starts(3)
            schedule.wait_for_waiters(1)
            schedule.advance(6 * step + 1e-9)
            assert schedule.wait_for_starts(4), schedule.starts
            for request in requests:
                assert request.result().status_code == 200
    finally:
        release_claimant.set()
        client.close()

    assert all(
        sum(start <= candidate < start + 1 for candidate in schedule.starts) <= 3
        for start in schedule.starts
    ), schedule.starts


def test_transport_gate_acquisition_is_bounded_by_the_request_deadline() -> None:
    class RejectingTransportGate:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        def acquire(self, *, timeout: float) -> bool:
            self.timeouts.append(timeout)
            return False

        def release(self) -> None:
            raise AssertionError("an unacquired transport gate must not be released")

    schedule = _ControlledSchedule(delay_first_waiter=False)
    client = EutilsClient(
        api_key=None,
        max_retries=1,
        clock=schedule.clock,
        sleep=schedule.sleep,
    )
    client._client.close()
    client._client = _RecordingHttpClient(schedule)  # type: ignore[assignment]
    transport_gate = RejectingTransportGate()
    client._transport_lock = transport_gate  # type: ignore[assignment]
    try:
        with pytest.raises(TimeoutError, match="NCBI transport gate"):
            client._get("/probe", {}, deadline=2.0)
    finally:
        client.close()

    assert transport_gate.timeouts == pytest.approx([2.0])
    assert schedule.starts == []


def test_rate_slot_wait_must_fit_inside_the_request_deadline() -> None:
    waits: list[float] = []
    client = EutilsClient(
        api_key=None,
        clock=lambda: 0.0,
        sleep=waits.append,
    )
    try:
        client._throttle(deadline=1.0)
        with pytest.raises(TimeoutError, match="NCBI rate gate"):
            client._throttle(deadline=0.2)
    finally:
        client.close()

    assert waits == []
