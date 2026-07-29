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
    return json.loads(path.read_text(encoding="utf-8"))


def _segment_text(segment: dict[str, Any], formulas: dict[str, dict[str, Any]]) -> str:
    stype = segment.get("type")
    if stype == "text":
        return str(segment.get("text") or "")
    if stype == "formula_ref":
        formula = formulas.get(str(segment.get("formula_id") or ""))
        if formula:
            return f"公式：{formula.get('latex', '')}"
        return str(segment.get("formula_id") or "")
    if stype == "image_ref":
        return f"图片：{segment.get('image_id') or segment.get('image_ref') or ''}"
    return str(segment)


def _block_text(block: dict[str, Any], formulas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    segments = block.get("segments") if isinstance(block.get("segments"), list) else []
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
    exam = _read_json(sdir / "structured_exam.json") or {}
    fragments_data = _read_json(sdir / "answer_fragments.json") or {}
    plans_data = _read_json(sdir / "knowledge_plans.json") or {}
    coverage = _read_json(sdir / "answer_coverage_audit.json") or {}
    content_quality = _read_json(sdir / "content_quality_audit.json") or {}
    fragments = {
        str(fragment.get("question_id") or "").strip(): fragment
        for fragment in fragments_data.get("fragments", [])
        if isinstance(fragment, dict) and str(fragment.get("question_id") or "").strip()
    }
    plans = {
        str(plan.get("question_id") or "").strip(): plan
        for plan in plans_data.get("plans", [])
        if isinstance(plan, dict) and str(plan.get("question_id") or "").strip()
    }
    quality_by_question = _quality_issues_by_question(content_quality)
    questions: list[dict[str, Any]] = []
    for index, item in enumerate(exam.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        fragment = fragments.get(qid)
        plan = plans.get(qid) or {}
        formulas = {
            str(formula.get("formula_id") or ""): formula
            for formula in (fragment or {}).get("formulas", [])
            if isinstance(formula, dict)
        }
        blocks = [_block_text(block, formulas) for block in (fragment or {}).get("blocks", []) if isinstance(block, dict)]
        questions.append(
            {
                "question_id": qid,
                "index": index,
                "number": str((fragment or {}).get("number") or item.get("number") or index),
                "type": _question_type(item, fragment),
                "section": str((fragment or {}).get("section") or item.get("section") or ""),
                "stem": str(item.get("stem") or ""),
                "subquestions": item.get("subquestions") or [],
                "score": item.get("score") or item.get("points") or "",
                "answer": str((fragment or {}).get("answer") or ""),
                "answer_summary": str((fragment or {}).get("answer_summary") or ""),
                "knowledge_points": plan.get("knowledge_points") or [],
                "key_terms": plan.get("key_terms") or [],
                "evidence_ids": (fragment or {}).get("evidence_ids") or [],
                "blocks": blocks,
                "formulas": list(formulas.values()),
                "warnings": (fragment or {}).get("warnings") or [],
                "quality_issues": quality_by_question.get(qid, []),
                "has_answer": fragment is not None,
            }
        )
    return {
        "task": record.__dict__,
        "metrics": {
            "question_count": len(exam.get("items") or []),
            "answered_count": len(fragments),
            "covered_count": coverage.get("covered_count", coverage.get("fragment_count", len(fragments))),
            "quality_score": max(0, 100 - int(content_quality.get("issue_count", 0)) * 6 - int(content_quality.get("warning_count", 0))),
            "evidence_count": sum(len(q.get("evidence_ids") or []) for q in questions),
            "issue_count": content_quality.get("issue_count", 0),
            "warning_count": content_quality.get("warning_count", 0),
        },
        "questions": questions,
    }
