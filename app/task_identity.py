"""Stable user-facing task numbers, independent of internal execution IDs."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def public_task_number(row: dict[str, Any]) -> str:
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    stable = str(row.get("practice_batch_id") or request.get("practice_batch_id")
                 or row.get("stable_task_id") or row.get("task_id")
                 or row.get("history_id") or row.get("job_id") or "").strip()
    if not stable:
        return ""
    digest = hashlib.sha256(("task-number-v1:" + stable).encode()).hexdigest()[:16].upper()
    return "RW-" + "-".join(digest[i:i + 4] for i in range(0, 16, 4))


def resolve_task_number(number: str) -> dict[str, Any]:
    """Read-only lookup including historical runs; never guess on collision."""
    from .practice_jobs import PRACTICE_JOB_DIR
    from .practice_store import _canonical_history_records
    from .task_store import list_tasks
    from .word_format_tasks import list_word_format_tasks

    normalized = str(number or "").strip().upper()
    if not re.fullmatch(r"RW-(?:[0-9A-F]{4}-){3}[0-9A-F]{4}", normalized):
        raise ValueError("请输入完整任务编号 RW-XXXX-XXXX-XXXX-XXXX")
    jobs = []
    for path in PRACTICE_JOB_DIR.glob("generation_*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            jobs.append(row)
    matches = []
    for kind, rows in (
        ("exam", list_tasks()),
        ("practice_job", jobs),
        ("practice_history", [row for _, row in _canonical_history_records()]),
        ("format", list_word_format_tasks()),
    ):
        for row in rows:
            if public_task_number(row) == normalized:
                matches.append({"kind": kind, **{key: row.get(key) for key in
                    ("task_id", "stable_task_id", "practice_batch_id", "job_id", "run_id", "history_id", "status")}})
    return {"public_task_id": normalized, "found": bool(matches), "matches": matches}
