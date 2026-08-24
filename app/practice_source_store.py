from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import DATA_ROOT

SOURCE_ROOT = DATA_ROOT / "practice_sources"
OBJECT_ROOT = SOURCE_ROOT / "objects"
CACHE_ROOT = SOURCE_ROOT / "extracted"
_RESOURCE_PATTERN = re.compile(r"^psrc_([0-9a-f]{64})$")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _decode_inline_file(item: dict[str, Any]) -> bytes:
    encoded = str(item.get("data_url") or "")
    if "," in encoded and encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        name = Path(str(item.get("name") or "未命名文件")).name
        raise ValueError(f"{name} 的文件内容无效。") from exc


def _object_dir(digest: str) -> Path:
    return OBJECT_ROOT / digest[:2] / digest


def persist_practice_source_files(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace inline uploads with durable, content-addressed references."""

    result = dict(payload)
    stored: list[dict[str, Any]] = []
    for raw in payload.get("source_files") or []:
        if not isinstance(raw, dict):
            continue
        existing = str(raw.get("resource_id") or "").strip()
        if existing:
            load_practice_source_file(raw)
            stored.append({key: value for key, value in raw.items() if key != "data_url"})
            continue
        data = _decode_inline_file(raw)
        if not data:
            raise ValueError("上传文件不能为空。")
        digest = hashlib.sha256(data).hexdigest()
        resource_id = f"psrc_{digest}"
        directory = _object_dir(digest)
        directory.mkdir(parents=True, exist_ok=True)
        data_path = directory / "data"
        if not data_path.exists():
            temporary = directory / "data.tmp"
            temporary.write_bytes(data)
            temporary.replace(data_path)
        name = Path(str(raw.get("name") or "未命名文件")).name
        mime = str(raw.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream")
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            manifest_path.write_text(json.dumps({
                "resource_id": resource_id,
                "sha256": digest,
                "size": len(data),
                "created_at": _now(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        stored.append({"resource_id": resource_id, "name": name, "type": mime, "size": len(data)})
    if stored or "source_files" in result:
        result["source_files"] = stored
    return result


def load_practice_source_file(item: dict[str, Any]) -> bytes:
    resource_id = str(item.get("resource_id") or "").strip()
    match = _RESOURCE_PATTERN.fullmatch(resource_id)
    if not match:
        raise ValueError("出题材料资源 ID 无效。")
    data_path = _object_dir(match.group(1)) / "data"
    if not data_path.is_file():
        raise ValueError("出题材料资源已不存在，请重新上传。")
    data = data_path.read_bytes()
    expected_size = item.get("size")
    if isinstance(expected_size, int) and expected_size >= 0 and len(data) != expected_size:
        raise ValueError("出题材料资源大小校验失败，请重新上传。")
    if hashlib.sha256(data).hexdigest() != match.group(1):
        raise ValueError("出题材料资源完整性校验失败，请重新上传。")
    return data


def extraction_cache_key(payload: dict[str, Any]) -> str:
    files = [
        {
            "resource_id": str(item.get("resource_id") or ""),
            "name": Path(str(item.get("name") or "")).name,
            "type": str(item.get("type") or ""),
        }
        for item in payload.get("source_files") or []
        if isinstance(item, dict)
    ]
    if not files or any(not item["resource_id"] for item in files):
        return ""
    encoded = json.dumps({
        "schema": 1,
        "question_text": str(payload.get("question_text") or ""),
        "image_data_url_sha256": hashlib.sha256(str(payload.get("image_data_url") or "").encode("utf-8")).hexdigest(),
        "files": files,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_extraction_cache(key: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[0-9a-f]{64}", str(key or "")):
        return None
    try:
        value = json.loads((CACHE_ROOT / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def save_extraction_cache(key: str, value: dict[str, Any]) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", str(key or "")):
        return
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    path = CACHE_ROOT / f"{key}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
