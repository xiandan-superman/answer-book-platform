from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .formula_audit import looks_like_formula
from .question_types import infer_question_type, is_calculation_question, iter_leaf_question_parts, question_has_type, question_kind

PENDING_ANSWERS = {"", "待复核", "待补充", "未完成", "未知"}
FORBIDDEN_PROCESS_PHRASES = [
    "模型无法判断",
    "等待用户补充",
    "本次测试",
    "本题需要人工复核",
    "由于工具限制",
    "请用户",
    "请人工",
]
GENERIC_ANALYSIS_PHRASES = [
    "根据教材可知",
    "根据定义可知",
    "代入公式得",
    "由图可知",
    "显然",
    "易得",
    "A 正确，其他错误",
]
UNIT_RE = re.compile(r"(mol|g|kg|J|kJ|Pa|kPa|MPa|K|℃|V|A|s|m|cm|mm|L|mL|%|N|Hz)\b")
FORMULA_PLACEHOLDER_RE = re.compile(r"\{f\d+\}")
CITATION_LEAK_RE = re.compile(r"(?:教材依据|参考教材|引用依据)\s*[:：]|课本-p\d+", re.IGNORECASE)
SECTION_KIND_KEYS = ("section", "question_type")
SUBQUESTION_HEADING_RE = re.compile(r"^第\s*[（(]?\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]?\s*(?:小问|问)\s*[:：、.．]?")
PAREN_SUBQUESTION_HEADING_RE = re.compile(r"^[（(]\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]")
INLINE_SUBQUESTION_HEADING_RE = re.compile(
    r"(?:^|\n|\s)(?:第\s*[（(]?\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]?\s*(?:小问|问)|[（(]\s*([一二三四五六七八九十0-9]{1,3})\s*[）)])"
)


def _qid(value: dict[str, Any]) -> str:
    return str(value.get("question_id", "")).strip()


def _cn_to_int(text: str) -> int:
    table = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    raw = str(text or "").strip()
    if raw == "十":
        return 10
    if raw.startswith("十"):
        return 10 + table.get(raw[1:], 0)
    if raw.endswith("十"):
        return table.get(raw[0], 0) * 10
    if "十" in raw:
        left, right = raw.split("十", 1)
        return table.get(left, 1) * 10 + table.get(right, 0)
    return table.get(raw, 0)


def _normalize_subquestion_number(value: Any) -> str:
    raw = str(value or "").strip().strip("第小问题（）()：:、.． ")
    if not raw:
        return ""
    if raw.isdigit():
        return str(int(raw))
    number = _cn_to_int(raw)
    return str(number) if number > 0 else raw


def _subquestion_heading_number(text: str) -> str:
    raw = str(text or "").strip()
    match = SUBQUESTION_HEADING_RE.match(raw) or PAREN_SUBQUESTION_HEADING_RE.match(raw)
    return _normalize_subquestion_number(match.group(1)) if match else ""


def _question_subquestion_numbers(question: dict[str, Any]) -> list[str]:
    numbers: list[str] = []
    for index, sub in enumerate(question.get("subquestions") or [], start=1):
        if not isinstance(sub, dict):
            continue
        requirements = [req for req in sub.get("requirements", []) or [] if isinstance(req, dict)]
        if requirements:
            for req_index, req in enumerate(requirements, start=1):
                if infer_question_type(req) != "计算题":
                    continue
                number = _normalize_subquestion_number(req.get("number") or f"{sub.get('number') or index}.{req_index}")
                if number and number not in numbers:
                    numbers.append(number)
            continue
        if infer_question_type(sub) == "计算题":
            number = _normalize_subquestion_number(sub.get("number") or index)
            if number and number not in numbers:
                numbers.append(number)
    return numbers


def _question_answer_unit_numbers(question: dict[str, Any]) -> list[str]:
    numbers: list[str] = []
    for index, sub in enumerate(question.get("subquestions") or [], start=1):
        if not isinstance(sub, dict):
            continue
        base_number = _normalize_subquestion_number(sub.get("number") or index)
        requirements = [req for req in sub.get("requirements", []) or [] if isinstance(req, dict)]
        if requirements:
            for req_index, req in enumerate(requirements, start=1):
                number = _normalize_subquestion_number(req.get("number") or f"{base_number or index}.{req_index}")
                if number and number not in numbers:
                    numbers.append(number)
            continue
        if base_number and base_number not in numbers:
            numbers.append(base_number)
    return numbers


def _question_answer_units(question: dict[str, Any]) -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    for index, sub in enumerate(question.get("subquestions") or [], start=1):
        if not isinstance(sub, dict):
            continue
        base_number = _normalize_subquestion_number(sub.get("number") or index)
        requirements = [req for req in sub.get("requirements", []) or [] if isinstance(req, dict)]
        if requirements:
            for req_index, req in enumerate(requirements, start=1):
                number = _normalize_subquestion_number(req.get("number") or f"{base_number or index}.{req_index}")
                if number:
                    units.append({"number": number, "question_type": infer_question_type(req)})
            continue
        if base_number:
            units.append({"number": base_number, "question_type": infer_question_type(sub)})
    return units


def _answer_unit_has_text_or_formula(unit: dict[str, Any]) -> bool:
    answer = str(unit.get("answer") or "").strip()
    if answer and answer not in PENDING_ANSWERS | {"见解析"} and not looks_like_formula(answer):
        return True
    for segment in unit.get("analysis_segments", []) or []:
        if isinstance(segment, dict) and (str(segment.get("text") or "").strip() or segment.get("formula_indices")):
            return True
        if isinstance(segment, str) and segment.strip():
            return True
    return False


def _answer_unit_has_steps(unit: dict[str, Any]) -> bool:
    for step in unit.get("steps", []) or []:
        if not isinstance(step, dict):
            if str(step).strip():
                return True
            continue
        if (
            str(step.get("text") or "").strip()
            or step.get("formula_indices")
            or step.get("relation_formula_indices")
            or step.get("substitution_formula_indices")
            or step.get("result_formula_indices")
            or str(step.get("result_text") or "").strip()
        ):
            return True
    return False


def _draft_steps_for_audit(draft: dict[str, Any]) -> list[dict[str, Any]]:
    units = draft.get("answer_units") if isinstance(draft.get("answer_units"), list) else []
    if not units:
        return [step for step in draft.get("steps", []) or [] if isinstance(step, dict)]
    out: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        number = _normalize_subquestion_number(unit.get("number"))
        for raw_step in unit.get("steps", []) or []:
            if not isinstance(raw_step, dict):
                continue
            step = dict(raw_step)
            step.setdefault("subquestion_number", number)
            out.append(step)
    return out


def _heading_numbers_in_text(text: str) -> set[str]:
    found: set[str] = set()
    for line in str(text or "").splitlines():
        number = _subquestion_heading_number(line)
        if number:
            found.add(number)
    for match in INLINE_SUBQUESTION_HEADING_RE.finditer(str(text or "")):
        raw = match.group(1) or match.group(2)
        number = _normalize_subquestion_number(raw)
        if number:
            found.add(number)
    return found


def _typed_leaf_parts(question: dict[str, Any], question_type: str) -> list[dict[str, Any]]:
    parts = [part for part in iter_leaf_question_parts(question) if infer_question_type(part) == question_type]
    if parts:
        return parts
    return [question] if question_has_type(question, question_type) else []


def _text_from_segments(node: Any) -> str:
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "text":
                parts.append(str(value.get("text", "")))
            else:
                for child in value.values():
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return "\n".join(part for part in parts if part.strip())


def _block_text(fragment: dict[str, Any], label: str) -> str:
    for block in fragment.get("blocks", []):
        if str(block.get("label", "")).strip() == label:
            return _text_from_segments(block.get("segments", []))
    return ""


def _block_segments(fragment: dict[str, Any], label: str) -> list[dict[str, Any]]:
    for block in fragment.get("blocks", []):
        if str(block.get("label", "")).strip() == label:
            return [seg for seg in block.get("segments", []) if isinstance(seg, dict)]
    return []


def _has_block(fragment: dict[str, Any], label: str) -> bool:
    return any(str(block.get("label", "")).strip() == label for block in fragment.get("blocks", []))


def _has_segment_type(fragment: dict[str, Any], segment_type: str) -> bool:
    return any(
        isinstance(seg, dict) and str(seg.get("type", "")) == segment_type
        for block in fragment.get("blocks", [])
        for seg in block.get("segments", [])
    )


def _needs_figure(question: dict[str, Any]) -> bool:
    return question_has_type(question, "作图题")


def _selected_and_rejected(selection: dict[str, Any] | None) -> tuple[set[str], set[str]]:
    selected: set[str] = set()
    rejected: set[str] = set()
    if not isinstance(selection, dict):
        return selected, rejected
    for point in selection.get("knowledge_points", []):
        selected.update(str(x).strip() for x in point.get("selected_evidence_ids", []) if str(x).strip())
        rejected.update(str(x).strip() for x in point.get("rejected_evidence_ids", []) if str(x).strip())
    return selected, rejected


def _evidence_selection_conflicts(selection: dict[str, Any] | None, bound_evidence: set[str]) -> tuple[list[str], list[str]]:
    selected, rejected = _selected_and_rejected(selection)
    missing_selected = sorted(selected - bound_evidence)
    # 同一证据可能支撑一个考查点，同时不支撑另一个考查点；只要它在本题任一考查点被选中，
    # 就不应因为另一个考查点的 rejected 列表而误判为引用了拒绝证据。
    used_rejected = sorted(bound_evidence & (rejected - selected))
    return missing_selected, used_rejected


def _entry(qid: str, code: str, message: str, severity: str) -> dict[str, str]:
    return {"question_id": qid, "code": code, "message": message, "severity": severity}


def _has_review_flag(fragment: dict[str, Any], code: str) -> bool:
    return any(isinstance(flag, dict) and str(flag.get("code", "")) == code for flag in fragment.get("_review_flags", []))


def audit_content_quality(
    structured_exam: dict[str, Any],
    fragments_data: dict[str, Any],
    answer_drafts_data: dict[str, Any] | None = None,
    evidence_selection_data: dict[str, Any] | None = None,
    output_json: Path | None = None,
) -> dict[str, Any]:
    items = [_ for _ in structured_exam.get("items", []) if _qid(_)]
    fragments_by_id = {_qid(fragment): fragment for fragment in fragments_data.get("fragments", []) if _qid(fragment)}
    drafts_by_id = {_qid(draft): draft for draft in (answer_drafts_data or {}).get("drafts", []) if _qid(draft)}
    selections_by_id = {_qid(selection): selection for selection in (evidence_selection_data or {}).get("selections", []) if _qid(selection)}

    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    by_question: list[dict[str, Any]] = []
    mistake_note_texts: list[tuple[str, str]] = []

    for question in items:
        qid = _qid(question)
        kind = question_kind(question)
        has_calculation_part = is_calculation_question(question)
        fragment = fragments_by_id.get(qid)
        draft = drafts_by_id.get(qid)
        q_issues: list[dict[str, str]] = []
        q_warnings: list[dict[str, str]] = []

        def issue(code: str, message: str) -> None:
            item = _entry(qid, code, message, "issue")
            issues.append(item)
            q_issues.append(item)

        def warning(code: str, message: str) -> None:
            item = _entry(qid, code, message, "warning")
            warnings.append(item)
            q_warnings.append(item)

        if fragment is None:
            issue("missing_fragment", "缺少本题最终结构化解析。")
            by_question.append({"question_id": qid, "issues": q_issues, "warnings": q_warnings})
            continue
        if draft is None:
            issue("missing_draft", "缺少模型原始解析草稿，无法审计解析生成质量。")
            draft = {}

        answer = str(fragment.get("answer", "") or draft.get("answer", "")).strip()
        answer_summary = str(fragment.get("answer_summary", "") or draft.get("answer", "")).strip()
        if answer in PENDING_ANSWERS or (kind == "term_explanation" and answer == "见解析"):
            issue("missing_answer", "答案为空或仍为待复核状态。")

        analysis_text = _block_text(fragment, "解析")
        if kind == "term_explanation":
            pass
        elif not analysis_text.strip():
            issue("missing_analysis", "缺少【解析】内容。")
        elif len(analysis_text.strip()) < 20:
            warning("short_analysis", "【解析】过短，可能没有说明本题推理过程。")
        expected_units = _question_answer_units(question)
        if len(expected_units) >= 2:
            raw_units = fragment.get("answer_units") if isinstance(fragment.get("answer_units"), list) else []
            units_by_number = {
                _normalize_subquestion_number(unit.get("number")): unit
                for unit in raw_units
                if isinstance(unit, dict) and _normalize_subquestion_number(unit.get("number"))
            }
            missing_units: list[str] = []
            missing_calculation_steps: list[str] = []
            for expected in expected_units:
                number = expected["number"]
                unit = units_by_number.get(number)
                if not isinstance(unit, dict) or not _answer_unit_has_text_or_formula(unit) and not _answer_unit_has_steps(unit):
                    missing_units.append(number)
                    continue
                if expected.get("question_type") == "计算题" and not _answer_unit_has_steps(unit):
                    missing_calculation_steps.append(number)
            if missing_units:
                issue(
                    "missing_answer_unit_content",
                    f"用户确认了{len(expected_units)}个作答单元，但第{', '.join(missing_units)}小问没有可渲染的答案内容。",
                )
            if missing_calculation_steps:
                issue(
                    "missing_answer_unit_steps",
                    f"计算作答单元第{', '.join(missing_calculation_steps)}小问缺少结构化解题步骤。",
                )

        all_text = "\n".join(
            [
                answer,
                analysis_text,
                _block_text(fragment, "选项分析"),
                _block_text(fragment, "解题步骤"),
                _block_text(fragment, "易错点及注意事项"),
                str(draft.get("analysis", "")),
            ]
        )
        for phrase in FORBIDDEN_PROCESS_PHRASES:
            if phrase in all_text:
                issue("forbidden_process_text", f"最终解析或草稿中包含流程性话术：{phrase}")
                break
        for phrase in GENERIC_ANALYSIS_PHRASES:
            if phrase in all_text:
                warning("generic_analysis_phrase", f"解析中包含空泛表达：{phrase}")
                break
        if FORMULA_PLACEHOLDER_RE.search(all_text):
            issue("unresolved_formula_placeholder", "正文中残留 {f数字} 公式占位符，说明公式未正确转换为公式对象。")
        if CITATION_LEAK_RE.search(all_text):
            issue("citation_leaked_into_answer", "教材依据或课本页码混入了解析/答案正文，应只出现在【教材依据】块。")

        bound_evidence = {str(x).strip() for x in fragment.get("evidence_ids", []) if str(x).strip()}
        missing_selected, used_rejected = _evidence_selection_conflicts(selections_by_id.get(qid), bound_evidence)
        if missing_selected:
            issue("missing_confirmed_evidence", "模型二次确认的教材依据未全部进入本题引用。")
        if used_rejected:
            issue("uses_rejected_evidence", "本题引用了模型已拒绝的候选依据。")

        if kind == "choice":
            option_map = draft.get("option_analysis") if isinstance(draft.get("option_analysis"), dict) else {}
            if not _has_block(fragment, "选项分析") and not option_map:
                issue("choice_missing_option_analysis", "选择题缺少选项辨析。")

        if has_calculation_part:
            if answer == "见解析" and not answer_summary:
                issue("missing_answer_summary", "计算题顶层答案为“见解析”，但缺少可直接展示的答案摘要。")
            if not fragment.get("formulas") and not draft.get("formulas") and not _has_review_flag(fragment, "formula_absence_after_retry"):
                issue("calculation_missing_formula", "计算题缺少关键关系式或公式对象。")
            elif not fragment.get("formulas") and _has_review_flag(fragment, "formula_absence_after_retry"):
                warning("formula_absence_after_retry", "按题型应有公式，模型二次生成仍未给出公式，已特殊放行并进入存疑题目审查文档。")
            steps = _draft_steps_for_audit(draft)
            if not _has_block(fragment, "解题步骤") and not steps:
                issue("calculation_missing_steps", "计算题缺少可复核的解题步骤或代入逻辑。")
            valid_subquestions = _question_subquestion_numbers(question)
            if len(valid_subquestions) >= 2:
                covered_subquestions: set[str] = set()
                invalid_subquestions: list[str] = []
                heading_in_text = False
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    parsed_number = _subquestion_heading_number(str(step.get("text") or ""))
                    if parsed_number:
                        heading_in_text = True
                    number = _normalize_subquestion_number(step.get("subquestion_number")) or parsed_number
                    if not number:
                        continue
                    if number in valid_subquestions:
                        covered_subquestions.add(number)
                    elif number not in invalid_subquestions:
                        invalid_subquestions.append(number)
                missing_subquestions = [number for number in valid_subquestions if number not in covered_subquestions]
                if missing_subquestions:
                    issue(
                        "calculation_missing_subquestion_steps",
                        f"多小问计算题缺少第{', '.join(missing_subquestions)}小问的结构化解题步骤。",
                    )
                if invalid_subquestions:
                    issue(
                        "calculation_invalid_subquestion_number",
                        f"计算步骤引用了题目中不存在的小问编号：{', '.join(invalid_subquestions)}。",
                    )
                if heading_in_text:
                    warning("calculation_step_text_contains_subquestion_heading", "计算步骤 text 中仍包含小问标题，程序会尝试纠偏；后续应改为 subquestion_number 字段。")
            step_formula_refs = [seg for seg in _block_segments(fragment, "解题步骤") if seg.get("type") == "formula_ref"]
            analysis_formula_refs = [seg for seg in _block_segments(fragment, "解析") if seg.get("type") == "formula_ref"]
            if fragment.get("formulas") and not step_formula_refs:
                issue("calculation_steps_missing_formula_refs", "计算题公式未进入【解题步骤】，解题步骤仍偏文字描述。")
            if analysis_formula_refs and len(analysis_formula_refs) >= len(fragment.get("formulas", [])):
                issue("calculation_formula_dumped_in_analysis", "计算题公式集中堆在【解析】中，未按步骤展开。")
            step_text = _block_text(fragment, "解题步骤")
            if "关系式与代入：" in step_text or "补充关系式：" in step_text:
                issue("calculation_formula_dumped_in_steps", "计算题公式在【解题步骤】中仍以成组堆放方式出现，未按关系式、代入、结果逐步展开。")
            if fragment.get("formulas") and steps:
                has_substitution = any(
                    isinstance(step, dict) and step.get("substitution_formula_indices")
                    for step in steps
                )
                has_relation = any(
                    isinstance(step, dict) and (step.get("relation_formula_indices") or step.get("formula_indices") or step.get("formulas"))
                    for step in steps
                )
                has_substitution_or_result = any(
                    isinstance(step, dict)
                    and (
                        step.get("substitution_formula_indices")
                        or step.get("result_formula_indices")
                        or str(step.get("result_text") or "").strip()
                    )
                    for step in steps
                )
                if not has_substitution:
                    issue("calculation_missing_substitution", "计算题缺少代入数据的公式或表达式，不能只给关系式和最终结果。")
                if not has_relation or not has_substitution_or_result:
                    issue("calculation_steps_not_sequential", "计算题步骤缺少“关系式 -> 代入 -> 结果”的分步结构。")
            mistake_text = _block_text(fragment, "易错点及注意事项")
            draft_mistakes = draft.get("mistake_notes") if isinstance(draft.get("mistake_notes"), list) else []
            if not mistake_text.strip() and not draft_mistakes:
                issue("calculation_missing_mistake_notes", "计算题缺少针对本题的易错点及注意事项。")
            if answer and answer not in PENDING_ANSWERS and re.search(r"\d", answer) and not UNIT_RE.search(answer):
                warning("calculation_answer_missing_unit", "计算题数值答案可能缺少单位。")

        if kind in {"short_answer", "graphic", "mixed"} and answer == "见解析" and not answer_summary:
            warning("missing_answer_summary", "本题答案为“见解析”，建议提供一句可直接展示的答案摘要。")

        if not has_calculation_part and _has_block(fragment, "待复核公式"):
            warning("noncalculation_unintegrated_formulas", "存在未自然融入解析正文的公式，已列入待复核公式并进入审查文档。")

        if _needs_figure(question) and not _has_segment_type(fragment, "image_ref"):
            issue("missing_required_figure", "题目存在作图或图示需求，但最终解析未插入图片。")

        mistake_text = _block_text(fragment, "易错点及注意事项").strip()
        if mistake_text:
            mistake_note_texts.append((qid, mistake_text))

        by_question.append({"question_id": qid, "issues": q_issues, "warnings": q_warnings})

    note_counter = Counter(text for _, text in mistake_note_texts)
    for qid, text in mistake_note_texts:
        if note_counter[text] > 1:
            warnings.append(_entry(qid, "duplicated_mistake_note", "易错点及注意事项疑似多题复用同一句模板。", "warning"))

    report = {
        "ok": not issues,
        "question_count": len(items),
        "checked_count": sum(1 for item in by_question if not any(issue["code"] == "missing_fragment" for issue in item.get("issues", []))),
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
        "by_question": by_question,
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
