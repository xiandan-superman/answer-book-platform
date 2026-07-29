from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import TASKS_DIR, ensure_project_dirs


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
    vision_provider: str = ""
    vision_model: str = ""
    image_provider: str = ""
    image_model: str = ""


INTERRUPTED_ON_STARTUP_STATUSES = {"running", "queued"}


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
    return TASKS_DIR / task_id


def task_record_path(task_id: str) -> Path:
    return task_dir(task_id) / "task.json"


def save_task(record: TaskRecord) -> None:
    task_record_path(record.task_id).write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")


def load_task(task_id: str) -> TaskRecord:
    data = json.loads(task_record_path(task_id).read_text(encoding="utf-8"))
    data.setdefault("reasoning_provider", "")
    data.setdefault("reasoning_model", "")
    data.setdefault("answer_provider", "")
    data.setdefault("answer_model", "")
    data.setdefault("vision_provider", "")
    data.setdefault("vision_model", "")
    data.setdefault("image_provider", "")
    data.setdefault("image_model", "")
    return TaskRecord(**data)


def update_task(task_id: str, *, status: str | None = None, current_stage: str | None = None, error: str | None = None) -> TaskRecord:
    record = load_task(task_id)
    if status is not None:
        record.status = status
    if current_stage is not None:
        record.current_stage = current_stage
    if error is not None:
        record.error = error
    record.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    save_task(record)
    append_event(task_id, "task_updated", {"status": record.status, "current_stage": record.current_stage, "error": record.error})
    return record


def list_tasks() -> list[dict[str, Any]]:
    ensure_project_dirs()
    out: list[dict[str, Any]] = []
    for path in sorted(TASKS_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        if not path.is_dir():
            continue
        record = path / "task.json"
        if record.exists():
            out.append(json.loads(record.read_text(encoding="utf-8")))
    return out


def recover_interrupted_tasks(reason: str = "server_startup") -> list[dict[str, Any]]:
    ensure_project_dirs()
    recovered: list[dict[str, Any]] = []
    message = "服务重启后任务后台执行已中断，请重新运行该任务。"
    for row in list_tasks():
        task_id = str(row.get("task_id") or "")
        if not task_id or str(row.get("status") or "") not in INTERRUPTED_ON_STARTUP_STATUSES:
            continue
        record = load_task(task_id)
        record.status = "failed"
        record.current_stage = "interrupted"
        record.error = message
        record.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        save_task(record)
        payload = {"reason": reason, "previous_status": row.get("status"), "previous_stage": row.get("current_stage"), "error": message}
        append_event(task_id, "task_interrupted", payload)
        append_event(task_id, "task_updated", {"status": record.status, "current_stage": record.current_stage, "error": record.error})
        recovered.append({"task_id": task_id, **payload})
    return recovered


def append_event(task_id: str, event: str, payload: dict[str, Any] | None = None) -> None:
    path = task_dir(task_id) / "events.jsonl"
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, "payload": payload or {}}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
