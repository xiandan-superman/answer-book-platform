from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .model_diagnostics import delete_model_diagnostics
from .paths import DATA_ROOT
from .runtime_capacity import bounded_env_int, practice_job_max_concurrency
from .runtime_monitor import model_call_context, model_call_cost_summary

PRACTICE_JOB_DIR = DATA_ROOT / "practice_jobs"
_LOCK = threading.RLock()
_JOB_TIMEOUT_SECONDS = {
    "analyze": bounded_env_int("PRACTICE_ANALYZE_JOB_TIMEOUT_SECONDS", 1800, 600, 14400),
    "plan": bounded_env_int("PRACTICE_PLAN_JOB_TIMEOUT_SECONDS", 3600, 900, 21600),
    "generate_from_plan": bounded_env_int("PRACTICE_GENERATE_JOB_TIMEOUT_SECONDS", 14400, 1800, 43200),
    "generate_from_contract": bounded_env_int("PRACTICE_GENERATE_JOB_TIMEOUT_SECONDS", 14400, 1800, 43200),
}


_MAX_CONCURRENT_JOBS = practice_job_max_concurrency()
_COMPLETED_HISTORY_RETENTION_DAYS = bounded_env_int("PRACTICE_COMPLETED_JOB_RETENTION_DAYS", 1, 1, 30)
_TERMINAL_JOB_RETENTION_DAYS = bounded_env_int("PRACTICE_TERMINAL_JOB_RETENTION_DAYS", 30, 7, 365)


def _clean_task_title(value: Any) -> str:
    """Keep user-facing task names short, plain, and safe for durable records."""
    return " ".join(str(value or "").split()).strip()[:80]


def _default_task_title(payload: dict[str, Any]) -> str:
    """Prefer the user's material name over an internal workflow-stage label."""
    explicit = _clean_task_title(payload.get("task_title"))
    if explicit:
        return explicit
    knowledge_title = _clean_task_title(payload.get("knowledge_title"))
    if knowledge_title:
        return knowledge_title
    for item in payload.get("source_files") or []:
        if not isinstance(item, dict):
            continue
        filename = Path(str(item.get("name") or "")).name
        material_name = _clean_task_title(Path(filename).stem)
        if material_name:
            return material_name
    source_text = " ".join(str(payload.get("question_text") or "").split()).strip()
    if source_text:
        prefix = source_text[:28].rstrip("，。；：,.!?！？;: ")
        return f"{prefix}{'…' if len(source_text) > len(prefix) else ''}"
    return "未命名材料"


def _batch_task_title(batch_id: str) -> str:
    """Reuse a renamed title when a workflow creates its next background step."""
    if not batch_id:
        return ""
    for path in sorted(PRACTICE_JOB_DIR.glob("generation_*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(record.get("practice_batch_id") or "") != batch_id:
            continue
        title = _clean_task_title(record.get("title"))
        if title:
            return title
    return ""


def _friendly_progress(operation: str, elapsed: int) -> str:
    waited = f"{elapsed // 60} 分 {elapsed % 60} 秒" if elapsed >= 60 else f"{elapsed} 秒"
    if operation == "analyze":
        action = "正在梳理材料内容与考点范围"
    elif operation == "plan":
        action = "正在根据已确认范围设计训练蓝图"
    elif operation == "generate_from_contract" and elapsed < 30:
        action = "正在根据已确认范围准备生成题目"
    elif elapsed < 30:
        action = "正在整理蓝图并准备生成题目"
    elif elapsed < 120:
        action = "正在生成题目"
    else:
        action = "内容较长，模型仍在生成完整结果"
    return f"{action} · 已等待 {waited}。可离开当前页面，任务会继续。"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _path(job_id: str) -> Path:
    if not job_id.startswith("generation_") or "/" in job_id or ".." in job_id:
        raise ValueError("出题任务 ID 无效。")
    return PRACTICE_JOB_DIR / f"{job_id}.json"


def _write(record: dict[str, Any]) -> None:
    PRACTICE_JOB_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(str(record["job_id"]))
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def practice_job_fingerprint(operation: str, payload: dict[str, Any]) -> str:
    """Return a stable identity for active-job deduplication.

    A practice batch identifies one explicit user workflow.  It must remain in
    the identity so that submitting the same material as a *new* task does not
    silently attach the user to an older running task.  Request identifiers are
    still excluded so a repeated click inside the same batch is deduplicated.
    Uploaded data remains private: only its digest is stored as the fingerprint.
    """
    canonical_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"_job_id", "client_request_id", "task_title"}
    }
    encoded = json.dumps(
        {"operation": operation, "payload": canonical_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def find_active_practice_job(operation: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    fingerprint = practice_job_fingerprint(operation, payload)
    PRACTICE_JOB_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        for path in sorted(PRACTICE_JOB_DIR.glob("generation_*.json"), reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("status") not in {"queued", "running"}:
                continue
            # Recalculate from the durable payload instead of trusting a stored
            # legacy fingerprint. This keeps active jobs created before an
            # identity-rule upgrade compatible with the current batch policy.
            stored_payload = record.get("payload")
            stored = (
                practice_job_fingerprint(str(record.get("operation") or ""), stored_payload)
                if isinstance(stored_payload, dict)
                else str(record.get("request_fingerprint") or "")
            )
            if stored == fingerprint:
                return record
    return None


def create_practice_job(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation not in {"analyze", "plan", "generate_from_plan", "generate_from_contract"}:
        raise ValueError("不支持的出题任务类型。")
    payload = dict(payload)
    batch_id = str(payload.get("practice_batch_id") or "")
    payload["task_title"] = _batch_task_title(batch_id) or _default_task_title(payload)
    now = _now()
    job_id = f"generation_{datetime.now():%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
    source_mode = str(payload.get("source_mode") or "exam")
    record = {
        "job_id": job_id,
        "operation": operation,
        "task_kind": "knowledge" if source_mode == "knowledge" else "practice",
        "practice_batch_id": batch_id,
        "title": payload["task_title"],
        "status": "queued",
        "current_stage": "analyzing" if operation == "analyze" else ("planning" if operation == "plan" else "generating"),
        "created_at": now,
        "updated_at": now,
        "last_heartbeat_at": now,
        "last_progress_at": now,
        "health_status": "waiting",
        "current_operation": "正在排队",
        "completed_count": 0,
        "total_count": 0,
        "active_item": "",
        "active_since": now,
        "warning_reason": "",
        "suggested_action": "正在等待可用处理位置。",
        "progress_message": "任务已提交，等待后台处理。",
        "payload": payload,
        "result": None,
        "history_id": "",
        "error": "",
        "request_fingerprint": practice_job_fingerprint(operation, payload),
        "max_concurrent_jobs": _MAX_CONCURRENT_JOBS,
    }
    with _LOCK:
        _write(record)
    return record


def create_or_reuse_practice_job(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Reuse a repeated request in the same batch or create a new user task."""
    with _LOCK:
        existing = find_active_practice_job(operation, payload)
        if existing:
            return {**existing, "deduplicated": True}
        return {**create_practice_job(operation, payload), "deduplicated": False}


def load_practice_job(job_id: str, *, include_payload: bool = True) -> dict[str, Any]:
    with _LOCK:
        path = _path(job_id)
        if not path.exists():
            raise FileNotFoundError("出题任务不存在。")
        record = json.loads(path.read_text(encoding="utf-8"))
    if not include_payload:
        record.pop("payload", None)
        record.pop("result", None)
    return record


def cancel_practice_job(job_id: str, reason: str = "用户取消出题任务") -> dict[str, Any]:
    """Mark a durable generation job cancelled; late provider responses are ignored."""
    with _LOCK:
        record = load_practice_job(job_id)
        if record.get("status") not in {"queued", "running"}:
            return {"ok": False, "task_id": job_id, "message": "当前出题任务已经结束，不能取消。", "status": record.get("status")}
        updated = update_practice_job(
            job_id,
            status="cancelled",
            current_stage="cancelled",
            error=reason,
            progress_message="任务已取消，已完成的中间结果仍保留。",
            health_status="error",
            current_operation="任务已取消",
            warning_reason=reason,
            suggested_action="可从已保存的中间结果重新发起任务。",
            cancel_requested=True,
        )
    return {"ok": True, "task_id": job_id, "status": updated.get("status"), "message": reason}


def delete_practice_job(job_id: str) -> dict[str, Any]:
    with _LOCK:
        record = load_practice_job(job_id)
        if record.get("status") in {"queued", "running"}:
            return {"ok": False, "task_id": job_id, "message": "任务仍在进行，请先取消或等待完成后再删除。"}
        path = _path(job_id)
        removed_bytes = path.stat().st_size if path.exists() else 0
        path.unlink(missing_ok=True)
    delete_model_diagnostics(job_id)
    return {"ok": True, "task_id": job_id, "removed_bytes": removed_bytes}


def rename_practice_job(job_id: str, title: str) -> dict[str, Any]:
    """Rename every saved step in one practice batch so later steps keep the name."""
    clean_title = _clean_task_title(title)
    if not clean_title:
        raise ValueError("任务名称不能为空。")
    with _LOCK:
        target = load_practice_job(job_id)
        batch_id = str(target.get("practice_batch_id") or "")
        updated = 0
        for path in list(PRACTICE_JOB_DIR.glob("generation_*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            same_task = str(record.get("job_id") or "") == job_id
            same_batch = bool(batch_id) and str(record.get("practice_batch_id") or "") == batch_id
            if not same_task and not same_batch:
                continue
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            record["title"] = clean_title
            record["payload"] = {**payload, "task_title": clean_title}
            _write(record)
            updated += 1
    return {"ok": True, "task_id": job_id, "practice_batch_id": batch_id, "title": clean_title, "updated_jobs": updated}


def delete_jobs_for_history(history_id: str) -> dict[str, Any]:
    removed = 0
    removed_bytes = 0
    for path in list(PRACTICE_JOB_DIR.glob("generation_*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(record.get("history_id") or "") != str(history_id):
            continue
        removed_bytes += path.stat().st_size
        path.unlink(missing_ok=True)
        delete_model_diagnostics(str(record.get("job_id") or path.stem))
        removed += 1
    return {"removed_job_records": removed, "removed_bytes": removed_bytes}


def _record_time(record: dict[str, Any]) -> datetime | None:
    raw = str(record.get("updated_at") or record.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def cleanup_practice_jobs(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Remove expired transient jobs without touching canonical history data."""
    current = now or datetime.now().astimezone()
    candidates: list[dict[str, Any]] = []
    removed_bytes = 0
    PRACTICE_JOB_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        for path in sorted(PRACTICE_JOB_DIR.glob("generation_*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            status = str(record.get("status") or "")
            if status in {"queued", "running"}:
                continue
            timestamp = _record_time(record)
            if timestamp is None:
                continue
            has_history = bool(str(record.get("history_id") or ""))
            retention_days = (
                _COMPLETED_HISTORY_RETENTION_DAYS
                if status == "completed" and has_history
                else _TERMINAL_JOB_RETENTION_DAYS
            )
            if current - timestamp < timedelta(days=retention_days):
                continue
            size = path.stat().st_size
            candidates.append({
                "job_id": str(record.get("job_id") or path.stem),
                "status": status,
                "updated_at": timestamp.isoformat(timespec="seconds"),
                "retention_days": retention_days,
                "bytes": size,
            })
            removed_bytes += size
            if not dry_run:
                path.unlink(missing_ok=True)
    if not dry_run:
        for candidate in candidates:
            delete_model_diagnostics(str(candidate.get("job_id") or ""))
    return {
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "removed_count": 0 if dry_run else len(candidates),
        "removed_bytes": 0 if dry_run else removed_bytes,
        "candidate_bytes": removed_bytes,
        "candidates": candidates,
    }


def update_practice_job(
    job_id: str,
    *,
    expected_status: str | None = None,
    **updates: Any,
) -> dict[str, Any]:
    """Atomically update a durable job without reviving stale worker state.

    Background heartbeats and provider callbacks may arrive after cancellation,
    timeout, or completion.  Callers that perform a state transition can use
    ``expected_status`` as a compare-and-set guard.  Routine progress updates
    never mutate an already terminal record.
    """
    with _LOCK:
        record = load_practice_job(job_id)
        current_status = str(record.get("status") or "")
        if expected_status is not None and current_status != expected_status:
            return record
        # Terminal states are immutable. Retries create a new durable job and
        # link back to the old checkpoint; no heartbeat, queue recovery, or
        # late provider callback may revive an ended task in place.
        if current_status in {"completed", "failed", "cancelled"}:
            return record
        previous_generated = int(record.get("generated_count") or 0)
        record.update(updates)
        now = _now()
        record["updated_at"] = now
        if "generated_count" in updates:
            record["completed_count"] = max(0, int(record.get("generated_count") or 0))
        if "total_count" in updates:
            record["total_count"] = max(0, int(record.get("total_count") or 0))
        if int(record.get("generated_count") or 0) > previous_generated:
            record["last_progress_at"] = now
            record["health_status"] = "normal"
            record["warning_reason"] = ""
            record["suggested_action"] = ""
        _write(record)
    return record


def list_practice_jobs(limit: int = 100) -> list[dict[str, Any]]:
    PRACTICE_JOB_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for path in sorted(PRACTICE_JOB_DIR.glob("generation_*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # A completed generation is represented by its normal history record.
        if record.get("status") == "completed" and record.get("history_id"):
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if _clean_task_title(record.get("title")) in {"按题出题", "知识点出题", "范围解析", "蓝图设计", "题目生成"}:
            record["title"] = _default_task_title(payload)
        record.pop("payload", None)
        record.pop("result", None)
        rows.append(record)
        if len(rows) >= max(1, min(limit, 100)):
            break
    return rows


def run_practice_job(job_id: str, worker: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
    # The durable Huey queue owns task-level concurrency. Claiming is atomic so
    # a duplicate persisted queue message cannot execute or bill the same job
    # twice. Model calls retain their separate per-provider concurrency ceiling.
    with _LOCK:
        current = load_practice_job(job_id)
        if current.get("status") != "queued":
            return
        record = update_practice_job(
            job_id,
            status="running",
            error="",
            last_heartbeat_at=_now(),
            last_progress_at=_now(),
            health_status="normal",
            current_operation="正在开始后台处理",
            warning_reason="",
            suggested_action="",
            elapsed_seconds=0,
            started_at=_now(),
            completed_at="",
            progress_message=_friendly_progress(str(current.get("operation") or ""), 0),
        )
    operation = str(record.get("operation") or "")
    timeout_seconds = _JOB_TIMEOUT_SECONDS.get(operation, 600)
    finished = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        while not finished.wait(10):
            elapsed = int(time.monotonic() - started)
            current = load_practice_job(job_id)
            if current.get("status") != "running":
                return
            if elapsed >= timeout_seconds:
                update_practice_job(
                    job_id,
                    expected_status="running",
                    last_heartbeat_at=_now(),
                    status="failed",
                    current_stage="failed",
                    error=f"后台任务超过 {timeout_seconds} 秒未完成，已停止等待，可直接重试。",
                    progress_message="任务超时。",
                    health_status="error",
                    warning_reason="后台任务已超过允许等待时间。",
                    suggested_action="可重新运行任务。",
                    completed_at=_now(),
                    elapsed_seconds=elapsed,
                )
                return
            update_practice_job(
                job_id,
                expected_status="running",
                last_heartbeat_at=_now(),
                health_status="waiting",
                current_operation="正在等待模型返回",
                warning_reason="",
                suggested_action="正在等待模型或耗时处理完成。",
                elapsed_seconds=elapsed,
                progress_message=(
                    f"已完成 {int(current.get('generated_count') or 0)}/{int(current.get('total_count') or 0)} 道题，正在继续生成并检查后续内容。可离开当前页面，任务会继续。"
                    if operation in {"generate_from_plan", "generate_from_contract"} and int(current.get("generated_count") or 0) > 0
                    else _friendly_progress(operation, elapsed)
                ),
            )

    threading.Thread(target=heartbeat, name=f"heartbeat-{job_id}", daemon=True).start()
    try:
        with model_call_context(task_id=job_id, stage=str(record.get("current_stage") or ""), operation="出题任务"):
            outcome = worker(str(record["operation"]), {**(record.get("payload") or {}), "_job_id": job_id})
            # A timed-out or cancelled task may return from a provider socket
            # later. Never let that stale response overwrite terminal state.
        if load_practice_job(job_id).get("status") == "running":
            update_practice_job(
                job_id,
                expected_status="running",
                status="completed",
                current_stage="completed",
                result=outcome.get("result"),
                model_usage=model_call_cost_summary(job_id),
                history_id=str(outcome.get("history_id") or ""),
                progress_message="任务已完成。",
                last_heartbeat_at=_now(),
                last_progress_at=_now(),
                health_status="normal",
                current_operation="任务已完成",
                warning_reason="",
                suggested_action="",
                completed_at=_now(),
                elapsed_seconds=max(0, int(time.monotonic() - started)),
            )
    except BaseException as exc:
        if load_practice_job(job_id).get("status") == "running":
            update_practice_job(
                job_id,
                expected_status="running",
                status="failed",
                current_stage="failed",
                error=str(exc) or exc.__class__.__name__,
                model_usage=model_call_cost_summary(job_id),
                progress_message="后台任务异常结束。",
                last_heartbeat_at=_now(),
                health_status="error",
                warning_reason=str(exc) or exc.__class__.__name__,
                suggested_action="可重新运行任务。",
                completed_at=_now(),
                elapsed_seconds=max(0, int(time.monotonic() - started)),
            )
    finally:
        finished.set()


def recover_practice_jobs(*, fail_interrupted: bool = True) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    PRACTICE_JOB_DIR.mkdir(parents=True, exist_ok=True)
    # Recovery must inspect every durable record.  The task-manager list is
    # intentionally paginated, so using it here would strand older active jobs
    # once more than 100 records existed.
    for path in sorted(PRACTICE_JOB_DIR.glob("generation_*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("status") not in {"queued", "running"}:
            continue
        job_id = str(record.get("job_id") or "")
        if not job_id:
            continue
        recovered.append(record)
        if fail_interrupted:
            update_practice_job(
                job_id,
                expected_status=str(record.get("status") or ""),
                status="failed",
                current_stage="failed",
                error="服务重启导致任务中断，请重新发起。",
                health_status="error",
                current_operation="任务已中断",
                warning_reason="服务重启导致任务中断。",
                suggested_action="可重新运行任务。",
            )
    return recovered
