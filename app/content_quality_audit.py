from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .calculation_consistency import calculation_draft_consistency_issues
from .capabilities.catalog import capability_policy_contributions
from .formula_audit import looks_like_formula
from .question_requirements import answer_figure_required
from .question_types import infer_question_type, is_calculation_question, iter_leaf_question_parts, question_has_type, question_kind
from .user_facing_text import contains_internal_repair_provenance

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
# Symbol units such as % and ℃ have no trailing word boundary. Keep them
# outside the alphabetic-unit boundary so percentage answers are recognized.
UNIT_RE = re.compile(r"(?:%|‰|℃|(?:mol|g|kg|J|kJ|Pa|kPa|MPa|K|V|A|s|m|cm|mm|L|mL|N|Hz)\b)")
SPECIFIC_PHYSICAL_UNIT_RE = re.compile(
    r"^(?:%|‰|℃|mol|g|kg|J|kJ|Pa|kPa|MPa|K|V|A|s|m|cm|mm|L|mL|N|Hz)$"
)
FORMULA_PLACEHOLDER_RE = re.compile(r"\{f\d+\}")
CITATION_LEAK_RE = re.compile(r"(?:教材依据|参考教材|引用依据)\s*[:：]|课本-p\d+", re.IGNORECASE)
SECTION_KIND_KEYS = ("section", "question_type")
SUBQUESTION_HEADING_RE = re.compile(r"^第\s*[（(]?\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]?\s*(?:小问|问)\s*[:：、.．]?")
PAREN_SUBQUESTION_HEADING_RE = re.compile(r"^[（(]\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]")
INLINE_SUBQUESTION_HEADING_RE = re.compile(
    r"(?:^|\n|\s)(?:第\s*[（(]?\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]?\s*(?:小问|问)|[（(]\s*([一二三四五六七八九十0-9]{1,3})\s*[）)])"
)
COMPARATIVE_PROPERTY_RE = re.compile(
    r"([A-Za-z\u4e00-\u9fff]{1,12}(?:性|率|度|量|能|系数|时间|速度|浓度|成本|误差))"
    r"[^，。；;\n]{0,8}?"
    r"(更高|较高|略高|稍高|提高|上升|增加|增强|较好|更好|优于|"
    r"更低|较低|略低|稍低|降低|下降|减少|减弱|较差|更差|劣于)"
)
POSITIVE_COMPARATIVES = {"更高", "较高", "略高", "稍高", "提高", "上升", "增加", "增强", "较好", "更好", "优于"}
CANONICAL_PROPERTY_TERMS = (
    "拉伸强度", "屈服强度", "抗压强度", "强度", "硬度", "塑性", "韧性", "延伸率", "收缩率",
    "导电率", "电阻率", "导热率", "浓度", "密度", "精度", "误差", "成本", "时间", "速度",
)
COMPARISON_SUBJECT_RE = re.compile(
    r"工艺[A-Za-z0-9甲乙丙丁一二三四五六七八九十]+|"
    r"方案[A-Za-z0-9甲乙丙丁一二三四五六七八九十]+|"
    r"材料[A-Za-z0-9甲乙丙丁一二三四五六七八九十]+|"
    r"(?:[A-Za-z0-9一-鿿]{1,8})型"
)
SPATIAL_MEMBERSHIP_INFERENCE_RE = re.compile(
    r"(?:包含|含有|含|归入|属于|纳入)[^。；;\n]{0,12}"
    r"(?:依附|附着|包覆|嵌入|邻接)[^。；;\n]{0,40}"
)


def _calculation_has_high_confidence_missing_unit(fragment: dict[str, Any], draft: dict[str, Any]) -> bool:
    """Flag only a numeric physical result with an explicitly declared unit.

    Question numbers, chemical indices, symbolic lattice results such as ``a/2``,
    and dimensionless values must not trigger a generic missing-unit warning.
    """

    contract = fragment.get("calculation_contract")
    if not isinstance(contract, dict):
        contract = draft.get("calculation_contract")
    if not isinstance(contract, dict):
        return False
    visible_text = " ".join(
        [
            str(fragment.get("answer") or ""),
            *[
                str(unit.get("answer") or "")
                for unit in fragment.get("answer_units", []) or []
                if isinstance(unit, dict)
            ],
            *[
                str(step.get("result_text") or "")
                for step in fragment.get("steps", []) or []
                if isinstance(step, dict)
            ],
        ]
    )
    for result in contract.get("result_quantities", []) or []:
        if not isinstance(result, dict):
            continue
        value = result.get("value")
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if isinstance(value, str):
            numeric = bool(re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()))
        unit = str(result.get("unit") or "").strip()
        if numeric and SPECIFIC_PHYSICAL_UNIT_RE.fullmatch(unit) and unit not in visible_text:
            return True
    return False


def _comparative_property_directions(text: Any) -> dict[str, set[int]]:
    directions: dict[str, set[int]] = {}
    for property_name, direction in COMPARATIVE_PROPERTY_RE.findall(str(text or "")):
        normalized = re.sub(r"^(?:表现为|因此|同时|其|且|和|与|也)", "", property_name).strip()
        canonical = next((term for term in CANONICAL_PROPERTY_TERMS if term in normalized), "")
        normalized = canonical or normalized
        if normalized:
            directions.setdefault(normalized, set()).add(1 if direction in POSITIVE_COMPARATIVES else -1)
    return directions


def _answer_unit_comparative_contradictions(unit: dict[str, Any]) -> list[str]:
    answer_text = str(unit.get("answer") or "")
    analysis_text = _text_from_segments(unit.get("analysis_segments", []))
    answer_subject = COMPARISON_SUBJECT_RE.search(answer_text)
    analysis_subject = COMPARISON_SUBJECT_RE.search(analysis_text)
    # Direction words alone are not enough: ``A更高`` and ``B更低`` may state
    # the same comparison.  Only enforce a contradiction when both passages
    # explicitly lead with the same named comparison subject.  Ambiguous prose
    # remains advisory territory for selective review rather than a hard gate.
    if not answer_subject or not analysis_subject or answer_subject.group() != analysis_subject.group():
        return []
    answer_directions = _comparative_property_directions(answer_text)
    analysis_directions = _comparative_property_directions(analysis_text)
    return sorted(
        property_name
        for property_name in answer_directions.keys() & analysis_directions.keys()
        if len(answer_directions[property_name]) == 1
        and len(analysis_directions[property_name]) == 1
        and answer_directions[property_name] != analysis_directions[property_name]
    )


def _split_top_level_composition(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in str(text or ""):
        if char in "（([【":
            depth += 1
        elif char in "）)]】":
            depth = max(0, depth - 1)
        if depth == 0 and char in "+＋":
            part = "".join(current).strip(" ，、:：")
            if part:
                parts.append(part)
            current = []
        else:
            current.append(char)
    part = "".join(current).strip(" ，、:：")
    if part:
        parts.append(part)
    return parts


def _composition_partition_omissions(fragment: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    """Find a declared same-level constituent omitted by its numeric partition.

    This deliberately covers only a narrow, machine-verifiable case: prose
    explicitly declares three or more ``A+B+C`` constituents; the calculation
    ledger partitions the same requested composition, matches at least two of
    those names, yet omits another declared constituent.  It does not judge
    alternative terminology, rounding, or nested forms such as eutectic (α+β).
    """

    contract = fragment.get("calculation_contract")
    if not isinstance(contract, dict):
        contract = draft.get("calculation_contract")
    if not isinstance(contract, dict):
        return []
    requested_text = " ".join(
        str(item.get("request_text") or "")
        for item in contract.get("requested_outputs", []) or []
        if isinstance(item, dict)
    )
    if "组成" not in requested_text:
        return []
    quantities = {
        str(item.get("quantity_id") or "").strip(): re.sub(
            r"(?:质量分数|摩尔分数|体积分数|百分比|分数)$", "", str(item.get("name") or "").strip()
        ).strip()
        for item in contract.get("result_quantities", []) or []
        if isinstance(item, dict) and str(item.get("quantity_id") or "").strip()
    }
    answer_text = "\n".join(
        str(unit.get("answer") or "")
        for unit in fragment.get("answer_units", []) or []
        if isinstance(unit, dict)
    ) or str(fragment.get("answer") or "")
    declared_groups = []
    for match in re.finditer(r"(?:组织(?:组成)?|成分组成|组成)\s*(?:为|是|包括)\s*([^。；;\n]{3,120})", answer_text):
        parts = _split_top_level_composition(match.group(1))
        if len(parts) >= 3:
            declared_groups.append(parts)
    omissions: set[str] = set()
    for partition in contract.get("partitions", []) or []:
        if not isinstance(partition, dict):
            continue
        partition_names = [
            quantities.get(str(quantity_id or "").strip(), "")
            for quantity_id in partition.get("component_quantity_ids", []) or []
        ]
        partition_names = [name for name in partition_names if name]
        if len(partition_names) < 2:
            continue
        for declared in declared_groups:
            matched = {
                index
                for index, part in enumerate(declared)
                if any(name in part or part in name for name in partition_names)
            }
            if len(matched) < 2 or not all(any(name in part or part in name for part in declared) for name in partition_names):
                continue
            omissions.update(declared[index] for index in range(len(declared)) if index not in matched)
    return sorted(omissions)


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
            # Answer units may carry the same segment contract before the
            # renderer has added an explicit ``type=text`` discriminator.
            # Treat a direct text field as prose, while still walking the
            # remaining children for nested segment containers.
            if "text" in value and str(value.get("text") or "").strip():
                parts.append(str(value.get("text", "")))
            for key, child in value.items():
                if key != "text":
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
    return answer_figure_required(question)


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
    *,
    draft_optional_question_ids: set[str] | None = None,
    active_figure_specs_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = [_ for _ in structured_exam.get("items", []) if _qid(_)]
    fragments_by_id = {_qid(fragment): fragment for fragment in fragments_data.get("fragments", []) if _qid(fragment)}
    drafts_by_id = {_qid(draft): draft for draft in (answer_drafts_data or {}).get("drafts", []) if _qid(draft)}
    selections_by_id = {_qid(selection): selection for selection in (evidence_selection_data or {}).get("selections", []) if _qid(selection)}
    active_figure_specs_by_id: dict[str, list[dict[str, Any]]] = {}
    for spec in (active_figure_specs_data or {}).get("figures", []) or []:
        if not isinstance(spec, dict):
            continue
        spec_qid = str(spec.get("question_id") or "").strip()
        if spec_qid:
            active_figure_specs_by_id.setdefault(spec_qid, []).append(spec)
    draft_optional_question_ids = {
        str(question_id).strip()
        for question_id in (draft_optional_question_ids or set())
        if str(question_id).strip()
    }

    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    diagnostics: list[dict[str, str]] = []
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
        q_diagnostics: list[dict[str, str]] = []

        def issue(
            code: str,
            message: str,
            *,
            question_id: str = qid,
            question_issues: list[dict[str, str]] = q_issues,
        ) -> None:
            item = _entry(question_id, code, message, "issue")
            issues.append(item)
            question_issues.append(item)

        def warning(
            code: str,
            message: str,
            *,
            question_id: str = qid,
            question_warnings: list[dict[str, str]] = q_warnings,
        ) -> None:
            item = _entry(question_id, code, message, "warning")
            warnings.append(item)
            question_warnings.append(item)

        def diagnostic(
            code: str,
            message: str,
            *,
            question_id: str = qid,
            question_diagnostics: list[dict[str, str]] = q_diagnostics,
        ) -> None:
            item = _entry(question_id, code, message, "diagnostic")
            diagnostics.append(item)
            question_diagnostics.append(item)

        if fragment is None:
            issue("missing_fragment", "缺少本题最终结构化解析。")
            by_question.append(
                {
                    "question_id": qid,
                    "issues": q_issues,
                    "warnings": q_warnings,
                    "diagnostics": q_diagnostics,
                }
            )
            continue
        if draft is None:
            if qid in draft_optional_question_ids:
                diagnostic(
                    "checkpoint_draft_unavailable",
                    "历史检查点未保存模型原始草稿；最终结构与交付审计仍已执行，草稿对照审计已跳过。",
                )
            else:
                issue("missing_draft", "缺少模型原始解析草稿，无法审计解析生成质量。")
            draft = {}
        repaired_draft = fragment.get("_draft") if isinstance(fragment.get("_draft"), dict) else None
        if repaired_draft is not None:
            draft = repaired_draft

        if _has_review_flag(fragment, "high_risk_correctness_unresolved"):
            issue(
                "high_risk_correctness_unresolved",
                "高风险学科正确性复核提出了具体修复，但候选修复未通过确定性校验；本题不得作为正式验收答案。",
            )

        capability_text = "\n".join(
            [
                str(question.get("stem") or ""),
                *[
                    str(subquestion.get("stem") or subquestion.get("text") or "")
                    for subquestion in question.get("subquestions", []) or []
                    if isinstance(subquestion, dict)
                ],
            ]
        )
        for contribution in capability_policy_contributions(
            "content_quality",
            {
                "question": question,
                "fragment": fragment,
                "draft": draft,
                "active_figure_specs": active_figure_specs_by_id.get(qid, []),
            },
            text=capability_text,
        ):
            if not isinstance(contribution, dict):
                continue
            for finding in contribution.get("issues", []) or []:
                if isinstance(finding, dict):
                    issue(str(finding.get("code") or "capability_content_issue"), str(finding.get("message") or "学科能力包检出内容问题。"))
            for finding in contribution.get("warnings", []) or []:
                if isinstance(finding, dict):
                    warning(str(finding.get("code") or "capability_content_warning"), str(finding.get("message") or "学科能力包给出内容提示。"))

        answer = str(fragment.get("answer", "") or draft.get("answer", "")).strip()
        answer_summary = str(fragment.get("answer_summary", "") or draft.get("answer", "")).strip()
        if answer in PENDING_ANSWERS or (kind == "term_explanation" and answer == "见解析"):
            issue("missing_answer", "答案为空或仍为待复核状态。")

        analysis_text = _block_text(fragment, "解析")
        calculation_steps_text = _block_text(fragment, "解题步骤") if has_calculation_part else ""
        calculation_steps_are_reasoning = bool(
            has_calculation_part
            and len(calculation_steps_text.strip()) >= 20
            and (
                _block_segments(fragment, "解题步骤")
                or _draft_steps_for_audit(draft)
            )
        )
        if kind == "term_explanation":
            pass
        elif calculation_steps_are_reasoning and not analysis_text.strip():
            diagnostic(
                "analysis_satisfied_by_calculation_steps",
                "计算题已由结构化【解题步骤】完整承载推导过程，不再要求重复的【解析】标签。",
            )
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
        if contains_internal_repair_provenance(all_text):
            issue(
                "internal_repair_provenance_leak",
                "正式答案的答案、解析、步骤或易错点中包含原答案、回修或已修正等内部流程痕迹。",
            )
        if SPATIAL_MEMBERSHIP_INFERENCE_RE.search(all_text):
            issue(
                "spatial_relation_improper_membership_inference",
                "正文把“依附/附着/包覆/嵌入/邻接”等空间或观察关系直接写成“包含/归入/属于”的分类关系。必须依据教材明确区分空间关系、来源与题目要求的同级组成分区。",
            )
        for phrase in GENERIC_ANALYSIS_PHRASES:
            if phrase in all_text:
                warning("generic_analysis_phrase", f"解析中包含空泛表达：{phrase}")
                break
        if FORMULA_PLACEHOLDER_RE.search(all_text):
            issue("unresolved_formula_placeholder", "正文中残留 {f数字} 公式占位符，说明公式未正确转换为公式对象。")
        if CITATION_LEAK_RE.search(all_text):
            issue("citation_leaked_into_answer", "教材依据或课本页码混入了解析/答案正文，应只出现在【教材依据】块。")
        contradictory_properties = sorted(
            {
                property_name
                for unit in fragment.get("answer_units", []) or []
                if isinstance(unit, dict)
                for property_name in _answer_unit_comparative_contradictions(unit)
            }
        )
        if contradictory_properties:
            issue(
                "answer_analysis_comparative_contradiction",
                "答案与解析对同一性质的比较方向相反：" + "、".join(contradictory_properties),
            )
        omitted_composition_items = _composition_partition_omissions(fragment, draft)
        if omitted_composition_items:
            issue(
                "composition_partition_missing_declared_component",
                "答案明确列出的组成项未进入同一组成质量分区："
                + "、".join(omitted_composition_items)
                + "。须按题目与依据重新推导同层、互斥且穷尽的组成及比例；若该项并非同层独立组成，应同步修正组成表述，不能只补名称或删除账本项。",
            )

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
            consistency_issues = calculation_draft_consistency_issues(draft)
            if consistency_issues:
                issue(
                    "calculation_internal_inconsistency",
                    "计算题的公式等式、代入结果或步骤结论存在数值矛盾。",
                )
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
            if _calculation_has_high_confidence_missing_unit(fragment, draft):
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

        by_question.append(
            {
                "question_id": qid,
                "issues": q_issues,
                "warnings": q_warnings,
                "diagnostics": q_diagnostics,
            }
        )

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
        "diagnostic_count": len(diagnostics),
        "issues": issues,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "by_question": by_question,
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
