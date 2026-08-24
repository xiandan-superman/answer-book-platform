from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .drawing_code import normalize_drawing_mode
from .question_scores import format_score, infer_suggested_score, normalize_score, parse_score
from .question_types import QUESTION_TYPES, infer_question_type, normalize_question_type
from .task_control import TaskCancelled, read_task_control
from .task_store import append_event, task_dir, update_task
from .text_utils import cn_to_int


class ExamStructureRejected(RuntimeError):
    pass


def exam_structure_request_path(task_id: str) -> Path:
    return task_dir(task_id) / "exam_structure_review_request.json"


def exam_structure_response_path(task_id: str) -> Path:
    return task_dir(task_id) / "exam_structure_review_response.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _major_cn(item: dict[str, Any]) -> str:
    section = str(item.get("section") or item.get("section_raw") or "")
    m = re.match(r"\s*([一二三四五六七八九十]+)、", section)
    if m:
        return m.group(1)
    number = cn_to_int(str(item.get("major_number") or "")) or int(item.get("major_number") or 0)
    digits = "一二三四五六七八九"
    if 1 <= number <= 9:
        return digits[number - 1]
    if number == 10:
        return "十"
    return str(item.get("major_number") or "")


def _review_subquestions(item: dict[str, Any], parent_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(item.get("subquestions") or [], start=1):
        if not isinstance(raw, dict):
            continue
        sub = dict(raw)
        if not sub.get("question_type") and not sub.get("confirmed_question_type"):
            sub["question_type"] = parent_type
        out.append(
            {
                "index": index,
                "number": str(sub.get("number") or index),
                "marker": str(sub.get("marker") or ""),
                "stem": str(sub.get("stem") or ""),
                "question_type": infer_question_type(sub),
                **_review_score_fields(sub),
                "requirements": _review_requirements(sub, str(sub.get("number") or index), infer_question_type(sub)),
            }
        )
    return out


def _review_requirements(item: dict[str, Any], parent_number: str, parent_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(item.get("requirements") or [], start=1):
        if not isinstance(raw, dict):
            continue
        req = dict(raw)
        if not req.get("question_type") and not req.get("confirmed_question_type"):
            req["question_type"] = parent_type
        number = str(req.get("number") or f"{parent_number}.{index}")
        out.append(
            {
                "index": index,
                "number": number,
                "marker": str(req.get("marker") or number),
                "stem": str(req.get("stem") or ""),
                "question_type": infer_question_type(req),
                **_review_score_fields(req),
            }
        )
    return out


def _review_image_refs(task_id: str, item: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for raw in item.get("image_refs") or []:
        path = Path(str(raw))
        if not path.exists() or not path.is_file():
            continue
        refs.append(
            {
                "path": str(path),
                "name": path.name,
                "preview_url": f"/api/tasks/{quote(task_id)}/preview?path={quote(str(path))}",
            }
        )
    return refs


def _review_question_snapshot_refs(task_id: str, item: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for raw in item.get("question_snapshot_refs") or []:
        path = Path(str(raw))
        if not path.exists() or not path.is_file():
            continue
        refs.append(
            {
                "path": str(path),
                "name": path.name,
                "preview_url": f"/api/tasks/{quote(task_id)}/preview?path={quote(str(path))}",
            }
        )
    return refs


def _review_score_fields(item: dict[str, Any]) -> dict[str, Any]:
    confirmed = normalize_score(item.get("confirmed_score")) if item.get("score_reviewed") else None
    suggested = normalize_score(confirmed if confirmed is not None else infer_suggested_score(item))
    return {
        "score": format_score(confirmed if confirmed is not None else suggested),
        "suggested_score": format_score(suggested),
        "confirmed_score": format_score(confirmed),
        "score_reviewed": bool(item.get("score_reviewed")),
    }


def _apply_confirmed_score(target: dict[str, Any], update: dict[str, Any]) -> None:
    if "confirmed_score" not in update and "score" not in update:
        return
    raw = update.get("confirmed_score") if "confirmed_score" in update else update.get("score")
    score = normalize_score(raw)
    target["score"] = score
    target["confirmed_score"] = score
    target["score_reviewed"] = True
    target["score_review_origin"] = str(update.get("score_review_origin") or "manual")


def _validate_score_row(row: dict[str, Any], label: str, issues: list[str]) -> None:
    raw = row.get("confirmed_score") if "confirmed_score" in row else row.get("score")
    if parse_score(raw) is None:
        issues.append(f"{label} 缺少确认分值")


def validate_exam_structure_review_updates(updates: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for q_index, row in enumerate(updates, start=1):
        if not isinstance(row, dict):
            continue
        label = f"第{q_index}题"
        _validate_score_row(row, label, issues)
        for sub_index, sub in enumerate(row.get("subquestions") or [], start=1):
            if not isinstance(sub, dict) or sub.get("deleted") or sub.get("_delete"):
                continue
            sub_label = f"{label}小问{sub.get('number') or sub_index}"
            _validate_score_row(sub, sub_label, issues)
            for req_index, req in enumerate(sub.get("requirements") or [], start=1):
                if not isinstance(req, dict) or req.get("deleted") or req.get("_delete"):
                    continue
                _validate_score_row(req, f"{sub_label}要求{req.get('number') or req_index}", issues)
    return issues


def _apply_subquestion_updates(item: dict[str, Any], update: dict[str, Any], parent_type: str) -> list[dict[str, Any]]:
    raw_update_rows = update.get("subquestions")
    has_authoritative_rows = "subquestions" in update and isinstance(raw_update_rows, list)
    update_rows: list[Any] = raw_update_rows if isinstance(raw_update_rows, list) else []
    by_number = {str(row.get("number") or "").strip(): row for row in update_rows if isinstance(row, dict)}
    if has_authoritative_rows:
        existing_by_number = {
            str(raw.get("number") or index).strip(): dict(raw)
            for index, raw in enumerate(item.get("subquestions") or [], start=1)
            if isinstance(raw, dict)
        }
        authoritative_out: list[dict[str, Any]] = []
        for index, row in enumerate(update_rows, start=1):
            if not isinstance(row, dict) or row.get("deleted") or row.get("_delete"):
                continue
            number = str(row.get("number") or index).strip() or str(index)
            sub = dict(existing_by_number.get(number, {}))
            stem = str(row.get("stem") if row.get("stem") is not None else sub.get("stem") or "").strip()
            marker = str(row.get("marker") or sub.get("marker") or f"({number})").strip()
            question_type = normalize_question_type(row.get("question_type")) or infer_question_type(sub) or parent_type
            sub.update(
                {
                    "number": number,
                    "marker": marker,
                    "stem": stem,
                    "question_type": question_type,
                    "confirmed_question_type": question_type,
                    "requirements": _apply_requirement_updates(sub, row, number, question_type),
                    "type_reviewed": True,
                    "structure_reviewed": True,
                }
            )
            _apply_confirmed_score(sub, row)
            authoritative_out.append(sub)
        return authoritative_out

    out: list[dict[str, Any]] = []
    for index, raw in enumerate(item.get("subquestions") or [], start=1):
        if not isinstance(raw, dict):
            continue
        sub = dict(raw)
        number = str(sub.get("number") or index).strip()
        row = by_number.get(number, {})
        question_type = normalize_question_type(row.get("question_type")) if row else ""
        if not question_type:
            if sub.get("question_type") or sub.get("confirmed_question_type"):
                question_type = infer_question_type(sub)
            else:
                question_type = parent_type
        sub["question_type"] = question_type
        sub["confirmed_question_type"] = question_type
        sub["requirements"] = _apply_requirement_updates(sub, row, number, question_type) if row else _apply_requirement_updates(sub, {}, number, question_type)
        sub["type_reviewed"] = number in by_number
        if row:
            _apply_confirmed_score(sub, row)
        out.append(sub)
    return out


def _apply_requirement_updates(item: dict[str, Any], update: dict[str, Any], parent_number: str, parent_type: str) -> list[dict[str, Any]]:
    raw_update_rows = update.get("requirements")
    has_authoritative_rows = "requirements" in update and isinstance(raw_update_rows, list)
    update_rows: list[Any] = raw_update_rows if isinstance(raw_update_rows, list) else []
    if has_authoritative_rows:
        existing_by_number = {
            str(raw.get("number") or f"{parent_number}.{index}").strip(): dict(raw)
            for index, raw in enumerate(item.get("requirements") or [], start=1)
            if isinstance(raw, dict)
        }
        authoritative_out: list[dict[str, Any]] = []
        for index, row in enumerate(update_rows, start=1):
            if not isinstance(row, dict) or row.get("deleted") or row.get("_delete"):
                continue
            number = str(row.get("number") or f"{parent_number}.{index}").strip() or f"{parent_number}.{index}"
            req = dict(existing_by_number.get(number, {}))
            stem = str(row.get("stem") if row.get("stem") is not None else req.get("stem") or "").strip()
            marker = str(row.get("marker") or req.get("marker") or number).strip()
            question_type = normalize_question_type(row.get("question_type")) or infer_question_type(req) or parent_type
            req.update(
                {
                    "number": number,
                    "marker": marker,
                    "stem": stem,
                    "question_type": question_type,
                    "confirmed_question_type": question_type,
                    "type_reviewed": True,
                    "structure_reviewed": True,
                }
            )
            _apply_confirmed_score(req, row)
            authoritative_out.append(req)
        return authoritative_out
    out: list[dict[str, Any]] = []
    by_number = {str(row.get("number") or "").strip(): row for row in update_rows if isinstance(row, dict)}
    for index, raw in enumerate(item.get("requirements") or [], start=1):
        if not isinstance(raw, dict):
            continue
        req = dict(raw)
        number = str(req.get("number") or f"{parent_number}.{index}").strip()
        row = by_number.get(number, {})
        question_type = normalize_question_type(row.get("question_type")) if row else ""
        if not question_type:
            question_type = infer_question_type(req) if (req.get("question_type") or req.get("confirmed_question_type")) else parent_type
        req["number"] = number
        req["marker"] = str(req.get("marker") or number)
        req["question_type"] = question_type
        req["confirmed_question_type"] = question_type
        req["type_reviewed"] = number in by_number
        if row:
            _apply_confirmed_score(req, row)
        out.append(req)
    return out


def build_exam_structure_review_request(task_id: str, structured_exam: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for index, item in enumerate(structured_exam.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        if not qid:
            continue
        question_type = infer_question_type(item)
        items.append(
            {
                "question_id": qid,
                "index": index,
                "number": str(item.get("number") or index),
                "section": str(item.get("section") or ""),
                "section_raw": str(item.get("section_raw") or ""),
                "question_type": question_type,
                "extracted_type": question_type,
                "stem": str(item.get("stem") or ""),
                **_review_score_fields(item),
                "question_snapshot_refs": _review_question_snapshot_refs(task_id, item),
                "image_refs": _review_image_refs(task_id, item),
                "subquestions": _review_subquestions(item, question_type),
                "subquestion_count": len(item.get("subquestions") or []),
                "needs_figure": bool(item.get("needs_figure")),
                "drawing_generation_mode": normalize_drawing_mode(item.get("drawing_generation_mode") or "code"),
            }
        )
    return {
        "status": "waiting",
        "request_id": f"exam_structure_review_{int(time.time())}",
        "task_id": task_id,
        "stage": "exam_structure_review",
        "title": "真题结构、题型与分值确认",
        "message": "请确认每道题的题干抽取、题型识别和分值。确认后的题型与分值会用于后续考点判断、教材检索、答案生成、内容审查和 Word 排版。",
        "question_types": list(QUESTION_TYPES),
        "items": items,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def apply_exam_structure_review_updates(structured_exam: dict[str, Any], updates: list[dict[str, Any]]) -> dict[str, Any]:
    by_qid = {str(row.get("question_id") or "").strip(): row for row in updates if isinstance(row, dict)}
    updated = dict(structured_exam)
    items: list[dict[str, Any]] = []
    for raw in structured_exam.get("items") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        qid = str(item.get("question_id") or "").strip()
        update = by_qid.get(qid, {})
        question_type = normalize_question_type(update.get("question_type")) if update else infer_question_type(item)
        if not question_type:
            question_type = infer_question_type(item)
        if update and "stem" in update:
            item["stem"] = str(update.get("stem") or "").strip()
        if update:
            _apply_confirmed_score(item, update)
        if "extracted_section" not in item:
            item["extracted_section"] = str(item.get("section") or "")
        if "extracted_section_raw" not in item:
            item["extracted_section_raw"] = str(item.get("section_raw") or "")
        item["question_type"] = question_type
        item["confirmed_question_type"] = question_type
        item["drawing_generation_mode"] = normalize_drawing_mode(update.get("drawing_generation_mode") if update else item.get("drawing_generation_mode") or "code")
        item["subquestions"] = _apply_subquestion_updates(item, update, question_type)
        major = _major_cn(item)
        if major:
            item["section"] = f"{major}、{question_type}"
            item["section_raw"] = f"{major}、{question_type}"
        else:
            item["section"] = question_type
            item["section_raw"] = question_type
        item["type_reviewed"] = qid in by_qid
        items.append(item)
    updated["items"] = items
    updated["exam_structure_reviewed"] = True
    updated["exam_structure_reviewed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return updated


def get_pending_exam_structure_review(task_id: str) -> dict[str, Any]:
    request = _read_json(exam_structure_request_path(task_id))
    if request.get("status") != "waiting":
        return {"ok": True, "pending": False}
    response = _read_json(exam_structure_response_path(task_id))
    if response.get("request_id") == request.get("request_id") and response.get("decision") in {"confirm", "reject"}:
        return {"ok": True, "pending": False, "response": response}
    return {"ok": True, "pending": True, "request": request}


def submit_exam_structure_review(task_id: str, updates: list[dict[str, Any]], decision: str = "confirm", note: str = "") -> dict[str, Any]:
    decision = str(decision or "confirm").strip()
    if decision not in {"confirm", "reject"}:
        raise ValueError("decision must be confirm or reject")
    if decision == "confirm":
        score_issues = validate_exam_structure_review_updates(updates)
        if score_issues:
            raise ValueError("；".join(score_issues))
    request = _read_json(exam_structure_request_path(task_id))
    request_id = str(request.get("request_id") or "")
    response = {
        "request_id": request_id,
        "decision": decision,
        "updates": updates,
        "note": str(note or "").strip(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    exam_structure_response_path(task_id).write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(task_id, f"exam_structure_review_{decision}", {"request_id": request_id, "updated_count": len(updates), "note": note})
    return {"ok": True, "response": response}


def _automatic_review_row(row: dict[str, Any]) -> dict[str, Any]:
    update: dict[str, Any] = {
        key: row[key]
        for key in ("question_id", "number", "stem", "question_type", "drawing_generation_mode")
        if key in row
    }
    suggested_score = str(row.get("suggested_score") or row.get("score") or "").strip()
    if suggested_score:
        update["confirmed_score"] = suggested_score
        update["score_review_origin"] = "auto"
    for child_key in ("subquestions", "requirements"):
        children = row.get(child_key)
        if isinstance(children, list):
            update[child_key] = [
                _automatic_review_row(child)
                for child in children
                if isinstance(child, dict)
            ]
    return update


def auto_confirm_exam_structure(
    task_id: str,
    structured_exam: dict[str, Any],
    output_json: Path,
) -> dict[str, Any]:
    """Persist an unattended, rule-derived structure decision without pausing."""

    request = build_exam_structure_review_request(task_id, structured_exam)
    request["status"] = "auto_confirmed"
    request["mode"] = "unattended"
    request["human_review_required"] = False
    request["message"] = "结构审计通过后，系统已自动确认题干、题型与可确定的分值。"
    updates = [_automatic_review_row(row) for row in request.get("items", []) if isinstance(row, dict)]
    reviewed = apply_exam_structure_review_updates(structured_exam, updates)
    reviewed["exam_structure_review_mode"] = "unattended"
    reviewed["human_review_required"] = False
    decided_at = time.strftime("%Y-%m-%d %H:%M:%S")
    response = {
        "request_id": request["request_id"],
        "decision": "auto_confirm",
        "mode": "unattended",
        "updates": updates,
        "updated_at": decided_at,
    }
    request["confirmed_at"] = decided_at
    exam_structure_request_path(task_id).write_text(
        json.dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    exam_structure_response_path(task_id).write_text(
        json.dumps(response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_json.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(
        task_id,
        "exam_structure_review_auto_confirmed",
        {
            "question_count": len(reviewed.get("items", [])),
            "request_id": request["request_id"],
            "human_review_required": False,
        },
    )
    return reviewed


def wait_for_exam_structure_review(task_id: str, structured_exam: dict[str, Any], stage_dir: Path, output_json: Path) -> dict[str, Any]:
    request = build_exam_structure_review_request(task_id, structured_exam)
    exam_structure_request_path(task_id).write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(task_id, "exam_structure_review_waiting", {"question_count": len(request.get("items", [])), "request_id": request["request_id"]})
    update_task(task_id, status="paused", current_stage="exam_structure_review", error="等待确认真题结构、题型与分值")
    while True:
        control = read_task_control(task_id)
        if control.get("action") == "cancel":
            update_task(task_id, status="cancelled", current_stage="cancelled", error=control.get("reason") or "用户取消任务")
            raise TaskCancelled(control.get("reason") or "用户取消任务")
        response = _read_json(exam_structure_response_path(task_id))
        if str(response.get("request_id") or "") == request["request_id"]:
            if response.get("decision") == "reject":
                request["status"] = "rejected"
                exam_structure_request_path(task_id).write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
                update_task(task_id, status="failed", current_stage="exam_structure_review", error="用户拒绝真题结构确认")
                raise ExamStructureRejected("用户拒绝真题结构确认")
            if response.get("decision") == "confirm":
                reviewed = apply_exam_structure_review_updates(structured_exam, response.get("updates") or [])
                output_json.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
                request["status"] = "confirmed"
                request["confirmed_at"] = response.get("updated_at")
                exam_structure_request_path(task_id).write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
                update_task(task_id, status="running", current_stage="exam_structure_review", error="")
                append_event(task_id, "exam_structure_review_confirmed", {"question_count": len(reviewed.get("items", []))})
                return reviewed
        time.sleep(1)
