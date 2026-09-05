from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Iterable

from .paths import LOCAL_CONFIG_DIR, ensure_project_dirs

KEEP_NEWEST_TASKS = 40
LIVE_OR_PROTECTED_STATUSES = {
    "running", "queued", "paused", "needs_input", "action_required", "cancel_requested"
}
DOWNLOAD_LEDGER_PATH = LOCAL_CONFIG_DIR / "task_downloads.json"
PLATFORM_LAUNCH_ID = uuid.uuid4().hex
_LOCK = threading.RLock()


def _read_download_ledger() -> dict[str, dict[str, Any]]:
    ensure_project_dirs()
    if not DOWNLOAD_LEDGER_PATH.exists():
        return {}
    try:
        value = json.loads(DOWNLOAD_LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_download_ledger(value: dict[str, dict[str, Any]]) -> None:
    DOWNLOAD_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DOWNLOAD_LEDGER_PATH.with_name(
        f".{DOWNLOAD_LEDGER_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(DOWNLOAD_LEDGER_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def mark_task_downloaded(*task_ids: str) -> None:
    identifiers = [str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()]
    if not identifiers:
        return
    with _LOCK:
        ledger = _read_download_ledger()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for task_id in identifiers:
            previous = ledger.get(task_id) if isinstance(ledger.get(task_id), dict) else {}
            ledger[task_id] = {
                "downloaded_at": now,
                "download_count": int(previous.get("download_count") or 0) + 1,
            }
        _write_download_ledger(ledger)


def forget_deleted_tasks(task_ids: Iterable[str]) -> None:
    identifiers = {str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()}
    if not identifiers:
        return
    with _LOCK:
        ledger = _read_download_ledger()
        changed = False
        for task_id in identifiers:
            changed = ledger.pop(task_id, None) is not None or changed
        if changed:
            _write_download_ledger(ledger)


def _task_time(row: dict[str, Any]) -> str:
    return str(row.get("run_started_at") or row.get("created_at") or row.get("updated_at") or "")


def _task_progress(row: dict[str, Any]) -> int:
    value = row.get("progress_percent")
    if value is not None:
        try:
            return max(0, min(100, int(float(value))))
        except (TypeError, ValueError):
            pass
    completed = int(row.get("completed_count") or row.get("generated_count") or 0)
    total = int(row.get("total_count") or row.get("question_count") or 0)
    return max(0, min(100, round(completed * 100 / total))) if total > 0 else 0


def build_cleanup_recommendation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [row for row in rows if str(row.get("task_id") or "").strip()]
    ordered = sorted(normalized, key=_task_time, reverse=True)
    overflow = ordered[KEEP_NEWEST_TASKS:]
    ledger = _read_download_ledger()
    all_candidates: list[dict[str, Any]] = []
    recommended: list[dict[str, Any]] = []
    for row in overflow:
        task_id = str(row.get("task_id") or "")
        status = str(row.get("status") or "").lower()
        if status in LIVE_OR_PROTECTED_STATUSES:
            continue
        progress = _task_progress(row)
        reasons: list[str] = []
        if task_id in ledger:
            reasons.append("已有下载记录")
        if status == "failed":
            reasons.append("任务失败")
        if status in {"cancelled", "canceled", "stopped"}:
            reasons.append("用户已停止")
        if progress < 60:
            reasons.append("进度未达 60%")
        item = {
            "task_id": task_id,
            "title": str(row.get("display_title") or row.get("title") or row.get("description") or task_id),
            "task_kind": str(row.get("task_kind") or "exam"),
            "status": status,
            "progress_percent": progress,
            "started_at": _task_time(row),
            "reasons": reasons,
        }
        all_candidates.append(item)
        if reasons:
            recommended.append(item)
    return {
        "ok": True,
        "launch_id": PLATFORM_LAUNCH_ID,
        "task_count": len(ordered),
        "keep_newest": KEEP_NEWEST_TASKS,
        "overflow_count": len(overflow),
        "safe_overflow_count": len(all_candidates),
        "recommended_count": len(recommended),
        "recommended": recommended,
        "overflow_all": all_candidates,
        "show_prompt": len(ordered) > KEEP_NEWEST_TASKS and bool(all_candidates),
    }
