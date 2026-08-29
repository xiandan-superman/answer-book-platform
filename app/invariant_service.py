from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .artifact_store import build_artifact_integrity_report
from .execution_projection import build_execution_projection_report
from .paths import LOGS_DIR

INVARIANT_REPORT_SCHEMA = "answer_book.invariant_report.v1"


def _task_ref(value: Any) -> str:
    return hashlib.sha256(str(value or "unscoped").encode("utf-8")).hexdigest()[:16]


def _read_execution_ledger(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return rows, malformed
    with handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(value, dict):
                rows.append(value)
            else:
                malformed += 1
    return rows, malformed


def _finding(
    code: str,
    finding_class: str,
    *,
    count: int,
    samples: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "class": finding_class,
        "count": max(0, int(count)),
        "task_samples": list(samples or [])[:5],
        "actual_blocked": False,
    }


def build_invariant_report(
    *,
    model_execution_ledger: Path = LOGS_DIR / "model_execution_events.jsonl",
    projection_report: dict[str, Any] | None = None,
    artifact_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate deterministic cross-layer facts without changing runtime state.

    The first release intentionally remains observation-only.  It distinguishes
    contradictions from missing evidence so legacy rows cannot be mislabeled as
    production failures.
    """

    rows, malformed_rows = _read_execution_ledger(model_execution_ledger)
    intents: dict[str, list[dict[str, Any]]] = defaultdict(list)
    results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    retry_scheduled: Counter[str] = Counter()
    retry_started: Counter[str] = Counter()
    for row in rows:
        invocation_id = str(row.get("invocation_id") or "")
        event_type = str(row.get("event_type") or "")
        if not invocation_id:
            continue
        if event_type == "invocation.intent":
            intents[invocation_id].append(row)
        elif event_type == "invocation.result":
            results[invocation_id].append(row)
        elif event_type == "retry.scheduled":
            retry_scheduled[invocation_id] += 1
        elif event_type == "retry.started":
            retry_started[invocation_id] += 1

    duplicate_intents = [key for key, value in intents.items() if len(value) > 1]
    duplicate_results = [key for key, value in results.items() if len(value) > 1]
    result_without_intent = sorted(set(results) - set(intents))
    unresolved = sorted(set(intents) - set(results))
    prompt_missing = [key for key, values in intents.items() if not isinstance(values[0].get("prompt_observation"), dict)]
    prompt_unregistered = [
        key
        for key, values in intents.items()
        if isinstance(values[0].get("prompt_observation"), dict)
        and not values[0]["prompt_observation"].get("registered")
        and not values[0]["prompt_observation"].get("report_unavailable")
    ]
    retry_started_without_schedule = [key for key, count in retry_started.items() if count > retry_scheduled.get(key, 0)]

    def samples(invocation_ids: list[str]) -> list[str]:
        task_ids: list[str] = []
        for invocation_id in invocation_ids:
            source = (intents.get(invocation_id) or results.get(invocation_id) or [{}])[0]
            task_ref = _task_ref(source.get("task_id"))
            if task_ref not in task_ids:
                task_ids.append(task_ref)
        return task_ids[:5]

    projection = projection_report
    projection_unavailable = False
    if projection is None:
        try:
            projection = build_execution_projection_report()
        except Exception:
            projection = {}
            projection_unavailable = True

    projection_counts = projection.get("finding_counts") if isinstance(projection, dict) else {}
    projection_counts = projection_counts if isinstance(projection_counts, dict) else {}
    projection_contradictions = int((projection or {}).get("real_state_contradiction_task_count") or 0)
    artifact_unavailable = False
    if artifact_report is None:
        try:
            artifact_report = build_artifact_integrity_report()
        except Exception:
            artifact_report = {}
            artifact_unavailable = True
    artifact_violations = int((artifact_report or {}).get("integrity_violation_count") or 0)

    findings = [
        _finding("execution_ledger_malformed_row", "evidence_gap", count=malformed_rows),
        _finding("duplicate_invocation_intent", "state_contradiction", count=len(duplicate_intents), samples=samples(duplicate_intents)),
        _finding("duplicate_invocation_result", "state_contradiction", count=len(duplicate_results), samples=samples(duplicate_results)),
        _finding("result_without_intent", "state_contradiction", count=len(result_without_intent), samples=samples(result_without_intent)),
        _finding("unresolved_invocation_intent", "evidence_gap", count=len(unresolved), samples=samples(unresolved)),
        _finding("legacy_intent_without_prompt_observation", "coverage_gap", count=len(prompt_missing), samples=samples(prompt_missing)),
        _finding("unregistered_prompt_observation", "coverage_gap", count=len(prompt_unregistered), samples=samples(prompt_unregistered)),
        _finding(
            "retry_started_without_schedule",
            "state_contradiction",
            count=len(retry_started_without_schedule),
            samples=samples(retry_started_without_schedule),
        ),
        _finding("task_projection_state_contradiction", "state_contradiction", count=projection_contradictions),
        _finding("artifact_integrity_violation", "state_contradiction", count=artifact_violations),
    ]
    findings = [item for item in findings if item["count"]]
    class_counts: Counter[str] = Counter()
    for item in findings:
        class_counts[item["class"]] += item["count"]

    return {
        "schema_version": INVARIANT_REPORT_SCHEMA,
        "mode": "shadow",
        "authority": "observation_only",
        "enforced": False,
        "actual_blocked_count": 0,
        "behavior_changed": False,
        "execution_event_count": len(rows),
        "invocation_intent_count": len(intents),
        "invocation_result_count": len(results),
        "finding_counts": {item["code"]: item["count"] for item in findings},
        "finding_class_counts": dict(sorted(class_counts.items())),
        "findings": findings,
        "projection_finding_counts": dict(sorted(projection_counts.items())),
        "report_unavailable": projection_unavailable or artifact_unavailable,
        "readiness": {
            "fail_closed_ready": False,
            "reasons": [
                "legacy_execution_intents_lack_prompt_observation",
                "task_event_streams_are_not_continuous_for_all_business_lines",
                "artifact_integrity_coverage_does_not_yet_include_all_document_outputs",
                "fixed_real_task_corpus_quality_review_not_completed",
            ],
        },
        "privacy": {
            "task_ids_included": False,
            "prompt_or_response_content_included": False,
            "hashed_task_samples_only": True,
        },
        "added_model_calls": 0,
        "added_tokens": 0,
        "added_network_requests": 0,
    }
