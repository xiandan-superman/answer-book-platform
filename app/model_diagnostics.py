from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from .paths import DATA_ROOT

MODEL_DIAGNOSTICS_DIR = DATA_ROOT / "model_diagnostics"
PER_TASK_LIMIT_BYTES = 25 * 1024 * 1024
GLOBAL_LIMIT_BYTES = 256 * 1024 * 1024
MAX_TEXT_BYTES = 3 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 6 * 1024 * 1024
SUCCESS_RETENTION_DAYS = 30
FAILURE_RETENTION_DAYS = 60

_LOCK = threading.RLock()
_TRACE_HINT: ContextVar[dict[str, Any] | None] = ContextVar("model_diagnostic_hint", default=None)
_CREDENTIAL_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "token", "access_token", "refresh_token"}


@contextmanager
def model_diagnostic_hint(**values: Any):
    current = dict(_TRACE_HINT.get() or {})
    current.update({key: value for key, value in values.items() if value not in (None, "")})
    token = _TRACE_HINT.set(current)
    try:
        yield
    finally:
        _TRACE_HINT.reset(token)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(value: Any, fallback: str = "unscoped") -> str:
    text = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", str(value or "").strip())
    return text[:120] or fallback


def _redact_text(value: Any, limit: int = MAX_TEXT_BYTES) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1***", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "***", text)
    text = re.sub(r"(?i)(api[_-]?key|password|secret|access[_-]?token)(\s*[:=]\s*)[^\s,;]+", r"\1\2***", text)
    home = str(Path.home())
    if home:
        text = text.replace(home, "<user-home>")
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore") + "<truncated>"


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if parsed.scheme not in {"http", "https"}:
        return _redact_text(value, 500)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _attachment_dir(task_id: str) -> Path:
    path = MODEL_DIAGNOSTICS_DIR / _safe_id(task_id) / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _store_attachment(task_id: str, raw: bytes, media_type: str) -> dict[str, Any]:
    digest = hashlib.sha256(raw).hexdigest()
    suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(media_type.lower(), ".bin")
    result: dict[str, Any] = {"sha256": digest, "media_type": media_type, "size_bytes": len(raw)}
    if len(raw) > MAX_ATTACHMENT_BYTES:
        result.update({"stored": False, "reason": "attachment_exceeds_limit"})
        return result
    target = _attachment_dir(task_id) / f"{digest}{suffix}"
    if not target.exists():
        fd, raw_tmp = tempfile.mkstemp(prefix=".attachment-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(raw_tmp, target)
        finally:
            Path(raw_tmp).unlink(missing_ok=True)
    result.update({"stored": True, "relative_path": f"attachments/{target.name}"})
    return result


def _normalize(value: Any, task_id: str, *, key: str = "", depth: int = 0) -> Any:
    if depth > 10:
        return "<max-depth>"
    lowered_key = key.lower()
    if lowered_key in _CREDENTIAL_KEYS or any(part in lowered_key for part in ("api_key", "password", "authorization")):
        return "***"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return _store_attachment(task_id, value, "application/octet-stream")
    if isinstance(value, str):
        data_match = re.match(r"^data:([^;,]+);base64,(.+)$", value, flags=re.DOTALL)
        if data_match:
            media_type = data_match.group(1).lower()
            try:
                raw = base64.b64decode(data_match.group(2), validate=False)
            except Exception:
                return {"stored": False, "media_type": media_type, "reason": "invalid_base64"}
            return _store_attachment(task_id, raw, media_type)
        if lowered_key in {"url", "image_url"} and value.startswith(("http://", "https://")):
            return _safe_url(value)
        return _redact_text(value)
    if isinstance(value, list):
        return [_normalize(item, task_id, depth=depth + 1) for item in value[:500]]
    if isinstance(value, dict):
        return {
            str(raw_key)[:160]: _normalize(item, task_id, key=str(raw_key), depth=depth + 1)
            for raw_key, item in list(value.items())[:300]
        }
    return _redact_text(value, 1000)


def _trace_dir(task_id: str) -> Path:
    path = MODEL_DIAGNOSTICS_DIR / _safe_id(task_id) / "traces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_model_diagnostic(
    call_record: dict[str, Any] | None,
    request_payload: Any,
    *,
    response_payload: Any = None,
    error: Any = "",
    outcome: str = "succeeded",
) -> Path | None:
    if not isinstance(call_record, dict):
        return None
    task_id = str(call_record.get("task_id") or "unscoped")
    trace_id = uuid4().hex
    status = "ok" if outcome == "succeeded" and not error else "failed"
    row = {
        "schema_version": 1,
        "trace_id": trace_id,
        "created_at": _now(),
        "outcome": status,
        "attempt": _normalize(_TRACE_HINT.get() or {}, task_id),
        "call": _normalize(call_record, task_id),
        "request": _normalize(request_payload, task_id),
        "response": _normalize(response_payload, task_id) if response_payload is not None else None,
        "error": _redact_text(error, 10000) if error else "",
    }
    target = _trace_dir(task_id) / f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{status}-{trace_id[:8]}.json.gz"
    with _LOCK:
        fd, raw_tmp = tempfile.mkstemp(prefix=".trace-", dir=str(target.parent))
        os.close(fd)
        tmp = Path(raw_tmp)
        try:
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as handle:
                json.dump(row, handle, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        _enforce_limits(task_id)
    return target


def _files_size(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _trace_files(root: Path) -> list[Path]:
    return sorted(root.glob("traces/*.json.gz"), key=lambda path: path.stat().st_mtime if path.exists() else 0)


def _remove_orphan_attachments(task_root: Path) -> None:
    referenced: set[str] = set()
    for trace in _trace_files(task_root):
        try:
            with gzip.open(trace, "rt", encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        referenced.update(re.findall(r'attachments\/([0-9a-f]{64}\.[A-Za-z0-9]+)', text))
    attachment_root = task_root / "attachments"
    if not attachment_root.exists():
        return
    for path in attachment_root.iterdir():
        if path.is_file() and path.name not in referenced:
            path.unlink(missing_ok=True)


def _prune_expired(root: Path) -> None:
    now = datetime.now().timestamp()
    for path in _trace_files(root):
        age_days = max(0, (now - path.stat().st_mtime) / 86400)
        retention = SUCCESS_RETENTION_DAYS if "-ok-" in path.name else FAILURE_RETENTION_DAYS
        if age_days > retention:
            path.unlink(missing_ok=True)


def _enforce_limits(task_id: str) -> None:
    task_root = MODEL_DIAGNOSTICS_DIR / _safe_id(task_id)
    _prune_expired(task_root)
    files = [path for path in task_root.rglob("*") if path.is_file()]
    if _files_size(files) > PER_TASK_LIMIT_BYTES:
        for path in _trace_files(task_root):
            if "-ok-" not in path.name:
                continue
            path.unlink(missing_ok=True)
            files = [item for item in task_root.rglob("*") if item.is_file()]
            if _files_size(files) <= PER_TASK_LIMIT_BYTES:
                break
    files = [item for item in task_root.rglob("*") if item.is_file()]
    if _files_size(files) > PER_TASK_LIMIT_BYTES:
        failed = [path for path in _trace_files(task_root) if "-failed-" in path.name]
        for path in failed[:-3]:
            path.unlink(missing_ok=True)
            _remove_orphan_attachments(task_root)
            files = [item for item in task_root.rglob("*") if item.is_file()]
            if _files_size(files) <= PER_TASK_LIMIT_BYTES:
                break
    _remove_orphan_attachments(task_root)
    if not MODEL_DIAGNOSTICS_DIR.exists():
        return
    all_files = [path for path in MODEL_DIAGNOSTICS_DIR.rglob("*") if path.is_file()]
    if _files_size(all_files) <= GLOBAL_LIMIT_BYTES:
        return
    successful = sorted(
        (path for path in MODEL_DIAGNOSTICS_DIR.glob("*/traces/*-ok-*.json.gz")),
        key=lambda path: path.stat().st_mtime,
    )
    for path in successful:
        path.unlink(missing_ok=True)
        all_files = [item for item in MODEL_DIAGNOSTICS_DIR.rglob("*") if item.is_file()]
        if _files_size(all_files) <= GLOBAL_LIMIT_BYTES:
            break
    if _files_size([item for item in MODEL_DIAGNOSTICS_DIR.rglob("*") if item.is_file()]) > GLOBAL_LIMIT_BYTES:
        old_failures: list[Path] = []
        for root in MODEL_DIAGNOSTICS_DIR.iterdir():
            if not root.is_dir():
                continue
            failed = [path for path in _trace_files(root) if "-failed-" in path.name]
            old_failures.extend(failed[:-3])
        for path in sorted(old_failures, key=lambda item: item.stat().st_mtime):
            path.unlink(missing_ok=True)
            if _files_size([item for item in MODEL_DIAGNOSTICS_DIR.rglob("*") if item.is_file()]) <= GLOBAL_LIMIT_BYTES:
                break
    for root in MODEL_DIAGNOSTICS_DIR.iterdir():
        if root.is_dir():
            _remove_orphan_attachments(root)


def pin_model_diagnostics_for_failure(task_id: str, limit: int = 4) -> int:
    """Protect the most recent successful calls when their enclosing task later fails.

    A provider call can succeed while a deterministic post-call gate rejects its
    result. Renaming a few traces into the failure-retention class keeps that
    evidence available without changing the recorded provider-call outcome.
    """
    root = MODEL_DIAGNOSTICS_DIR / _safe_id(task_id)
    with _LOCK:
        candidates = [path for path in reversed(_trace_files(root)) if "-ok-" in path.name]
        pinned = 0
        for path in candidates[:max(0, int(limit))]:
            target = path.with_name(path.name.replace("-ok-", "-failed-outer-", 1))
            try:
                path.replace(target)
            except OSError:
                continue
            pinned += 1
        if pinned:
            _enforce_limits(task_id)
        return pinned


def relevant_model_diagnostics(task_id: str, active_item: str = "", limit: int = 24) -> list[dict[str, Any]]:
    root = MODEL_DIAGNOSTICS_DIR / _safe_id(task_id)
    candidates = _trace_files(root)
    selected: list[dict[str, Any]] = []
    for path in reversed(candidates):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                row = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        call = row.get("call") if isinstance(row.get("call"), dict) else {}
        matches_item = bool(active_item and str(call.get("active_item") or "") == active_item)
        if row.get("outcome") != "ok" or matches_item or len(selected) < 4:
            selected.append(row)
        if len(selected) >= max(1, limit):
            break
    return list(reversed(selected))


def diagnostic_attachments(task_id: str, traces: list[dict[str, Any]]) -> list[Path]:
    names: set[str] = set()
    for trace in traces:
        text = json.dumps(trace, ensure_ascii=False)
        names.update(re.findall(r'attachments\/([0-9a-f]{64}\.[A-Za-z0-9]+)', text))
    root = MODEL_DIAGNOSTICS_DIR / _safe_id(task_id) / "attachments"
    return [root / name for name in sorted(names) if (root / name).is_file()]


def delete_model_diagnostics(task_id: str) -> None:
    root = MODEL_DIAGNOSTICS_DIR / _safe_id(task_id)
    try:
        root.relative_to(MODEL_DIAGNOSTICS_DIR)
    except ValueError:
        return
    if root.is_dir():
        shutil.rmtree(root)
