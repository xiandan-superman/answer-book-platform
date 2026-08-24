from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pipeline import stage_dir
from .question_types import infer_question_type
from .task_store import load_task


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _unique_question_records(
    value: Any,
    *,
    expected_question_ids: set[str],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for item in _dict_list(value):
        qid = str(item.get("question_id") or "").strip()
        if not qid or qid not in expected_question_ids or qid in duplicates:
            continue
        if qid in records:
            records.pop(qid)
            duplicates.add(qid)
            continue
        records[qid] = item
    return records


def _segment_text(segment: dict[str, Any], formulas: dict[str, dict[str, Any]]) -> str:
    stype = segment.get("type")
    if stype == "text":
        return str(segment.get("text") or "")
    if stype == "formula_ref":
        formula = formulas.get(str(segment.get("formula_id") or ""))
        if formula:
            # The web renderer consumes MathJax delimiters. Preserve the same
            # structured formula boundary used by DOCX instead of flattening a
            # formula back into raw LaTeX prose.
            return rf"\({formula.get('latex', '')}\)"
        return str(segment.get("formula_id") or "")
    if stype == "image_ref":
        return f"图片：{segment.get('image_id') or segment.get('image_ref') or ''}"
    return str(segment)


def _block_text(block: dict[str, Any], formulas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw_segments = block.get("segments")
    segments: list[Any] = raw_segments if isinstance(raw_segments, list) else []
    return {
        "label": str(block.get("label") or "解析"),
        "text": "\n".join(x for x in (_segment_text(seg, formulas).strip() for seg in segments if isinstance(seg, dict)) if x),
    }


def _question_type(item: dict[str, Any], fragment: dict[str, Any] | None) -> str:
    data = dict(item)
    if fragment:
        data.setdefault("section", fragment.get("section"))
    return infer_question_type(data)


def _quality_issues_by_question(stage_data: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(stage_data, dict):
        return out
    for severity, key in (("issue", "issues"), ("warning", "warnings")):
        for raw in stage_data.get(key) or []:
            if not isinstance(raw, dict):
                continue
            qid = str(raw.get("question_id") or "").strip()
            if not qid:
                continue
            out.setdefault(qid, []).append(
                {
                    "severity": raw.get("severity") or severity,
                    "code": raw.get("code") or "",
                    "message": raw.get("message") or str(raw),
                }
            )
    return out


def build_task_result_view(task_id: str) -> dict[str, Any]:
    record = load_task(task_id)
    sdir = stage_dir(task_id)
    exam = _json_dict(_read_json(sdir / "structured_exam.json"))
    fragments_data = _json_dict(_read_json(sdir / "answer_fragments.json"))
    plans_data = _json_dict(_read_json(sdir / "knowledge_plans.json"))
    coverage = _json_dict(_read_json(sdir / "answer_coverage_audit.json"))
    content_quality = _json_dict(_read_json(sdir / "content_quality_audit.json"))
    checkpoint_reconciliation = _json_dict(
        _read_json(sdir / "answer_checkpoint_reconciliation.json")
    )
    exam_items = _dict_list(exam.get("items"))
    exam_question_ids = {
        str(item.get("question_id") or "").strip()
        for item in exam_items
        if str(item.get("question_id") or "").strip()
    }
    redrive_checkpoint_ids = _string_set(
        checkpoint_reconciliation.get("redrive_question_ids")
    ) & exam_question_ids
    reusable_checkpoint_ids = (
        _string_set(checkpoint_reconciliation.get("reusable_question_ids"))
        & exam_question_ids
    ) - redrive_checkpoint_ids
    fragments = _unique_question_records(
        fragments_data.get("fragments"),
        expected_question_ids=exam_question_ids,
    )
    plans = _unique_question_records(
        plans_data.get("plans"),
        expected_question_ids=exam_question_ids,
    )
    quality_by_question = _quality_issues_by_question(content_quality)
    questions: list[dict[str, Any]] = []
    for index, item in enumerate(exam_items, start=1):
        qid = str(item.get("question_id") or "").strip()
        fragment = fragments.get(qid)
        plan = plans.get(qid) or {}
        formulas = {
            str(formula.get("formula_id") or ""): formula
            for formula in _dict_list((fragment or {}).get("formulas"))
        }
        blocks = [
            _block_text(block, formulas)
            for block in _dict_list((fragment or {}).get("blocks"))
        ]
        questions.append(
            {
                "question_id": qid,
                "index": index,
                "number": str((fragment or {}).get("number") or item.get("number") or index),
                "display_number": str(
                    (fragment or {}).get("display_number")
                    or item.get("display_number")
                    or (fragment or {}).get("number")
                    or item.get("number")
                    or index
                ),
                "type": _question_type(item, fragment),
                "section": str((fragment or {}).get("section") or item.get("section") or ""),
                "stem": str(item.get("stem") or ""),
                "subquestions": item.get("subquestions") or [],
                "score": item.get("score") or item.get("points") or "",
                "answer": str((fragment or {}).get("answer") or ""),
                "answer_summary": str((fragment or {}).get("answer_summary") or ""),
                "knowledge_points": plan.get("knowledge_points") if isinstance(plan.get("knowledge_points"), list) else [],
                "key_terms": plan.get("key_terms") if isinstance(plan.get("key_terms"), list) else [],
                "evidence_ids": (
                    (fragment or {}).get("evidence_ids")
                    if isinstance((fragment or {}).get("evidence_ids"), list)
                    else []
                ),
                "blocks": blocks,
                "formulas": list(formulas.values()),
                "warnings": (
                    (fragment or {}).get("warnings")
                    if isinstance((fragment or {}).get("warnings"), list)
                    else []
                ),
                "quality_issues": quality_by_question.get(qid, []),
                "has_answer": fragment is not None,
                "checkpoint_status": (
                    "reusable"
                    if qid in reusable_checkpoint_ids
                    else "redrive"
                    if qid in redrive_checkpoint_ids
                    else "not_evaluated"
                ),
            }
        )
    return {
        "task": record.__dict__,
        "metrics": {
            "question_count": len(exam_items),
            "answered_count": len(fragments),
            "covered_count": coverage.get("covered_count", coverage.get("fragment_count", len(fragments))),
            "quality_score": max(
                0,
                100
                - _int_value(content_quality.get("issue_count")) * 6
                - _int_value(content_quality.get("warning_count")),
            ),
            "evidence_count": sum(len(q.get("evidence_ids") or []) for q in questions),
            "issue_count": content_quality.get("issue_count", 0),
            "warning_count": content_quality.get("warning_count", 0),
            "checkpoint_reusable_count": len(reusable_checkpoint_ids),
            "checkpoint_redrive_count": len(redrive_checkpoint_ids),
        },
        "checkpoint_reconciliation": {
            "exists": bool(checkpoint_reconciliation),
            "resume_strategy": checkpoint_reconciliation.get("resume_strategy") or "",
            "source_contract": _json_dict(checkpoint_reconciliation.get("source_contract")),
            "inconsistencies": (
                checkpoint_reconciliation.get("inconsistencies")
                if isinstance(checkpoint_reconciliation.get("inconsistencies"), list)
                else []
            ),
        },
        "questions": questions,
    }
