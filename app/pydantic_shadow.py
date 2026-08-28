from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from .paths import DATA_ROOT
from .practice_output_contracts import NormalizedPracticePlanContract, NormalizedPracticeSetContract
from .runtime_monitor import current_model_call_context

SHADOW_SCHEMA_VERSION = "answer_book.pydantic_shadow.v1"
SHADOW_DIR = DATA_ROOT / "validation" / "shadow" / "pydantic"
DEFAULT_SHADOW_EVENT_LOG = SHADOW_DIR / "events.jsonl"
SHADOW_EVENT_LOG = DEFAULT_SHADOW_EVENT_LOG
SHADOW_REVIEW_LOG = SHADOW_DIR / "reviews.jsonl"
SHADOW_REPORT_JSON = SHADOW_DIR / "pydantic-shadow-report.json"
_WRITE_LOCK = threading.RLock()


class FigureSpecContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    figure_id: str
    kind: str
    question_id: Optional[str] = None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[position], 3)


def _safe_context() -> dict[str, str]:
    try:
        context = current_model_call_context()
    except Exception:
        return {}
    return {
        key: str(context.get(key) or "")[:160]
        for key in ("task_id", "run_id", "stage", "operation")
        if context.get(key)
    }


def _validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for issue in exc.errors(include_url=False, include_input=False)[:20]:
        location = ".".join(str(part) for part in issue.get("loc") or ()) or "root"
        errors.append({"path": location[:240], "type": str(issue.get("type") or "validation_error")[:120]})
    return errors


def _append_event(event: dict[str, Any]) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST") and SHADOW_EVENT_LOG == DEFAULT_SHADOW_EVENT_LOG:
        return
    SHADOW_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, SHADOW_EVENT_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def observe_contract(
    object_type: str,
    boundary: str,
    model: type[BaseModel],
    data: Any,
    *,
    item_count: int | None = None,
) -> dict[str, Any]:
    """Validate locally and record metadata; this function must never affect a task."""

    started = time.perf_counter()
    errors: list[dict[str, str]] = []
    try:
        model.model_validate(data)
    except ValidationError as exc:
        errors = _validation_errors(exc)
    except Exception as exc:
        errors = [{"path": "root", "type": exc.__class__.__name__[:120]}]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    event: dict[str, Any] = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "object_type": str(object_type)[:80],
        "boundary": str(boundary)[:120],
        "mode": "shadow",
        "enforced": False,
        "passed": not errors,
        "actual_blocked": False,
        "would_block_if_enforced": bool(errors),
        "issue_classification": "deterministic_structure" if errors else "none",
        "semantic_decision_owner": "existing_quality_gate",
        "validation_ms": elapsed_ms,
        "errors": errors,
        "model_calls_added": 0,
        "tokens_added": 0,
        "network_requests_added": 0,
        **_safe_context(),
    }
    if item_count is not None:
        event["item_count"] = max(0, int(item_count))
    try:
        _append_event(event)
    except Exception:
        # Shadow storage is deliberately best-effort and cannot fail production work.
        pass
    return event


def record_shadow_review(event_id: str, verdict: str) -> dict[str, Any]:
    """Record a human verdict without storing prompts, answers or materials."""

    event_id = str(event_id or "").strip()
    verdict = str(verdict or "").strip()
    if not event_id or verdict not in {"confirmed_issue", "false_positive"}:
        raise ValueError("影子复核必须提供事件 ID，结论只能是 confirmed_issue 或 false_positive")
    review = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event_id": event_id[:64],
        "verdict": verdict,
    }
    SHADOW_REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, SHADOW_REVIEW_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(review, ensure_ascii=False, separators=(",", ":")) + "\n")
    return review


def observe_practice_plan(data: dict[str, Any]) -> dict[str, Any]:
    blueprint = data.get("blueprint") if isinstance(data, dict) else None
    items = blueprint.get("exercise_plan") if isinstance(blueprint, dict) else None
    return observe_contract(
        "blueprint",
        "practice_plan.normalized",
        NormalizedPracticePlanContract,
        data,
        item_count=len(items) if isinstance(items, list) else 0,
    )


def observe_practice_set(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("exercises") if isinstance(data, dict) else None
    return observe_contract(
        "practice_output",
        "practice_set.normalized",
        NormalizedPracticeSetContract,
        data,
        item_count=len(items) if isinstance(items, list) else 0,
    )


def observe_figure_spec(data: dict[str, Any]) -> dict[str, Any]:
    return observe_contract("figure_spec", "figure_spec.normalized", FigureSpecContract, data, item_count=1)


def _read_events(path: Path = SHADOW_EVENT_LOG) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema_version") == SHADOW_SCHEMA_VERSION:
            rows.append(row)
    return rows


def _review_verdicts(path: Path = SHADOW_REVIEW_LOG) -> dict[str, str]:
    verdicts: dict[str, str] = {}
    for row in _read_events(path):
        event_id = str(row.get("event_id") or "")
        verdict = str(row.get("verdict") or "")
        if event_id and verdict in {"confirmed_issue", "false_positive"}:
            verdicts[event_id] = verdict
    return verdicts


def build_pydantic_shadow_report(
    *,
    event_path: Path = SHADOW_EVENT_LOG,
    review_path: Path = SHADOW_REVIEW_LOG,
) -> dict[str, Any]:
    events = _read_events(event_path)
    verdicts = _review_verdicts(review_path)
    object_counts: Counter[str] = Counter()
    object_failures: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    task_ids: set[str] = set()
    elapsed_values: list[float] = []
    timestamps: list[datetime] = []
    for event in events:
        object_type = str(event.get("object_type") or "unknown")
        object_counts[object_type] += 1
        if not bool(event.get("passed")):
            object_failures[object_type] += 1
        task_id = str(event.get("task_id") or "")
        if task_id:
            task_ids.add(task_id)
        elapsed_values.append(float(event.get("validation_ms") or 0.0))
        try:
            timestamps.append(datetime.fromisoformat(str(event.get("timestamp") or "")))
        except ValueError:
            pass
        for error in event.get("errors") or []:
            if isinstance(error, dict):
                error_counts[f"{error.get('path') or 'root'}:{error.get('type') or 'validation_error'}"] += 1
    sample_count = len(events)
    would_block_count = sum(bool(event.get("would_block_if_enforced")) for event in events)
    issue_patterns = {
        f"{event.get('object_type') or 'unknown'}:{error.get('path') or 'root'}:{error.get('type') or 'validation_error'}"
        for event in events
        for error in (event.get("errors") or [])
        if isinstance(error, dict)
    }
    confirmed_issue_count = sum(verdict == "confirmed_issue" for verdict in verdicts.values())
    false_positive_count = sum(verdict == "false_positive" for verdict in verdicts.values())
    observed_days = 0
    if timestamps:
        observed_days = max(1, (max(timestamps).date() - min(timestamps).date()).days + 1)
    ready_for_review = sample_count >= 100 and len(task_ids) >= 20
    report = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "shadow",
        "enforced": False,
        "actual_blocked_count": 0,
        "automatic_promotion_enabled": False,
        "enforcement_requires_explicit_user_confirmation": True,
        "sample_count": sample_count,
        "task_count": len(task_ids),
        "observed_days": observed_days,
        "would_block_count": would_block_count,
        "would_block_rate": round(would_block_count / sample_count, 4) if sample_count else 0.0,
        "objects": {
            name: {
                "sample_count": count,
                "passed_count": count - object_failures[name],
                "would_block_count": object_failures[name],
            }
            for name, count in sorted(object_counts.items())
        },
        "top_error_locations": [
            {"key": key, "count": count} for key, count in error_counts.most_common(20)
        ],
        "validation_time_ms": {
            "total": round(sum(elapsed_values), 3),
            "p95": _percentile(elapsed_values, 0.95),
            "max": round(max(elapsed_values), 3) if elapsed_values else 0.0,
        },
        "added_model_calls": 0,
        "added_tokens": 0,
        "added_network_requests": 0,
        "problem_tracking": {
            "detected_issue_sample_count": would_block_count,
            "unique_new_issue_pattern_count": len(issue_patterns),
            "human_reviewed_count": len(verdicts),
            "confirmed_issue_count": confirmed_issue_count,
            "false_positive_count": false_positive_count,
            "avoided_downstream_failure_count": 0,
            "avoided_retry_count": 0,
            "avoidance_measurement_status": "not_claimed_in_shadow_mode",
        },
        "blocking_policy": {
            "current": "observe_only",
            "future_if_user_approved": "deterministic_structure_only",
            "semantic_risks": "existing_quality_gate",
        },
        "review_readiness": {
            "ready": ready_for_review,
            "minimum_samples": 100,
            "minimum_tasks": 20,
            "reason": "样本已达到人工评审门槛" if ready_for_review else "继续收集样本；不会自动转为阻断",
        },
        "event_log": str(event_path),
    }
    try:
        SHADOW_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        SHADOW_REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return report
