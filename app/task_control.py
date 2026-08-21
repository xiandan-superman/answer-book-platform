from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .concurrency import ModelRequestAborted
from .paths import OUTPUTS_DIR
from .pipeline_checkpoints import reconcile_answer_generation_checkpoint
from .resource_ids import bounded_resource_path
from .task_store import append_event, load_task, task_dir, update_task


class TaskCancelled(ModelRequestAborted):
    pass


def control_path(task_id: str) -> Path:
    return task_dir(task_id) / "control.json"


def read_task_control(task_id: str) -> dict[str, Any]:
    path = control_path(task_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_task_control(task_id: str, action: str, reason: str = "") -> dict[str, Any]:
    data = {"action": action, "reason": reason, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write_json_atomic(control_path(task_id), data)
    append_event(task_id, f"control_{action}", data)
    return data


def clear_task_control(task_id: str) -> None:
    path = control_path(task_id)
    if path.exists():
        path.unlink()


def checkpoint(task_id: str) -> None:
    while True:
        control = read_task_control(task_id)
        action = control.get("action")
        if action == "cancel":
            update_task(task_id, status="cancelled", error=control.get("reason") or "用户取消任务")
            raise TaskCancelled(control.get("reason") or "用户取消任务")
        if action != "pause":
            return
        record = load_task(task_id)
        if record.status != "paused":
            update_task(task_id, status="paused", error=control.get("reason") or "用户暂停任务")
        time.sleep(1)


def control_task(task_id: str, action: str, *, detached_resume: bool = False) -> dict[str, Any]:
    record = load_task(task_id)
    if action == "pause":
        if record.status != "running":
            return {"ok": False, "message": "只有进行中的任务可以暂停。", "task": record.__dict__}
        control = write_task_control(task_id, "pause", "用户暂停任务")
        update_task(task_id, status="paused", error="用户暂停任务")
        return {"ok": True, "message": "已请求暂停；当前阶段结束或到达检查点后暂停。", "control": control, "task": load_task(task_id).__dict__}
    if action == "resume":
        if record.status != "paused":
            return {"ok": False, "message": "当前任务不处于暂停状态。", "task": record.__dict__}
        stage_dir = task_dir(task_id) / "stage_outputs"
        reconciliation = reconcile_answer_generation_checkpoint(
            stage_dir,
            output_json=stage_dir / "answer_checkpoint_reconciliation.json",
        )
        append_event(
            task_id,
            "checkpoint_reconciled",
            {
                "schema_version": reconciliation.get("schema_version"),
                "resume_strategy": reconciliation.get("resume_strategy"),
                "source_contract_status": (reconciliation.get("source_contract") or {}).get("status"),
                "expected_count": reconciliation.get("expected_count", 0),
                "reusable_fragment_count": reconciliation.get("reusable_fragment_count", 0),
                "redrive_count": reconciliation.get("redrive_count", 0),
                "inconsistency_count": len(reconciliation.get("inconsistencies") or []),
            },
        )
        clear_task_control(task_id)
        next_status = "queued" if detached_resume else "running"
        update_task(task_id, status=next_status, error="")
        append_event(
            task_id,
            "control_resume",
            {
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "detached_resume": detached_resume,
                "reusable_fragment_count": reconciliation.get("reusable_fragment_count", 0),
                "redrive_count": reconciliation.get("redrive_count", 0),
            },
        )
        expected_count = int(reconciliation.get("expected_count") or 0)
        reconciliation_summary = ""
        if expected_count:
            reconciliation_summary = (
                f" 检查点对账：复用 {int(reconciliation.get('reusable_fragment_count') or 0)} 题，"
                f"重做 {int(reconciliation.get('redrive_count') or 0)} 题。"
            )
        return {
            "ok": True,
            "message": (
                "已排队从检查点恢复任务。" if detached_resume else "已继续任务。"
            ) + reconciliation_summary,
            "restart_required": detached_resume,
            "checkpoint_reconciliation": reconciliation,
            "task": load_task(task_id).__dict__,
        }
    if action == "cancel":
        if record.status not in {"running", "created", "pending", "queued", "paused"}:
            return {"ok": False, "message": "当前任务已经结束，不能取消。", "task": record.__dict__}
        control = write_task_control(task_id, "cancel", "用户取消任务")
        if record.status in {"created", "pending", "queued", "paused"}:
            update_task(task_id, status="cancelled", current_stage="cancelled", error="用户取消任务")
        return {"ok": True, "message": "已请求取消；运行中的阶段会在下一个检查点停止。", "control": control, "task": load_task(task_id).__dict__}
    if action == "move-up":
        append_event(task_id, "control_move_up", {"note": "当前版本无集中队列，任务创建后由用户启动或已在执行。"})
        return {"ok": True, "message": "当前版本没有集中排队队列；已记录上移请求。", "task": record.__dict__}
    raise ValueError(f"Unsupported task control action: {action}")


def delete_task(task_id: str) -> dict[str, Any]:
    record = load_task(task_id)
    if record.status in {"running", "paused"}:
        return {"ok": False, "message": "运行中任务请先取消，确认停止后再删除。", "task": record.__dict__}
    task_root = task_dir(task_id)
    output_root = bounded_resource_path(OUTPUTS_DIR, task_id)
    if task_root.exists():
        shutil.rmtree(task_root)
    if output_root.exists():
        shutil.rmtree(output_root)
    return {"ok": True, "message": "任务和输出文件已删除。", "task_id": task_id}
