from __future__ import annotations

import hashlib
import http.client
import json
import os
import platform
import random
import re
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from .model_diagnostics import diagnostic_attachments, relevant_model_diagnostics
from .paths import DATA_ROOT, LOCAL_CONFIG_DIR, PROJECT_ROOT
from .runtime_monitor import ERROR_TRACE_LOG, MODEL_CALL_LEDGER, RUNTIME_LOG
from .task_store import task_dir
from .version import get_app_version, get_source_revision

SUPPORT_ROOT = DATA_ROOT / "support_reports"
PENDING_DIR = SUPPORT_ROOT / "pending"
RECEIPTS_PATH = SUPPORT_ROOT / "receipts.jsonl"
LOCAL_CONFIG_PATH = LOCAL_CONFIG_DIR / "support_reporting.json"
BUNDLED_CONFIG_PATH = PROJECT_ROOT / "config" / "support_reporting.json"
MAX_COMPRESSED_BYTES = 12 * 1024 * 1024
PENDING_TOTAL_LIMIT = 25 * 1024 * 1024
PENDING_FILE_LIMIT = 5
MAX_FRONTEND_EVENTS = 240
RETRY_SECONDS = 60

_LOCK = threading.RLock()
_STOP = threading.Event()
_WORKER: threading.Thread | None = None
_ALLOWED_EVENT_FIELDS = {
    "time", "kind", "page", "action", "target", "method", "path", "request_id",
    "status", "duration_ms", "error_code", "support_id", "message", "task_id",
    "question_id", "exercise_index", "history_id",
}
_LIFECYCLE_PAYLOAD_FIELDS = {
    "status", "current_stage", "stage", "operation", "action", "reason", "previous_status",
    "previous_stage", "question_id", "figure_id", "completed", "total", "error", "error_code",
    "support_id", "request_id", "use_model", "render", "reuse_fragments",
}
_RELATED_STAGE_FILES = (
    "structured_exam.json",
    "question_understanding.json",
    "knowledge_plans.json",
    "evidence_selection.json",
    "answer_fragments.json",
    "answer_drafts.json",
    "answer_coverage_audit.json",
    "content_quality_audit.json",
    "semantic_quality_advisories.json",
    "selective_quality_review.json",
    "prefigure_correctness_review.json",
    "figure_schema_plan.json",
    "figure_visual_qa.json",
    "docx_audit.json",
    "docx_repair.json",
    "figure_size_audit.json",
    "render_audit.json",
    "question_review_docx.json",
    "figure_review_docx.json",
    "acceptance_report.json",
    "pipeline_error.json",
    "final_acceptance_report.json",
)

_GLOBAL_DELIVERY_REPORTS = {
    "docx_audit.json",
    "docx_repair.json",
    "figure_size_audit.json",
    "render_audit.json",
    "question_review_docx.json",
    "figure_review_docx.json",
    "acceptance_report.json",
    "final_acceptance_report.json",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return None
    except OSError:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _config() -> dict[str, str]:
    value: dict[str, Any] = {}
    for path in (BUNDLED_CONFIG_PATH, LOCAL_CONFIG_PATH):
        loaded = _read_json(path)
        if isinstance(loaded, dict):
            value.update(loaded)
    return {
        "receiver_url": str(os.environ.get("ANSWER_BOOK_SUPPORT_URL") or value.get("receiver_url") or "").strip().rstrip("/"),
        "receiver_token": str(os.environ.get("ANSWER_BOOK_SUPPORT_TOKEN") or value.get("receiver_token") or "").strip(),
    }


def _redact(value: Any, limit: int = 20000) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1***", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "***", text)
    text = re.sub(r"(?i)(api[_-]?key|password|secret|access[_-]?token)(\s*[:=]\s*)[^\s,;]+", r"\1\2***", text)
    home = str(Path.home())
    if home:
        text = text.replace(home, "<user-home>")
    encoded = text.encode("utf-8", errors="replace")
    return text if len(encoded) <= limit else encoded[:limit].decode("utf-8", errors="ignore") + "<truncated>"


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_sanitize(item, depth=depth + 1) for item in value[:500]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:400]:
            key = str(raw_key)[:160]
            lowered = key.lower()
            if lowered in {"api_key", "authorization", "password", "secret", "token", "access_token"}:
                result[key] = "***"
            else:
                result[key] = _sanitize(item, depth=depth + 1)
        return result
    return _redact(value, 1000)


def _frontend_events(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw[-MAX_FRONTEND_EVENTS:]:
        if not isinstance(item, dict):
            continue
        row = {key: _sanitize(item.get(key)) for key in _ALLOWED_EVENT_FIELDS if item.get(key) not in (None, "")}
        if row.get("path"):
            row["path"] = str(row["path"]).split("?", 1)[0]
        if row:
            rows.append(row)
    return rows


def _jsonl_rows(path: Path, limit: int = 1000) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _task_lifecycle(task_id: str) -> list[dict[str, Any]]:
    if not task_id:
        return []
    if task_id.startswith("generation_"):
        return _practice_job_lifecycle(task_id)
    if task_id.startswith("word_format_"):
        return _word_format_task_lifecycle(task_id)
    try:
        event_path = task_dir(task_id) / "events.jsonl"
    except (ValueError, OSError):
        return []
    rows = _jsonl_rows(event_path, limit=100000)
    result: list[dict[str, Any]] = []
    previous_signature = ""
    for item in rows:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        compact = {key: _sanitize(payload.get(key)) for key in _LIFECYCLE_PAYLOAD_FIELDS if payload.get(key) not in (None, "")}
        row = {"time": _sanitize(item.get("time")), "event": _sanitize(item.get("event")), "payload": compact}
        signature = json.dumps({"event": row["event"], "payload": compact}, ensure_ascii=False, sort_keys=True)
        if signature == previous_signature and str(row["event"]) in {"task_updated", "heartbeat"}:
            if result:
                result[-1]["repeat_count"] = int(result[-1].get("repeat_count") or 1) + 1
                result[-1]["last_time"] = row["time"]
            continue
        result.append(row)
        previous_signature = signature
    return result


def _word_format_task_lifecycle(task_id: str) -> list[dict[str, Any]]:
    try:
        from .word_format_tasks import word_format_task_payload

        payload = word_format_task_payload(task_id)
    except Exception:
        return []
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    status = str(payload.get("status") or task.get("status") or "")
    rows = [{
        "time": _sanitize(task.get("created_at")),
        "event": "word_format_task_created",
        "payload": {"status": "running", "operation": "word_format", "stage": "format_review"},
    }]
    if status in {"completed", "completed_with_issues", "failed"}:
        rows.append({
            "time": _sanitize(task.get("updated_at")),
            "event": "word_format_task_finished",
            "payload": {
                "status": status,
                "operation": "word_format",
                "stage": "completed" if status != "failed" else "failed",
                "error": _sanitize(task.get("error")) if task.get("error") else "",
            },
        })
    return rows


def _practice_job_lifecycle(job_id: str) -> list[dict[str, Any]]:
    try:
        from .practice_jobs import list_practice_jobs_for_batch, load_practice_job

        current = load_practice_job(job_id, include_payload=False)
        batch_id = str(current.get("practice_batch_id") or "")
        records = list_practice_jobs_for_batch(batch_id) if batch_id else [current]
        if not any(str(row.get("job_id") or "") == job_id for row in records):
            records.append(current)
    except Exception:
        return []
    result: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda row: str(row.get("created_at") or "")):
        base = {
            "job_id": _sanitize(record.get("job_id")),
            "operation": _sanitize(record.get("operation")),
            "stage": _sanitize(record.get("current_stage")),
        }
        result.append({
            "time": _sanitize(record.get("created_at")),
            "event": "practice_job_created",
            "payload": {**base, "status": "queued"},
        })
        if record.get("started_at"):
            result.append({
                "time": _sanitize(record.get("started_at")),
                "event": "practice_job_started",
                "payload": {**base, "status": "running"},
            })
        status = str(record.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            result.append({
                "time": _sanitize(record.get("completed_at") or record.get("updated_at")),
                "event": "practice_job_finished",
                "payload": {
                    **base,
                    "status": status,
                    "error": _sanitize(record.get("error")) if record.get("error") else "",
                    "completed": _sanitize(record.get("completed_count")),
                    "total": _sanitize(record.get("total_count")),
                },
            })
    return result


def _matching_nodes(value: Any, question_id: str, *, depth: int = 0) -> list[Any]:
    if depth > 10 or not question_id:
        return []
    matches: list[Any] = []
    if isinstance(value, dict):
        ids = {
            str(value.get(key) or "")
            for key in ("question_id", "source_question_id", "id", "parent_question_id", "active_item")
        }
        if question_id in ids:
            matches.append(_sanitize(value))
            return matches
        for item in value.values():
            matches.extend(_matching_nodes(item, question_id, depth=depth + 1))
            if len(matches) >= 60:
                break
    elif isinstance(value, list):
        for item in value:
            matches.extend(_matching_nodes(item, question_id, depth=depth + 1))
            if len(matches) >= 60:
                break
    return matches


def _exam_content(task_id: str, question_id: str) -> dict[str, Any]:
    if not task_id:
        return {}
    try:
        stage_root = task_dir(task_id) / "stage_outputs"
    except (ValueError, OSError):
        return {"task_id": task_id, "unavailable": True}
    content: dict[str, Any] = {}
    legacy_missing: list[str] = []
    for name in _RELATED_STAGE_FILES:
        path = stage_root / name
        value = _read_json(path)
        if value is None:
            continue
        if name == "pipeline_error.json" or name in _GLOBAL_DELIVERY_REPORTS:
            content[name] = _sanitize(value)
        elif question_id:
            nodes = _matching_nodes(value, question_id)
            if nodes:
                content[name] = nodes
        elif name in {"pipeline_error.json", "content_quality_audit.json", "final_acceptance_report.json"}:
            content[name] = _sanitize(value)
        elif name in {"answer_fragments.json", "structured_exam.json"}:
            legacy_missing.append(name)
    if legacy_missing:
        content["selection_notice"] = {
            "message": "未定位到具体题目，未发送整份题目或答案文件。",
            "omitted_files": legacy_missing,
        }
    snapshot = stage_root / "question_snapshots" / f"{question_id}.png"
    if question_id and snapshot.is_file():
        content["question_snapshot"] = str(snapshot)
    return content


def _practice_content(history_id: str, exercise_index: Any) -> dict[str, Any]:
    if not history_id:
        return {}
    try:
        from .practice_store import load_practice_record

        record = load_practice_record(history_id)
    except Exception:
        return {"history_id": history_id, "unavailable": True}
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    try:
        index = int(exercise_index)
    except (TypeError, ValueError):
        index = -1
    exercises = data.get("exercises") if isinstance(data.get("exercises"), list) else []
    exercise = exercises[index] if 0 <= index < len(exercises) and isinstance(exercises[index], dict) else None
    result: dict[str, Any] = {
        "history_id": history_id,
        "quality": _sanitize(data.get("quality") or {}),
        "generation": _sanitize(data.get("generation") or {}),
    }
    try:
        from .practice_export_jobs import EXPORT_CACHE_DIR

        export_jobs = []
        job_root = EXPORT_CACHE_DIR / "jobs"
        for path in sorted(job_root.glob("practice_word_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            record = _read_json(path)
            payload = record.get("payload") if isinstance(record, dict) and isinstance(record.get("payload"), dict) else {}
            if str(payload.get("history_id") or "") != history_id:
                continue
            export_jobs.append(_sanitize({
                key: record.get(key)
                for key in (
                    "job_id", "status", "current_operation", "created_at", "updated_at", "completed_count",
                    "total_count", "size_bytes", "filename", "cached", "error", "warning_issues",
                    "release_level", "document_contract_version", "build_seconds", "elapsed_seconds",
                    "diagnostic_context",
                )
            }))
            if len(export_jobs) >= 5:
                break
        if export_jobs:
            result["word_export_jobs"] = export_jobs
    except (OSError, ImportError):
        pass
    if exercise is not None:
        result["exercise_index"] = index
        result["exercise"] = _sanitize(exercise)
        parent_id = str(exercise.get("parent_plan_item_id") or exercise.get("plan_item_id") or "")
        source_id = str(exercise.get("source_question_id") or "")
        plan = data.get("blueprint") if isinstance(data.get("blueprint"), dict) else {}
        plan_items = plan.get("exercise_plan") if isinstance(plan.get("exercise_plan"), list) else []
        result["blueprint_items"] = [_sanitize(item) for item in plan_items if isinstance(item, dict) and str(item.get("plan_item_id") or "") == parent_id]
        sources = data.get("selected_source_questions") if isinstance(data.get("selected_source_questions"), list) else []
        result["source_questions"] = [_sanitize(item) for item in sources if isinstance(item, dict) and str(item.get("source_question_id") or "") == source_id]
    else:
        result["selection_notice"] = "未定位到具体练习题，只发送任务质量与失败摘要。"
    return result


def _practice_job_content(job_id: str) -> dict[str, Any]:
    try:
        from .practice_jobs import load_practice_job

        record = load_practice_job(job_id, include_payload=True)
    except Exception:
        return {"job_id": job_id, "unavailable": True}
    return {
        "job_id": job_id,
        "task_kind": _sanitize(record.get("task_kind")),
        "practice_batch_id": _sanitize(record.get("practice_batch_id")),
        "title": _sanitize(record.get("title")),
        "operation": _sanitize(record.get("operation")),
        "status": _sanitize(record.get("status")),
        "current_stage": _sanitize(record.get("current_stage")),
        "created_at": _sanitize(record.get("created_at")),
        "started_at": _sanitize(record.get("started_at")),
        "completed_at": _sanitize(record.get("completed_at")),
        "elapsed_seconds": _sanitize(record.get("elapsed_seconds")),
        "error": _sanitize(record.get("error")),
        "progress_message": _sanitize(record.get("progress_message")),
        "model_usage": _sanitize(record.get("model_usage") or {}),
        "diagnostic_context": _sanitize(record.get("diagnostic_context") or {}),
        "request_payload": _sanitize(record.get("payload") or {}),
        "failure_context": _sanitize(record.get("failure_context") or {}),
        "partial_or_final_result": _sanitize(record.get("result")) if record.get("result") is not None else None,
    }


def _format_task_content(task_id: str) -> dict[str, Any]:
    try:
        from .word_format_tasks import _load_record, word_format_task_payload

        payload = word_format_task_payload(task_id)
        record = _load_record(task_id)
    except Exception:
        return {"task_id": task_id, "unavailable": True}
    return _sanitize({
        "task_id": task_id,
        "status": payload.get("status"),
        "mode": payload.get("mode"),
        "task": payload.get("task") or {},
        "initial_audit": payload.get("report") or {},
        "final_audit": payload.get("final_report") or {},
        "error": record.get("error") or "",
        "diagnostic_context": record.get("diagnostic_context") or {},
    })


def _related_runtime(task_id: str, request_ids: set[str], support_ids: set[str]) -> list[dict[str, Any]]:
    rows = _jsonl_rows(RUNTIME_LOG, 2000)
    result: list[dict[str, Any]] = []
    for row in rows:
        text = json.dumps(row, ensure_ascii=False)
        related = bool(task_id and task_id in text) or any(value and value in text for value in request_ids | support_ids)
        if related:
            result.append(_sanitize(row))
    return result[-300:]


def _model_summary(task_id: str) -> dict[str, Any]:
    rows = _jsonl_rows(MODEL_CALL_LEDGER, 100000)
    if task_id:
        rows = [row for row in rows if str(row.get("task_id") or "") == task_id]
    else:
        rows = []
    grouped: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for row in rows:
        key = "|".join(str(row.get(field) or "") for field in ("provider", "model", "stage", "purpose"))
        item = grouped.setdefault(key, {
            "provider": row.get("provider", ""), "model": row.get("model", ""), "stage": row.get("stage", ""),
            "purpose": row.get("purpose", ""), "count": 0, "success": 0, "failed": 0, "elapsed_ms": 0,
        })
        item["count"] += 1
        item["elapsed_ms"] += int(row.get("elapsed_ms") or 0)
        if row.get("outcome") == "succeeded":
            item["success"] += 1
        else:
            item["failed"] += 1
            failures.append(_sanitize(row))
    return {"groups": list(grouped.values()), "failures": failures[-30:]}


def _related_error_traces(task_id: str, request_ids: set[str], support_ids: set[str]) -> list[dict[str, Any]]:
    rows = _jsonl_rows(ERROR_TRACE_LOG, 200)
    result: list[dict[str, Any]] = []
    for row in rows:
        text = json.dumps(row, ensure_ascii=False)
        if bool(task_id and task_id in text) or any(value and value in text for value in request_ids | support_ids):
            result.append(_sanitize(row))
    if result:
        return result[-30:]
    return []


def _diagnostic_score(manifest: dict[str, Any]) -> int:
    """Rank duplicate bundles by retained evidence, not merely arrival time."""
    coverage = manifest.get("diagnostic_coverage") if isinstance(manifest.get("diagnostic_coverage"), dict) else {}
    available = coverage.get("available") if isinstance(coverage.get("available"), dict) else {}
    legacy_counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    counts = coverage.get("counts") if isinstance(coverage.get("counts"), dict) else legacy_counts
    missing = coverage.get("missing_expected_evidence") if isinstance(coverage.get("missing_expected_evidence"), list) else []
    weights = {
        "failure_context": 120,
        "failure_diagnostic": 100,
        "model_diagnostics": 80,
        "task_lifecycle": 30,
        "backend_error_traces": 25,
        "runtime_context": 20,
        "model_call_summary": 15,
        "user_feedback": 10,
    }
    score = sum(weight for key, weight in weights.items() if available.get(key))
    score += min(20, int(counts.get("model_traces") or 0)) * 4
    score += min(40, int(counts.get("lifecycle_events") or 0))
    score += min(20, int(counts.get("backend_error_traces") or 0))
    score += min(20, int(counts.get("runtime_events") or 0))
    score += min(20, int(counts.get("frontend_events") or 0)) // 4
    score -= len(missing) * 20
    return score


def _fingerprint(context: dict[str, Any], events: list[dict[str, Any]], lifecycle: list[dict[str, Any]], traces: list[dict[str, Any]]) -> str:
    latest_failure = next((row for row in reversed(events) if row.get("error_code") or str(row.get("status") or "").startswith(("4", "5"))), {})
    failed_trace = next((row for row in reversed(traces) if row.get("outcome") != "ok"), {})
    call = failed_trace.get("call") if isinstance(failed_trace.get("call"), dict) else {}
    latest_lifecycle = lifecycle[-1] if lifecycle else {}
    lifecycle_payload = latest_lifecycle.get("payload") if isinstance(latest_lifecycle.get("payload"), dict) else {}
    signature = {
        "schema": 2,
        "version": get_app_version(),
        "scope": context.get("scope"),
        "page": context.get("page"),
        "task_id": context.get("task_id"),
        "question_id": context.get("question_id"),
        "exercise_index": context.get("exercise_index"),
        "task_run_started_at": context.get("task_run_started_at"),
        "feedback_kind": context.get("feedback_kind"),
        "feedback_note": context.get("feedback_note"),
        "last_event": {key: latest_failure.get(key) for key in ("kind", "action", "path", "status", "error_code")},
        "last_lifecycle": {
            "event": latest_lifecycle.get("event"),
            "status": lifecycle_payload.get("status"),
            "stage": lifecycle_payload.get("stage") or lifecycle_payload.get("current_stage"),
            "operation": lifecycle_payload.get("operation"),
            "error": lifecycle_payload.get("error"),
            "error_code": lifecycle_payload.get("error_code"),
        },
        "model_failure": {"provider": call.get("provider"), "model": call.get("model"), "stage": call.get("stage"), "error": failed_trace.get("error")},
    }
    raw = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _writestr(zf: zipfile.ZipFile, name: str, value: Any, *, budget: int) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(raw) <= budget:
        zf.writestr(name, raw)
        return
    if isinstance(value, list):
        edge = max(1, min(len(value) // 2, 80))
        while True:
            reduced: Any = {
                "truncated": True,
                "original_item_count": len(value),
                "first_items": value[:edge],
                "last_items": value[-edge:],
            }
            reduced_raw = (json.dumps(reduced, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            if len(reduced_raw) <= budget or edge <= 1:
                break
            edge = max(1, edge // 2)
    else:
        preview = raw[: max(1, budget - 400)].decode("utf-8", errors="ignore")
        reduced = {"truncated": True, "original_bytes": len(raw), "preview": preview}
        reduced_raw = (json.dumps(reduced, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(reduced_raw) > budget:
        reduced_raw = json.dumps({"truncated": True, "original_bytes": len(raw)}, ensure_ascii=False).encode("utf-8")
    zf.writestr(name, reduced_raw)


def _build_report(context: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    SUPPORT_ROOT.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    events = _frontend_events(context.get("events"))
    task_id = _redact(context.get("task_id"), 160)
    question_id = _redact(context.get("question_id"), 160)
    history_id = _redact(context.get("history_id"), 160)
    active_item = question_id or _redact(context.get("active_item"), 160)
    lifecycle = _task_lifecycle(task_id)
    diagnostic_task_id = task_id or str(context.get("job_id") or "")
    try:
        traces = relevant_model_diagnostics(diagnostic_task_id, active_item=active_item) if diagnostic_task_id else []
    except Exception:
        traces = []
    request_ids = {str(row.get("request_id") or "") for row in events}
    support_ids = {str(row.get("support_id") or "") for row in events}
    fingerprint = _fingerprint(context, events, lifecycle, traces)
    report_id = f"AB-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"
    target = PENDING_DIR / f"{fingerprint}-{report_id}.zip"
    fd, raw_tmp = tempfile.mkstemp(prefix=".support-", suffix=".part", dir=str(PENDING_DIR))
    os.close(fd)
    tmp = Path(raw_tmp)
    related_history_content: dict[str, Any] = {}
    if task_id.startswith("generation_"):
        content = _practice_job_content(task_id)
        if history_id:
            related_history_content = _practice_content(history_id, context.get("exercise_index"))
        primary_content_kind = "practice_job"
    elif task_id.startswith("word_format_") or str(context.get("task_kind") or "") == "format":
        content = _format_task_content(task_id)
        primary_content_kind = "word_format_task"
    elif history_id:
        content = _practice_content(history_id, context.get("exercise_index"))
        primary_content_kind = "practice_history"
    else:
        content = _exam_content(task_id, question_id)
        primary_content_kind = "exam_task"
    snapshot = content.pop("question_snapshot", "") if isinstance(content, dict) else ""
    selection = str(context.get("selection") or "").strip()
    runtime_context = _related_runtime(task_id, request_ids, support_ids)
    backend_traces = _related_error_traces(task_id, request_ids, support_ids)
    model_summary = _model_summary(task_id)
    model_call_count = sum(int(group.get("count") or 0) for group in model_summary.get("groups") or [])
    failure_context = content.get("failure_context") if isinstance(content, dict) else {}
    failure_diagnostic = content.get("diagnostic_context") if isinstance(content, dict) else {}
    feedback_kind = _redact(context.get("feedback_kind"), 80)
    feedback_note = _redact(context.get("feedback_note"), 4000)
    user_feedback = {"kind": feedback_kind, "note": feedback_note} if feedback_kind or feedback_note else {}
    task_failed = str(content.get("status") or context.get("task_status") or "") == "failed" if isinstance(content, dict) else False
    missing_expected_evidence: list[str] = []
    if task_id and primary_content_kind in {"practice_job", "exam_task", "word_format_task"} and not lifecycle:
        missing_expected_evidence.append("task_lifecycle")
    pipeline_error = content.get("pipeline_error.json") if isinstance(content.get("pipeline_error.json"), dict) else {}
    task_error = str(content.get("error") or pipeline_error.get("error") or "")
    if task_failed and not task_error:
        missing_expected_evidence.append("task_error")
    error_text = task_error
    if task_failed and ("蓝图" in error_text or "门禁" in error_text) and not failure_context:
        missing_expected_evidence.append("rejected_plan_and_gate_findings")
    if model_call_count and not traces:
        missing_expected_evidence.append("model_diagnostics")
    diagnostic_coverage = {
        "schema_version": 1,
        "primary_content_kind": primary_content_kind,
        "related_history_included": bool(related_history_content),
        "available": {
            "task_lifecycle": bool(lifecycle),
            "runtime_context": bool(runtime_context),
            "backend_error_traces": bool(backend_traces),
            "model_call_summary": bool(model_call_count),
            "model_diagnostics": bool(traces),
            "failure_context": bool(failure_context),
            "failure_diagnostic": bool(failure_diagnostic),
            "user_feedback": bool(user_feedback),
        },
        "counts": {
            "frontend_events": len(events),
            "lifecycle_events": len(lifecycle),
            "runtime_events": len(runtime_context),
            "backend_error_traces": len(backend_traces),
            "model_calls": model_call_count,
            "model_traces": len(traces),
        },
        "missing_expected_evidence": missing_expected_evidence,
    }
    manifest = {
        "schema_version": 2,
        "report_id": report_id,
        "fingerprint": fingerprint,
        "created_at": _now(),
        "scope": _redact(context.get("scope"), 40),
        "application": {"version": get_app_version(), "source_revision": get_source_revision()},
        "system": {"platform": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": platform.python_version()},
        "context": {
            "session_id": _redact(context.get("session_id"), 120), "page": _redact(context.get("page"), 80),
            "task_id": task_id, "question_id": question_id, "history_id": history_id,
            "exercise_index": context.get("exercise_index"), "selected_text": _redact(selection, 2000),
            "task_kind": _redact(context.get("task_kind"), 40), "task_status": _redact(context.get("task_status"), 40),
            "task_stage": _redact(context.get("task_stage"), 120), "operation": _redact(context.get("operation"), 80),
            "task_title": _redact(context.get("task_title"), 200),
            "task_model": _redact(context.get("task_model"), 120),
            "task_model_label": _redact(context.get("task_model_label"), 120),
            "practice_batch_id": _redact(context.get("practice_batch_id"), 160),
            "report_group_id": _redact(context.get("report_group_id"), 120),
            "task_run_started_at": _redact(context.get("task_run_started_at"), 80),
            "feedback_kind": feedback_kind,
            "feedback_note": feedback_note,
        },
        "content_policy": {
            "related_question_answer_model_context_included": True,
            "unrelated_task_content_included": False,
            "credentials_included": False,
        },
        "model_context_available": bool(traces),
        "diagnostic_coverage": diagnostic_coverage,
        "counts": diagnostic_coverage["counts"],
    }
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as zf:
            _writestr(zf, "manifest.json", manifest, budget=64 * 1024)
            _writestr(zf, "frontend_events.json", events, budget=256 * 1024)
            _writestr(zf, "task_lifecycle.json", lifecycle, budget=256 * 1024)
            _writestr(zf, "runtime_error_context.json", runtime_context, budget=1024 * 1024)
            _writestr(zf, "backend_error_traces.json", backend_traces, budget=1024 * 1024)
            _writestr(zf, "model_call_summary.json", model_summary, budget=512 * 1024)
            _writestr(zf, "diagnostic_coverage.json", diagnostic_coverage, budget=128 * 1024)
            if user_feedback:
                _writestr(zf, "user_feedback.json", user_feedback, budget=16 * 1024)
            _writestr(zf, "related_content.json", content, budget=3 * 1024 * 1024)
            if related_history_content:
                _writestr(zf, "related_history_content.json", related_history_content, budget=1024 * 1024)
            if failure_context:
                _writestr(zf, "failure_context.json", failure_context, budget=2 * 1024 * 1024)
            if failure_diagnostic:
                _writestr(zf, "task_failure_diagnostic.json", failure_diagnostic, budget=1024 * 1024)
            _writestr(zf, "model_diagnostics.json", traces, budget=2 * 1024 * 1024)
            if snapshot and Path(str(snapshot)).is_file() and Path(str(snapshot)).stat().st_size <= 6 * 1024 * 1024:
                zf.write(Path(str(snapshot)), "attachments/question_snapshot.png")
            attachment_budget = 6 * 1024 * 1024
            try:
                attachment_paths = diagnostic_attachments(diagnostic_task_id, traces) if diagnostic_task_id else []
            except Exception:
                attachment_paths = []
            for path in attachment_paths:
                size = path.stat().st_size
                if size > attachment_budget:
                    continue
                zf.write(path, f"attachments/model/{path.name}")
                attachment_budget -= size
        if tmp.stat().st_size > MAX_COMPRESSED_BYTES:
            raise ValueError("诊断包超过 12 MiB 安全上限。")
        result_target = target
        result_manifest = manifest
        with _LOCK:
            duplicates = list(PENDING_DIR.glob(f"{fingerprint}-AB-*.zip"))
            richer_duplicate: tuple[Path, dict[str, Any]] | None = None
            for duplicate in duplicates:
                try:
                    with zipfile.ZipFile(duplicate) as existing_zip:
                        existing_manifest = json.loads(existing_zip.read("manifest.json"))
                except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError):
                    continue
                if _diagnostic_score(existing_manifest) > _diagnostic_score(manifest):
                    richer_duplicate = (duplicate, existing_manifest)
                    break
            if richer_duplicate:
                result_target, result_manifest = richer_duplicate
                for duplicate in duplicates:
                    if duplicate != result_target:
                        duplicate.unlink(missing_ok=True)
            else:
                for duplicate in duplicates:
                    duplicate.unlink(missing_ok=True)
                os.replace(tmp, target)
            _enforce_pending_limits()
    finally:
        tmp.unlink(missing_ok=True)
    return result_target, result_manifest


def _pending_files() -> list[Path]:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(PENDING_DIR.glob("*-AB-*.zip"), key=lambda path: path.stat().st_mtime)


def _enforce_pending_limits() -> None:
    files = _pending_files()
    while len(files) > PENDING_FILE_LIMIT or sum(path.stat().st_size for path in files) > PENDING_TOTAL_LIMIT:
        files.pop(0).unlink(missing_ok=True)


def _upload(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    config = _config()
    url = str(config.get("receiver_url") or "")
    if not url:
        return {"submitted": False, "reason": "receiver_not_configured"}
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {"submitted": False, "reason": "invalid_receiver_url"}
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=8)
    request_path = (parsed.path.rstrip("/") if parsed.path else "") + "/api/support-reports"
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    try:
        connection.putrequest("POST", request_path)
        connection.putheader("Content-Type", "application/zip")
        connection.putheader("Content-Length", str(size))
        connection.putheader("Authorization", f"Bearer {config.get('receiver_token', '')}")
        connection.putheader("X-Support-Report-ID", str(manifest.get("report_id") or ""))
        connection.putheader("X-Support-Fingerprint", str(manifest.get("fingerprint") or ""))
        connection.putheader("X-Support-SHA256", digest.hexdigest())
        connection.endheaders()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                connection.send(chunk)
        response = connection.getresponse()
        raw = response.read(128 * 1024)
        data = json.loads(raw.decode("utf-8")) if raw else {}
        if response.status != 200 or not isinstance(data, dict) or not data.get("ok"):
            return {"submitted": False, "reason": f"receiver_http_{response.status}"}
        return {"submitted": True, **data}
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        return {"submitted": False, "reason": _redact(exc, 300)}
    finally:
        connection.close()


def _append_receipt(value: dict[str, Any]) -> None:
    SUPPORT_ROOT.mkdir(parents=True, exist_ok=True)
    with RECEIPTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_sanitize(value), ensure_ascii=False) + "\n")


def submit_support_report(context: dict[str, Any]) -> dict[str, Any]:
    path, manifest = _build_report(context if isinstance(context, dict) else {})
    result = _upload(path, manifest)
    if result.get("submitted"):
        path.unlink(missing_ok=True)
        report_id = str(result.get("report_id") or manifest["report_id"])
        _append_receipt({"time": _now(), "report_id": report_id, "fingerprint": manifest["fingerprint"], "duplicate": result.get("duplicate", False)})
        return {"ok": True, "status": "submitted", "report_id": report_id, "duplicate": bool(result.get("duplicate")), "message": "问题反馈已提交。"}
    return {"ok": True, "status": "queued", "report_id": manifest["report_id"], "message": "问题反馈已保存，将在接收端上线后自动提交。"}


def retry_pending_reports() -> dict[str, int]:
    attempted = 0
    submitted = 0
    for path in _pending_files():
        try:
            with zipfile.ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
        except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        attempted += 1
        result = _upload(path, manifest)
        if not result.get("submitted"):
            break
        path.unlink(missing_ok=True)
        submitted += 1
        _append_receipt({"time": _now(), "report_id": result.get("report_id") or manifest.get("report_id"), "fingerprint": manifest.get("fingerprint"), "duplicate": result.get("duplicate", False)})
    return {"attempted": attempted, "submitted": submitted, "pending": len(_pending_files())}


def support_status() -> dict[str, Any]:
    return {"configured": bool(_config().get("receiver_url")), "pending_count": len(_pending_files())}


def _retry_loop() -> None:
    delay = RETRY_SECONDS
    while not _STOP.wait(delay + random.uniform(0, min(10, delay / 4))):
        try:
            result = retry_pending_reports()
            delay = RETRY_SECONDS if result["submitted"] else min(15 * 60, delay * 2)
        except Exception:
            delay = min(15 * 60, delay * 2)


def start_support_retry_worker() -> None:
    global _WORKER
    if _WORKER and _WORKER.is_alive():
        return
    SUPPORT_ROOT.mkdir(parents=True, exist_ok=True)
    for path in SUPPORT_ROOT.rglob("*.part"):
        try:
            if time.time() - path.stat().st_mtime > 3600:
                path.unlink(missing_ok=True)
        except OSError:
            continue
    _STOP.clear()
    _WORKER = threading.Thread(target=_retry_loop, name="support-report-retry", daemon=True)
    _WORKER.start()


def stop_support_retry_worker() -> None:
    _STOP.set()
