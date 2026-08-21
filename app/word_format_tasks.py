from __future__ import annotations

import base64
import binascii
import json
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from standalone_word_format_reviewer.format_engine import (
    PROFILE_LABELS,
    audit_docx,
    default_task_options,
    normalize_task_options,
    repair_docx,
)

from .paths import CONFIG_DIR, TASKS_DIR

SETTINGS_FILE = CONFIG_DIR / "word_format_reviewer_settings.json"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_SETTINGS_LOCK = threading.Lock()


def _safe_filename(value: str) -> str:
    name = Path(value or "document.docx").name
    stem = "".join(ch for ch in Path(name).stem if ch not in '\\/:*?"<>|').strip() or "document"
    return f"{stem[:100]}.docx"


def _task_dir(task_id: str) -> Path:
    if not task_id.startswith("word_format_") or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in task_id):
        raise ValueError("无效的格式审查任务编号")
    target = (TASKS_DIR / task_id).resolve()
    target.relative_to(TASKS_DIR.resolve())
    return target


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"任务文件内容无效：{path.name}")
    return value


def _saved_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        value = _read_json(SETTINGS_FILE)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value


def word_format_settings_payload() -> dict:
    saved = _saved_settings()
    profiles = {}
    for profile in PROFILE_LABELS:
        defaults = default_task_options(profile)
        saved_profile = saved.get(profile) if isinstance(saved.get(profile), dict) else None
        effective = normalize_task_options(profile, saved_profile)
        profiles[profile] = {"defaults": defaults, "saved": saved_profile, "effective": effective}
    return {"profiles": profiles}


def save_word_format_profile_settings(profile: str, settings: dict) -> dict:
    if profile not in PROFILE_LABELS:
        raise ValueError("请选择真题答案或讲义标准")
    if not isinstance(settings, dict):
        raise ValueError("缺少要保存的标准配置")
    normalized = normalize_task_options(profile, settings)
    with _SETTINGS_LOCK:
        saved = _saved_settings()
        saved[profile] = normalized
        _write_json(SETTINGS_FILE, saved)
        SETTINGS_FILE.chmod(0o600)
    return normalized


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _duration_text(record: dict) -> str:
    seconds = max(0, int(float(record.get("updated_epoch") or time.time()) - float(record.get("created_epoch") or time.time())))
    if seconds >= 3600:
        return f"{seconds // 3600}小时{seconds % 3600 // 60}分"
    if seconds >= 60:
        return f"{seconds // 60}分{seconds % 60}秒"
    return f"{seconds}秒"


def _completion_status(report: dict) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return "completed_with_issues" if int(summary.get("issue_count") or 0) > 0 else "completed"


def _with_display_filename(report: dict, filename: str) -> dict:
    report["source_name"] = filename
    return report


def _task_row(record: dict) -> dict:
    task_id = str(record.get("task_id") or "")
    status = str(record.get("status") or "failed")
    report = record.get("final_report") or record.get("report") or {}
    summary = report.get("summary") if isinstance(report, dict) and isinstance(report.get("summary"), dict) else {}
    warning_count = int(summary.get("warning_count") or 0)
    issue_count = int(summary.get("issue_count") or 0)
    output_exists = (_task_dir(task_id) / "modified.docx").exists()
    needs_input = status == "needs_input"
    quality = None
    if status == "completed_with_issues":
        quality = {
            "label": f"仍有 {issue_count} 项需复核",
            "class_name": "warning",
            "icon": "fas fa-triangle-exclamation",
        }
    elif warning_count:
        quality = {
            "label": f"{warning_count} 项需人工确认",
            "class_name": "warning",
            "icon": "fas fa-user-check",
        }
    return {
        "task_id": task_id,
        "task_kind": "format",
        "workflow_type": "word_format_review",
        "is_format_task": True,
        "display_title": f"格式审查 · {record.get('filename') or 'Word 文档'}",
        "description": record.get("filename") or "Word 文档",
        "exam_path": record.get("filename") or "Word 文档",
        "model_label": "规则引擎",
        "textbooks_dir": PROFILE_LABELS.get(str(record.get("profile") or ""), "格式标准"),
        "format_profile_label": PROFILE_LABELS.get(str(record.get("profile") or ""), "格式标准"),
        "mode": record.get("mode"),
        "mode_label": "先审查，再确认修改" if record.get("mode") == "review" else "直接审查并修改",
        "status": status,
        "error": record.get("error") or "",
        "current_stage": "format_review" if needs_input else "completed",
        "progress_percent": 60 if needs_input else 100,
        "progress_message": (
            f"发现 {issue_count} 项格式问题，等待确认后生成修改文件"
            if needs_input
            else (f"格式修改已完成，仍有 {issue_count} 项需要复核" if issue_count else "格式审查与修改已完成")
        ),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "duration_text": _duration_text(record),
        "quality_presentation": quality,
        "capabilities": {
            "view_result": True,
            "view_progress": needs_input,
            "download": output_exists,
            "delete": True,
        },
    }


def _load_record(task_id: str) -> dict:
    path = _task_dir(task_id) / "format_task.json"
    if not path.exists():
        raise FileNotFoundError("格式审查任务不存在")
    return _read_json(path)


def _save_record(record: dict) -> None:
    _write_json(_task_dir(str(record["task_id"])) / "format_task.json", record)


def create_word_format_task(payload: dict) -> dict:
    profile = str(payload.get("profile") or "")
    if profile not in PROFILE_LABELS:
        raise ValueError("请选择真题答案或讲义标准")
    mode = str(payload.get("mode") or "review")
    if mode not in {"review", "auto"}:
        raise ValueError("处理方式无效")
    header_text = str(payload.get("header_text") or "").strip()
    if len(header_text) > 200:
        raise ValueError("页眉文字不能超过200个字符")
    raw_options = payload.get("task_options")
    if not isinstance(raw_options, dict):
        raw_options = word_format_settings_payload()["profiles"][profile]["effective"]
    task_options = normalize_task_options(profile, raw_options)
    filename = _safe_filename(str(payload.get("filename") or "document.docx"))
    try:
        content = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("上传文件内容无效") from exc
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("请选择不超过50 MB的DOCX文件")
    if not content.startswith(b"PK"):
        raise ValueError("文件不是有效的DOCX文件")

    task_id = f"word_format_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    directory = _task_dir(task_id)
    directory.mkdir(parents=True, exist_ok=False)
    created_at = _now_text()
    created_epoch = time.time()
    source = directory / "source.docx"
    source.write_bytes(content)
    record = {
        "task_id": task_id,
        "filename": filename,
        "profile": profile,
        "mode": mode,
        "header_text": header_text,
        "task_options": task_options,
        "status": "running",
        "created_at": created_at,
        "updated_at": created_at,
        "created_epoch": created_epoch,
        "updated_epoch": created_epoch,
    }
    _save_record(record)
    try:
        report = _with_display_filename(audit_docx(source, profile, header_text, task_options), filename)
        _write_json(directory / "audit.json", report)
        record["report"] = report
        if mode == "auto":
            final_report = _with_display_filename(
                repair_docx(source, directory / "modified.docx", profile, header_text, task_options),
                filename,
            )
            _write_json(directory / "final_audit.json", final_report)
            record["final_report"] = final_report
            record["status"] = _completion_status(final_report)
        else:
            record["status"] = "needs_input"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"无法处理该DOCX文件：{exc}"
        record["diagnostic_context"] = {
            "exception_type": exc.__class__.__name__,
            "traceback": traceback.format_exc(),
        }
        record["updated_at"] = _now_text()
        record["updated_epoch"] = time.time()
        _save_record(record)
        raise ValueError(record["error"]) from exc
    record["updated_at"] = _now_text()
    record["updated_epoch"] = time.time()
    _save_record(record)
    return word_format_task_payload(task_id)


def apply_word_format_task(task_id: str) -> dict:
    record = _load_record(task_id)
    directory = _task_dir(task_id)
    source = directory / "source.docx"
    if not source.exists():
        raise FileNotFoundError("原始Word文件不存在")
    try:
        final_report = _with_display_filename(
            repair_docx(
                source,
                directory / "modified.docx",
                str(record["profile"]),
                str(record.get("header_text") or ""),
                record.get("task_options"),
            ),
            str(record.get("filename") or "document.docx"),
        )
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"无法应用格式修改：{exc}"
        record["diagnostic_context"] = {
            "exception_type": exc.__class__.__name__,
            "traceback": traceback.format_exc(),
        }
        record["updated_at"] = _now_text()
        record["updated_epoch"] = time.time()
        _save_record(record)
        raise ValueError(record["error"]) from exc
    _write_json(directory / "final_audit.json", final_report)
    record["final_report"] = final_report
    record["status"] = _completion_status(final_report)
    record["updated_at"] = _now_text()
    record["updated_epoch"] = time.time()
    _save_record(record)
    return word_format_task_payload(task_id)


def word_format_task_payload(task_id: str) -> dict:
    record = _load_record(task_id)
    directory = _task_dir(task_id)
    output_exists = (directory / "modified.docx").exists()
    return {
        "job_id": task_id,
        "task_id": task_id,
        "mode": record.get("mode"),
        "status": record.get("status"),
        "report": record.get("report"),
        "final_report": record.get("final_report"),
        "download_url": f"/api/word-format/tasks/{task_id}/download" if output_exists else None,
        "task": _task_row(record),
    }


def list_word_format_tasks() -> list[dict]:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for directory in TASKS_DIR.glob("word_format_*"):
        path = directory / "format_task.json"
        if not path.exists():
            continue
        try:
            rows.append(_task_row(_read_json(path)))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    rows.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return rows


def word_format_download_path(task_id: str) -> tuple[Path, str]:
    record = _load_record(task_id)
    output = _task_dir(task_id) / "modified.docx"
    if not output.exists():
        raise FileNotFoundError("修改后的Word文件尚未生成")
    filename = f"{Path(str(record.get('filename') or 'document.docx')).stem}_格式已修改.docx"
    return output, filename


def delete_word_format_task(task_id: str) -> dict:
    directory = _task_dir(task_id)
    if not directory.exists():
        raise FileNotFoundError("格式审查任务不存在")
    shutil.rmtree(directory)
    return {"ok": True, "task_id": task_id, "message": "格式审查任务及相关文件已删除"}
