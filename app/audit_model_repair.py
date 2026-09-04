from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .answer_generation import (
    _with_main_model_image_tool_contract,
    answer_generation_thinking_mode,
    attach_program_evidence_block,
    evidence_for_answer_generation,
    fallback_fragment,
    fragment_from_analysis_draft,
    semantic_generation_issues,
    structured_answer_max_tokens,
)
from .calculation_consistency import calculation_draft_consistency_issues
from .concurrency import model_request_slot, run_limited_concurrent
from .drawing_code import question_drawing_mode
from .expression_promotion import promote_inline_mathematical_expressions, promote_inline_reactions
from .formula_audit import audit_text_segments_no_formula
from .image_artifacts import ImageArtifactStore
from .image_orchestration import (
    GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT,
    ensure_generation_image_label_language_requirement,
)
from .llm_client import OpenAICompatibleClient
from .model_tool_loop import ImageGenerationTool, ModelToolLoop, tool_loop_supported
from .prompt_registry import prompt_contract
from .prompts import question_image_parts
from .question_requirements import answer_figure_required
from .question_types import question_has_type
from .retrieval import EvidenceCandidate
from .settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig, provider_model_supports_vision
from .v4_schema import validate_v4_answer_fragment

AUDIT_MODEL_REPAIR_TIMEOUT_SECONDS = 180
AUDIT_MODEL_REPAIR_COMPLEX_TIMEOUT_SECONDS = 300


def audit_model_repair_worker_count() -> int:
    raw = os.environ.get("AUDIT_MODEL_REPAIR_MAX_WORKERS", "6")
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 6


def audit_model_repair_max_attempts() -> int:
    """Bound semantic correction turns while allowing a real validate/repair loop."""

    raw = os.environ.get("AUDIT_MODEL_REPAIR_MAX_ATTEMPTS", "3")
    try:
        return max(2, min(4, int(raw)))
    except ValueError:
        return 3


def _bounded_timeout_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return max(30, min(900, int(raw)))
    except ValueError:
        return default


def audit_model_repair_timeout_seconds(question: dict[str, Any]) -> int:
    """Give complex full-draft repairs the same response window as generation."""

    if question_has_type(question, "计算题") or question_has_type(question, "作图题"):
        return _bounded_timeout_env(
            "AUDIT_MODEL_REPAIR_COMPLEX_TIMEOUT_SECONDS",
            AUDIT_MODEL_REPAIR_COMPLEX_TIMEOUT_SECONDS,
        )
    return _bounded_timeout_env(
        "AUDIT_MODEL_REPAIR_TIMEOUT_SECONDS",
        AUDIT_MODEL_REPAIR_TIMEOUT_SECONDS,
    )


def _qid(value: dict[str, Any]) -> str:
    return str(value.get("question_id") or "").strip()


def _selection_map(selection_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not selection_data:
        return {}
    if isinstance(selection_data.get("selections"), list):
        return {
            _qid(selection): selection
            for selection in selection_data.get("selections", [])
            if isinstance(selection, dict) and _qid(selection)
        }
    return {
        str(key).strip(): value
        for key, value in selection_data.items()
        if str(key).strip() and isinstance(value, dict)
    }


def _qids_from_text(text: str, known_qids: set[str]) -> list[str]:
    found = []
    for qid in known_qids:
        if qid and qid in text:
            found.append(qid)
    if found:
        return found
    head = str(text).split(":", 1)[0].strip()
    return [head] if head in known_qids else []


def collect_audit_issue_targets(audit_report: dict[str, Any], known_qids: set[str]) -> dict[str, list[dict[str, Any]]]:
    targets: dict[str, list[dict[str, Any]]] = {}
    for severity in ("issues", "warnings"):
        for raw in audit_report.get(severity, []) if isinstance(audit_report, dict) else []:
            item = raw if isinstance(raw, dict) else {"message": str(raw)}
            qids: list[str] = []
            if isinstance(item, dict) and _qid(item):
                qids = [_qid(item)]
            elif isinstance(item, dict):
                text = str(item.get("message") or item)
                qids = _qids_from_text(text, known_qids)
                if "missing answer fragments:" in text:
                    missing_text = text.split("missing answer fragments:", 1)[1]
                    for token in re.split(r"[,，、\s]+", missing_text):
                        token = token.strip()
                        if token in known_qids and token not in qids:
                            qids.append(token)
            for qid in qids:
                targets.setdefault(qid, []).append({**item, "severity": "issue" if severity == "issues" else "warning"})
    return targets


def _segments_text(segments: Any) -> str:
    parts: list[str] = []
    for segment in segments or []:
        if isinstance(segment, dict) and segment.get("type") == "text":
            text = str(segment.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


_SUBSTITUTION_MISMATCH_RE = re.compile(
    r"formula_substitution_result_mismatch:(\d+):([-+0-9.eE]+)!=\[([^\]]*)\]"
)
_PARTITION_SUM_MISMATCH_RE = re.compile(
    r"calculation_contract_partition_sum_mismatch:(\d+):([-+0-9.eE]+)!=([-+0-9.eE]+)"
)
_TRANSITION_CONSERVATION_MISMATCH_RE = re.compile(
    r"calculation_contract_transition_conservation_mismatch:(\d+):"
    r"products=([-+0-9.eE]+):parent=([-+0-9.eE]+)"
)


def _deterministic_numeric_diagnostics(
    fragment: dict[str, Any], issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Expose local arithmetic results to the repair model as hard facts."""

    stored_draft = fragment.get("_draft")
    draft = stored_draft if isinstance(stored_draft, dict) else fragment
    formulas = [item for item in draft.get("formulas", []) or [] if isinstance(item, dict)]
    diagnostics: list[dict[str, Any]] = []
    for issue in issues:
        message = str(issue.get("message") or "")
        match = _SUBSTITUTION_MISMATCH_RE.search(message)
        if not match:
            continue
        formula_index = int(match.group(1))
        expected = float(match.group(2))
        formula = formulas[formula_index - 1] if 0 < formula_index <= len(formulas) else {}
        diagnostics.append(
            {
                "kind": "authoritative_local_arithmetic",
                "formula_index": formula_index,
                "substitution_formula": str(formula.get("latex") or ""),
                "computed_decimal": expected,
                "computed_percentage": expected * 100.0,
                "rejected_declared_values": match.group(3),
                "instruction": "同步修正该量的 result 公式、答案、步骤结果和 calculation_contract；不得保留被拒绝的数值。",
            }
        )
    return diagnostics


def _validation_issue_messages(issues: list[dict[str, Any]] | list[str]) -> list[str]:
    return [
        str(item.get("message") or item) if isinstance(item, dict) else str(item)
        for item in issues
    ]


def _quantity_snapshot(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = [
        *(contract.get("result_quantities") or []),
        *(contract.get("intermediate_quantities") or []),
    ]
    return {
        str(item.get("quantity_id") or "").strip(): {
            key: item.get(key)
            for key in ("quantity_id", "name", "value", "unit", "basis", "formula_index")
            if key in item
        }
        for item in rows
        if isinstance(item, dict) and str(item.get("quantity_id") or "").strip()
    }


def _deterministic_contract_diagnostics(
    draft: dict[str, Any], issues: list[dict[str, Any]] | list[str]
) -> list[dict[str, Any]]:
    """Turn terse ledger failures into exact, discipline-neutral repair facts."""

    contract = draft.get("calculation_contract")
    if not isinstance(contract, dict):
        return []
    quantities = _quantity_snapshot(contract)
    partitions = [item for item in contract.get("partitions", []) or [] if isinstance(item, dict)]
    transitions = [item for item in contract.get("transitions", []) or [] if isinstance(item, dict)]
    diagnostics: list[dict[str, Any]] = []
    for message in _validation_issue_messages(issues):
        partition_match = _PARTITION_SUM_MISMATCH_RE.search(message)
        if partition_match:
            index = int(partition_match.group(1))
            actual = float(partition_match.group(2))
            expected = float(partition_match.group(3))
            partition = partitions[index - 1] if 0 < index <= len(partitions) else {}
            component_ids = [str(value or "").strip() for value in partition.get("component_quantity_ids", []) or []]
            diagnostics.append(
                {
                    "kind": "authoritative_partition_arithmetic",
                    "partition_index": index,
                    "basis": partition.get("basis", ""),
                    "components": [quantities[value] for value in component_ids if value in quantities],
                    "actual_total": actual,
                    "expected_total": expected,
                    "required_delta": expected - actual,
                    "instruction": (
                        "这些量自称是同一总体的互斥且穷尽组成，但当前和不等于总体。必须依据题目和证据重算，"
                        "并同步答案、公式、步骤与账本；不得只改 expected_total，也不得删除 partition 绕过校验。"
                    ),
                }
            )
            continue
        transition_match = _TRANSITION_CONSERVATION_MISMATCH_RE.search(message)
        if transition_match:
            index = int(transition_match.group(1))
            products_total = float(transition_match.group(2))
            parent_total = float(transition_match.group(3))
            transition = transitions[index - 1] if 0 < index <= len(transitions) else {}
            parent_id = str(transition.get("parent_quantity_id") or "").strip()
            product_ids = [str(value or "").strip() for value in transition.get("product_quantity_ids", []) or []]
            diagnostics.append(
                {
                    "kind": "authoritative_transition_arithmetic",
                    "transition_index": index,
                    "basis": transition.get("basis", ""),
                    "parent": quantities.get(parent_id, {"quantity_id": parent_id}),
                    "products": [quantities[value] for value in product_ids if value in quantities],
                    "products_total": products_total,
                    "parent_total": parent_total,
                    "unaccounted_amount": parent_total - products_total,
                    "instruction": (
                        "在同一全局基准下，全部子项必须守恒地等于父项。重新确定子项定义和值；"
                        "若父项冷却或转变后只是更名的整体组织，则该整体子项应保留父项全量，不能误乘其内部某一组成的局部分数。"
                    ),
                }
            )
    return diagnostics


def _repair_context(fragment: dict[str, Any] | None, issues: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(fragment, dict):
        return {}
    wanted_labels = {"解析", "答案", "选项分析", "解题步骤", "易错点及注意事项", "待复核公式"}
    blocks: list[dict[str, Any]] = []
    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict):
            continue
        label = str(block.get("label") or "").strip()
        if label == "教材依据" or label not in wanted_labels:
            continue
        blocks.append({"label": label, "text": _segments_text(block.get("segments"))})
    stored_draft = fragment.get("_draft")
    draft = stored_draft if isinstance(stored_draft, dict) else fragment
    deterministic_validation_issues = list(dict.fromkeys(calculation_draft_consistency_issues(draft)))
    diagnostic_issues = [
        *issues,
        *(
            {
                "code": "calculation_deterministic_validation",
                "message": message,
                "severity": "issue",
            }
            for message in deterministic_validation_issues
        ),
    ]
    return {
        "question_id": _qid(fragment),
        "section": fragment.get("section", ""),
        "number": fragment.get("number", ""),
        "answer": fragment.get("answer", ""),
        "answer_summary": fragment.get("answer_summary", ""),
        "blocks_to_repair": blocks,
        "formulas": draft.get("formulas", fragment.get("formulas", [])),
        "calculation_contract": draft.get("calculation_contract", fragment.get("calculation_contract", {})),
        "answer_units": draft.get("answer_units", fragment.get("answer_units", [])),
        "drawing_code_specs": fragment.get("drawing_code_specs", []),
        "figure_specs": fragment.get("figure_specs", []),
        "repair_scope": [str(issue.get("code") or issue.get("message") or "") for issue in issues],
        "deterministic_validation_issues": deterministic_validation_issues,
        "deterministic_numeric_diagnostics": _deterministic_numeric_diagnostics(fragment, diagnostic_issues),
        "deterministic_contract_diagnostics": _deterministic_contract_diagnostics(draft, diagnostic_issues),
        "note": "已移除程序生成的教材依据块；不要补写教材页码或教材依据。",
    }


def _repair_prompt(
    *,
    audit_stage: str,
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    fragment: dict[str, Any] | None,
    issues: list[dict[str, Any]],
    include_images: bool = False,
    include_textbook_evidence: bool = True,
) -> list[dict[str, Any]]:
    image_parts = question_image_parts(question) if include_images else []
    needs_answer_figure = answer_figure_required(question)
    drawing_mode = question_drawing_mode(question)
    drawing_output_key = "drawing_code_specs" if drawing_mode == "code" else "figure_specs"
    visual_context = {
        "has_input_images": bool(image_parts),
        "input_image_count": len(image_parts),
        "needs_figure": needs_answer_figure,
        "drawing_generation_mode": drawing_mode,
        "required_drawing_output": drawing_output_key,
        "image_refs": list(question.get("image_refs") or []),
        "repair_instruction": "",
    }
    if image_parts:
        visual_context["repair_instruction"] = "本题原题图片已随同本消息附上；修复答案时必须结合图片信息。"
    elif needs_answer_figure:
        visual_context["repair_instruction"] = f"本题未抽取到原题图片，但题干要求作图/图示；请仅按题干文字生成 {drawing_output_key}，并在 uncertainties 中说明“原题未抽取到图片，按题干文字生成图示规格”。"

    user_payload = {
        "task": "repair_one_answer_draft_after_audit_failed",
        "audit_stage": audit_stage,
        "audit_issues": issues,
        "question": question,
        "visual_context": visual_context,
        "confirmed_evidence": evidence[:20],
        "current_answer_context": _repair_context(fragment, issues),
        "output_schema": {
            "schema_version": "answer_book.answer_draft.v1",
            "question_id": _qid(question),
            "answer": "答案。计算题可写最终答案摘要；若含公式，公式也必须进入 formulas。",
            "analysis": "解析思路。计算题不要堆完整计算过程。",
            "analysis_segments": "非计算题使用；公式必须用 {f1} 等占位符真正融入句子。",
            "answer_units": [{"number": "多小问题必填的原始编号", "question_type": "确认题型", "answer": "该小问结论", "analysis_segments": [], "steps": []}],
            "steps": "计算题必须逐步写。text 只写本步目标；不要在 text 中写 {f1}。每步用 relation_formula_indices / substitution_formula_indices / result_formula_indices 引用 formulas。",
            "formulas": [{"latex": "公式 LaTeX", "role": "relation|substitution|result|definition", "meaning": "用途"}],
            "calculation_contract": {
                "requested_outputs": [{"answer_unit_number": "原题计算单元编号", "request_text": "仅复述题干要求", "basis": "计算基准"}],
                "result_quantities": [{"quantity_id": "q1", "answer_unit_number": "编号", "name": "结果量", "value": 0.5, "unit": "fraction", "basis": "计算基准", "formula_index": 3}],
                "intermediate_quantities": [{"quantity_id": "i1", "answer_unit_number": "编号", "name": "转变前父项量", "value": 0.6, "unit": "fraction", "basis": "与子项相同的全局基准"}],
                "partitions": [{"answer_unit_number": "编号", "basis": "计算基准", "component_quantity_ids": ["q1", "q2"], "expected_total": 1.0}],
                "transitions": [{"transition_id": "t1", "answer_unit_number": "编号", "basis": "全局基准", "parent_quantity_id": "i1", "product_quantity_ids": ["q1", "q2"], "derived_quantity_id": "q2", "local_fraction": 0.2}],
            },
            "drawing_code_specs": [{"figure_id": "可选", "caption": "中文图注", "code": "必须定义 draw(output_path: str) -> None 的 Python/Matplotlib 代码", "notes": "可选"}],
            "figure_specs": [],
            "mistake_notes": [],
            "uncertainties": [],
        },
        "hard_rules": [
            "只修复当前这一题，不要改变题号、题型和教材依据含义。",
            "必须针对 audit_issues 指出的失败原因修改，不要只复述原答案。",
            "题目有多个作答单元时，必须返回 answer_units，并为每个原始小问编号返回一个独立对象；答案、解析和步骤只能放入所属小问，不能混写在顶层字段。",
            "不得丢失原题要求、答案、代入过程、单位和图示需求。",
            "严格服从确认后的 question_type；如果确认题型不是作图题，不得因题干中的绘图相关词汇自行补充 drawing_code_specs 或 figure_specs。",
            "如果 visual_context.has_input_images 为 true，必须结合随消息附带的原题图片进行修复。",
            "如果 visual_context.needs_figure 为 true，必须按 visual_context.required_drawing_output 补充作图输出：code 模式输出 drawing_code_specs，figure_specs 模式输出 figure_specs。",
            GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT,
            "code 模式的 drawing_code_specs 每项必须包含 code 字符串，代码必须定义 draw(output_path: str) -> None，使用 Matplotlib 保存 PNG；黑白打印可读，不能靠颜色区分关键含义。",
            "如果 visual_context.needs_figure 为 true 且 visual_context.has_input_images 为 false，必须仅依据题干文字生成 visual_context.required_drawing_output，并在 uncertainties 中说明原题未抽取到图片、按题干文字生成图示规格。",
            "如果 audit_issues 包含 missing_required_figure，必须优先补充 visual_context.required_drawing_output；只有在题干与图片均不足以确定图形时，才可在 uncertainties 中说明无法可靠作图。",
            "不要输出教材依据、页码、课本-p、evidence_id 或引用格式；教材依据由程序统一合并。",
            "不要复制 current_answer_context 之外的程序字段；current_answer_context 中也不包含教材依据块。",
            "计算题必须保留循序渐进的解题步骤：本步目的 -> 关系式 -> 带入数值 -> 求得结果。",
            "计算题必须返回 calculation_contract：requested_outputs 只能复述原题明确要求，不得把教材中出现的相关量扩展成题目要求；result_quantities 的 formula_index 必须指向完全相同结果值的 result 公式；同一总体的组成、分数或概率必须在 partitions 中共用同一 basis 并加和为 expected_total。",
            "如果 audit_issues 包含 atomic_defects，它们已经由程序核对到当前答案原文和原题作答单元，必须逐项修正；current_answer_quote 是要被替换的错误原文，requirement_quote 是修复范围，不得忽略或扩大范围。",
            "如果 audit_issues 指出 composition_partition_missing_declared_component，必须依据原题、已确认的题面视觉/数据事实和教材依据重新推导同层、互斥且穷尽的全部组成；同步更新答案、公式、步骤和 calculation_contract。不得仅删除组成名称、虚构0%项或保留未计量的独立组成项。",
            "不得从纯空间/观察关系推断分类归属或来源：证据说X依附于、包覆于、嵌入、邻接或难与Y分辨，不等于X属于Y。若X在题目要求的同级分区中不单独计量，只能表述为“该层级不单独分辨/计量”，除非教材明确将其归入某一名称组成。",
            "如果 audit_issues 指出 spatial_relation_improper_membership_inference，必须删除“A含依附的X”这类无证据归属，根据已确认依据分别说明X的来源、空间关系，以及为何在题目要求的组织组成层级不单独计量；不得改坏已验证的组成数值账本。",
            "如果 audit_issues 指出 answer_analysis_comparative_contradiction，必须先依据题干和已确认依据判定同一比较对象、同一性质的正确方向，再将答案和解析同步；不得只为消除关键词而删除该性质，也不得改动无关小问。",
            "如果 audit_issues 包含 validated_numeric_patch，该结构已通过程序的分区加和、父子守恒和局部分数乘法校验。必须以它为数值修复边界，将同一结果同步到 answer、answer_units、formulas、steps 和 calculation_contract；不得改成 suggested_fix 中与该账本冲突的自由文本数值。",
            "如果 audit_issues 指出 calculation_internal_inconsistency，必须重新计算每个数值等式，并确保 answer、answer_units[].answer、formulas 与 steps[].result_text 完全一致；不得只修改其中一处。",
            "current_answer_context.deterministic_numeric_diagnostics 是程序确定性算术求值结果，优先级高于当前答案和模型心算。必须采用 computed_decimal（或等价的 computed_percentage）同步所有相关字段，禁止保留 rejected_declared_values。",
            "current_answer_context.deterministic_contract_diagnostics 是程序从当前数值账本精确计算出的守恒失败：required_delta 或 unaccounted_amount 不是可忽略误差。必须先重新推导物理/化学/数学含义，再让同一基准下的分区与父子转变同时闭合；不得仅修改 expected_total、basis、component_quantity_ids 或删除 transitions 来掩盖错误。",
            "分步组成计算必须追踪质量血缘：若某组成物由指定前驱体析出、转移、反应或损失，其全局质量分数必须乘以该前驱体的全局质量分数，不能误乘另一共存组成物；最终组成必须同时保留新生成物和前驱体剩余量并守恒。",
            "不得把一个整体组织/总体类别与它内部的子组成同时作为同一层级的互斥分区项；也不得用该整体内部某一子组成的局部分数替代整体在全局中的份额。先明确层级，再计算互斥且穷尽的同层组成。",
            "只要修复涉及多阶段拆分、析出、反应、转移、损失或转变，calculation_contract 必须填写 intermediate_quantities 和 transitions：父项、新生子项、剩余子项使用同一全局基准，子项之和等于父项；局部分数只能乘它所属的父项。该账本由程序硬校验，不得留空或仅在正文叙述。",
            "计算题 step.text 只写本步要计算什么和依据什么，不能写 {f1}、公式正文、代入式或结果式；公式统一放入 formulas 并通过索引字段引用。",
            "不得在 第(2)问、第2小问、第3步 这类中文序号标签中间插入换行。",
            "非计算题如需公式，必须在 analysis_segments.text 中用 {f1} 这类占位符把公式自然嵌入解析句子，不要集中罗列公式。",
            "不得把公式、判据、等量关系、比例关系、反应式或中文公式化表达写成普通正文。",
            "Return exactly one valid JSON object.",
        ],
    }
    if not include_textbook_evidence:
        user_payload["analysis_profile"] = "question_only"
        user_payload["confirmed_evidence"] = []
        replacements = {
            "不要改变题号、题型和教材依据含义": "不要改变题号、题型和原题含义",
            "不要输出教材依据、页码、课本-p、evidence_id 或引用格式；教材依据由程序统一合并": "不要输出教材依据、页码、课本-p、evidence_id 或引用格式",
            "不得把教材中出现的相关量扩展成题目要求": "不得把背景知识中的相关量扩展成题目要求",
            "依据原题、已确认的题面视觉/数据事实和教材依据": "依据原题、已确认的题面视觉/数据事实和可靠学科原理",
            "除非教材明确将其归入某一名称组成": "除非题干或已确认的学科事实明确将其归入某一名称组成",
        }
        for index, rule in enumerate(user_payload["hard_rules"]):
            for source, target in replacements.items():
                rule = rule.replace(source, target)
            user_payload["hard_rules"][index] = rule
    user_text = json.dumps(user_payload, ensure_ascii=False)
    user_content: str | list[dict[str, Any]]
    if image_parts:
        user_content = [{"type": "text", "text": user_text}, *image_parts]
    else:
        user_content = user_text
    return ensure_generation_image_label_language_requirement([
        {
            "role": "system",
            "content": "你是真题解析平台的单题审查修复器。只能返回 JSON，不要返回 Markdown。",
        },
        {
            "role": "user",
            "content": user_content,
        },
    ])


def _repair_retry_prompt(
    base_messages: list[dict[str, Any]],
    candidate: dict[str, Any],
    validation_issues: list[str],
) -> list[dict[str, Any]]:
    """Ask once more only after a candidate fails deterministic postconditions."""

    arithmetic_diagnostics: list[dict[str, Any]] = []
    for issue in validation_issues:
        match = _SUBSTITUTION_MISMATCH_RE.search(str(issue))
        if match:
            actual = float(match.group(2))
            arithmetic_diagnostics.append(
                {
                    "formula_index": int(match.group(1)),
                    "authoritative_decimal": actual,
                    "authoritative_percentage": actual * 100.0,
                    "rejected_result_values": match.group(3),
                }
            )
    validation_tool_result = _repair_validation_tool_result(candidate, validation_issues)
    retry_instruction = {
        "task": "repair_previous_candidate_validation_only",
        "previous_candidate": candidate,
        "validation_tool_result": validation_tool_result,
        "deterministic_validation_issues": validation_issues,
        "authoritative_arithmetic_diagnostics": arithmetic_diagnostics,
        "authoritative_contract_diagnostics": _deterministic_contract_diagnostics(candidate, validation_issues),
        "hard_rules": [
            "Return the complete corrected answer draft JSON object, not a patch and no Markdown.",
            "Preserve the content correction already made in previous_candidate; repair only the deterministic validation failures.",
            "For every authoritative_arithmetic_diagnostic, replace rejected_result_values with authoritative_decimal or its equivalent percentage in the matching result formula, answer text, step result, and calculation ledger.",
            "When a constituent precipitates, transfers, reacts, or is removed from a named precursor, multiply by the mass fraction of that exact precursor, not a different coexisting constituent. Report both the derived constituent and the remaining precursor so mass is conserved.",
            "For every partition, every component result quantity must use exactly the same basis string and that same string must be copied to partition.basis.",
            "Do not mix whole-population quantities with subset or precursor-internal quantities in one partition.",
            "Do not place an aggregate category and one of its internal constituents in the same mutually exclusive partition. If a transformed aggregate retains the entire parent amount, do not replace it with only one internal fraction.",
            "Use authoritative_contract_diagnostics to account for every required_delta and unaccounted_amount. Re-derive and synchronize the content; never hide a mismatch by changing expected_total, deleting a partition/transition, or relabeling a basis.",
            "Keep answer, answer_units, formulas, steps, result_quantities, and partitions numerically synchronized.",
        ],
    }
    return ensure_generation_image_label_language_requirement([
        copy.deepcopy(base_messages[0]),
        {"role": "assistant", "content": json.dumps(candidate, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(retry_instruction, ensure_ascii=False)},
    ])


def _repair_validation_tool_result(
    candidate: dict[str, Any], validation_issues: list[str]
) -> dict[str, Any]:
    """Return a stable, actionable validator envelope for the next model turn."""

    candidate_json = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    suggestion = "根据确定性校验结果修改上一版候选，并同步答案、公式、步骤、数值账本和图示字段。"
    if any("missing_required" in str(issue) or "required_figure" in str(issue) for issue in validation_issues):
        suggestion = "保留上一版内容，按原题要求补齐可渲染的 drawing_code_specs 或 figure_specs，不能只写‘见图’。"
    return {
        "schema_version": "answer_book.repair_validation_result.v1",
        "ok": False,
        "error": {
            "code": "ANSWER_REPAIR_VALIDATION_FAILED",
            "message": "上一版候选未通过确定性验收。",
            "suggestion": suggestion,
            "responsibility": "model_output",
            "retryable": True,
            "details": {"issues": validation_issues},
        },
        "meta": {
            "candidate_sha256": hashlib.sha256(candidate_json.encode("utf-8")).hexdigest(),
            "issue_count": len(validation_issues),
        },
    }


def _block_has_payload(fragment: dict[str, Any], label: str) -> bool:
    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("label") or "").strip() != label:
            continue
        return any(
            isinstance(segment, dict)
            and (
                str(segment.get("text") or "").strip()
                or str(segment.get("formula_id") or "").strip()
                or str(segment.get("path") or "").strip()
            )
            for segment in block.get("segments", []) or []
        )
    return False


def _repair_regressions(
    original: dict[str, Any] | None,
    repaired: dict[str, Any],
    question: dict[str, Any],
    issues: list[dict[str, Any]],
) -> list[str]:
    """Reject a scoped repair that damages already-valid answer structure.

    Audit repair is deliberately a full-draft model call for compatibility, but
    it must behave like a patch at the acceptance boundary.  These postconditions
    prevent a partial response for one subquestion from replacing a complete
    multi-part answer, and prevent a warning repair from deleting core sections.
    """

    regressions = list(semantic_generation_issues(question, repaired))
    if not isinstance(original, dict):
        return regressions

    target_codes = {str(item.get("code") or "").strip() for item in issues if isinstance(item, dict)}
    if str(original.get("answer") or "").strip() and not str(repaired.get("answer") or "").strip():
        regressions.append("repair_removed_existing_answer")
    if original.get("formulas") and not repaired.get("formulas") and not any("formula" in code for code in target_codes):
        regressions.append("repair_removed_existing_formulas")

    protected_blocks = {
        "解析": "missing_analysis",
        "解题步骤": "calculation_missing_steps",
        "易错点及注意事项": "calculation_missing_mistake_notes",
    }
    for label, repair_code in protected_blocks.items():
        if (
            _block_has_payload(original, label)
            and not _block_has_payload(repaired, label)
            and repair_code not in target_codes
        ):
            regressions.append(f"repair_removed_existing_block:{label}")

    # A missing-figure repair must actually add a renderable drawing request.  A
    # prose promise such as "见图" is not an acceptable postcondition.
    if "missing_required_figure" in target_codes and not (
        repaired.get("generated_images") or repaired.get("figure_specs") or repaired.get("drawing_code_specs")
    ):
        regressions.append("repair_did_not_add_required_figure_spec")
    return list(dict.fromkeys(regressions))


def _preserve_accepted_generated_images(
    original: dict[str, Any] | None,
    repaired: dict[str, Any],
) -> None:
    """Keep only previously proven main-model image bindings through text repair.

    A later audit call replaces the answer draft as a whole.  Images are an
    independent accepted artifact, so a text-only repair must not erase them.
    The binding is preserved only when the original fragment also contains the
    tool-loop artifact record proving that the main model received that asset.
    """

    if not isinstance(original, dict) or repaired.get("generated_images"):
        return
    original_images = [
        copy.deepcopy(item)
        for item in original.get("generated_images", []) or []
        if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
    ]
    original_meta = original.get("_meta") if isinstance(original.get("_meta"), dict) else {}
    original_loop = original_meta.get("image_tool_loop") if isinstance(original_meta.get("image_tool_loop"), dict) else {}
    artifacts = [
        copy.deepcopy(item)
        for item in original_loop.get("generated_artifacts", []) or []
        if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
    ]
    proven_ids = {str(item.get("asset_id") or "").strip() for item in artifacts}
    preserved_images = [item for item in original_images if str(item.get("asset_id") or "").strip() in proven_ids]
    if not preserved_images:
        return
    repaired["generated_images"] = preserved_images
    if isinstance(repaired.get("_draft"), dict):
        repaired["_draft"]["generated_images"] = copy.deepcopy(preserved_images)
    repaired_meta = dict(repaired.get("_meta") or {})
    repaired_meta["image_tool_loop"] = copy.deepcopy(original_loop)
    repaired["_meta"] = repaired_meta


def _attach_image_tool_loop_result(repaired: dict[str, Any], result: Any) -> None:
    if result is None:
        return
    repaired.setdefault("_meta", {})["image_tool_loop"] = {
        "steps": result.steps,
        "tool_calls": result.tool_calls,
        "generated_artifacts": result.generated_artifacts,
        "tool_event_log": getattr(result, "tool_event_log", ""),
    }


def _drafts_by_question_id(fragments_json: Path) -> dict[str, dict[str, Any]]:
    drafts_path = fragments_json.parent / "answer_drafts.json"
    try:
        payload = json.loads(drafts_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {
        _qid(item): item
        for item in payload.get("drafts", []) or []
        if isinstance(item, dict) and _qid(item)
    }


def _merge_safe_preserved_blocks(original: dict[str, Any] | None, repaired: dict[str, Any]) -> None:
    """Preserve independent advisory prose when a scoped repair omits it.

    Core answer/analysis/steps are intentionally not merged because they can
    contain the defect being repaired.  Formula numbering may legitimately
    change during a calculation repair, so preserve independent prose while
    dropping only stale formula references.
    """

    if not isinstance(original, dict) or _block_has_payload(repaired, "易错点及注意事项"):
        return
    valid_formula_ids = {
        str(item.get("formula_id") or "")
        for item in repaired.get("formulas", []) or []
        if isinstance(item, dict) and str(item.get("formula_id") or "")
    }
    for block in original.get("blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("label") or "").strip() != "易错点及注意事项":
            continue
        preserved = copy.deepcopy(block)
        preserved["segments"] = [
            segment
            for segment in preserved.get("segments", []) or []
            if isinstance(segment, dict)
            and (
                segment.get("type") != "formula_ref"
                or str(segment.get("formula_id") or "") in valid_formula_ids
            )
        ]
        if preserved["segments"]:
            repaired.setdefault("blocks", []).append(preserved)
        return


def _drop_formula_like_repair_advisories(repaired: dict[str, Any]) -> int:
    """Do not reject a correct core repair for optional malformed advice.

    A model can fix the answer and numeric ledger while adding an optional
    mistake-note sentence that restates a formula in prose. The old behavior
    rejected the entire transaction and restored the known-wrong answer. Drop
    only those optional advisory segments; the original validated note is then
    restored by ``_merge_safe_preserved_blocks`` when available.
    """

    dropped = 0
    kept_blocks: list[dict[str, Any]] = []
    for block in repaired.get("blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("label") or "").strip() != "易错点及注意事项":
            if isinstance(block, dict):
                kept_blocks.append(block)
            continue
        kept_segments = []
        for segment in block.get("segments", []) or []:
            if (
                isinstance(segment, dict)
                and segment.get("type") == "text"
                and audit_text_segments_no_formula(
                    [segment],
                    include_chinese_paraphrase=True,
                )
            ):
                dropped += 1
                continue
            if isinstance(segment, dict):
                kept_segments.append(segment)
        if kept_segments:
            block["segments"] = kept_segments
            kept_blocks.append(block)
    repaired["blocks"] = kept_blocks
    return dropped


def _repair_formula_leaks(candidate: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Separate unreadable symbol leakage from readable Chinese paraphrase.

    Symbolic formula text can break the Word expression contract and remains a
    hard rejection. A readable Chinese relationship is a presentation concern;
    it must not roll back a numerically validated correctness repair.
    """

    hard = audit_text_segments_no_formula(
        candidate.get("blocks", []),
        ignored_block_labels={"教材依据"},
        include_chinese_paraphrase=False,
    )[:10]
    all_leaks = audit_text_segments_no_formula(
        candidate.get("blocks", []),
        ignored_block_labels={"教材依据"},
        include_chinese_paraphrase=True,
    )[:10]
    return hard, [issue for issue in all_leaks if issue not in hard]


def repair_fragments_with_model_for_audit(
    fragments_json: Path,
    structured_exam: dict[str, Any],
    candidates: list[EvidenceCandidate],
    *,
    selection_data: dict[str, Any] | None,
    provider: ProviderConfig,
    model: str,
    audit_stage: str,
    audit_report: dict[str, Any],
    client: Any | None = None,
    image_provider: ProviderConfig | None = None,
    image_model: str = "",
    backup_path: Path | None = None,
    max_repairs: int = 5,
) -> dict[str, Any]:
    data = json.loads(fragments_json.read_text(encoding="utf-8")) if fragments_json.exists() else {"fragments": []}
    original = copy.deepcopy(data)
    fragments = [fragment for fragment in data.get("fragments", []) if isinstance(fragment, dict)]
    stored_drafts = _drafts_by_question_id(fragments_json)
    questions = {
        _qid(question): question
        for question in structured_exam.get("items", [])
        if isinstance(question, dict) and _qid(question)
    }
    targets = collect_audit_issue_targets(audit_report, set(questions))
    if not targets:
        return {"ok": False, "changed": False, "repaired_count": 0, "repaired_question_ids": [], "issues": ["未定位到可交给模型修复的题目。"]}
    if max_repairs <= 0:
        return {
            "ok": False,
            "changed": False,
            "repaired_count": 0,
            "repaired_question_ids": [],
            "issues": ["内容模型回修资源预算为 0，已按无人值守策略跳过。"],
            "budget_exhausted": True,
        }

    selections = _selection_map(selection_data)
    include_textbook_evidence = str((selection_data or {}).get("analysis_profile") or "") != "question_only"
    fragments_by_qid = {_qid(fragment): fragment for fragment in fragments if _qid(fragment)}
    repaired_qids: list[str] = []
    repair_issues: list[dict[str, Any]] = []

    target_rows = list(targets.items())[:max_repairs]
    max_workers = 1 if client is not None else audit_model_repair_worker_count()

    def repair_one(
        target: tuple[str, list[dict[str, Any]]]
    ) -> tuple[str, dict[str, Any] | None, list[str], list[dict[str, Any]]]:
        qid, issues = target
        question = questions.get(qid)
        if not question:
            return qid, None, ["缺少题目结构，无法模型修复。"], []
        evidence_selection = selections.get(qid)
        evidence = evidence_for_answer_generation(candidates, qid, evidence_selection)
        fragment = fragments_by_qid.get(qid)
        if isinstance(fragment, dict) and not isinstance(fragment.get("_draft"), dict) and qid in stored_drafts:
            fragment = {**fragment, "_draft": copy.deepcopy(stored_drafts[qid])}
        validation_history: list[dict[str, Any]] = []
        try:
            repair_client = client or OpenAICompatibleClient(provider)
            artifact_store = ImageArtifactStore(fragments_json.parent / "agent_images" / qid)
            tool_loop = None
            image_tool_route_requested = bool(
                image_provider is not None and str(image_model or "").strip()
            )
            if image_tool_route_requested and not tool_loop_supported(
                repair_client, provider, model
            ):
                raise ValueError(
                    "当前内容修复模型未登记等价的原生图片工具回路；"
                    "已停止修复，未静默改为纯文本或传统绘图。"
                )
            if image_tool_route_requested:
                image_tool = ImageGenerationTool(
                    image_provider,
                    image_model,
                    artifact_store,
                    reference_images=question.get("image_refs") or [],
                )
                tool_loop = ModelToolLoop(
                    repair_client,
                    [image_tool],
                    artifact_store,
                    session_id=f"audit_repair:{audit_stage}:{qid}",
                )
            base_messages = _repair_prompt(
                audit_stage=audit_stage,
                question=question,
                evidence=evidence,
                fragment=fragment,
                issues=issues,
                include_images=provider_model_supports_vision(provider, model),
                include_textbook_evidence=include_textbook_evidence,
            )
            if tool_loop is not None:
                base_messages = _with_main_model_image_tool_contract(base_messages)
            draft: dict[str, Any] = {}
            repaired: dict[str, Any] | None = None
            candidate_issues: list[str] = []
            max_attempts = audit_model_repair_max_attempts()
            for attempt in range(max_attempts):
                messages = base_messages if attempt == 0 else _repair_retry_prompt(base_messages, draft, candidate_issues)
                agent_result = None
                with prompt_contract("exam.answer_audit_repair"):
                    if tool_loop is not None:
                        agent_result = tool_loop.run_json(
                            messages,
                            model=model,
                            max_tokens=max(structured_answer_max_tokens(provider, question), DEFAULT_MODEL_MAX_TOKENS),
                            thinking=answer_generation_thinking_mode(provider),
                            timeout=audit_model_repair_timeout_seconds(question),
                        )
                        draft = agent_result.value
                    else:
                        with model_request_slot(provider):
                            draft = repair_client.chat_json_object(
                                messages,
                                model=model,
                                max_tokens=max(int(provider.max_tokens or DEFAULT_MODEL_MAX_TOKENS), DEFAULT_MODEL_MAX_TOKENS),
                                thinking="disabled",
                                timeout=audit_model_repair_timeout_seconds(question),
                                task_stage="review",
                                item_ids=[qid],
                                enforce_context_budget=True,
                            )
                candidate = fragment_from_analysis_draft(draft, question, evidence, evidence_selection)
                candidate = promote_inline_reactions(candidate)
                candidate = promote_inline_mathematical_expressions(candidate)
                _attach_image_tool_loop_result(candidate, agent_result)
                _preserve_accepted_generated_images(fragment, candidate)
                attach_program_evidence_block(candidate, evidence, evidence_selection)
                _drop_formula_like_repair_advisories(candidate)
                _merge_safe_preserved_blocks(fragment, candidate)
                syntax_issues = validate_v4_answer_fragment(candidate)
                formula_leaks, deferred_formula_paraphrases = _repair_formula_leaks(candidate)
                if deferred_formula_paraphrases:
                    candidate.setdefault("_meta", {})["deferred_formula_paraphrases"] = deferred_formula_paraphrases
                regression_issues = _repair_regressions(fragment, candidate, question, issues)
                candidate_issues = syntax_issues + formula_leaks[:10] + regression_issues
                if not candidate_issues:
                    repaired = candidate
                    break
                validation_result = _repair_validation_tool_result(draft, candidate_issues)
                validation_result["meta"].update(
                    {
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "question_id": qid,
                    }
                )
                validation_history.append(validation_result)
                # Each retry receives the latest full candidate plus the exact
                # deterministic tool result. No retry starts again from the
                # original answer, and no failed candidate is persisted.
                if attempt + 1 < max_attempts:
                    continue
                return qid, None, candidate_issues, validation_history
            if repaired is None:
                return qid, None, candidate_issues or ["repair_candidate_not_accepted"], validation_history
            repaired.pop("_review_candidate_issues", None)
            repaired["_review_flags"] = [
                flag
                for flag in repaired.get("_review_flags", [])
                if not isinstance(flag, dict) or str(flag.get("code") or "") != "answer_generation_review_candidate"
            ]
            repaired["warnings"] = [
                warning
                for warning in repaired.get("warnings", [])
                if "模型生成内容存在审查问题" not in str(warning)
            ]
            meta = dict(repaired.get("_meta") or {})
            meta.update(
                {
                    "provider": provider.name,
                    "model": model,
                    "recovered_by": f"{audit_stage}_model_repair",
                    "audit_repair_issues": issues[:10],
                    "llm_retry": getattr(repair_client, "last_json_retry_report", {}),
                    "repair_validation_history": validation_history,
                }
            )
            repaired["_meta"] = meta
        except Exception as exc:
            return qid, None, [str(exc)], validation_history
        return qid, repaired, [], validation_history

    repair_results = run_limited_concurrent(target_rows, repair_one, max_workers=max_workers)
    validation_results: dict[str, list[dict[str, Any]]] = {}
    for qid, repaired, issues, validation_history in repair_results:
        if validation_history:
            validation_results[qid] = validation_history
        if issues:
            repair_issues.append({"question_id": qid, "issues": issues})
            continue
        if repaired is None:
            continue
        fragment = fragments_by_qid.get(qid)
        if fragment is None:
            fragments.append(repaired)
        else:
            for index, current in enumerate(fragments):
                if _qid(current) == qid:
                    fragments[index] = repaired
                    break
        fragments_by_qid[qid] = repaired
        repaired_qids.append(qid)

    changed = bool(repaired_qids)
    report = {
        "ok": changed and not repair_issues,
        "changed": changed,
        "repaired_count": len(repaired_qids),
        "repaired_question_ids": repaired_qids,
        "issue_count": len(repair_issues),
        "issues": repair_issues[:30],
        "validation_tool_results": validation_results,
        "targets": targets,
        "budget": {
            "max_repairs": max_repairs,
            "target_count": len(targets),
            "scheduled_count": len(target_rows),
            "truncated": len(targets) > len(target_rows),
        },
        "concurrency": {
            "max_workers": min(max_workers, len(target_rows)) if target_rows else 1,
            "parallel_enabled": max_workers > 1 and len(target_rows) > 1,
        },
    }
    if not changed:
        return report

    if backup_path:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if fragments_json.exists():
            shutil.copy2(fragments_json, backup_path)
        report["backup"] = str(backup_path)
    data["fragments"] = fragments
    data.setdefault("recovery_events", []).extend(
        {"question_id": qid, "strategy": f"{audit_stage}_model_repair", "issues": targets.get(qid, [])[:5]}
        for qid in repaired_qids
    )
    data["recovered_count"] = int(data.get("recovered_count", 0)) + len(repaired_qids)
    fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    drafts_path = fragments_json.parent / "answer_drafts.json"
    try:
        drafts_payload = json.loads(drafts_path.read_text(encoding="utf-8")) if drafts_path.exists() else {"drafts": []}
    except (OSError, ValueError, TypeError):
        drafts_payload = {"drafts": []}
    existing_drafts = [item for item in drafts_payload.get("drafts", []) or [] if isinstance(item, dict)]
    repaired_by_qid = {_qid(fragment): fragment for fragment in fragments if _qid(fragment) in repaired_qids}
    updated_drafts: list[dict[str, Any]] = []
    written_qids: set[str] = set()
    for stored in existing_drafts:
        qid = _qid(stored)
        repaired_fragment = repaired_by_qid.get(qid)
        if repaired_fragment is None:
            updated_drafts.append(stored)
            continue
        repaired_draft = repaired_fragment.get("_draft")
        if isinstance(repaired_draft, dict):
            updated_drafts.append({**copy.deepcopy(repaired_draft), "question_id": qid})
            written_qids.add(qid)
    for qid in repaired_qids:
        if qid in written_qids:
            continue
        repaired_draft = (repaired_by_qid.get(qid) or {}).get("_draft")
        if isinstance(repaired_draft, dict):
            updated_drafts.append({**copy.deepcopy(repaired_draft), "question_id": qid})
            written_qids.add(qid)
    drafts_payload["drafts"] = updated_drafts
    drafts_path.write_text(json.dumps(drafts_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report["updated_answer_draft_question_ids"] = sorted(written_qids)
    report["original_preserved"] = original != data
    return report


def fill_missing_fragments_locally(
    fragments_json: Path,
    structured_exam: dict[str, Any],
    candidates: list[EvidenceCandidate],
    reason: str,
) -> dict[str, Any]:
    data = json.loads(fragments_json.read_text(encoding="utf-8")) if fragments_json.exists() else {"fragments": []}
    fragments = [fragment for fragment in data.get("fragments", []) if isinstance(fragment, dict)]
    existing = {_qid(fragment) for fragment in fragments if _qid(fragment)}
    repaired: list[str] = []
    for question in structured_exam.get("items", []):
        qid = _qid(question)
        if not qid or qid in existing:
            continue
        evidence = evidence_for_answer_generation(candidates, qid, None)
        fragment = fallback_fragment(question, evidence, reason)
        attach_program_evidence_block(fragment, evidence)
        fragments.append(fragment)
        repaired.append(qid)
    if repaired:
        data["fragments"] = fragments
        data.setdefault("recovery_events", []).extend({"question_id": qid, "strategy": "coverage_local_placeholder"} for qid in repaired)
        data["recovered_count"] = int(data.get("recovered_count", 0)) + len(repaired)
        data["fallback_count"] = int(data.get("fallback_count", 0)) + len(repaired)
        fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "changed": bool(repaired), "repaired_question_ids": repaired, "repaired_count": len(repaired)}
