from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .text_utils import clean_text


TRACE_COLUMNS = [
    "题目ID",
    "题型题号",
    "原题",
    "最终确定考查点",
    "模型检索关键词",
    "程序候选依据数量",
    "程序候选依据Top5",
    "模型选中依据",
    "模型选中依据支持类型",
    "模型非直接依据标记",
    "模型第一次放弃依据",
    "模型第一次选择/放弃理由",
    "扩检/无依据问题",
    "是否二次扩检",
    "扩检前候选数",
    "扩检后候选数",
    "扩检新增数",
    "模型二次扩检后选中依据",
    "模型二次选中依据支持类型",
    "模型二次非直接依据标记",
    "模型二次扩检放弃依据",
    "模型二次选择/放弃理由",
    "扩检后仍未解决",
    "最终非直接教材依据标记",
    "最终教材依据部分",
]

SUPPORT_TYPE_LABELS = {
    "direct_support": "直接证据",
    "general_principle_support": "通用原理证据",
    "transferable_support": "可迁移证据",
    "inverse_process_support": "反向过程证据",
    "background_only": "背景材料",
    "keyword_only": "仅关键词相似",
}
NON_DIRECT_SUPPORT_TYPES = {
    "general_principle_support",
    "transferable_support",
    "inverse_process_support",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(x) for x in value]
    else:
        values = []
    out: list[str] = []
    for item in values:
        text = clean_text(item)
        if text and text not in out:
            out.append(text)
    return out


def _points(selection: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(selection, dict):
        return []
    return [point for point in selection.get("knowledge_points", []) if isinstance(point, dict)]


def _selected_ids(selection: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for point in _points(selection):
        for evidence_id in _strings(point.get("selected_evidence_ids")):
            if evidence_id not in out:
                out.append(evidence_id)
    return out


def _rejected_ids(selection: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for point in _points(selection):
        for evidence_id in _strings(point.get("rejected_evidence_ids")):
            if evidence_id not in out:
                out.append(evidence_id)
    return out


def _reasons(selection: dict[str, Any] | None) -> str:
    parts: list[str] = []
    for point in _points(selection):
        name = clean_text(point.get("knowledge_point") or "考查点")
        reason = clean_text(point.get("reason") or "")
        no_reason = clean_text(point.get("no_suitable_evidence_reason") or "")
        if reason:
            parts.append(f"{name}：{reason}")
        if no_reason:
            parts.append(f"{name}：未确认依据，{no_reason}")
    return "\n".join(parts)


def _support_type(value: Any, default: str = "direct_support") -> str:
    text = str(value or "").strip()
    return text if text in SUPPORT_TYPE_LABELS else default


def _support_type_summary(selection: dict[str, Any] | None) -> str:
    parts: list[str] = []
    for point in _points(selection):
        name = clean_text(point.get("knowledge_point") or "考查点")
        selected_ids = _strings(point.get("selected_evidence_ids"))
        if not selected_ids:
            continue
        raw_map = point.get("evidence_support_types") if isinstance(point.get("evidence_support_types"), dict) else {}
        point_type = _support_type(point.get("support_type"))
        id_parts = []
        for evidence_id in selected_ids:
            support_type = _support_type(raw_map.get(evidence_id) or point_type)
            id_parts.append(f"{evidence_id}={SUPPORT_TYPE_LABELS[support_type]}")
        if id_parts:
            parts.append(f"{name}：" + "；".join(id_parts))
    return "\n".join(parts)


def _non_direct_summary(selection: dict[str, Any] | None) -> str:
    parts: list[str] = []
    for point in _points(selection):
        name = clean_text(point.get("knowledge_point") or "考查点")
        selected_ids = _strings(point.get("selected_evidence_ids"))
        if not selected_ids:
            continue
        raw_map = point.get("evidence_support_types") if isinstance(point.get("evidence_support_types"), dict) else {}
        point_type = _support_type(point.get("support_type"))
        id_parts = []
        for evidence_id in selected_ids:
            support_type = _support_type(raw_map.get(evidence_id) or point_type)
            if support_type in NON_DIRECT_SUPPORT_TYPES:
                id_parts.append(f"{evidence_id}={SUPPORT_TYPE_LABELS[support_type]}")
        if id_parts:
            parts.append(f"{name}：" + "；".join(id_parts))
    return "\n".join(parts)


def _unresolved(selection: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for point in _points(selection):
        name = clean_text(point.get("knowledge_point") or "考查点")
        no_reason = clean_text(point.get("no_suitable_evidence_reason") or "")
        if point.get("needs_expansion") or not _strings(point.get("selected_evidence_ids")):
            out.append(f"{name}{f'：{no_reason}' if no_reason else ''}")
    return out


def _candidate_label(row: dict[str, Any]) -> str:
    evidence_id = str(row.get("evidence_id") or "").strip()
    textbook = str(row.get("citation_textbook") or row.get("textbook") or "").strip()
    section = str(row.get("chapter_section") or "").strip()
    page = str(row.get("printed_page") or row.get("pdf_page_idx") or "").strip()
    score = str(row.get("score") or "").strip()
    text = clean_text(row.get("evidence_text") or "")[:180]
    location = " ".join(part for part in [textbook, section, f"p{page}" if page else ""] if part)
    score_text = f" score={score}" if score else ""
    return f"{evidence_id}｜{location}{score_text}｜{text}".strip("｜")


def _candidate_text(rows: list[dict[str, Any]], ids: list[str] | None = None, limit: int | None = None) -> str:
    selected_rows = rows
    if ids is not None:
        id_set = set(ids)
        by_id = {str(row.get("evidence_id") or ""): row for row in rows}
        selected_rows = [by_id[evidence_id] for evidence_id in ids if evidence_id in by_id]
        selected_rows.extend(row for row in rows if str(row.get("evidence_id") or "") in id_set and row not in selected_rows)
    if limit is not None:
        selected_rows = selected_rows[:limit]
    return "\n".join(_candidate_label(row) for row in selected_rows)


def _candidate_score(row: dict[str, Any]) -> float:
    try:
        return float(row.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _ranked_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one globally ranked row per evidence ID for the trace's Top5 column."""
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        evidence_id = str(row.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        previous = unique.get(evidence_id)
        if previous is None or _candidate_score(row) > _candidate_score(previous):
            unique[evidence_id] = row
    return sorted(
        unique.values(),
        key=lambda row: (-_candidate_score(row), str(row.get("evidence_id") or "")),
    )


def _question_label(question: dict[str, Any]) -> str:
    section = clean_text(question.get("section") or question.get("section_raw") or "")
    number = clean_text(question.get("number") or "")
    if section and number:
        return f"{section} 第{number}题"
    return section or number


def _plan_keywords(plan: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("search_queries", "key_terms", "formulas"):
        parts.extend(_strings(plan.get(key)))
    return "；".join(parts)


def build_evidence_trace_rows(stage_dir: Path) -> list[dict[str, Any]]:
    structured_exam = read_json(stage_dir / "structured_exam.json")
    plans_data = read_json(stage_dir / "knowledge_plans.json")
    selection_data = read_json(stage_dir / "evidence_selection.json")
    initial_candidates = read_csv(stage_dir / "retrieval_candidates.csv")
    expanded_candidates = read_csv(stage_dir / "retrieval_candidates.expanded.csv")

    plans = {
        str(plan.get("question_id") or ""): plan
        for plan in plans_data.get("plans", [])
        if isinstance(plan, dict)
    }
    selections = {
        str(selection.get("question_id") or ""): selection
        for selection in selection_data.get("selections", [])
        if isinstance(selection, dict)
    }
    initial_by_qid: dict[str, list[dict[str, Any]]] = {}
    expanded_by_qid: dict[str, list[dict[str, Any]]] = {}
    for row in initial_candidates:
        initial_by_qid.setdefault(str(row.get("question_id") or ""), []).append(row)
    for row in expanded_candidates:
        expanded_by_qid.setdefault(str(row.get("question_id") or ""), []).append(row)

    rows: list[dict[str, Any]] = []
    for question in structured_exam.get("items", []):
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        plan = plans.get(qid, {})
        selection = selections.get(qid, {})
        trace = selection.get("_trace") if isinstance(selection, dict) else {}
        first_selection = trace.get("first_selection") if isinstance(trace, dict) else None
        expansion_selection = trace.get("expansion_selection") if isinstance(trace, dict) else None
        first_selection = first_selection if isinstance(first_selection, dict) else selection
        expansion_selection = expansion_selection if isinstance(expansion_selection, dict) else None

        initial_rows = initial_by_qid.get(qid, [])
        expanded_rows = expanded_by_qid.get(qid, [])
        initial_ids = {str(row.get("evidence_id") or "") for row in initial_rows}
        expanded_ids = {str(row.get("evidence_id") or "") for row in expanded_rows}
        all_rows = initial_rows + [row for row in expanded_rows if str(row.get("evidence_id") or "") not in initial_ids]
        final_points = [clean_text(point.get("knowledge_point") or "") for point in _points(selection)]
        final_citations = []
        for group in selection.get("citation_groups", []) if isinstance(selection, dict) else []:
            if isinstance(group, dict) and clean_text(group.get("citation") or ""):
                final_citations.append(clean_text(group.get("citation") or ""))
        expanded = bool(expansion_selection) or bool(expanded_rows) or qid in set(selection_data.get("expanded_question_ids", []))
        rows.append(
            {
                "题目ID": qid,
                "题型题号": _question_label(question),
                "原题": clean_text(question.get("stem") or ""),
                "最终确定考查点": "；".join(point for point in final_points if point) or "；".join(_strings(plan.get("knowledge_points"))),
                "模型检索关键词": _plan_keywords(plan),
                "程序候选依据数量": len(initial_rows),
                "程序候选依据Top5": _candidate_text(_ranked_candidate_rows(initial_rows), limit=5),
                "模型选中依据": _candidate_text(all_rows, _selected_ids(first_selection)),
                "模型选中依据支持类型": _support_type_summary(first_selection),
                "模型非直接依据标记": _non_direct_summary(first_selection),
                "模型第一次放弃依据": _candidate_text(all_rows, _rejected_ids(first_selection)),
                "模型第一次选择/放弃理由": _reasons(first_selection),
                "扩检/无依据问题": "\n".join(_unresolved(first_selection)),
                "是否二次扩检": "是" if expanded else "否",
                "扩检前候选数": len(initial_rows),
                "扩检后候选数": len(initial_ids | expanded_ids) if expanded else "",
                "扩检新增数": len(expanded_ids - initial_ids) if expanded else "",
                "模型二次扩检后选中依据": _candidate_text(all_rows, _selected_ids(expansion_selection)) if expansion_selection else "",
                "模型二次选中依据支持类型": _support_type_summary(expansion_selection) if expansion_selection else "",
                "模型二次非直接依据标记": _non_direct_summary(expansion_selection) if expansion_selection else "",
                "模型二次扩检放弃依据": _candidate_text(all_rows, _rejected_ids(expansion_selection)) if expansion_selection else "",
                "模型二次选择/放弃理由": _reasons(expansion_selection) if expansion_selection else "",
                "扩检后仍未解决": "\n".join(_unresolved(selection)) if expanded else "",
                "最终非直接教材依据标记": _non_direct_summary(selection),
                "最终教材依据部分": "\n".join(final_citations),
            }
        )
    return rows


def write_evidence_trace_csv(stage_dir: Path, output_csv: Path | None = None) -> Path:
    output_csv = output_csv or (stage_dir / "题目依据排查.csv")
    rows = build_evidence_trace_rows(stage_dir)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=TRACE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in TRACE_COLUMNS})
    return output_csv
