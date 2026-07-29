from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .paths import DATA_ROOT


PRACTICE_HISTORY_DIR = DATA_ROOT / "practice_history"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"practice_[a-zA-Z0-9_-]{8,80}", text):
        raise ValueError("练习记录 ID 无效。")
    return text


def _path(history_id: str) -> Path:
    return PRACTICE_HISTORY_DIR / f"{_safe_id(history_id)}.json"


def save_practice_record(data: dict[str, Any], *, request: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("exercises"), list):
        raise ValueError("练习记录内容无效。")
    PRACTICE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_id = str(data.get("history_id") or "")
    existing: dict[str, Any] = {}
    if history_id:
        path = _path(history_id)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
    else:
        history_id = f"practice_{datetime.now():%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        path = _path(history_id)
    created_at = str(existing.get("created_at") or _now())
    record = {
        "history_id": history_id,
        "created_at": created_at,
        "updated_at": _now(),
        "title": str((data.get("blueprint") or {}).get("training_goal") or "研究生专项练习")[:120],
        "source_excerpt": str((request or existing.get("request") or {}).get("question_text") or "")[:240],
        "request": _compact_request(request) if request is not None else existing.get("request", {}),
        "data": {**data, "history_id": history_id},
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def _compact_request(request: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    return {
        "question_text": str(request.get("question_text") or "")[:30000],
        "source_file_names": [
            str(item.get("name") or "")[:200]
            for item in request.get("source_files") or []
            if isinstance(item, dict)
        ],
        "count": request.get("count"),
        "difficulty": request.get("difficulty"),
        "question_types": request.get("question_types") or [],
        "focus": str(request.get("focus") or "")[:1000],
        "generation_strategy": request.get("generation_strategy"),
        "strategy_count": request.get("strategy_count"),
        "variants_per_question": request.get("variants_per_question"),
        "selected_source_questions": [
            {
                "source_question_id": str(item.get("source_question_id") or "")[:80],
                "number": str(item.get("number") or "")[:50],
                "title": str(item.get("title") or "")[:300],
            }
            for item in request.get("selected_source_questions") or []
            if isinstance(item, dict)
        ],
        "provider": request.get("provider"),
        "model": request.get("model"),
    }


def list_practice_records(limit: int = 30) -> list[dict[str, Any]]:
    PRACTICE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for path in sorted(PRACTICE_HISTORY_DIR.glob("practice_*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        rows.append(
            {
                "history_id": record.get("history_id"),
                "title": record.get("title"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "source_excerpt": record.get("source_excerpt"),
                "question_count": len(data.get("exercises") or []),
                "generation": data.get("generation") or {},
            }
        )
        if len(rows) >= max(1, min(limit, 100)):
            break
    return rows


def load_practice_record(history_id: str) -> dict[str, Any]:
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    return json.loads(path.read_text(encoding="utf-8"))
