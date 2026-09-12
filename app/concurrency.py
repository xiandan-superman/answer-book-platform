from __future__ import annotations

import random
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from typing import Any, Callable, Iterable, Iterator, TypeVar, cast

from .provider_errors import classify_provider_error
from .runtime_capacity import (
    bigmodel_rate_limit_backoff,
    model_request_max_concurrency,
    provider_capacity_profile,
    provider_pressure_backoff,
    provider_request_max_concurrency,
)

T = TypeVar("T")
R = TypeVar("R")

_MISSING_CONCURRENT_RESULT = object()


_MODEL_REQUEST_LOCK = threading.Lock()
_MODEL_REQUEST_GATES: dict[tuple[str, str], "_FairProviderGate"] = {}
_UNLIMITED_ACTIVE: dict[tuple[str, str], int] = {}
_MODEL_REQUEST_OWNER: ContextVar[str] = ContextVar("model_request_owner", default="")
_BACKGROUND_REQUEST: ContextVar[tuple[threading.Event, threading.Event] | None] = ContextVar(
    "background_model_request", default=None,
)


@contextmanager
def background_model_requests(foreground_done: threading.Event, stopped: threading.Event):
    """Speculative requests use spare capacity until the foreground joins."""
    token = _BACKGROUND_REQUEST.set((foreground_done, stopped))
    try:
        yield
    finally:
        _BACKGROUND_REQUEST.reset(token)


_MODEL_REQUEST_ADMISSION_CHECK: ContextVar[Callable[[], None] | None] = ContextVar(
    "model_request_admission_check",
    default=None,
)
_MODEL_REQUEST_ADMITTED_CALLBACK: ContextVar[Callable[[], None] | None] = ContextVar(
    "model_request_admitted_callback",
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
        self._configured_limit = limit
        self._active = 0
        self._background_active = 0
        self._background: dict[object, tuple[threading.Event, threading.Event]] = {}
        self._queues: dict[str, deque[object]] = {}
        self._owners: deque[str] = deque()
        self._cooldown_until = 0.0
        self._rate_limit_streak = 0
        self._rate_limited_count = 0
        self._provider_pressure_count = 0
        self._recovery_successes = 0

    def set_limit(self, limit: int) -> None:
        with self._condition:
            self._configured_limit = limit
            self._limit = min(self._limit, limit)
            self._condition.notify_all()

    def acquire(self, owner: str) -> bool:
        token = object()
        with self._condition:
            queue = self._queues.get(owner)
            if queue is None:
                queue = deque()
                self._queues[owner] = queue
                self._owners.append(owner)
            queue.append(token)
            background = _BACKGROUND_REQUEST.get()
            if background:
                self._background[token] = background
            try:
                while True:
                    if background and background[1].is_set():
                        raise ModelRequestAborted("Parallel answer draft stopped")
                    check = _MODEL_REQUEST_ADMISSION_CHECK.get()
                    if check:
                        check()
                    # Preserve task round-robin within each priority. A foreground
                    # request may bypass a speculative request of the same task.
                    normal = next((item for name in self._owners for item in self._queues[name]
                                   if item not in self._background or self._background[item][0].is_set()), None)
                    first = normal if normal is not None else self._queues[self._owners[0]][0]
                    low = bool(background and not background[0].is_set())
                    spare = not low or self._background_active < max(0, self._limit - 1)
                    if time.monotonic() >= self._cooldown_until and self._active < self._limit and first is token and spare:
                        break
                    self._condition.wait(timeout=0.25)
            except BaseException:
                queue.remove(token)
                self._background.pop(token, None)
                if not queue:
                    self._queues.pop(owner, None)
                    self._owners.remove(owner)
                self._condition.notify_all()
                raise
            queue.remove(token)
            self._background.pop(token, None)
            self._owners.remove(owner)
            if queue:
                self._owners.append(owner)
            else:
                self._queues.pop(owner, None)
            self._active += 1
            self._background_active += int(low)
            self._condition.notify_all()
            return low

    def release(self, background: bool = False) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("model request gate released without an active request")
            self._active -= 1
            self._background_active -= int(background)
            self._condition.notify_all()

    def record_rate_limit(
        self,
        retry_after_seconds: float | None = None,
        *,
        backoff: tuple[float, float] | None = None,
    ) -> None:
        with self._condition:
            self._rate_limit_streak = min(8, self._rate_limit_streak + 1)
            self._rate_limited_count += 1
            base, cap = backoff or bigmodel_rate_limit_backoff()
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

    def record_provider_pressure(
        self,
        *,
        retry_after_seconds: float | None = None,
        backoff: tuple[float, float] | None = None,
    ) -> None:
        """Cool the shared route and reduce concurrency after transport pressure."""

        with self._condition:
            self._provider_pressure_count = min(8, self._provider_pressure_count + 1)
            self._limit = max(1, self._limit // 2)
            self._recovery_successes = 0
        self.record_rate_limit(retry_after_seconds, backoff=backoff)

    def record_success(self) -> None:
        with self._condition:
            if time.monotonic() >= self._cooldown_until:
                self._rate_limit_streak = 0
                self._recovery_successes += 1
                if self._recovery_successes >= max(3, self._limit):
                    self._limit = min(self._configured_limit, self._limit + 1)
                    self._recovery_successes = 0
                    self._provider_pressure_count = max(0, self._provider_pressure_count - 1)
                    self._condition.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "active": self._active,
                "background_active": self._background_active,
                "background_waiting": len(self._background),
                "waiting": sum(len(queue) for queue in self._queues.values()),
                "waiting_tasks": len(self._queues),
                "waiting_owners": list(self._owners),
                "limit": self._limit,
                "configured_limit": self._configured_limit,
                "cooldown_remaining_seconds": round(max(0.0, self._cooldown_until - time.monotonic()), 3),
                "rate_limit_streak": self._rate_limit_streak,
                "rate_limited_count": self._rate_limited_count,
                "provider_pressure_count": self._provider_pressure_count,
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


def _is_profiled_provider(provider: object | None) -> bool:
    return bool(provider_capacity_profile(provider))


def _is_rate_limit_error(exc: BaseException) -> bool:
    info = classify_provider_error(
        exc,
        status_code=getattr(exc, "status_code", None),
        retry_after_seconds=getattr(exc, "retry_after_seconds", None),
    )
    return info.kind in {"provider_concurrency_limit", "provider_rate_limit"}


@contextmanager
def model_request_context(
    owner: str,
    *,
    admission_check: Callable[[], None] | None = None,
    admitted_callback: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Associate all nested model calls with one user-visible task."""
    clean_owner = str(owner or "").strip()
    if not clean_owner:
        yield
        return
    token = _MODEL_REQUEST_OWNER.set(clean_owner)
    check_token = _MODEL_REQUEST_ADMISSION_CHECK.set(admission_check)
    admitted_token = _MODEL_REQUEST_ADMITTED_CALLBACK.set(admitted_callback)
    try:
        yield
    finally:
        _MODEL_REQUEST_ADMITTED_CALLBACK.reset(admitted_token)
        _MODEL_REQUEST_ADMISSION_CHECK.reset(check_token)
        _MODEL_REQUEST_OWNER.reset(token)


def _request_owner() -> str:
    # Calls outside a workflow (for example provider checks) still receive an
    # independent lane instead of being grouped into one anonymous bulk task.
    return _MODEL_REQUEST_OWNER.get() or f"thread:{threading.get_ident()}"


def _provider_key(provider: object | None) -> tuple[str, str]:
    provider_name = str(getattr(provider, "name", "") or "default").strip().lower()
    if provider_name == "lingsuan" or provider_name.startswith("lingsuan_"):
        # Google/OpenAI protocol adapters point at one upstream platform and
        # must therefore share one admission gate rather than each consuming
        # the full platform allowance.
        provider_name = "lingsuan"
    return (
        provider_name,
        str(getattr(provider, "base_url", "") or ""),
    )


def _capacity_pool_id(key: tuple[str, str]) -> str:
    import hashlib

    return hashlib.sha256("\x1f".join(key).encode()).hexdigest()[:24]


def provider_capacity_snapshot() -> list[dict[str, Any]]:
    """Report actual shared gates, counting a pool once across its models."""
    from .provider_control import capacity_observations
    from .settings import list_providers

    observations = capacity_observations()
    pools: dict[tuple[str, str], dict[str, Any]] = {}
    with _MODEL_REQUEST_LOCK:
        gates = dict(_MODEL_REQUEST_GATES)
        unlimited_active = dict(_UNLIMITED_ACTIVE)
    for provider in list_providers().values():
        if not getattr(provider, "api_key", ""):
            continue
        key = _provider_key(provider)
        limit = _provider_request_limit(provider)
        row = pools.setdefault(key, {
            "pool_id": _capacity_pool_id(key), "providers": [], "supports_text": False,
            "configured_limit": limit, "limit": limit, "active": 0, "waiting": 0,
        })
        row["providers"].append(provider.name)
        row["supports_text"] |= bool(getattr(provider, "supports_text_generation", True))
        if key in gates:
            row.update({name: value for name, value in gates[key].snapshot().items() if name != "waiting_owners"})
        elif limit <= 0:
            row["active"] = unlimited_active.get(key, 0)
        row["observations"] = observations.get(row["pool_id"], {})
    return list(pools.values())


def _record_capacity(key: tuple[str, str], concurrent: int, succeeded: bool, pressure: bool) -> None:
    try:
        from .provider_control import record_capacity_observation

        record_capacity_observation(_capacity_pool_id(key), concurrent=concurrent, success=succeeded, pressure=pressure)
    except Exception:
        # Monitoring storage must not turn a completed request into a failure.
        pass


@contextmanager
def model_request_slot(provider: object | None):
    """Apply the provider ceiling and shared cooldown across tasks.

    The context is re-entrant for a provider so legacy business-layer guards
    can coexist with the authoritative guard at the network client boundary.
    BigModel and Lingsuan have conservative default ceilings; other providers
    remain uncapped unless the global emergency ceiling is configured.
    """
    key = _provider_key(provider)
    background_request = _BACKGROUND_REQUEST.get()
    if background_request and background_request[1].is_set():
        raise ModelRequestAborted("Parallel answer draft stopped")
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
        admitted_callback = _MODEL_REQUEST_ADMITTED_CALLBACK.get()
        if admitted_callback:
            admitted_callback()
        with _MODEL_REQUEST_LOCK:
            concurrent = _UNLIMITED_ACTIVE.get(key, 0) + 1
            _UNLIMITED_ACTIVE[key] = concurrent
        token = _MODEL_REQUEST_HELD_KEYS.set(held_keys | {key})
        succeeded = False
        pressure = False
        try:
            yield
            succeeded = True
        except BaseException as exc:
            pressure = _is_rate_limit_error(exc)
            raise
        finally:
            _MODEL_REQUEST_HELD_KEYS.reset(token)
            with _MODEL_REQUEST_LOCK:
                _UNLIMITED_ACTIVE[key] = max(0, _UNLIMITED_ACTIVE.get(key, 0) - 1)
            _record_capacity(key, concurrent, succeeded, pressure)
        return
    with _MODEL_REQUEST_LOCK:
        gate = _MODEL_REQUEST_GATES.get(key)
        if gate is None:
            gate = _FairProviderGate(limit)
            _MODEL_REQUEST_GATES[key] = gate
        else:
            gate.set_limit(limit)
    background = gate.acquire(_request_owner())
    admission_check = _MODEL_REQUEST_ADMISSION_CHECK.get()
    if admission_check:
        try:
            admission_check()
        except BaseException:
            gate.release(background)
            raise
    admitted_callback = _MODEL_REQUEST_ADMITTED_CALLBACK.get()
    if admitted_callback:
        try:
            admitted_callback()
        except BaseException:
            gate.release(background)
            raise
    token = _MODEL_REQUEST_HELD_KEYS.set(held_keys | {key})
    concurrent = int(cast(int, gate.snapshot()["active"]))
    succeeded = False
    pressure = False
    try:
        try:
            yield
        except BaseException as exc:
            pressure = _is_rate_limit_error(exc)
            if _is_rate_limit_error(exc) and _is_profiled_provider(provider):
                retry_after = getattr(exc, "retry_after_seconds", None)
                gate.record_provider_pressure(
                    retry_after_seconds=retry_after if isinstance(retry_after, (int, float)) else None,
                    backoff=provider_pressure_backoff(provider),
                )
            elif _is_rate_limit_error(exc):
                retry_after = getattr(exc, "retry_after_seconds", None)
                gate.record_rate_limit(retry_after if isinstance(retry_after, (int, float)) else None)
            raise
        else:
            succeeded = True
            if _is_bigmodel_provider(provider) or _is_profiled_provider(provider):
                gate.record_success()
    finally:
        _MODEL_REQUEST_HELD_KEYS.reset(token)
        gate.release(background)
        _record_capacity(key, concurrent, succeeded, pressure)


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

    concurrent_results: list[R | object] = [_MISSING_CONCURRENT_RESULT] * len(values)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = iter(enumerate(values))
        futures: dict[Future[R], tuple[int, T]] = {}

        def replenish() -> None:
            while len(futures) < workers:
                try:
                    index, item = next(pending)
                except StopIteration:
                    return
                futures[executor.submit(copy_context().run, worker, item)] = (index, item)

        replenish()
        try:
            while futures:
                completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                # Commit/check downstream work before admitting more producers.
                # Callback failure or cancellation stops replenishment, while
                # the executor drains already-started calls on exit.
                for future in sorted(completed, key=lambda value: futures[value][0]):
                    index, item = futures.pop(future)
                    result = future.result()
                    concurrent_results[index] = result
                    if on_complete:
                        on_complete(index, item, result)
                replenish()
        finally:
            for future in futures:
                future.cancel()
    missing_indexes = [index for index, result in enumerate(concurrent_results) if result is _MISSING_CONCURRENT_RESULT]
    if missing_indexes:
        raise RuntimeError(f"concurrent workers did not produce results for indexes: {missing_indexes}")
    return [cast(R, result) for result in concurrent_results]
