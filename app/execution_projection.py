from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .paths import DATA_ROOT, LOGS_DIR, TASKS_DIR

EXECUTION_PROJECTION_SCHEMA = "answer_book.execution_projection.v1"

EXAM_PROGRESS_STAGE_ORDER = (
    "environment",
    "extract_exam",
    "exam_structure_review",
    "question_understanding",
    "figure_schema_planning",
    "textbook_index",
    "knowledge_planning",
    "retrieval",
    "evidence_selection",
    "answer_generation",
    "answer_coverage",
    "figures",
    "content_quality",
    "content_quality_model_repair",
    "figures_after_content_quality_model_repair",
    "content_quality_local_repair",
    "docx",
    "docx_model_repair",
    "docx_repair",
    "question_review",
    "render",
    "acceptance",
    "final_acceptance",
    "completed",
)

SUCCESS_TERMINAL_STATUSES = {"completed", "completed_with_issues"}
FAILURE_TERMINAL_STATUSES = {"failed"}
CANCELLED_TERMINAL_STATUSES = {"cancelled"}
TERMINAL_STATUSES = (
    SUCCESS_TERMINAL_STATUSES
    | FAILURE_TERMINAL_STATUSES
    | CANCELLED_TERMINAL_STATUSES
)
PRACTICE_GENERATION_OPERATIONS = {"generate_from_plan", "generate_from_contract"}


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        pass
    return rows


def stage_order_index(stage: str | None) -> int:
    try:
        return EXAM_PROGRESS_STAGE_ORDER.index(str(stage or ""))
    except ValueError:
        return -1


def exam_stage_progress_percent(stage: str | None) -> int:
    text = str(stage or "")
    if text == "completed":
        return 100
    index = max(0, stage_order_index(text))
    return min(95, round(((index + 1) / len(EXAM_PROGRESS_STAGE_ORDER)) * 100))


def effective_exam_stage(task: dict[str, Any], pipeline: dict[str, Any] | None) -> str:
    stages = pipeline.get("stages") if isinstance(pipeline, dict) else []
    stages = stages if isinstance(stages, list) else []
    actionable = [
        stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage") != "pipeline"
    ]
    current_stage = str(task.get("current_stage") or "")
    effective_stage = current_stage
    if actionable:
        last_name = str(actionable[-1].get("stage") or "")
        if stage_order_index(last_name) >= stage_order_index(effective_stage):
            effective_stage = last_name
    # Preserve the current public read-model behavior exactly. The shadow
    # projector may report a completed_with_issues stage mismatch, but it does
    # not silently rewrite that existing presentation behavior.
    if task.get("status") == "completed":
        return "completed"
    return effective_stage


def practice_presentation_progress(record: dict[str, Any]) -> tuple[int, str]:
    status = str(record.get("status") or "queued")
    elapsed = _safe_int(record.get("elapsed_seconds"))
    operation = str(record.get("operation") or "")
    running_progress = (
        min(88, 30 + elapsed // 15)
        if operation == "generate_from_plan"
        else min(88, 35 + elapsed // 10)
    )
    if status == "queued":
        return 15, "queue_activity_placeholder"
    if status in {"running", "paused"}:
        return running_progress, "elapsed_activity_estimate"
    return 100, "lifecycle_terminated"


def _lifecycle(status: str) -> dict[str, Any]:
    if status == "completed":
        outcome = "succeeded"
    elif status == "completed_with_issues":
        outcome = "succeeded_with_issues"
    elif status in FAILURE_TERMINAL_STATUSES:
        outcome = "failed"
    elif status in CANCELLED_TERMINAL_STATUSES:
        outcome = "cancelled"
    elif status == "queued":
        outcome = "queued"
    elif status in {"running", "paused"}:
        outcome = status
    else:
        outcome = "unknown"
    return {
        "status": status,
        "outcome": outcome,
        "terminal": status in TERMINAL_STATUSES,
        "successful": status in SUCCESS_TERMINAL_STATUSES,
    }


def _finding(
    code: str,
    finding_class: str,
    *,
    severity: str = "info",
    contradiction: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "class": finding_class,
        "severity": severity,
        "contradiction": contradiction,
    }


def _counter_work(completed: int, total: int, *, evidence: str) -> dict[str, Any]:
    return {
        "metric": "scheduled_output_units",
        "applicable": total > 0,
        "completed_units": completed,
        "total_units": total,
        "completion_percent": (
            min(100, round((completed / total) * 100)) if total > 0 else None
        ),
        "output_complete": completed >= total if total > 0 else None,
        "evidence": evidence,
    }


def _task_event_observation(
    task: dict[str, Any],
    task_events: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events = list(task_events)
    task_updates = [event for event in events if event.get("event") == "task_updated"]
    latest = task_updates[-1].get("payload") if task_updates else None
    latest = latest if isinstance(latest, dict) else None
    matches = None
    findings: list[dict[str, Any]] = []
    if latest is not None:
        matches = all(
            str(latest.get(key) or "") == str(task.get(key) or "")
            for key in ("status", "current_stage")
        )
        if not matches:
            findings.append(
                _finding(
                    "task_event_snapshot_divergence",
                    "evidence_gap",
                    severity="warning",
                )
            )
    return {
        "task_event_count": len(events),
        "task_update_event_count": len(task_updates),
        "latest_task_update_matches_snapshot": matches,
        "continuous_sequence_available": False,
    }, findings


def _model_observation(summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = summary or {}
    return {
        "intent_count": _safe_int(summary.get("intent_count")),
        "result_count": _safe_int(summary.get("result_count")),
        "unresolved_intent_count": _safe_int(summary.get("unresolved_intent_count")),
        "result_without_intent_count": _safe_int(summary.get("result_without_intent_count")),
        "covered_by_execution_ledger": bool(summary.get("covered_by_execution_ledger", False)),
    }


def project_exam_task(
    task: dict[str, Any],
    *,
    pipeline: dict[str, Any] | None = None,
    task_events: Iterable[dict[str, Any]] = (),
    model_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(task.get("status") or "")
    lifecycle = _lifecycle(status)
    completed = _safe_int(task.get("completed_count"))
    total = _safe_int(task.get("total_count"))
    work = _counter_work(completed, total, evidence="task_snapshot_counters")
    effective_stage = effective_exam_stage(task, pipeline)
    presentation = {
        "reported_percent": exam_stage_progress_percent(effective_stage),
        "semantic": "workflow_stage_position",
        "effective_stage": effective_stage,
        "represents_output_completion": False,
    }
    event_observation, findings = _task_event_observation(task, task_events)
    model_observation = _model_observation(model_execution)

    if completed > total and total > 0:
        findings.append(
            _finding(
                "completed_units_exceed_total",
                "state_contradiction",
                severity="error",
                contradiction=True,
            )
        )
    if lifecycle["successful"] and str(task.get("current_stage") or "") != "completed":
        findings.append(
            _finding(
                "successful_task_has_nonterminal_stage",
                "state_contradiction",
                severity="warning",
                contradiction=True,
            )
        )
    if not lifecycle["terminal"] and str(task.get("current_stage") or "") == "completed":
        findings.append(
            _finding(
                "active_task_has_terminal_stage",
                "state_contradiction",
                severity="warning",
                contradiction=True,
            )
        )
    if lifecycle["outcome"] in {"failed", "cancelled"} and work["output_complete"]:
        findings.append(
            _finding(
                "terminal_failure_after_output_units_complete",
                "phase_boundary",
            )
        )
    if lifecycle["terminal"] and model_observation["unresolved_intent_count"] > 0:
        findings.append(
            _finding(
                "terminal_task_has_unknown_model_result",
                "evidence_gap",
                severity="warning",
            )
        )

    return {
        "schema_version": EXECUTION_PROJECTION_SCHEMA,
        "mode": "shadow",
        "authority": "observation_only",
        "task_kind": "exam",
        "snapshot": {
            "status": status,
            "current_stage": str(task.get("current_stage") or ""),
            "completed_count": completed,
            "total_count": total,
        },
        "lifecycle": lifecycle,
        "work_completion": work,
        "presentation_progress": presentation,
        "task_events": event_observation,
        "model_execution": model_observation,
        "findings": findings,
        "business_state_changed": False,
    }


def _practice_result_count(record: dict[str, Any]) -> int:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    exercises = result.get("exercises") if isinstance(result, dict) else None
    return len(exercises) if isinstance(exercises, list) else 0


def project_practice_job(
    record: dict[str, Any],
    *,
    model_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(record.get("status") or "queued")
    operation = str(record.get("operation") or "")
    lifecycle = _lifecycle(status)
    snapshot_completed = max(
        _safe_int(record.get("completed_count")),
        _safe_int(record.get("generated_count")),
    )
    total = _safe_int(record.get("total_count"))
    result_count = _practice_result_count(record)
    effective_completed = snapshot_completed
    evidence = "practice_snapshot_counters"
    if operation in PRACTICE_GENERATION_OPERATIONS and result_count > effective_completed:
        effective_completed = result_count
        evidence = "durable_result_exercises"
    if operation in PRACTICE_GENERATION_OPERATIONS:
        work = _counter_work(effective_completed, total, evidence=evidence)
    else:
        work = {
            "metric": "scheduled_output_units",
            "applicable": False,
            "completed_units": snapshot_completed,
            "total_units": total,
            "completion_percent": None,
            "output_complete": None,
            "evidence": "not_applicable_to_operation",
        }
    reported_percent, progress_semantic = practice_presentation_progress(record)
    presentation = {
        "reported_percent": reported_percent,
        "semantic": progress_semantic,
        "effective_stage": str(record.get("current_stage") or ""),
        "represents_output_completion": False,
    }
    model_observation = _model_observation(model_execution)
    findings: list[dict[str, Any]] = []

    if snapshot_completed > total and total > 0:
        findings.append(
            _finding(
                "completed_units_exceed_total",
                "state_contradiction",
                severity="error",
                contradiction=True,
            )
        )
    if lifecycle["successful"] and str(record.get("current_stage") or "") != "completed":
        findings.append(
            _finding(
                "successful_task_has_nonterminal_stage",
                "state_contradiction",
                severity="warning",
                contradiction=True,
            )
        )
    if not lifecycle["terminal"] and str(record.get("current_stage") or "") == "completed":
        findings.append(
            _finding(
                "active_task_has_terminal_stage",
                "state_contradiction",
                severity="warning",
                contradiction=True,
            )
        )
    if status in FAILURE_TERMINAL_STATUSES | CANCELLED_TERMINAL_STATUSES and reported_percent == 100:
        findings.append(
            _finding(
                "terminal_progress_not_output_completion",
                "presentation_ambiguity",
            )
        )
    if (
        lifecycle["successful"]
        and operation in PRACTICE_GENERATION_OPERATIONS
        and result_count > snapshot_completed
    ):
        findings.append(
            _finding(
                "successful_result_has_stale_unit_counters",
                "counter_staleness",
                severity="warning",
            )
        )
    if (
        lifecycle["successful"]
        and operation in PRACTICE_GENERATION_OPERATIONS
        and work["output_complete"] is False
    ):
        findings.append(
            _finding(
                "successful_task_has_incomplete_output_units",
                "state_contradiction",
                severity="error",
                contradiction=True,
            )
        )
    if lifecycle["outcome"] in {"failed", "cancelled"} and work["output_complete"]:
        findings.append(
            _finding(
                "terminal_failure_after_output_units_complete",
                "phase_boundary",
            )
        )
    if lifecycle["terminal"] and model_observation["unresolved_intent_count"] > 0:
        findings.append(
            _finding(
                "terminal_task_has_unknown_model_result",
                "evidence_gap",
                severity="warning",
            )
        )

    return {
        "schema_version": EXECUTION_PROJECTION_SCHEMA,
        "mode": "shadow",
        "authority": "observation_only",
        "task_kind": "practice",
        "operation": operation,
        "snapshot": {
            "status": status,
            "current_stage": str(record.get("current_stage") or ""),
            "completed_count": snapshot_completed,
            "total_count": total,
            "durable_result_exercise_count": result_count,
        },
        "lifecycle": lifecycle,
        "work_completion": work,
        "presentation_progress": presentation,
        "task_events": {
            "task_event_count": 0,
            "continuous_sequence_available": False,
        },
        "model_execution": model_observation,
        "findings": findings,
        "business_state_changed": False,
    }


def _model_execution_by_task(
    ledger_path: Path,
    task_ids: set[str],
) -> dict[str, dict[str, Any]]:
    intents: dict[str, set[str]] = {task_id: set() for task_id in task_ids}
    results: dict[str, set[str]] = {task_id: set() for task_id in task_ids}
    covered: set[str] = set()
    try:
        handle = ledger_path.open("r", encoding="utf-8")
    except OSError:
        handle = None
    if handle is not None:
        with handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                task_id = str(event.get("task_id") or "")
                if task_id not in task_ids:
                    continue
                invocation_id = str(event.get("invocation_id") or "")
                event_type = str(event.get("event_type") or "")
                if not invocation_id or event_type not in {"invocation.intent", "invocation.result"}:
                    continue
                covered.add(task_id)
                if event_type == "invocation.intent":
                    intents[task_id].add(invocation_id)
                else:
                    results[task_id].add(invocation_id)
    return {
        task_id: {
            "intent_count": len(intents[task_id]),
            "result_count": len(results[task_id]),
            "unresolved_intent_count": len(intents[task_id] - results[task_id]),
            "result_without_intent_count": len(results[task_id] - intents[task_id]),
            "covered_by_execution_ledger": task_id in covered,
        }
        for task_id in task_ids
    }


def _task_ref(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]


def build_execution_projection_report(
    *,
    tasks_dir: Path = TASKS_DIR,
    practice_jobs_dir: Path = DATA_ROOT / "practice_jobs",
    model_execution_ledger: Path = LOGS_DIR / "model_execution_events.jsonl",
    include_tasks: bool = False,
) -> dict[str, Any]:
    exam_rows: list[tuple[str, dict[str, Any], Path]] = []
    practice_rows: list[tuple[str, dict[str, Any]]] = []
    for task_path in sorted(tasks_dir.glob("*/task.json")):
        task = _read_json(task_path)
        task_id = str((task or {}).get("task_id") or task_path.parent.name)
        if task is not None and task_id:
            exam_rows.append((task_id, task, task_path.parent))
    for job_path in sorted(practice_jobs_dir.glob("generation_*.json")):
        record = _read_json(job_path)
        job_id = str((record or {}).get("job_id") or job_path.stem)
        if record is not None and job_id:
            practice_rows.append((job_id, record))

    task_ids = {task_id for task_id, _, _ in exam_rows} | {
        job_id for job_id, _ in practice_rows
    }
    model_by_task = _model_execution_by_task(model_execution_ledger, task_ids)
    projections: list[tuple[str, dict[str, Any]]] = []
    for task_id, task, directory in exam_rows:
        projections.append(
            (
                task_id,
                project_exam_task(
                    task,
                    pipeline=_read_json(directory / "stage_outputs" / "pipeline_status.json"),
                    task_events=_read_jsonl(directory / "events.jsonl"),
                    model_execution=model_by_task.get(task_id),
                ),
            )
        )
    for job_id, record in practice_rows:
        projections.append(
            (
                job_id,
                project_practice_job(
                    record,
                    model_execution=model_by_task.get(job_id),
                ),
            )
        )

    finding_codes: Counter[str] = Counter()
    finding_classes: Counter[str] = Counter()
    contradiction_tasks = 0
    finding_samples: dict[str, list[str]] = {}
    for task_id, projection in projections:
        findings = projection["findings"]
        if any(bool(finding.get("contradiction")) for finding in findings):
            contradiction_tasks += 1
        for finding in findings:
            code = str(finding.get("code") or "unknown")
            finding_class = str(finding.get("class") or "unknown")
            finding_codes[code] += 1
            finding_classes[finding_class] += 1
            samples = finding_samples.setdefault(code, [])
            if len(samples) < 5:
                samples.append(_task_ref(task_id))

    report: dict[str, Any] = {
        "schema_version": EXECUTION_PROJECTION_SCHEMA,
        "mode": "shadow",
        "authority": "observation_only",
        "enforced": False,
        "business_state_changed": False,
        "sample_count": len(projections),
        "exam_task_count": len(exam_rows),
        "practice_job_count": len(practice_rows),
        "finding_counts": dict(sorted(finding_codes.items())),
        "finding_class_counts": dict(sorted(finding_classes.items())),
        "finding_samples": dict(sorted(finding_samples.items())),
        "real_state_contradiction_task_count": contradiction_tasks,
        "model_execution_ledger_covered_task_count": sum(
            1
            for _, projection in projections
            if projection["model_execution"]["covered_by_execution_ledger"]
        ),
        "event_capabilities": {
            "exam_snapshot_available": True,
            "exam_task_events_available": True,
            "exam_task_event_continuous_sequence": False,
            "practice_snapshot_available": True,
            "practice_lifecycle_events_available": False,
            "model_invocation_events_available": model_execution_ledger.exists(),
            "unified_task_lifecycle_sequence_available": False,
        },
        "readiness": {
            "authoritative_projection_ready": False,
            "reasons": [
                "exam_task_events_have_no_continuous_sequence",
                "practice_jobs_have_no_lifecycle_event_stream",
                "model_execution_ledger_does_not_cover_all_task_transitions",
                "fixed_real_task_corpus_quality_review_not_completed",
            ],
        },
        "privacy": {
            "task_ids_exposed": False,
            "sample_references": "sha256_prefix",
            "prompt_or_response_content_included": False,
        },
        "added_model_calls": 0,
        "added_tokens": 0,
        "added_network_requests": 0,
    }
    if include_tasks:
        report["tasks"] = [
            {"task_ref": _task_ref(task_id), **projection}
            for task_id, projection in projections
        ]
    return report
