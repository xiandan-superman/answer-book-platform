from __future__ import annotations

import atexit
import signal
import threading
from pathlib import Path
from typing import Any

from huey import SqliteHuey

from .paths import CACHE_DIR
from .practice_jobs import load_practice_job, run_practice_job, update_practice_job
from .practice_worker import execute_practice_operation
from .runtime_capacity import practice_job_max_concurrency, runtime_capacity_summary

QUEUE_BACKEND = "huey_sqlite"
QUEUE_DATABASE = CACHE_DIR / "practice_queue.sqlite3"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _worker_count() -> int:
    return practice_job_max_concurrency()


practice_huey = SqliteHuey(
    "answer-book-practice",
    filename=str(QUEUE_DATABASE),
    results=True,
    store_none=False,
    strict_fifo=True,
    timeout=15,
)


@practice_huey.task(name="answer_book.execute_practice_job")
def execute_queued_practice_job(job_id: str) -> None:
    run_practice_job(job_id, execute_practice_operation)


def enqueue_practice_job(job_id: str, *, recovered: bool = False) -> dict[str, Any]:
    """Persist a job ID in SQLite and record queue provenance on the job."""
    record = load_practice_job(job_id)
    if record.get("status") not in {"queued", "running"}:
        return record
    if recovered:
        record = update_practice_job(
            job_id,
            status="queued",
            current_operation="服务恢复后重新排队",
            progress_message="任务已恢复并重新进入后台队列。",
        )
    result = execute_queued_practice_job(job_id)
    return update_practice_job(
        job_id,
        queue_backend=QUEUE_BACKEND,
        queue_task_id=str(result.id),
        queue_enqueued_at=record.get("updated_at"),
    )


def recover_practice_queue(records: list[dict[str, Any]]) -> dict[str, int]:
    """Requeue interrupted work; leave already-persisted queued calls alone."""
    resumed = 0
    already_queued = 0
    pending_ids = {str(task.id) for task in practice_huey.pending()}
    for record in records:
        status = str(record.get("status") or "")
        backend = str(record.get("queue_backend") or "")
        task_id = str(record.get("queue_task_id") or "")
        if status in {"queued", "running"} and backend == QUEUE_BACKEND and task_id in pending_ids:
            if status != "queued":
                update_practice_job(
                    str(record["job_id"]),
                    status="queued",
                    current_operation="服务恢复后等待已持久化的队列任务",
                    progress_message="已恢复原队列任务，未重复排队。",
                )
            already_queued += 1
            continue
        enqueue_practice_job(str(record["job_id"]), recovered=True)
        resumed += 1
    return {"resumed": resumed, "already_queued": already_queued}


_CONSUMER_LOCK = threading.Lock()
_CONSUMER: Any | None = None


def _restore_signal_handlers(handlers: dict[signal.Signals, Any]) -> None:
    for sig, handler in handlers.items():
        signal.signal(sig, handler)


def start_practice_queue_consumer() -> Any:
    """Start Huey's thread consumer once while preserving server signal handling."""
    global _CONSUMER
    with _CONSUMER_LOCK:
        if _CONSUMER is not None:
            return _CONSUMER
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        consumer = practice_huey.create_consumer(
            workers=_worker_count(),
            worker_type="thread",
            periodic=False,
            check_worker_health=True,
            health_check_interval=10,
        )
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            signals.append(signal.SIGHUP)
        handlers = {sig: signal.getsignal(sig) for sig in signals}
        try:
            consumer.start()
        finally:
            _restore_signal_handlers(handlers)
        _CONSUMER = consumer
        atexit.register(stop_practice_queue_consumer)
        return consumer


def stop_practice_queue_consumer() -> None:
    global _CONSUMER
    with _CONSUMER_LOCK:
        consumer = _CONSUMER
        _CONSUMER = None
    if consumer is not None:
        consumer.stop(graceful=False)


def practice_queue_status() -> dict[str, Any]:
    return {
        "backend": QUEUE_BACKEND,
        "database": str(Path(QUEUE_DATABASE)),
        "pending_count": len(practice_huey),
        "consumer_running": _CONSUMER is not None,
        "workers": _worker_count(),
        "capacity": runtime_capacity_summary(),
    }
