from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import TASKS_DIR, ensure_project_dirs
from .resource_ids import bounded_resource_path

_STORE_LOCK = threading.RLock()


@dataclass
class TaskRecord:
    task_id: str
    exam_path: str
    textbooks_dir: str
    provider: str
    model: str
    status: str
    created_at: str
    updated_at: str
    current_stage: str = "created"
    error: str = ""
    selected_textbooks: list[str] | None = None
    textbook_display_names: dict[str, str] | None = None
    model_thinking: str = "auto"
    reasoning_provider: str = ""
    reasoning_model: str = ""
    answer_provider: str = ""
    answer_model: str = ""
    correctness_provider: str = ""
    correctness_model: str = ""
    vision_provider: str = ""
    vision_model: str = ""
    image_provider: str = ""
    image_model: str = ""
    health_status: str = "unknown"
    current_operation: str = ""
    completed_count: int = 0
    total_count: int = 0
    last_heartbeat_at: str = ""
    last_progress_at: str = ""
    active_item: str = ""
    active_since: str = ""
    warning_reason: str = ""
    suggested_action: str = ""
    last_run_use_model: bool = True
    last_run_render: bool = True
    last_run_reuse_fragments: bool = False
    interrupted_stage: str = ""
    run_started_at: str = ""
    last_run_duration_seconds: int = 0


INTERRUPTED_ON_STARTUP_STATUSES = {"running", "queued"}
SUCCESS_TERMINAL_STATUSES = {"completed", "completed_with_issues"}
TERMINAL_STATUSES = SUCCESS_TERMINAL_STATUSES | {"failed", "cancelled"}


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text).strip("_")
    return slug[:40] or "task"


def new_task_id(exam_path: str) -> str:
    base = slugify(Path(exam_path).stem)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = f"{base}_{stamp}"
    existing = {p.name for p in TASKS_DIR.iterdir() if p.is_dir()} if TASKS_DIR.exists() else set()
    if candidate not in existing:
        return candidate
    suffix = 1
    while f"{candidate}_{suffix}" in existing:
        suffix += 1
    return f"{candidate}_{suffix}"


def create_task(
    exam_path: str,
    textbooks_dir: str,
    provider: str,
    model: str,
    model_thinking: str = "auto",
    reasoning_provider: str = "",
    reasoning_model: str = "",
    answer_provider: str = "",
    answer_model: str = "",
    correctness_provider: str = "",
    correctness_model: str = "",
    vision_provider: str = "",
    vision_model: str = "",
    image_provider: str = "",
    image_model: str = "",
) -> TaskRecord:
    ensure_project_dirs()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    record = TaskRecord(
        task_id=new_task_id(exam_path),
        exam_path=exam_path,
        textbooks_dir=textbooks_dir,
        provider=provider,
        model=model,
        model_thinking=model_thinking,
        reasoning_provider=reasoning_provider,
        reasoning_model=reasoning_model,
        answer_provider=answer_provider,
        answer_model=answer_model,
        correctness_provider=correctness_provider,
        correctness_model=correctness_model,
        vision_provider=vision_provider,
        vision_model=vision_model,
        image_provider=image_provider,
        image_model=image_model,
        status="created",
        created_at=now,
        updated_at=now,
    )
    task_dir(record.task_id).mkdir(parents=True, exist_ok=False)
    save_task(record)
    append_event(record.task_id, "created", asdict(record))
    return record


def task_dir(task_id: str) -> Path:
    return bounded_resource_path(TASKS_DIR, task_id)


def task_record_path(task_id: str) -> Path:
    return task_dir(task_id) / "task.json"


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_task(record: TaskRecord) -> None:
    with _STORE_LOCK:
        _atomic_write_json(task_record_path(record.task_id), asdict(record))


def load_task(task_id: str) -> TaskRecord:
    with _STORE_LOCK:
        data = json.loads(task_record_path(task_id).read_text(encoding="utf-8"))
    data.setdefault("reasoning_provider", "")
    data.setdefault("reasoning_model", "")
    data.setdefault("answer_provider", "")
    data.setdefault("answer_model", "")
    data.setdefault("correctness_provider", "")
    data.setdefault("correctness_model", "")
    data.setdefault("vision_provider", "")
    data.setdefault("vision_model", "")
    data.setdefault("image_provider", "")
    data.setdefault("image_model", "")
    data.setdefault("health_status", "unknown")
    data.setdefault("current_operation", "")
    data.setdefault("completed_count", 0)
    data.setdefault("total_count", 0)
    data.setdefault("last_heartbeat_at", "")
    data.setdefault("last_progress_at", "")
    data.setdefault("active_item", "")
    data.setdefault("active_since", "")
    data.setdefault("warning_reason", "")
    data.setdefault("suggested_action", "")
    data.setdefault("last_run_use_model", True)
    data.setdefault("last_run_render", True)
    data.setdefault("last_run_reuse_fragments", False)
    data.setdefault("interrupted_stage", "")
    data.setdefault("run_started_at", "")
    data.setdefault("last_run_duration_seconds", 0)
    return TaskRecord(**data)


def update_task(task_id: str, *, status: str | None = None, current_stage: str | None = None, error: str | None = None) -> TaskRecord:
    with _STORE_LOCK:
        record = load_task(task_id)
        previous_status = record.status
        if status is not None:
            record.status = status
        if current_stage is not None:
            record.current_stage = current_stage
        if error is not None:
            record.error = error
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if status == "running" and previous_status != "running":
            record.run_started_at = now
            record.last_run_duration_seconds = 0
            record.completed_count = 0
            record.total_count = 0
            record.active_item = ""
        if status in SUCCESS_TERMINAL_STATUSES and record.total_count > 0:
            # A successfully completed run has processed every scheduled item.
            # Keep this invariant at the durable task boundary so every caller,
            # including checkpoint reuse and recovery, gets consistent progress.
            record.completed_count = record.total_count
            record.active_item = ""
        if status in TERMINAL_STATUSES and record.run_started_at:
            try:
                started = time.mktime(time.strptime(record.run_started_at, "%Y-%m-%d %H:%M:%S"))
                record.last_run_duration_seconds = max(0, int(time.time() - started))
            except ValueError:
                pass
        record.updated_at = now
        if record.status == "running":
            record.health_status = "normal"
            record.current_operation = record.current_stage or record.current_operation
            record.last_heartbeat_at = now
            if current_stage is not None:
                record.last_progress_at = now
                record.active_since = now
            record.warning_reason = ""
            record.suggested_action = ""
        elif record.status in {"failed", "cancelled"}:
            record.health_status = "error"
            record.warning_reason = record.error
            record.suggested_action = "可重新运行任务。" if record.status == "failed" else "任务已取消，可在需要时重新运行。"
        elif record.status in SUCCESS_TERMINAL_STATUSES:
            record.health_status = "normal"
            record.last_progress_at = now
            record.warning_reason = ""
            record.suggested_action = ""
        save_task(record)
    append_event(task_id, "task_updated", {"status": record.status, "current_stage": record.current_stage, "error": record.error})
    return record


def remember_task_run_options(
    task_id: str,
    *,
    use_model: bool,
    render: bool,
    reuse_fragments: bool,
) -> TaskRecord:
    with _STORE_LOCK:
        record = load_task(task_id)
        record.last_run_use_model = bool(use_model)
        record.last_run_render = bool(render)
        record.last_run_reuse_fragments = bool(reuse_fragments)
        record.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        save_task(record)
        return record


def update_task_health(
    task_id: str,
    *,
    current_operation: str | None = None,
    completed_count: int | None = None,
    total_count: int | None = None,
    active_item: str | None = None,
    health_status: str | None = None,
    warning_reason: str | None = None,
    suggested_action: str | None = None,
    progress: bool = False,
) -> TaskRecord:
    with _STORE_LOCK:
        record = load_task(task_id)
        # Heartbeats and stage callbacks can race with cancel/fail/complete.
        # Terminal task state is authoritative; a late observability write must
        # not make it look active or healthy again.
        if record.status in TERMINAL_STATUSES:
            return record
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        record.last_heartbeat_at = now
        if current_operation is not None:
            record.current_operation = current_operation
        if completed_count is not None:
            record.completed_count = max(0, int(completed_count))
        if total_count is not None:
            record.total_count = max(0, int(total_count))
        if record.total_count > 0:
            record.completed_count = min(record.completed_count, record.total_count)
        if active_item is not None:
            if active_item != record.active_item:
                record.active_since = now
            record.active_item = active_item
        if health_status is not None:
            record.health_status = health_status
        if warning_reason is not None:
            record.warning_reason = warning_reason
        if suggested_action is not None:
            record.suggested_action = suggested_action
        if progress:
            record.last_progress_at = now
            record.health_status = "normal"
            record.warning_reason = ""
            record.suggested_action = ""
        record.updated_at = now
        save_task(record)
        return record


def list_tasks() -> list[dict[str, Any]]:
    ensure_project_dirs()
    out: list[dict[str, Any]] = []
    for path in sorted(TASKS_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        if not path.is_dir():
            continue
        record = path / "task.json"
        if record.exists():
            try:
                with _STORE_LOCK:
                    out.append(json.loads(record.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def recover_interrupted_tasks(reason: str = "server_startup") -> list[dict[str, Any]]:
    ensure_project_dirs()
    recovered: list[dict[str, Any]] = []
    message = "服务重启后正在从已保存检查点恢复任务。"
    for row in list_tasks():
        task_id = str(row.get("task_id") or "")
        if not task_id or str(row.get("status") or "") not in INTERRUPTED_ON_STARTUP_STATUSES:
            continue
        record = load_task(task_id)
        previous_stage = record.current_stage
        record.status = "queued"
        record.interrupted_stage = previous_stage
        record.current_stage = "recovering"
        record.current_operation = "等待恢复"
        record.health_status = "waiting"
        record.error = ""
        record.warning_reason = message
        record.suggested_action = "任务会自动复用已保存内容继续执行。"
        record.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        save_task(record)
        payload = {
            "reason": reason,
            "previous_status": row.get("status"),
            "previous_stage": previous_stage,
            "message": message,
            "use_model": record.last_run_use_model,
            "render": record.last_run_render,
            "reuse_fragments": True,
        }
        append_event(task_id, "task_recovery_queued", payload)
        append_event(task_id, "task_updated", {"status": record.status, "current_stage": record.current_stage, "error": record.error})
        recovered.append({"task_id": task_id, **payload})
    return recovered


def append_event(task_id: str, event: str, payload: dict[str, Any] | None = None) -> None:
    path = task_dir(task_id) / "events.jsonl"
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, "payload": payload or {}}
    with _STORE_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
