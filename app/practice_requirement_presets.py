from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from typing import Any

from .paths import LOCAL_CONFIG_DIR

PRESETS_FILE = LOCAL_CONFIG_DIR / "practice_requirement_presets.json"
SCHEMA_VERSION = 1
MAX_PRESETS = 60
MAX_TEXT_LENGTH = 1000
_PRESETS_LOCK = threading.Lock()


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise ValueError("常用要求不能为空")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"常用要求不能超过 {MAX_TEXT_LENGTH} 个字符")
    return text


def _empty_payload() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "items": []}


def _read_payload() -> dict[str, Any]:
    if not PRESETS_FILE.exists():
        return _empty_payload()
    try:
        payload = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_payload()
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return _empty_payload()
    items = []
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            continue
        preset_id = str(raw.get("id") or "").strip()
        text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()
        if not preset_id or not text:
            continue
        items.append(
            {
                "id": preset_id,
                "text": text[:MAX_TEXT_LENGTH],
                "created_at": str(raw.get("created_at") or ""),
                "updated_at": str(raw.get("updated_at") or ""),
            }
        )
    return {"schema_version": SCHEMA_VERSION, "items": items[:MAX_PRESETS]}


def _write_payload(payload: dict[str, Any]) -> None:
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PRESETS_FILE.with_suffix(f"{PRESETS_FILE.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(PRESETS_FILE)
    PRESETS_FILE.chmod(0o600)


def practice_requirement_presets_payload() -> dict[str, Any]:
    with _PRESETS_LOCK:
        payload = _read_payload()
    return {
        "schema_version": SCHEMA_VERSION,
        "items": payload["items"],
        "storage": "local_user_data",
        "max_items": MAX_PRESETS,
        "max_text_length": MAX_TEXT_LENGTH,
    }


def update_practice_requirement_presets(action: str, *, preset_id: str = "", text: Any = "") -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"create", "update", "delete"}:
        raise ValueError("不支持的常用要求操作")
    normalized_id = str(preset_id or "").strip()
    with _PRESETS_LOCK:
        payload = _read_payload()
        items = payload["items"]
        if normalized_action == "create":
            normalized_text = _normalize_text(text)
            if len(items) >= MAX_PRESETS:
                raise ValueError(f"最多保存 {MAX_PRESETS} 条常用要求")
            if any(item["text"] == normalized_text for item in items):
                raise ValueError("这条常用要求已经保存")
            now = _now_text()
            items.append(
                {
                    "id": f"requirement_{uuid.uuid4().hex}",
                    "text": normalized_text,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        else:
            if not normalized_id:
                raise ValueError("缺少常用要求编号")
            index = next((i for i, item in enumerate(items) if item["id"] == normalized_id), -1)
            if index < 0:
                raise FileNotFoundError("这条常用要求已不存在")
            if normalized_action == "delete":
                items.pop(index)
            else:
                normalized_text = _normalize_text(text)
                if any(item["id"] != normalized_id and item["text"] == normalized_text for item in items):
                    raise ValueError("这条常用要求已经保存")
                items[index] = {**items[index], "text": normalized_text, "updated_at": _now_text()}
        payload = {"schema_version": SCHEMA_VERSION, "items": items}
        _write_payload(payload)
    return practice_requirement_presets_payload()
