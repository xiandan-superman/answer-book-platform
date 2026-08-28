from __future__ import annotations

import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from typing import Any, Callable, Iterable, Iterator, TypeVar

from .provider_errors import classify_provider_error
from .runtime_capacity import (
    bigmodel_rate_limit_backoff,
    model_request_max_concurrency,
    provider_request_max_concurrency,
)

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
        self._cooldown_until = 0.0
        self._rate_limit_streak = 0
        self._rate_limited_count = 0

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
            while True:
                cooldown_remaining = max(0.0, self._cooldown_until - time.monotonic())
                can_enter = (
                    cooldown_remaining <= 0
                    and self._active < self._limit
                    and self._owners
                    and self._owners[0] == owner
                    and self._queues.get(owner)
                    and self._queues[owner][0] is token
                )
                if can_enter:
                    break
                self._condition.wait(timeout=cooldown_remaining or None)
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

    def record_rate_limit(self, retry_after_seconds: float | None = None) -> None:
        with self._condition:
            self._rate_limit_streak = min(8, self._rate_limit_streak + 1)
            self._rate_limited_count += 1
            base, cap = bigmodel_rate_limit_backoff()
            exponential = min(cap, base * (2 ** (self._rate_limit_streak - 1)))
            retry_after = (
                max(0.0, min(cap, float(retry_after_seconds)))
                if isinstance(retry_after_seconds, (int, float))
                else 0.0
            )
            delay = max(exponential, retry_after)
            jitter = random.uniform(0.0, min(1.0, delay * 0.25))
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay + jitter)
            self._condition.notify_all()

    def record_success(self) -> None:
        with self._condition:
            if time.monotonic() >= self._cooldown_until:
                self._rate_limit_streak = 0

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "active": self._active,
                "waiting": sum(len(queue) for queue in self._queues.values()),
                "waiting_tasks": len(self._queues),
                "waiting_owners": list(self._owners),
                "limit": self._limit,
                "cooldown_remaining_seconds": round(max(0.0, self._cooldown_until - time.monotonic()), 3),
                "rate_limit_streak": self._rate_limit_streak,
                "rate_limited_count": self._rate_limited_count,
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


def _provider_request_limit(provider: object | None) -> int:
    return provider_request_max_concurrency(provider)


def _is_bigmodel_provider(provider: object | None) -> bool:
    return str(getattr(provider, "name", "") or "").strip().lower() == "bigmodel"


def _is_rate_limit_error(exc: BaseException) -> bool:
    info = classify_provider_error(
        exc,
        status_code=getattr(exc, "status_code", None),
        retry_after_seconds=getattr(exc, "retry_after_seconds", None),
    )
    return info.kind in {"provider_concurrency_limit", "provider_rate_limit"}


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
    """Apply the provider ceiling and shared cooldown across tasks.

    The context is re-entrant for a provider so legacy business-layer guards
    can coexist with the authoritative guard at the network client boundary.
    BigModel has a conservative default ceiling; other providers remain
    uncapped unless the global emergency ceiling is configured.
    """
    key = _provider_key(provider)
    held_keys = _MODEL_REQUEST_HELD_KEYS.get()
    if key in held_keys:
        admission_check = _MODEL_REQUEST_ADMISSION_CHECK.get()
        if admission_check:
            admission_check()
        yield
        return

    limit = _provider_request_limit(provider)
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
        try:
            yield
        except BaseException as exc:
            if _is_bigmodel_provider(provider) and _is_rate_limit_error(exc):
                retry_after = getattr(exc, "retry_after_seconds", None)
                gate.record_rate_limit(retry_after if isinstance(retry_after, (int, float)) else None)
            raise
        else:
            if _is_bigmodel_provider(provider):
                gate.record_success()
    finally:
        _MODEL_REQUEST_HELD_KEYS.reset(token)
        gate.release()


def model_request_snapshot() -> dict[str, object]:
    with _MODEL_REQUEST_LOCK:
        configured = list(_MODEL_REQUEST_GATES.items())
    rows: list[dict[str, Any]] = [
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
        "provider_specific_limits": {
            str(row.get("provider") or ""): int(row.get("limit") or 0)
            for row in rows
        },
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
        sequential_results = []
        for index, item in enumerate(values):
            result = worker(item)
            if on_complete:
                on_complete(index, item, result)
            sequential_results.append(result)
        return sequential_results

    concurrent_results: list[R | None] = [None] * len(values)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(copy_context().run, worker, item): (index, item)
            for index, item in enumerate(values)
        }
        for future in as_completed(futures):
            index, item = futures[future]
            result = future.result()
            concurrent_results[index] = result
            if on_complete:
                on_complete(index, item, result)
    return [result for result in concurrent_results if result is not None]
