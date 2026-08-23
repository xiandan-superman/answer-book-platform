from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .paths import CACHE_DIR
from .practice_document_contracts import PRACTICE_DOCUMENT_CONTRACT_VERSION
from .practice_export import build_practice_question_docx, validate_docx_output, validate_practice_export
from .runtime_monitor import append_runtime_log

EXPORT_CACHE_VERSION = "practice-word-v7"
EXPORT_CACHE_DIR = CACHE_DIR / "practice_exports"
_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE: set[str] = set()
try:
    _MAX_CONCURRENT_EXPORTS = max(1, min(4, int(os.environ.get("PRACTICE_EXPORT_MAX_CONCURRENCY", "2"))))
except ValueError:
    _MAX_CONCURRENT_EXPORTS = 2
_RUN_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_EXPORTS)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _cache_key(data: dict[str, Any]) -> str:
    # Request-only metadata stays out of the document identity. Any actual
    # question edit changes the exercises and therefore produces a new key.
    document_data = {
        "cache_version": EXPORT_CACHE_VERSION,
        "source_mode": data.get("source_mode"),
        "title": data.get("title"),
        "goal": data.get("goal"),
        "exercises": data.get("exercises") or [],
    }
    encoded = json.dumps(
        document_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_job(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"cache_path", "payload", "diagnostic_context"}
    }


def _job_record_path(job_id: str) -> Path:
    if not job_id.startswith("practice_word_") or "/" in job_id or ".." in job_id:
        raise ValueError("Word 导出任务 ID 无效。")
    return EXPORT_CACHE_DIR / "jobs" / f"{job_id}.json"


def _persist_job(record: dict[str, Any]) -> None:
    path = _job_record_path(str(record.get("job_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_persisted_job(job_id: str) -> dict[str, Any] | None:
    path = _job_record_path(job_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) and record.get("job_id") == job_id else None


def _load_job_record(job_id: str) -> dict[str, Any]:
    record = _JOBS.get(job_id)
    if record is None:
        record = _load_persisted_job(job_id)
        if record is not None:
            _JOBS[job_id] = record
    if record is None:
        raise FileNotFoundError("Word 导出任务不存在，请重新点击下载。")
    return record


def _cleanup_cache() -> None:
    try:
        retention_days = max(1, min(30, int(os.environ.get("PRACTICE_EXPORT_CACHE_DAYS", "7"))))
    except ValueError:
        retention_days = 7
    cutoff = datetime.now().timestamp() - timedelta(days=retention_days).total_seconds()
    if not EXPORT_CACHE_DIR.exists():
        return
    for path in EXPORT_CACHE_DIR.glob("*.docx"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _LOCK:
        record = _JOBS[job_id]
        record.update(changes)
        record["updated_at"] = _now()
        _persist_job(record)
        return dict(record)


def _queue_automatic_failure_report(record: dict[str, Any]) -> None:
    try:
        from .support_reporting import queue_automatic_failure_report

        queue_automatic_failure_report({
            "task_id": str(record.get("job_id") or ""),
            "task_kind": "practice_export",
            "task_status": "failed",
            "task_stage": "practice_word_export",
            "task_run_started_at": str(record.get("created_at") or ""),
            "task_title": str(record.get("filename") or "练习题 Word"),
            "operation": "practice_word_export",
            "error": str(record.get("error") or ""),
        })
    except Exception:
        pass


def _execute_export_job(job_id: str, data: dict[str, Any]) -> None:
    started = time.perf_counter()
    with _LOCK:
        _ACTIVE.add(job_id)
    try:
        _update_job(job_id, status="running", current_operation="正在准备 Word 文档")

        def report_progress(completed: int, total: int) -> None:
            _update_job(
                job_id,
                completed_count=completed,
                total_count=total,
                current_operation=f"正在处理第 {completed}/{total} 题",
            )

        build_started = time.perf_counter()
        content = build_practice_question_docx(data, progress_callback=report_progress)
        build_seconds = time.perf_counter() - build_started
        docx_report = validate_docx_output(content, data)
        if not docx_report.get("ok"):
            issues = list(dict.fromkeys(str(issue) for issue in docx_report.get("issues") or [] if str(issue).strip()))
            raise ValueError("生成的 Word 未通过完整性校验：" + "；".join(issues[:8]))
        with _LOCK:
            cache_path = Path(str(_JOBS[job_id]["cache_path"]))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".docx.tmp")
        temporary.write_bytes(content)
        temporary.replace(cache_path)
        elapsed = time.perf_counter() - started
        _update_job(
            job_id,
            status="completed",
            current_operation="Word 已生成并通过完整性校验，可下载",
            completed_count=len(data.get("exercises") or []),
            total_count=len(data.get("exercises") or []),
            size_bytes=len(content),
            build_seconds=round(build_seconds, 3),
            elapsed_seconds=round(elapsed, 3),
            cached=False,
            document_contract_version=PRACTICE_DOCUMENT_CONTRACT_VERSION,
        )
        append_runtime_log(
            "practice_export",
            f"题目 Word 后台生成完成：{len(data.get('exercises') or [])} 题，耗时 {elapsed:.2f} 秒",
            payload={
                "job_id": job_id,
                "build_seconds": round(build_seconds, 3),
                "size_bytes": len(content),
            },
        )
    except Exception as exc:
        elapsed = time.perf_counter() - started
        failed_record = _update_job(
            job_id,
            status="failed",
            current_operation="Word 生成失败",
            error=str(exc) or exc.__class__.__name__,
            diagnostic_context={
                "exception_type": exc.__class__.__name__,
                "traceback": traceback.format_exc(),
            },
            elapsed_seconds=round(elapsed, 3),
        )
        append_runtime_log(
            "practice_export",
            f"题目 Word 后台生成失败：{str(exc) or exc.__class__.__name__}",
            "error",
            {"job_id": job_id, "elapsed_seconds": round(elapsed, 3)},
        )
        _queue_automatic_failure_report(failed_record)
    finally:
        with _LOCK:
            _ACTIVE.discard(job_id)


def _run_export_job(job_id: str, data: dict[str, Any]) -> None:
    with _RUN_SLOTS:
        _execute_export_job(job_id, data)


def create_or_reuse_practice_export_job(
    data: dict[str, Any],
    filename: str,
) -> dict[str, Any]:
    EXPORT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_cache()
    key = _cache_key(data)
    job_id = f"practice_word_{key[:24]}"
    cache_path = EXPORT_CACHE_DIR / f"{key}.docx"
    total = len(data.get("exercises") or [])
    export_validation = validate_practice_export(data)
    release_level = str(export_validation.get("release_level") or "formal")
    warning_issues = list(export_validation.get("warning_issues") or [])
    now = _now()
    with _LOCK:
        existing = _JOBS.get(job_id) or _load_persisted_job(job_id)
        if existing is not None:
            _JOBS[job_id] = existing
        if cache_path.is_file():
            cached_content = cache_path.read_bytes()
            docx_report = validate_docx_output(cached_content, data)
            if not docx_report.get("ok"):
                cache_path.unlink(missing_ok=True)
                append_runtime_log(
                    "practice_export",
                    "发现不完整的题目 Word 缓存，已删除并重新生成",
                    "warning",
                    {"job_id": job_id, "issues": docx_report.get("issues", [])[:8]},
                )
            else:
                record = {
                    "job_id": job_id,
                    "status": "completed",
                    "current_operation": "已复用通过完整性校验的 Word 缓存",
                    "created_at": existing.get("created_at", now) if existing else now,
                    "updated_at": now,
                    "completed_count": total,
                    "total_count": total,
                    "size_bytes": cache_path.stat().st_size,
                    "filename": filename,
                    "cache_key": key,
                    "cache_path": str(cache_path),
                    "cached": True,
                    "error": "",
                    "warning_issues": warning_issues,
                    "release_level": release_level,
                    "document_contract_version": PRACTICE_DOCUMENT_CONTRACT_VERSION,
                    "payload": data,
                }
                _JOBS[job_id] = record
                _persist_job(record)
                append_runtime_log(
                    "practice_export",
                    f"复用题目 Word 缓存：{total} 题",
                    payload={"job_id": job_id, "size_bytes": record["size_bytes"]},
                )
                return _public_job(record)
        if existing and job_id in _ACTIVE and existing.get("status") in {"queued", "running"}:
            return _public_job(existing)
        record = {
            "job_id": job_id,
            "status": "queued",
            "current_operation": "等待生成 Word",
            "created_at": now,
            "updated_at": now,
            "completed_count": 0,
            "total_count": total,
            "size_bytes": 0,
            "filename": filename,
            "cache_key": key,
            "cache_path": str(cache_path),
            "cached": False,
            "error": "",
            "warning_issues": warning_issues,
            "release_level": release_level,
            "document_contract_version": PRACTICE_DOCUMENT_CONTRACT_VERSION,
            "payload": data,
        }
        _JOBS[job_id] = record
        _ACTIVE.add(job_id)
        _persist_job(record)
    worker = threading.Thread(
        target=_run_export_job,
        args=(job_id, data),
        name=f"practice-word-{key[:8]}",
        daemon=True,
    )
    worker.start()
    return _public_job(record)


def load_practice_export_job(job_id: str) -> dict[str, Any]:
    with _LOCK:
        record = _load_job_record(job_id)
        return _public_job(dict(record))


def retry_practice_export_job(job_id: str) -> dict[str, Any]:
    """Retry from the server-owned request snapshot without exposing it to the browser."""
    with _LOCK:
        record = dict(_load_job_record(job_id))
        status = str(record.get("status") or "")
        cache_path = Path(str(record.get("cache_path") or ""))
        if status in {"queued", "running"} or (status == "completed" and cache_path.is_file()):
            return _public_job(record)
        payload = record.get("payload")
        filename = str(record.get("filename") or "专项练习-题目.docx")
    if not isinstance(payload, dict):
        raise ValueError("Word 导出请求快照不可用，请返回原练习重新下载。")
    return create_or_reuse_practice_export_job(payload, filename)


def practice_export_download(job_id: str) -> tuple[Path, str]:
    with _LOCK:
        record = _load_job_record(job_id)
        if record.get("status") != "completed":
            raise ValueError("Word 尚未生成完成。")
        path = Path(str(record.get("cache_path") or ""))
        filename = str(record.get("filename") or "专项练习-题目.docx")
    if not path.is_file():
        _update_job(
            job_id,
            status="failed",
            current_operation="Word 文件已不可用",
            error="Word 文件已过期或不可用，请重新生成。",
        )
        raise FileNotFoundError("Word 文件已过期或不可用，请重新生成。")
    return path, filename


def recover_practice_export_jobs() -> dict[str, int]:
    """Restore deterministic Word exports after a process restart."""
    job_dir = EXPORT_CACHE_DIR / "jobs"
    if not job_dir.exists():
        return {"restored": 0, "resumed": 0, "completed_from_cache": 0, "failed": 0}
    to_resume: list[tuple[str, dict[str, Any]]] = []
    counts = {"restored": 0, "resumed": 0, "completed_from_cache": 0, "failed": 0}
    with _LOCK:
        for path in sorted(job_dir.glob("practice_word_*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job_id = str(record.get("job_id") or "")
            if not job_id.startswith("practice_word_"):
                continue
            _JOBS[job_id] = record
            counts["restored"] += 1
            if record.get("status") not in {"queued", "running"}:
                continue
            payload = record.get("payload")
            cache_path = Path(str(record.get("cache_path") or ""))
            if isinstance(payload, dict) and cache_path.is_file():
                report = validate_docx_output(cache_path.read_bytes(), payload)
                if report.get("ok"):
                    record.update(
                        status="completed",
                        current_operation="服务恢复后复用已完成的 Word 文件",
                        completed_count=len(payload.get("exercises") or []),
                        total_count=len(payload.get("exercises") or []),
                        size_bytes=cache_path.stat().st_size,
                        cached=True,
                        error="",
                        updated_at=_now(),
                    )
                    _persist_job(record)
                    counts["completed_from_cache"] += 1
                    continue
            if not isinstance(payload, dict):
                record.update(
                    status="failed",
                    current_operation="Word 恢复失败",
                    error="服务重启后缺少导出请求快照，请重新点击下载。",
                    updated_at=_now(),
                )
                _persist_job(record)
                _queue_automatic_failure_report(record)
                counts["failed"] += 1
                continue
            record.update(
                status="queued",
                current_operation="服务恢复后重新排队生成 Word",
                error="",
                updated_at=_now(),
            )
            _ACTIVE.add(job_id)
            _persist_job(record)
            to_resume.append((job_id, payload))
    for job_id, payload in to_resume:
        threading.Thread(
            target=_run_export_job,
            args=(job_id, payload),
            name=f"practice-word-recover-{job_id[-8:]}",
            daemon=True,
        ).start()
        counts["resumed"] += 1
    return counts
