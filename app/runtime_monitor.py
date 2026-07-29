from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path
from typing import Any

from .paths import DATA_ROOT, LOGS_DIR, PROJECT_ROOT, TASKS_DIR, ensure_project_dirs
from .task_store import list_tasks
from .version import get_version


RUNTIME_LOG = LOGS_DIR / "runtime_server.jsonl"
MAX_TEXT_LENGTH = 1200


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _safe_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _safe_payload(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        return [_safe_payload(item) for item in value[:40]]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            key_text = str(key)
            if any(token in key_text.lower() for token in ("key", "token", "secret", "password")):
                redacted[key_text] = "***"
            else:
                redacted[key_text] = _safe_payload(item)
        return redacted
    return _safe_text(value)


def append_runtime_log(source: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    try:
        ensure_project_dirs()
        row = {
            "time": _now(),
            "level": level,
            "source": _safe_text(source, 80),
            "message": _safe_text(message),
            "payload": _safe_payload(payload or {}),
        }
        with RUNTIME_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_runtime_logs(limit: int = 200) -> list[dict[str, Any]]:
    return _read_jsonl_tail(RUNTIME_LOG, max(1, min(limit, 1000)))


def _task_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(tasks), "running": 0, "paused": 0, "queued": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for task in tasks:
        status = str(task.get("status") or "")
        current_stage = str(task.get("current_stage") or "")
        if status in ("created", "queued"):
            counts["queued"] += 1
        elif status in counts:
            counts[status] += 1
        elif current_stage == "completed":
            counts["completed"] += 1
    return counts


def _recent_task_events(limit: int = 80) -> list[dict[str, Any]]:
    if not TASKS_DIR.exists():
        return []
    event_files = sorted(TASKS_DIR.glob("*/events.jsonl"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:40]
    rows: list[dict[str, Any]] = []
    for path in event_files:
        task_id = path.parent.name
        for row in _read_jsonl_tail(path, 20):
            rows.append(
                {
                    "task_id": task_id,
                    "time": row.get("time") or "",
                    "event": row.get("event") or "",
                    "payload": _safe_payload(row.get("payload") or {}),
                }
            )
    rows.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
    return rows[:limit]


def build_system_status(access_host: str | None = None) -> dict[str, Any]:
    ensure_project_dirs()
    tasks = list_tasks()
    return {
        "ok": True,
        "version": get_version(),
        "time": _now(),
        "host": {
            "name": platform.node(),
            "system": platform.system(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pid": os.getpid(),
            "project_root": str(PROJECT_ROOT),
            "data_root": str(DATA_ROOT),
            "access_host": access_host or "",
        },
        "tasks": {
            "counts": _task_counts(tasks),
            "recent": tasks[:8],
        },
        "runtime_logs": read_runtime_logs(80),
        "task_events": _recent_task_events(80),
    }
