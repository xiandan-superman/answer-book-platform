"""Early deterministic Word checks, explicitly not delivery acceptance.

These checks run as each model batch settles, before the bounded producer
admits another batch. They do not call a model or replace global review.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .document_contracts import DOCUMENT_CONTRACT_VERSION
from .output_checkpoints import file_dependencies, load_output_checkpoint, save_output_checkpoint
from .pandoc_word import PANDOC_CONTRACT


def preflight_object(stage_dir: Path, *, object_id: str, source: dict[str, Any],
                     candidate: dict[str, Any], kind: str, question: dict[str, Any] | None = None) -> dict[str, Any]:
    dependencies = {"candidate": candidate, "question": question, "kind": kind,
                    "assets": file_dependencies(candidate),
                    "contracts": [DOCUMENT_CONTRACT_VERSION, PANDOC_CONTRACT, "unit-preflight-v1"]}
    previous = load_output_checkpoint(stage_dir, stage="word_preflight", object_id=object_id, dependencies=dependencies)
    if previous and previous["candidate"]["report"].get("status") == "passed":
        return previous["candidate"]["report"]
    report: dict[str, Any]
    try:
        if kind == "exam":
            from .capabilities.academic_expressions import audit_academic_expressions
            from .v4_schema import validate_v4_answer_fragment

            academic = audit_academic_expressions({"fragments": [candidate]},
                                                 structured_exam={"items": [question or {}]}, render_preflight=True)
            issues = [*validate_v4_answer_fragment(candidate), *academic["issues"]]
        else:
            from .practice_export import validate_practice_export

            validation = validate_practice_export({"exercises": [candidate]})
            issues = validation["blocking_issues"]
        report = {"status": "blocked" if issues else "passed", "issues": issues}
    except Exception as exc:
        from .document_tool import DocumentToolFailure

        report = {"status": "blocked", "issues": [exc.to_dict() if isinstance(exc, DocumentToolFailure) else str(exc)],
                  "responsibility": exc.responsibility if isinstance(exc, DocumentToolFailure) else "unknown"}
    report.update({"scope": "word_preflight_only", "delivery_ready": False,
                   "content_review": "pending", "set_review": "pending"})
    save_output_checkpoint(stage_dir, stage="word_preflight", object_id=object_id,
                           source=source, candidate={"content": candidate, "report": report},
                           diagnostics=report, dependencies=dependencies)
    return report


def preflight_practice_batch(stage_dir: Path, items: list[dict[str, Any]], plan: list[dict[str, Any]]) -> dict[str, int]:
    from .exercise_generation import normalize_practice_set

    planned = {str(row.get("plan_item_id") or ""): (index, row) for index, row in enumerate(plan, 1)}
    summary = {"checked": 0, "passed": 0, "blocked": 0}
    for raw in items:
        object_id = str(raw.get("plan_item_id") or "")
        if object_id not in planned:
            continue
        number, row = planned[object_id]
        normalized = normalize_practice_set(
            {"exercises": [raw]}, requested_count=1, subject="",
            planned_types=[str(row.get("question_type") or "")],
            planned_source_ids=[str(row.get("source_question_id") or "")],
            planned_plan_ids=[object_id], planned_difficulties=[str(row.get("difficulty") or "")],
        )["exercises"][0]
        normalized["number"] = number
        report = preflight_object(stage_dir, object_id=object_id, source=raw,
                                  candidate=normalized, kind="practice", question=row)
        summary["checked"] += 1
        summary[report["status"]] += 1
    return summary
