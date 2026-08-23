from __future__ import annotations

import json
import os
import platform
import shutil
import threading
import time
import traceback
from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .capabilities.quality_budget import QualityExecutionBudget
from .concurrency import model_request_context, model_request_snapshot
from .paths import DATA_ROOT, LOGS_DIR, PROJECT_ROOT, TASKS_DIR, ensure_project_dirs
from .redaction import redact_credentials
from .resource_ids import bounded_resource_path
from .task_store import list_tasks, load_task
from .version import get_version

RUNTIME_LOG = LOGS_DIR / "runtime_server.jsonl"
MODEL_CALL_LEDGER = LOGS_DIR / "model_calls.jsonl"
ERROR_TRACE_LOG = LOGS_DIR / "error_traces.jsonl"
MAX_TEXT_LENGTH = 1200
SERVICE_STARTED_AT = datetime.now().astimezone()
HEARTBEAT_ERROR_SECONDS = max(30, int(os.environ.get("RUNTIME_HEARTBEAT_ERROR_SECONDS", "90")))
MODEL_WAIT_SECONDS = max(30, int(os.environ.get("RUNTIME_MODEL_WAIT_SECONDS", "150")))
PROGRESS_WARNING_SECONDS = max(MODEL_WAIT_SECONDS, int(os.environ.get("RUNTIME_PROGRESS_WARNING_SECONDS", "240")))
_MODEL_CALL_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar("model_call_context", default=None)
_MODEL_LOCK = threading.RLock()
_MODEL_ACTIVE: dict[str, dict[str, Any]] = {}
_MODEL_HISTORY: deque[dict[str, Any]] = deque(maxlen=240)
_MODEL_SEQUENCE = 0
_RUN_MODEL_BUDGETS: dict[tuple[str, str], dict[str, Any]] = {}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str:
    text = redact_credentials(value)
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
            if any(token in key_text.lower() for token in ("key", "token", "secret", "password", "prompt", "content", "response", "material")):
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
        with RUNTIME_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def append_exception_log(exc: BaseException, *, path: str = "", support_id: str = "", request_id: str = "") -> None:
    """Persist a redacted traceback locally so one-click reports can explain backend failures."""

    try:
        ensure_project_dirs()
        row = {
            "time": _now(),
            "path": _safe_text(path, 240),
            "support_id": _safe_text(support_id, 80),
            "request_id": _safe_text(request_id, 80),
            "error_type": exc.__class__.__name__,
            "error": _safe_text(exc, 1200),
            "traceback": _safe_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), 20000),
        }
        with ERROR_TRACE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
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


def model_call_cost_summary(task_id: str) -> dict[str, Any]:
    rows = [row for row in _read_jsonl_tail(MODEL_CALL_LEDGER, 100000) if str(row.get("task_id") or "") == str(task_id)]
    prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in rows if isinstance(row.get("prompt_tokens"), int))
    completion_tokens = sum(int(row.get("completion_tokens") or 0) for row in rows if isinstance(row.get("completion_tokens"), int))
    known_waste_tokens = sum(
        int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
        for row in rows
        if row.get("billable_disposition") == "failed_attempt"
    )
    provider_costs = [float(row["provider_cost"]) for row in rows if isinstance(row.get("provider_cost"), (int, float))]
    return {
        "call_count": len(rows),
        "success_count": sum(row.get("outcome") == "succeeded" for row in rows),
        "failed_count": sum(row.get("outcome") != "succeeded" for row in rows),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "known_waste_tokens": known_waste_tokens,
        "unclassified_success_tokens": prompt_tokens + completion_tokens - known_waste_tokens,
        "usage_missing_count": sum(row.get("prompt_tokens") is None and row.get("completion_tokens") is None for row in rows),
        "provider_reported_cost": sum(provider_costs) if provider_costs else None,
        "elapsed_ms": sum(int(row.get("elapsed_ms") or 0) for row in rows),
        "ledger": str(MODEL_CALL_LEDGER),
    }


@contextmanager
def model_call_context(
    *,
    task_id: str = "",
    run_id: str = "",
    stage: str = "",
    operation: str = "",
    active_item: str = "",
) -> Iterator[None]:
    current = dict(_MODEL_CALL_CONTEXT.get() or {})
    current.update(
        {
            key: value
            for key, value in {
                "task_id": task_id,
                "run_id": run_id,
                "stage": stage,
                "operation": operation,
                "active_item": active_item,
            }.items()
            if value
        }
    )
    token = _MODEL_CALL_CONTEXT.set(current)

    def ensure_task_is_active() -> None:
        current_task_id = str(current.get("task_id") or "")
        if not current_task_id:
            return
        from .task_control import TaskCancelled

        if current_task_id.startswith("generation_"):
            from .practice_jobs import load_practice_job

            try:
                if str(load_practice_job(current_task_id).get("status") or "") == "cancelled":
                    raise TaskCancelled("用户取消任务")
            except FileNotFoundError:
                pass
            return
        from .task_control import read_task_control

        if str(read_task_control(current_task_id).get("action") or "") == "cancel":
            raise TaskCancelled("用户取消任务")
        try:
            if load_task(current_task_id).status == "cancelled":
                raise TaskCancelled("用户取消任务")
        except FileNotFoundError:
            return

    try:
        with model_request_context(
            str(current.get("task_id") or ""),
            admission_check=ensure_task_is_active,
        ):
            yield
    finally:
        _MODEL_CALL_CONTEXT.reset(token)


def _model_error_kind(error: BaseException) -> str:
    text = str(error).lower()
    if "429" in text or "rate limit" in text or "限流" in text:
        return "rate_limited"
    if "timeout" in text or "timed out" in text or "超时" in text:
        return "timeout"
    return "failed"


def record_model_call_usage(record: dict[str, Any] | None, raw: dict[str, Any] | None) -> None:
    """Attach provider-reported usage without persisting prompts or responses."""

    if not isinstance(record, dict) or not isinstance(raw, dict):
        return
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    completion_details = usage.get("completion_tokens_details")
    output_details = usage.get("output_tokens_details")
    details = completion_details if isinstance(completion_details, dict) else output_details if isinstance(output_details, dict) else {}
    values = {
        "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "provider_cost": usage.get("cost", usage.get("total_cost")),
        "currency": usage.get("currency"),
        "response_id": raw.get("id"),
    }
    for key, value in values.items():
        if isinstance(value, (int, float, str)) and str(value).strip():
            record[key] = value


def _append_model_ledger(record: dict[str, Any]) -> None:
    try:
        ensure_project_dirs()
        # Fixed metadata only: prompts, responses and credentials never enter
        # the ledger.
        with MODEL_CALL_LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


@contextmanager
def track_model_call(*, provider: str, model: str, purpose: str, timeout: int) -> Iterator[dict[str, Any]]:
    global _MODEL_SEQUENCE
    started = time.monotonic()
    context = dict(_MODEL_CALL_CONTEXT.get() or {})
    if context.get("task_id") and (not context.get("stage") or not context.get("active_item")):
        try:
            context_task_id = str(context["task_id"])
            if context_task_id.startswith("generation_"):
                from .practice_jobs import load_practice_job

                task = load_practice_job(context_task_id)
                context.setdefault("stage", str(task.get("current_stage") or ""))
                context.setdefault("active_item", str(task.get("active_item") or ""))
            else:
                task = load_task(context_task_id)
                context.setdefault("stage", task.current_stage)
                context.setdefault("active_item", task.active_item)
        except Exception:
            pass
    budget_key = (str(context.get("task_id") or ""), str(context.get("run_id") or ""))
    budget: QualityExecutionBudget | None = None
    with _MODEL_LOCK:
        if all(budget_key):
            budget = QualityExecutionBudget.from_environment()
            state = _RUN_MODEL_BUDGETS.setdefault(
                budget_key,
                {
                    "started_monotonic": time.monotonic(),
                    "call_count": 0,
                    "token_count": 0,
                    "provider_failures": {},
                },
            )
            elapsed = max(0.0, time.monotonic() - float(state["started_monotonic"]))
            provider_failures = int((state.get("provider_failures") or {}).get(provider, 0) or 0)
            exhausted_reason = ""
            if int(state["call_count"]) >= budget.max_model_calls_per_run:
                exhausted_reason = f"model call budget exhausted ({budget.max_model_calls_per_run})"
            elif int(state["token_count"]) >= budget.max_model_tokens_per_run:
                exhausted_reason = f"model token budget exhausted ({budget.max_model_tokens_per_run})"
            elif elapsed >= budget.max_model_wall_seconds_per_run:
                exhausted_reason = f"model wall-clock budget exhausted ({budget.max_model_wall_seconds_per_run}s)"
            elif provider_failures >= budget.provider_failure_circuit_breaker:
                exhausted_reason = (
                    f"provider circuit breaker open for {provider} after {provider_failures} consecutive failures"
                )
            if exhausted_reason:
                append_runtime_log(
                    "model_budget",
                    exhausted_reason,
                    level="warning",
                    payload={"task_id": budget_key[0], "run_id": budget_key[1], "provider": provider},
                )
                raise RuntimeError(exhausted_reason)
            state["call_count"] = int(state["call_count"]) + 1
        _MODEL_SEQUENCE += 1
        call_id = str(_MODEL_SEQUENCE)
        record = {
            "call_id": call_id,
            "provider": _safe_text(provider, 120),
            "model": _safe_text(model, 160),
            "purpose": _safe_text(purpose, 80),
            "task_id": _safe_text(context.get("task_id"), 120),
            "run_id": _safe_text(context.get("run_id"), 120),
            "stage": _safe_text(context.get("stage"), 80),
            "operation": _safe_text(context.get("operation"), 120),
            "active_item": _safe_text(context.get("active_item"), 120),
            "started_at": _now(),
            "timeout_seconds": max(1, int(timeout or 1)),
        }
        _MODEL_ACTIVE[call_id] = record
    outcome = "succeeded"
    error_text = ""
    try:
        yield record
    except BaseException as exc:
        outcome = _model_error_kind(exc)
        error_text = _safe_text(exc, 300)
        raise
    finally:
        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        with _MODEL_LOCK:
            record = _MODEL_ACTIVE.pop(call_id, record)
            final_record = {
                **record,
                "finished_at": _now(),
                "elapsed_ms": elapsed_ms,
                "outcome": outcome,
                "billable_disposition": "unclassified_success" if outcome == "succeeded" else "failed_attempt",
                "error": error_text,
            }
            _MODEL_HISTORY.append(final_record)
            _append_model_ledger(final_record)
            if budget is not None and all(budget_key):
                state = _RUN_MODEL_BUDGETS.get(budget_key)
                if state is not None:
                    token_value = record.get("total_tokens")
                    if not isinstance(token_value, (int, float)):
                        token_value = int(record.get("prompt_tokens") or 0) + int(record.get("completion_tokens") or 0)
                    state["token_count"] = int(state.get("token_count") or 0) + int(token_value or 0)
                    failures = dict(state.get("provider_failures") or {})
                    failures[provider] = 0 if outcome == "succeeded" else int(failures.get(provider, 0) or 0) + 1
                    state["provider_failures"] = failures
                if len(_RUN_MODEL_BUDGETS) > 200:
                    oldest_key = min(
                        _RUN_MODEL_BUDGETS,
                        key=lambda key: float(_RUN_MODEL_BUDGETS[key].get("started_monotonic") or 0),
                    )
                    _RUN_MODEL_BUDGETS.pop(oldest_key, None)


def model_call_summary() -> dict[str, Any]:
    try:
        slot_snapshot = model_request_snapshot()
    except Exception as exc:
        slot_snapshot = {"active": 0, "waiting": 0, "limit": 0, "error": _safe_text(exc, 180)}
    with _MODEL_LOCK:
        active = [dict(value) for value in _MODEL_ACTIVE.values()]
        history = [dict(value) for value in _MODEL_HISTORY]
    counts = Counter(str(row.get("outcome") or "unknown") for row in history)
    recent = history[-30:]
    recent_failures = [row for row in recent if row.get("outcome") in {"failed", "timeout", "rate_limited"}]
    durations = [int(row.get("elapsed_ms") or 0) for row in recent if row.get("outcome") == "succeeded"]
    average_ms = round(sum(durations) / len(durations)) if durations else 0
    if not history:
        status, label = "unknown", "暂无调用记录"
    elif len(recent_failures) >= 2 or (recent and len(recent_failures) / len(recent) >= 0.35):
        status, label = "warning", "近期调用不稳定"
    elif average_ms >= 30000:
        status, label = "waiting", "近期响应较慢"
    else:
        status, label = "normal", "最近调用正常"
    return {
        "health_status": status,
        "label": label,
        "active_count": len(active),
        "waiting_count": int(slot_snapshot.get("waiting") or 0),
        "waiting_task_count": len(slot_snapshot.get("waiting_task_ids") or []),
        "waiting_task_ids": list(slot_snapshot.get("waiting_task_ids") or []),
        "concurrency_limit": int(slot_snapshot.get("limit") or 0),
        "recent_success_count": counts["succeeded"],
        "recent_failure_count": counts["failed"],
        "recent_timeout_count": counts["timeout"],
        "recent_rate_limited_count": counts["rate_limited"],
        "recent_retry_count": max(0, counts["succeeded"] + len(recent_failures) - len({str(row.get("task_id") or row.get("started_at")) for row in recent})),
        "average_duration_ms": average_ms,
        "active": active[:12],
        "recent": recent[-12:],
    }


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def _seconds_since(value: object, now: datetime) -> int | None:
    parsed = _parse_time(value)
    return max(0, int((now - parsed).total_seconds())) if parsed else None


def _operation_label(stage: object) -> str:
    labels = {
        "environment": "正在检查运行环境",
        "extract_exam": "正在读取原题",
        "exam_structure_review": "等待用户确认题目结构",
        "question_understanding": "正在理解题面",
        "knowledge_planning": "正在规划知识点",
        "evidence_selection": "正在选择依据材料",
        "answer_generation": "正在生成答案",
        "figure_schema_planning": "正在设计图件",
        "figures": "正在生成和检查图件",
        "content_quality": "正在进行质量检查",
        "docx": "正在生成 Word",
        "render": "正在渲染文档",
        "final_acceptance": "正在整理验收结果",
        "analyzing": "正在解析范围",
        "planning": "正在设计蓝图",
        "generating": "正在生成练习",
    }
    return labels.get(str(stage or ""), "正在处理")


def _progress_snapshot(task_id: str, current_stage: str) -> dict[str, Any]:
    task_root = bounded_resource_path(TASKS_DIR, task_id)
    # ``stage_outputs`` is the current durable pipeline location. Keep the old
    # ``stages`` location readable so tasks created by earlier versions retain
    # accurate progress after an upgrade.
    roots = (task_root / "stage_outputs", task_root / "stages")
    names = [f"{current_stage}_progress.json"] if current_stage else []
    if current_stage == "completed":
        # Compatibility recovery for successful tasks written before terminal
        # counts were finalized in task.json. Answer generation is the durable
        # one-row-per-question checkpoint for the task-level counter.
        names.append("answer_generation_progress.json")
    latest: dict[str, Any] = {}
    latest_mtime = 0.0
    for root in roots:
        for name in names:
            path = root / name
            if not name or not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    continue
                mtime = path.stat().st_mtime
            except Exception:
                continue
            if mtime >= latest_mtime:
                latest, latest_mtime = value, mtime
    if latest_mtime:
        latest["_updated_at"] = datetime.fromtimestamp(latest_mtime).astimezone().isoformat(timespec="seconds")
    return latest


def _task_health(
    row: dict[str, Any],
    now: datetime,
    *,
    kind: str,
    model_waiting_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    status = str(row.get("status") or "")
    stage = str(row.get("current_stage") or "")
    task_id = str(row.get("task_id") or row.get("job_id") or "")
    success_terminal = status in {"completed", "completed_with_issues"} or stage == "completed"
    progress = _progress_snapshot(task_id, stage) if kind == "exam" and task_id else {}
    completed = int(progress.get("completed") or progress.get("generated_count") or row.get("completed_count") or row.get("generated_count") or 0)
    total = int(progress.get("total") or progress.get("question_count") or row.get("total_count") or 0)
    if total > 0:
        completed = min(completed, total)
    active = progress.get("active") if isinstance(progress.get("active"), dict) else {}
    active_item = "" if success_terminal else str(
        active.get("question_id")
        or active.get("figure_id")
        or active.get("label")
        or row.get("active_item")
        or ""
    )
    stored_operation = str(row.get("current_operation") or "")
    operation = _operation_label(stage) if not stored_operation or stored_operation == stage else stored_operation
    heartbeat_at = row.get("last_heartbeat_at") or row.get("updated_at") or row.get("created_at")
    progress_at = (
        row.get("last_progress_at") or row.get("updated_at") or row.get("created_at")
        if success_terminal
        else progress.get("_updated_at") or row.get("last_progress_at") or row.get("updated_at") or row.get("created_at")
    )
    heartbeat_age = _seconds_since(heartbeat_at, now)
    progress_age = _seconds_since(progress_at, now)
    health_status = "unknown"
    warning_reason = ""
    suggested_action = ""
    if status in {"failed", "cancelled"} or stage in {"failed", "interrupted", "cancelled"}:
        health_status = "error"
        warning_reason = str(row.get("error") or "任务已中断")
        suggested_action = "可重新运行任务。" if status == "failed" or stage == "interrupted" else "任务已取消，可按需重新运行。"
    elif success_terminal:
        health_status = "normal"
        operation = "任务已完成"
    elif status in {"paused", "needs_input"} or stage in {"exam_structure_review", "review_decision"}:
        health_status = "waiting"
        operation = "等待用户确认"
        suggested_action = "确认后任务会继续执行。"
    elif status in {"created", "queued"}:
        health_status = "waiting"
        operation = "正在排队"
        suggested_action = "正在等待可用处理位置。"
    elif status == "running" and task_id in (model_waiting_task_ids or set()):
        health_status = "waiting"
        operation = "正在等待模型处理位置"
        suggested_action = "任务已进入公平队列，有可用模型位置后会自动继续。"
    elif status == "running":
        if heartbeat_age is None:
            health_status = "unknown"
        elif heartbeat_age > HEARTBEAT_ERROR_SECONDS:
            health_status = "error"
            warning_reason = "后台心跳长时间未更新，任务可能已中断。"
            suggested_action = "查看任务详情或重新运行。"
        elif progress_age is not None and progress_age > PROGRESS_WARNING_SECONDS:
            health_status = "warning"
            warning_reason = "后台仍在运行，但较长时间没有新的业务进展。"
            suggested_action = "建议继续等待，或查看详情后取消并重新运行。"
        elif progress_age is not None and progress_age > 10:
            health_status = "waiting"
            suggested_action = "正在等待模型或耗时处理完成。"
        else:
            health_status = "normal"
    return {
        "task_id": task_id,
        "title": str(row.get("title") or row.get("exam_path") or row.get("display_title") or task_id),
        "task_kind": kind,
        "health_status": health_status,
        "current_operation": operation,
        "completed_count": completed,
        "total_count": total,
        "last_heartbeat_at": heartbeat_at or "",
        "last_progress_at": progress_at or "",
        "heartbeat_age_seconds": heartbeat_age,
        "progress_age_seconds": progress_age,
        "active_item": active_item,
        "active_since": row.get("active_since") or "",
        "warning_reason": warning_reason or str(row.get("warning_reason") or ""),
        "suggested_action": suggested_action or str(row.get("suggested_action") or ""),
        "status": status,
        "current_stage": stage,
    }


def task_health_summary(row: dict[str, Any], *, kind: str = "exam") -> dict[str, Any]:
    try:
        waiting = set(model_request_snapshot().get("waiting_task_ids") or [])
    except Exception:
        waiting = set()
    return _task_health(row, datetime.now().astimezone(), kind=kind, model_waiting_task_ids=waiting)


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
    event_files = sorted(TASKS_DIR.glob("*/events.jsonl"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)[:40]
    rows: list[dict[str, Any]] = []
    for path in event_files:
        for row in _read_jsonl_tail(path, 20):
            rows.append({"task_id": path.parent.name, "time": row.get("time") or "", "event": row.get("event") or "", "payload": _safe_payload(row.get("payload") or {})})
    rows.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
    return rows[:limit]


def _directory_health(path: Path, name: str) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".health_{os.getpid()}_{threading.get_ident()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"name": name, "path": str(path), "writable": True, "error": ""}
    except Exception as exc:
        return {"name": name, "path": str(path), "writable": False, "error": _safe_text(exc, 240)}


def _service_health() -> dict[str, Any]:
    checks = [_directory_health(DATA_ROOT, "数据目录"), _directory_health(TASKS_DIR, "任务目录")]
    try:
        disk = shutil.disk_usage(DATA_ROOT)
        disk_info = {"free_bytes": disk.free, "total_bytes": disk.total, "free_gb": round(disk.free / 1024 ** 3, 2)}
    except Exception as exc:
        disk_info = {"free_bytes": 0, "total_bytes": 0, "free_gb": 0, "error": _safe_text(exc, 240)}
    errors = [check["error"] for check in checks if check.get("error")]
    if disk_info.get("error"):
        errors.append(str(disk_info["error"]))
    return {
        "started_at": SERVICE_STARTED_AT.isoformat(timespec="seconds"),
        "uptime_seconds": max(0, int((datetime.now().astimezone() - SERVICE_STARTED_AT).total_seconds())),
        "pid": os.getpid(),
        "directories": checks,
        "disk": disk_info,
        "errors": errors,
    }


def _is_current_failure(item: dict[str, Any]) -> bool:
    if item.get("health_status") != "error":
        return False
    updated = _parse_time(item.get("last_heartbeat_at") or item.get("last_progress_at"))
    return bool(updated and updated >= SERVICE_STARTED_AT)


def build_system_status(access_host: str | None = None) -> dict[str, Any]:
    ensure_project_dirs()
    failures: list[str] = []
    try:
        exam_rows = list_tasks()
    except Exception as exc:
        exam_rows = []
        failures.append(f"读取解析任务失败：{_safe_text(exc, 180)}")
    try:
        from .practice_jobs import list_practice_jobs

        practice_rows = list_practice_jobs(limit=100)
    except Exception as exc:
        practice_rows = []
        failures.append(f"读取出题任务失败：{_safe_text(exc, 180)}")
    model = model_call_summary()
    model_waiting_task_ids = set(model.get("waiting_task_ids") or [])
    now = datetime.now().astimezone()
    health_tasks = [_task_health(row, now, kind="exam", model_waiting_task_ids=model_waiting_task_ids) for row in exam_rows]
    health_tasks.extend(_task_health(row, now, kind="practice", model_waiting_task_ids=model_waiting_task_ids) for row in practice_rows)
    live_tasks = [item for item in health_tasks if item.get("status") in {"running", "queued", "paused"}]
    current_failures = [item for item in health_tasks if _is_current_failure(item)]
    monitored_tasks = live_tasks + current_failures
    health_counts = Counter(str(item.get("health_status") or "unknown") for item in monitored_tasks)
    service = _service_health()
    failures.extend(service.get("errors") or [])
    if health_counts["error"] or failures:
        health_status, headline = "error", "系统存在需要处理的问题"
    elif health_counts["warning"]:
        health_status, headline = "warning", "有任务等待时间较长"
    elif model.get("health_status") == "warning":
        health_status, headline = "warning", "部分模型调用不稳定"
    else:
        health_status, headline = "normal", "系统运行正常"
    counts = _task_counts(exam_rows + practice_rows)
    counts.update({key: health_counts[key] for key in ("normal", "waiting", "warning", "error", "unknown")})
    counts["active"] = counts["running"] + counts["queued"] + counts["paused"]
    return {
        "ok": health_status != "error",
        "version": get_version(),
        "time": _now(),
        "health": {"status": health_status, "headline": headline, "errors": failures, "thresholds": {"heartbeat_error_seconds": HEARTBEAT_ERROR_SECONDS, "model_wait_seconds": MODEL_WAIT_SECONDS, "progress_warning_seconds": PROGRESS_WARNING_SECONDS}},
        "host": {"name": platform.node(), "system": platform.system(), "platform": platform.platform(), "python": platform.python_version(), "pid": os.getpid(), "project_root": str(PROJECT_ROOT), "access_host": access_host or ""},
        "service": service,
        "tasks": {"counts": counts, "running": live_tasks[:20], "recent": health_tasks[:20]},
        "models": model,
        "runtime_logs": read_runtime_logs(80),
        "task_events": _recent_task_events(80),
    }
