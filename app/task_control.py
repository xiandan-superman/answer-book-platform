from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .paths import OUTPUTS_DIR
from .task_store import append_event, load_task, task_dir, update_task


class TaskCancelled(RuntimeError):
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
    except Exception:
        return {}


def write_task_control(task_id: str, action: str, reason: str = "") -> dict[str, Any]:
    data = {"action": action, "reason": reason, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    control_path(task_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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


def control_task(task_id: str, action: str) -> dict[str, Any]:
    record = load_task(task_id)
    if action == "pause":
        if record.status not in {"running", "created", "pending"}:
            return {"ok": False, "message": "只有进行中的任务可以暂停。", "task": record.__dict__}
        control = write_task_control(task_id, "pause", "用户暂停任务")
        update_task(task_id, status="paused", error="用户暂停任务")
        return {"ok": True, "message": "已请求暂停；当前阶段结束或到达检查点后暂停。", "control": control, "task": load_task(task_id).__dict__}
    if action == "resume":
        if record.status != "paused":
            return {"ok": False, "message": "当前任务不处于暂停状态。", "task": record.__dict__}
        clear_task_control(task_id)
        update_task(task_id, status="running", error="")
        append_event(task_id, "control_resume", {"updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return {"ok": True, "message": "已继续任务。", "task": load_task(task_id).__dict__}
    if action == "cancel":
        control = write_task_control(task_id, "cancel", "用户取消任务")
        if record.status in {"created", "pending", "paused"}:
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
    output_root = OUTPUTS_DIR / task_id
    if task_root.exists():
        shutil.rmtree(task_root)
    if output_root.exists():
        shutil.rmtree(output_root)
    return {"ok": True, "message": "任务和输出文件已删除。", "task_id": task_id}
