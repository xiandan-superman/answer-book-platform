from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.read_snapshot import ReadSnapshotCoalescer


def test_concurrent_waiters_share_one_completed_snapshot() -> None:
    cache = ReadSnapshotCoalescer()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def build() -> dict[str, int]:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2)
        return {"version": calls}

    with ThreadPoolExecutor(max_workers=12) as pool:
        first = pool.submit(cache.get, "tasks", build)
        assert entered.wait(2)
        waiters = [pool.submit(cache.get, "tasks", build) for _ in range(11)]
        time.sleep(0.03)
        release.set()
        results = [first.result(), *(future.result() for future in waiters)]

    assert calls == 1
    assert results == [{"version": 1}] * 12


def test_request_started_after_completion_builds_fresh_snapshot() -> None:
    cache = ReadSnapshotCoalescer()
    calls = 0

    def build() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert cache.get("tasks", build) == 1
    assert cache.get("tasks", build) == 2
    assert calls == 2


def test_failed_build_is_not_cached() -> None:
    cache = ReadSnapshotCoalescer()
    calls = 0

    def build() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary read failure")
        return "ok"

    with pytest.raises(RuntimeError, match="temporary read failure"):
        cache.get("tasks", build)
    assert cache.get("tasks", build) == "ok"
    assert calls == 2
