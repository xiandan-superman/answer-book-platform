from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

T = TypeVar("T")


@dataclass(frozen=True)
class _Snapshot:
    generation: int
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
        self._generations: dict[str, int] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._registry_lock:
            return self._locks.setdefault(key, threading.Lock())

    def get(self, key: str, builder: Callable[[], T]) -> T:
        # Capture a logical generation before waiting for the per-key lock.
        # High-frequency monotonic timestamps can have insufficient resolution
        # on Windows and make a later request look concurrent with an old read.
        with self._registry_lock:
            requested_generation = self._generations.get(key, 0)
        lock = self._lock_for(key)
        with lock:
            snapshot = self._snapshots.get(key)
            if snapshot is not None and snapshot.generation > requested_generation:
                return cast(T, snapshot.value)
            value = builder()
            with self._registry_lock:
                generation = self._generations.get(key, 0) + 1
                self._generations[key] = generation
            self._snapshots[key] = _Snapshot(generation=generation, value=value)
            return value


READ_SNAPSHOTS = ReadSnapshotCoalescer()
