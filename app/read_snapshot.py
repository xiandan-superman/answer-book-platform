from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

T = TypeVar("T")


@dataclass(frozen=True)
class _Snapshot:
    completed_at: float
    value: Any


class ReadSnapshotCoalescer:
    """Share one build across requests that were already waiting for it.

    This is deliberately not a time-to-live cache. A request that starts after
    the latest build completed performs a fresh read, so task mutations become
    visible without explicit invalidation. Only concurrent duplicate reads are
    collapsed.
    """

    def __init__(self) -> None:
        self._registry_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._snapshots: dict[str, _Snapshot] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._registry_lock:
            return self._locks.setdefault(key, threading.Lock())

    def get(self, key: str, builder: Callable[[], T]) -> T:
        requested_at = time.monotonic()
        lock = self._lock_for(key)
        with lock:
            snapshot = self._snapshots.get(key)
            if snapshot is not None and snapshot.completed_at >= requested_at:
                return cast(T, snapshot.value)
            value = builder()
            self._snapshots[key] = _Snapshot(completed_at=time.monotonic(), value=value)
            return value


READ_SNAPSHOTS = ReadSnapshotCoalescer()
