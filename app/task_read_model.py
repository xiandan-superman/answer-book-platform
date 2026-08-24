from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .runtime_monitor import task_health_summary
from .task_contracts import (
    QualityStatus,
    enrich_contract,
    exam_run_status,
    practice_run_status,
    present_error,
    public_support_id,
    quality_from_practice,
    quality_from_summary,
    workflow_for_kind,
)
from .task_titles import build_display_task_title, friendly_material_title, title_matches_material_name


def _time_key(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return 0


_INTERNAL_MATERIAL_NAMES = {
    "selected_textbooks",
    "textbooks",
    "教材库",
}


def _display_basename(value: object, *, fallback: str = "") -> str:
    """Return a public label, never a local path or an internal storage key."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return fallback
    name = re.split(r"[\\/]", text.rstrip("\\/"))[-1].strip()
    if not name or name.lower() in _INTERNAL_MATERIAL_NAMES:
        return fallback
    return name[:120]


def _textbook_display_names(row: dict[str, Any]) -> list[str]:
    selected = row.get("selected_textbooks") if isinstance(row.get("selected_textbooks"), list) else []
    supplied = row.get("textbook_display_names") if isinstance(row.get("textbook_display_names"), dict) else {}
    by_basename = {
        _display_basename(path): _display_basename(label)
        for path, label in supplied.items()
        if _display_basename(path) and _display_basename(label)
    }
    names: list[str] = []
    for path in selected:
        path_text = str(path or "")
        name = _display_basename(supplied.get(path_text)) or by_basename.get(_display_basename(path_text), "") or _display_basename(path_text)
        if name and name not in names:
            names.append(name)
    if not names:
        for label in supplied.values():
            name = _display_basename(label)
            if name and name not in names:
                names.append(name)
    return names


def _practice_material_metadata(record: dict[str, Any], task_kind: str) -> tuple[str, list[str]]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    metadata = payload or request
    title = _display_basename(record.get("title") or metadata.get("task_title"))
    if not title:
        title = _display_basename(metadata.get("knowledge_title"))
    material_names: list[str] = []
    for item in metadata.get("source_files") or []:
        if not isinstance(item, dict):
            continue
        name = _display_basename(item.get("name"))
        if name and name not in material_names:
            material_names.append(name)
    for item in metadata.get("source_file_names") or []:
        name = _display_basename(item)
        if name and name not in material_names:
            material_names.append(name)
    # Live jobs retain source names, which lets us preserve an explicitly
    # renamed title. Compact history rows omit those names, so their stored
    # automatic title is cleaned directly for display. Saved data is untouched.
    if title and (
        not material_names
        or any(title_matches_material_name(title, name) for name in material_names)
    ):
        title = friendly_material_title(title)
    if not title and material_names:
        title = material_names[0]
    return title or ("未命名知识点" if task_kind == "knowledge" else "未命名原题"), material_names


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def practice_network_statistics(record: dict[str, Any]) -> dict[str, Any]:
    """Derive public counters only from the durable job record and its ledger snapshot."""
    engine_status = str(record.get("status") or "queued")
    model_usage = record.get("model_usage") if isinstance(record.get("model_usage"), dict) else {}
    retry_state = record.get("generation_retry_state") if isinstance(record.get("generation_retry_state"), dict) else {}
    batches = retry_state.get("batches") if isinstance(retry_state.get("batches"), dict) else {}
    state_calls = sum(
        _optional_nonnegative_int(item.get("calls_used")) or 0
        for item in batches.values()
        if isinstance(item, dict)
    ) if batches else None
    state_budget = sum(
        _optional_nonnegative_int(item.get("limit")) or 0
        for item in batches.values()
        if isinstance(item, dict)
    ) if batches else None
    ledger_calls = _optional_nonnegative_int(model_usage.get("call_count"))
    stored_calls = _optional_nonnegative_int(record.get("network_attempted_count"))
    synced = record.get("network_stats_synced") is True
    calls = state_calls if state_calls is not None else ledger_calls
    if calls is None and (synced or (stored_calls is not None and stored_calls > 0)):
        calls = stored_calls
    budget = _optional_nonnegative_int(record.get("network_call_budget"))
    if state_budget is not None:
        budget = state_budget

    remaining_seconds = None
    deadline_text = str(record.get("generation_deadline_at") or "").strip()
    if deadline_text:
        try:
            deadline = datetime.fromisoformat(deadline_text)
            if deadline.tzinfo is None:
                deadline = deadline.astimezone()
            remaining_seconds = max(0, int((deadline - datetime.now().astimezone()).total_seconds()))
        except (TypeError, ValueError):
            pass
    active = engine_status in {"queued", "running"}
    statistics_status = "syncing" if active and (calls is None or budget is None or remaining_seconds is None) else (
        "ready" if any(value is not None for value in (calls, budget, remaining_seconds)) else "unavailable"
    )
    return {
        "network_attempted_count": calls,
        "network_call_budget": budget,
        "deadline_remaining_seconds": remaining_seconds,
        "network_statistics_status": statistics_status,
    }


def build_exam_run(row: dict[str, Any], quality_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    quality_summary = quality_summary or {}
    status = exam_run_status(row)
    quality = quality_from_summary(quality_summary)
    stage = str(row.get("current_stage") or "")
    if status.value in {"queued", "running", "paused", "needs_input"}:
        quality = QualityStatus.UNKNOWN
    elif status.value == "failed" and stage in {"answer_coverage", "content_quality", "figures", "docx", "render", "final_acceptance"}:
        quality = QualityStatus.BLOCKED
    final_acceptance = quality_summary.get("final_acceptance") if isinstance(quality_summary.get("final_acceptance"), dict) else None
    formally_accepted = False
    if final_acceptance:
        if "formal_acceptance_passed" in final_acceptance:
            formally_accepted = bool(final_acceptance.get("formal_acceptance_passed"))
        elif final_acceptance.get("status"):
            formally_accepted = str(final_acceptance.get("status")) in {"passed", "passed_with_warnings"}
        else:
            formally_accepted = final_acceptance.get("ok") is True
    is_review_candidate = bool(
        final_acceptance
        and (
            final_acceptance.get("status") == "completed_with_issues"
            or final_acceptance.get("delivery_tier") == "review_candidate"
        )
    )
    if is_review_candidate:
        quality = QualityStatus.WARNING
        status = practice_run_status("completed", quality=QualityStatus.WARNING)
    if status.value == "completed" and quality == QualityStatus.BLOCKED:
        status = practice_run_status("completed", quality=quality)
    elif status.value == "completed" and quality == QualityStatus.WARNING and not formally_accepted:
        status = practice_run_status("completed", quality=quality)
    exam_name = _display_basename(row.get("exam_display_name") or row.get("exam_path"), fallback="未命名真题")
    textbook_names = _textbook_display_names(row)
    public_row = {
        **row,
        "display_title": build_display_task_title(
            "真题解析",
            friendly_material_title(exam_name) or exam_name,
            model=row.get("model"),
            provider=row.get("provider"),
        ),
        "description": exam_name,
        "exam_display_name": exam_name,
        "material_display_names": [exam_name],
        "textbook_material_names": textbook_names,
    }
    enriched = enrich_contract(
        public_row,
        workflow=workflow_for_kind("exam"),
        status=status,
        quality=quality,
        stage=stage,
        error=str(row.get("error") or ""),
        final_acceptance=final_acceptance,
    )
    enriched["task_kind"] = "exam"
    enriched["quality_summary"] = quality_summary
    enriched["steps"] = []
    return enriched


def _practice_history_run(record: dict[str, Any]) -> dict[str, Any]:
    task_kind = str(record.get("task_kind") or "practice")
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    quality = quality_from_practice(
        data or {"generation": record.get("generation") or {}, "quality": record.get("quality") or {}}
    )
    engine_status = str(record.get("status") or "completed")
    status = practice_run_status(engine_status, operation="generate_from_plan", quality=quality)
    generation = record.get("generation") or data.get("generation") or {}
    configuration_blocked = record.get("configuration_blocked") is True or generation.get("configuration_blocked") is True
    config_error = next(
        (
            str(item.get("message") or "")
            for item in generation.get("batch_errors") or []
            if isinstance(item, dict) and item.get("requires_configuration") is True
        ),
        "",
    )
    phases = record.get("generation_phases") if isinstance(record.get("generation_phases"), list) else []
    kind_label = "知识点出题" if task_kind == "knowledge" else "按题出题"
    task_title, material_names = _practice_material_metadata(record, task_kind)
    row = {
        "task_id": record.get("history_id"),
        "task_kind": task_kind,
        "practice_batch_id": request.get("practice_batch_id") or record.get("practice_batch_id") or "",
        "operation": "generate_from_plan",
        "generation_phases": phases,
        "steps": phases,
        "is_generation_task": True,
        "display_title": build_display_task_title(
            kind_label,
            task_title,
            model=request.get("model") or record.get("model"),
            provider=request.get("provider") or record.get("provider"),
        ),
        "description": task_title,
        "material_display_names": material_names or [task_title],
        "exam_path": task_title,
        "provider": request.get("provider") or "",
        "model": request.get("model") or "",
        "textbooks_dir": "知识点出题" if task_kind == "knowledge" else "按题出题",
        "status": engine_status,
        "current_stage": "configuration" if configuration_blocked else "completed",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "question_count": int(record.get("generated_count") or record.get("question_count") or 0),
        "generated_count": int(record.get("generated_count") or record.get("question_count") or 0),
        "total_count": int(record.get("total_count") or len(data.get("exercises") or [])),
        "unfinished_count": int(record.get("unfinished_count") or 0),
        "configuration_blocked": configuration_blocked,
        "requires_configuration": configuration_blocked,
        "generation": generation,
        "quality": record.get("quality") or data.get("quality") or {},
        "completion_issues": record.get("completion_issues") or data.get("completion_issues") or {},
        "duration_seconds": 0,
        "duration_text": "已完成",
        "progress_percent": 100,
    }
    result = enrich_contract(
        row,
        workflow=workflow_for_kind(task_kind),
        status=status,
        quality=quality,
        stage="configuration" if configuration_blocked else "completed",
        operation="generate_from_plan",
        error=config_error,
        completion_source=data or row,
    )
    if configuration_blocked:
        result["capabilities"]["view_result"] = True
        result["capabilities"]["retry"] = True
        result["capabilities"]["reuse"] = False
    if any(
        item.get("code") == "generation_incomplete"
        for item in (result.get("completion_issues") or {}).get("issues") or []
        if isinstance(item, dict)
    ):
        result["capabilities"]["retry"] = True
        result["capabilities"]["reuse"] = False
    result["health"] = task_health_summary(row, kind="practice")
    return result


def _practice_job_run(record: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    engine_status = str(record.get("status") or "queued")
    operation = str(record.get("operation") or "")
    task_kind = str(record.get("task_kind") or "practice")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    elapsed = max(0, int(record.get("elapsed_seconds") or 0))
    running_progress = min(88, 30 + elapsed // 15) if operation in {"generate_from_plan", "generate_from_contract"} else min(88, 35 + elapsed // 10)
    status = practice_run_status(engine_status, operation=operation, quality=QualityStatus.UNKNOWN)
    kind_label = "知识点出题" if task_kind == "knowledge" else "按题出题"
    task_title, material_names = _practice_material_metadata(record, task_kind)
    support_id = public_support_id(
        str(record.get("support_id") or ""),
        task_id=str(record.get("job_id") or ""),
    )
    network_statistics = practice_network_statistics(record)
    row = {
        "task_id": record.get("job_id"),
        "task_kind": task_kind,
        "practice_batch_id": record.get("practice_batch_id") or "",
        "is_generation_task": True,
        "is_generation_job": True,
        "operation": operation,
        "display_title": build_display_task_title(
            kind_label,
            task_title,
            model=record.get("model") or payload.get("model"),
            provider=record.get("provider") or payload.get("provider"),
        ),
        "description": task_title,
        "material_display_names": material_names or [task_title],
        "steps": steps,
        "exam_path": task_title,
        "provider": record.get("provider") or "",
        "model": record.get("model") or "",
        "textbooks_dir": "知识点出题" if task_kind == "knowledge" else "按题出题",
        "status": engine_status,
        "current_stage": str(record.get("current_stage") or "planning"),
        "created_at": min((step.get("created_at") for step in steps if step.get("created_at")), default=record.get("created_at")),
        "updated_at": record.get("updated_at"),
        "error": record.get("error") or "",
        "support_id": support_id,
        "progress_message": record.get("progress_message") or "",
        "elapsed_seconds": elapsed,
        "network_phase": str(record.get("network_phase") or ""),
        **network_statistics,
        "duration_text": (
            "后台生成中" if engine_status in {"queued", "running"}
            else "已暂停" if engine_status == "paused"
            else "生成失败" if engine_status == "failed"
            else "等待下一步确认"
        ),
        "progress_percent": 15 if engine_status == "queued" else (running_progress if engine_status in {"running", "paused"} else 100),
    }
    result = enrich_contract(
        row,
        workflow=workflow_for_kind(task_kind),
        status=status,
        quality=QualityStatus.UNKNOWN,
        stage=str(row["current_stage"]),
        operation=operation,
        error=str(row["error"]),
        support_id=support_id,
    )
    if operation in {"generate_from_plan", "generate_from_contract"}:
        result["capabilities"]["pause"] = engine_status in {"queued", "running"}
        result["capabilities"]["resume"] = engine_status == "paused"
        result["capabilities"]["cancel"] = engine_status in {"queued", "running", "paused"}
    public_health_record = dict(record)
    if result.get("error_presentation"):
        public_health_record["error"] = result["error"]
        public_health_record["warning_reason"] = result["error"]
    result["health"] = task_health_summary(public_health_record, kind="practice")
    return result


def build_practice_runs(jobs: list[dict[str, Any]], histories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        batch_id = str(job.get("practice_batch_id") or "")
        groups[batch_id or f"legacy:{job.get('job_id')}"].append(job)

    history_runs = [_practice_history_run(record) for record in histories]
    for row in history_runs:
        batch_id = str(row.get("practice_batch_id") or "")
        group = groups.get(batch_id) or []
        if not group:
            continue
        created_values = [_time_key(item.get("created_at")) for item in group if _time_key(item.get("created_at"))]
        finished_values = [
            _time_key(item.get("completed_at") or item.get("updated_at"))
            for item in group
            if _time_key(item.get("completed_at") or item.get("updated_at"))
        ]
        wall_seconds = max(0, int(max(finished_values) - min(created_values))) if created_values and finished_values else 0
        active_seconds = sum(max(0, int(item.get("elapsed_seconds") or 0)) for item in group)
        queue_seconds = sum(
            max(0, int(_time_key(item.get("started_at")) - _time_key(item.get("created_at"))))
            for item in group
            if _time_key(item.get("started_at")) and _time_key(item.get("created_at"))
        )
        row["duration_seconds"] = wall_seconds
        row["duration_text"] = ""
        row["active_duration_seconds"] = active_seconds
        row["queue_duration_seconds"] = queue_seconds
        row["model_attempt_count"] = sum(
            max(0, int((item.get("model_usage") or {}).get("call_count") or 0))
            for item in group
        )
    history_batches = {str(row.get("practice_batch_id") or "") for row in history_runs if row.get("practice_batch_id")}

    job_runs: list[dict[str, Any]] = []
    operation_order = {"analyze": 0, "plan": 1, "generate_from_plan": 2, "generate_from_contract": 2}
    for key, group in groups.items():
        batch_id = "" if key.startswith("legacy:") else key
        if batch_id and batch_id in history_batches:
            continue
        if not batch_id and all(str(item.get("status")) == "completed" and str(item.get("operation")) in {"analyze", "plan"} for item in group):
            continue
        ordered = sorted(
            group,
            key=lambda item: (
                _time_key(item.get("created_at") or item.get("updated_at")),
                _time_key(item.get("updated_at")),
                operation_order.get(str(item.get("operation") or ""), -1),
            ),
            reverse=True,
        )
        current = ordered[0]
        steps = []
        for item in sorted(group, key=lambda item: (operation_order.get(str(item.get("operation") or ""), -1), _time_key(item.get("created_at")))):
            step_support_id = public_support_id(
                str(item.get("support_id") or ""),
                task_id=str(item.get("job_id") or ""),
            )
            step_presentation = present_error(
                str(item.get("error") or ""),
                stage=str(item.get("current_stage") or ""),
                support_id=step_support_id,
            )
            steps.append({
                "step_id": item.get("job_id"),
                "operation": item.get("operation"),
                "status": item.get("status"),
                "current_stage": item.get("current_stage"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "error": step_presentation.message if step_presentation else "",
                "support_id": step_support_id if step_presentation else "",
                "error_presentation": asdict(step_presentation) if step_presentation else None,
            })
        job_runs.append(_practice_job_run(current, steps))
    return sorted(history_runs + job_runs, key=lambda row: _time_key(row.get("updated_at")), reverse=True)
