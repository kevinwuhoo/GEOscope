from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from geo_index.eutils import EutilsClient


def test_rate_slots_are_reserved_atomically_without_holding_the_lock_while_waiting() -> None:
    waits: list[float] = []
    waits_lock = threading.Lock()

    def record_wait(seconds: float) -> None:
        with waits_lock:
            waits.append(seconds)

    client = EutilsClient(
        api_key=None,
        clock=lambda: 0.0,
        sleep=record_wait,
    )
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda _: client._throttle(deadline=2.0), range(3)))
    finally:
        client.close()

    assert sorted(waits) == pytest.approx([0.4, 0.8])


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
