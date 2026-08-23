from __future__ import annotations

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
    enriched = enrich_contract(
        row,
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
    status = practice_run_status("completed", operation="generate_from_plan", quality=quality)
    phases = record.get("generation_phases") if isinstance(record.get("generation_phases"), list) else []
    kind_label = "知识点出题" if task_kind == "knowledge" else "按题出题"
    task_title = str(record.get("title") or request.get("task_title") or "未命名材料").strip()
    row = {
        "task_id": record.get("history_id"),
        "task_kind": task_kind,
        "practice_batch_id": request.get("practice_batch_id") or record.get("practice_batch_id") or "",
        "operation": "generate_from_plan",
        "generation_phases": phases,
        "steps": phases,
        "is_generation_task": True,
        "display_title": f"{kind_label} · {task_title}",
        "description": task_title,
        "exam_path": task_title,
        "provider": request.get("provider") or "",
        "model": request.get("model") or "",
        "textbooks_dir": "知识点出题" if task_kind == "knowledge" else "按题出题",
        "status": "completed",
        "current_stage": "completed",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "question_count": record.get("question_count") or len(data.get("exercises") or []),
        "generation": record.get("generation") or data.get("generation") or {},
        "quality": record.get("quality") or data.get("quality") or {},
        "duration_seconds": 0,
        "duration_text": "已完成",
        "progress_percent": 100,
    }
    result = enrich_contract(
        row,
        workflow=workflow_for_kind(task_kind),
        status=status,
        quality=quality,
        stage="completed",
        operation="generate_from_plan",
    )
    result["health"] = task_health_summary(row, kind="practice")
    return result


def _practice_job_run(record: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    engine_status = str(record.get("status") or "queued")
    operation = str(record.get("operation") or "")
    task_kind = str(record.get("task_kind") or "practice")
    elapsed = max(0, int(record.get("elapsed_seconds") or 0))
    running_progress = min(88, 30 + elapsed // 15) if operation in {"generate_from_plan", "generate_from_contract"} else min(88, 35 + elapsed // 10)
    status = practice_run_status(engine_status, operation=operation, quality=QualityStatus.UNKNOWN)
    kind_label = "知识点出题" if task_kind == "knowledge" else "按题出题"
    task_title = str(record.get("title") or (record.get("payload") or {}).get("task_title") or "未命名材料").strip()
    support_id = public_support_id(
        str(record.get("support_id") or ""),
        task_id=str(record.get("job_id") or ""),
    )
    row = {
        "task_id": record.get("job_id"),
        "task_kind": task_kind,
        "practice_batch_id": record.get("practice_batch_id") or "",
        "is_generation_task": True,
        "is_generation_job": True,
        "operation": operation,
        "display_title": f"{kind_label} · {task_title}",
        "description": task_title,
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
        "duration_text": "后台生成中" if engine_status in {"queued", "running"} else ("生成失败" if engine_status == "failed" else "等待下一步确认"),
        "progress_percent": 15 if engine_status == "queued" else (running_progress if engine_status == "running" else 100),
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
    public_health_record = dict(record)
    if result.get("error_presentation"):
        public_health_record["error"] = result["error"]
        public_health_record["warning_reason"] = result["error"]
    result["health"] = task_health_summary(public_health_record, kind="practice")
    return result


def build_practice_runs(jobs: list[dict[str, Any]], histories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history_runs = [_practice_history_run(record) for record in histories]
    history_batches = {str(row.get("practice_batch_id") or "") for row in history_runs if row.get("practice_batch_id")}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        batch_id = str(job.get("practice_batch_id") or "")
        groups[batch_id or f"legacy:{job.get('job_id')}"].append(job)

    job_runs: list[dict[str, Any]] = []
    operation_order = {"analyze": 0, "plan": 1, "generate_from_plan": 2, "generate_from_contract": 2}
    status_order = {"running": 4, "queued": 3, "failed": 2, "completed": 1}
    for key, group in groups.items():
        batch_id = "" if key.startswith("legacy:") else key
        if batch_id and batch_id in history_batches:
            continue
        if not batch_id and all(str(item.get("status")) == "completed" and str(item.get("operation")) in {"analyze", "plan"} for item in group):
            continue
        ordered = sorted(
            group,
            key=lambda item: (
                status_order.get(str(item.get("status") or ""), 0),
                operation_order.get(str(item.get("operation") or ""), -1),
                _time_key(item.get("updated_at")),
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
