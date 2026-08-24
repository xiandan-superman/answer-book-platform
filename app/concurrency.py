from __future__ import annotations

import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from typing import Callable, Iterable, Iterator, TypeVar

from .runtime_capacity import model_request_max_concurrency

T = TypeVar("T")
R = TypeVar("R")


_MODEL_REQUEST_LOCK = threading.Lock()
_MODEL_REQUEST_GATES: dict[tuple[str, str], "_FairProviderGate"] = {}
_MODEL_REQUEST_OWNER: ContextVar[str] = ContextVar("model_request_owner", default="")
_MODEL_REQUEST_ADMISSION_CHECK: ContextVar[Callable[[], None] | None] = ContextVar(
    "model_request_admission_check",
    default=None,
)
_MODEL_REQUEST_HELD_KEYS: ContextVar[frozenset[tuple[str, str]]] = ContextVar(
    "model_request_held_keys",
    default=frozenset(),
)


class _FairProviderGate:
    """A dynamic ceiling with round-robin admission between user tasks."""

    def __init__(self, limit: int):
        self._condition = threading.Condition()
        self._limit = limit
        self._active = 0
        self._queues: dict[str, deque[object]] = {}
        self._owners: deque[str] = deque()

    def set_limit(self, limit: int) -> None:
        with self._condition:
            self._limit = limit
            self._condition.notify_all()

    def acquire(self, owner: str) -> None:
        token = object()
        with self._condition:
            queue = self._queues.get(owner)
            if queue is None:
                queue = deque()
                self._queues[owner] = queue
                self._owners.append(owner)
            queue.append(token)
            while not (
                self._active < self._limit
                and self._owners
                and self._owners[0] == owner
                and self._queues.get(owner)
                and self._queues[owner][0] is token
            ):
                self._condition.wait()
            queue.popleft()
            self._owners.popleft()
            if queue:
                self._owners.append(owner)
            else:
                self._queues.pop(owner, None)
            self._active += 1
            self._condition.notify_all()

    def release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("model request gate released without an active request")
            self._active -= 1
            self._condition.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "active": self._active,
                "waiting": sum(len(queue) for queue in self._queues.values()),
                "waiting_tasks": len(self._queues),
                "waiting_owners": list(self._owners),
                "limit": self._limit,
            }


class ModelRequestAborted(RuntimeError):
    """The owning user task stopped before a queued request was admitted."""


def ensure_model_request_active() -> None:
    """Revalidate a durable owner during and immediately after network I/O."""
    admission_check = _MODEL_REQUEST_ADMISSION_CHECK.get()
    if admission_check:
        admission_check()


def _model_request_limit() -> int:
    return model_request_max_concurrency()


@contextmanager
def model_request_context(owner: str, *, admission_check: Callable[[], None] | None = None) -> Iterator[None]:
    """Associate all nested model calls with one user-visible task."""
    clean_owner = str(owner or "").strip()
    if not clean_owner:
        yield
        return
    token = _MODEL_REQUEST_OWNER.set(clean_owner)
    check_token = _MODEL_REQUEST_ADMISSION_CHECK.set(admission_check)
    try:
        yield
    finally:
        _MODEL_REQUEST_ADMISSION_CHECK.reset(check_token)
        _MODEL_REQUEST_OWNER.reset(token)


def _request_owner() -> str:
    # Calls outside a workflow (for example provider checks) still receive an
    # independent lane instead of being grouped into one anonymous bulk task.
    return _MODEL_REQUEST_OWNER.get() or f"thread:{threading.get_ident()}"


def _provider_key(provider: object | None) -> tuple[str, str]:
    return (
        str(getattr(provider, "name", "") or "default"),
        str(getattr(provider, "base_url", "") or ""),
    )


@contextmanager
def model_request_slot(provider: object | None):
    """Apply an optional emergency provider ceiling across tasks.

    The context is re-entrant for a provider so legacy business-layer guards
    can coexist with the authoritative guard at the network client boundary.
    Normal operation intentionally has no global ceiling: individual workflows
    own their concurrency and stagger admission instead of blocking unrelated
    work behind one provider-wide fixed number.
    """
    key = _provider_key(provider)
    held_keys = _MODEL_REQUEST_HELD_KEYS.get()
    if key in held_keys:
        admission_check = _MODEL_REQUEST_ADMISSION_CHECK.get()
        if admission_check:
            admission_check()
        yield
        return

    limit = _model_request_limit()
    if limit <= 0:
        admission_check = _MODEL_REQUEST_ADMISSION_CHECK.get()
        if admission_check:
            admission_check()
        yield
        return
    with _MODEL_REQUEST_LOCK:
        gate = _MODEL_REQUEST_GATES.get(key)
        if gate is None:
            gate = _FairProviderGate(limit)
            _MODEL_REQUEST_GATES[key] = gate
        else:
            gate.set_limit(limit)
    gate.acquire(_request_owner())
    admission_check = _MODEL_REQUEST_ADMISSION_CHECK.get()
    if admission_check:
        try:
            admission_check()
        except BaseException:
            gate.release()
            raise
    token = _MODEL_REQUEST_HELD_KEYS.set(held_keys | {key})
    try:
        yield
    finally:
        _MODEL_REQUEST_HELD_KEYS.reset(token)
        gate.release()


def model_request_snapshot() -> dict[str, object]:
    with _MODEL_REQUEST_LOCK:
        configured = list(_MODEL_REQUEST_GATES.items())
    rows = [
        {"provider": key[0], "base_url": key[1], **gate.snapshot()}
        for key, gate in configured
    ]
    waiting_task_ids = sorted({
        str(owner)
        for row in rows
        for owner in (row.get("waiting_owners") or [])
        if str(owner) and not str(owner).startswith("thread:")
    })
    return {
        "active": sum(int(row.get("active") or 0) for row in rows),
        "waiting": sum(int(row.get("waiting") or 0) for row in rows),
        "waiting_tasks": sum(int(row.get("waiting_tasks") or 0) for row in rows),
        "waiting_task_ids": waiting_task_ids,
        "limit": _model_request_limit(),
        "providers": rows,
    }


def run_limited_concurrent(
    items: Iterable[T],
    worker: Callable[[T], R],
    *,
    max_workers: int = 1,
    on_complete: Callable[[int, T, R], None] | None = None,
) -> list[R]:
    values = list(items)
    if not values:
        return []
    workers = max(1, min(int(max_workers or 1), len(values)))
    if workers == 1:
        results = []
        for index, item in enumerate(values):
            result = worker(item)
            if on_complete:
                on_complete(index, item, result)
            results.append(result)
        return results

    results: list[R | None] = [None] * len(values)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(copy_context().run, worker, item): (index, item)
            for index, item in enumerate(values)
        }
        for future in as_completed(futures):
            index, item = futures[future]
            result = future.result()
            results[index] = result
            if on_complete:
                on_complete(index, item, result)
    return [result for result in results if result is not None]
