from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from .answer_quality_requirements import ANSWER_CONTENT_QUALITY_REQUIREMENTS
from .capabilities.catalog import (
    capability_ids_for_text,
    capability_policy_contributions,
    get_schema,
    schema_prompt_catalog,
)
from .drawing_code import question_drawing_mode
from .image_orchestration import ensure_generation_image_label_language_requirement
from .omml_input import strip_structured_math_metadata
from .question_requirements import answer_figure_required
from .question_scores import confirmed_score_from_question, normalize_score
from .question_types import has_calculation_answer_unit, question_kind

SYSTEM_PROMPT = """你是专业考研真题解析教师，你输出的答案要倾向专业考研真题解析，要平衡学生理解和解析深度。
你只负责根据输入的真题和教材内容完成解析，并输出一个合法 JSON object。
不得描述流程，不得生成 Markdown，不得输出 JSON 之外的任何文字，不得要求用户补文件。
不得输出页码、evidence_id、候选证据编号、平台内部字段或最终排版内容。
解析内容必须满足输入中的《真题解析内容质量要求》。
"""

QUESTION_ONLY_SYSTEM_PROMPT = """你是专业考研题目解析教师，你输出的答案要平衡学生理解和解析深度。
你只负责根据输入题目和已确认的题面信息完成解析，并输出一个合法 JSON object。
不得描述流程，不得生成 Markdown，不得输出 JSON 之外的任何文字，不得要求用户补教材。
不得输出教材依据、教材引用、页码、evidence_id、候选证据编号或平台内部字段。
解析内容必须满足输入中的《题目解析内容质量要求》；缺少题干条件时必须明确说明，不得编造。
"""


def _question_only_quality_requirements(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _question_only_quality_requirements(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_question_only_quality_requirements(item) for item in value]
    if not isinstance(value, str):
        return value
    replacements = {
        "名词解释题最终只展示“教材依据”和“答案”": "名词解释题最终展示“答案”",
        "根据教材可知": "根据相关原理可知",
        "教材或题干": "题干或学科规范",
        "教材结论": "学科结论",
        "教材段落": "背景段落",
        "教材依据": "外部依据",
    }
    result = value
    for source, target in replacements.items():
        result = result.replace(source, target)
    return result


def _score_from_question(question: dict[str, Any]) -> float | None:
    confirmed = confirmed_score_from_question(question)
    if confirmed is not None or question.get("score_reviewed"):
        return confirmed
    for key in ("score", "points", "point", "分值"):
        raw = question.get(key)
        if raw is None:
            continue
        match = re.search(r"\d+(?:\.\d+)?", str(raw))
        if match:
            return float(match.group(0))
    section_text = " ".join(str(question.get(key, "")) for key in ("section", "section_raw"))
    section_match = re.search(r"每小题\s*(\d+(?:\.\d+)?)\s*分", section_text)
    if section_match:
        return float(section_match.group(1))
    text = " ".join(str(question.get(key, "")) for key in ("stem", "title", "raw_title"))
    for pattern in (
        r"每小题\s*(\d+(?:\.\d+)?)\s*分",
        r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]",
        r"(\d+(?:\.\d+)?)\s*分",
    ):
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def build_answer_depth_profile(question: dict[str, Any]) -> dict[str, Any]:
    score = _score_from_question(question)
    kind = question_kind(question)
    has_calc_unit = has_calculation_answer_unit(question)
    if score is None:
        depth = "standard"
    elif score <= 2:
        depth = "concise"
    elif score <= 5:
        depth = "standard"
    elif score < 10:
        depth = "expanded"
    else:
        depth = "deep"

    profile: dict[str, Any] = {
        "score": int(score) if score is not None and float(score).is_integer() else score,
        "question_kind": kind,
        "depth": depth,
        "style": "按分值控制详略；低分题只讲关键依据，高分题展开步骤、依据和易错点。",
        "require_option_analysis": kind == "choice",
        "require_mistake_notes": depth in {"expanded", "deep"} or has_calc_unit,
        "max_analysis_sentences": 8,
        "min_steps": 0,
    }
    if depth == "concise":
        profile.update(
            {
                "max_analysis_sentences": 4,
                "min_steps": 2 if has_calc_unit else 0,
                "require_mistake_notes": False,
                "instruction": "答案优先；解析只保留本题关键判断依据，不扩写教材背景。",
            }
        )
    elif depth == "standard":
        profile.update(
            {
                "max_analysis_sentences": 8,
                "min_steps": 3 if has_calc_unit else 0,
                "instruction": "给出定义、核心依据、必要推理和结论；不要泛泛讲课。",
            }
        )
    elif depth == "expanded":
        profile.update(
            {
                "max_analysis_sentences": 12,
                "min_steps": 4 if has_calc_unit else 2,
                "instruction": "按小问或关键考点分段展开，说明方法选择、依据和结论。",
            }
        )
    else:
        profile.update(
            {
                "max_analysis_sentences": 18,
                "min_steps": 4,
                "instruction": "综合题深度解析；按小问拆解，分步骤说明依据、计算或论证、结论和易错点。",
            }
        )
    return profile


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def question_image_parts(question: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for raw in question.get("image_refs") or []:
        path = Path(str(raw))
        if not path.exists() or not path.is_file():
            continue
        parts.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
    return parts


_question_image_parts = question_image_parts


def _prompt_question_record(question: dict[str, Any]) -> dict[str, Any]:
    def clean_source_markup(value: Any) -> Any:
        if isinstance(value, str):
            return strip_structured_math_metadata(value)
        if isinstance(value, list):
            return [clean_source_markup(item) for item in value]
        if isinstance(value, dict):
            return {key: clean_source_markup(item) for key, item in value.items()}
        return value

    drawing_required = answer_figure_required(question)
    drawing_mode = question_drawing_mode(question) if drawing_required else ""
    return {
        "question_id": question.get("question_id", ""),
        "subject_index": question.get("subject_index", ""),
        "subject": question.get("subject", ""),
        "major_number": question.get("major_number", ""),
        "section": question.get("section", ""),
        "section_raw": question.get("section_raw", ""),
        "number": question.get("number", ""),
        "question_type": question.get("question_type", ""),
        "score": normalize_score(question.get("confirmed_score") if question.get("score_reviewed") else question.get("score")),
        "confirmed_score": normalize_score(question.get("confirmed_score")) if question.get("score_reviewed") else None,
        "score_reviewed": bool(question.get("score_reviewed")),
        "stem": clean_source_markup(question.get("stem", "")),
        "subquestions": clean_source_markup(question.get("subquestions", [])),
        "needs_figure": drawing_required,
        "drawing_generation_mode": drawing_mode,
        "figure_schema_plan": question.get("figure_schema_plan", {}) if drawing_required else {},
    }


def _figure_schema_registry_for_prompt(question: dict[str, Any]) -> list[dict[str, Any]]:
    if not answer_figure_required(question):
        return []
    if question_drawing_mode(question) == "code":
        return []
    raw_plan = question.get("figure_schema_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_decision = plan.get("render_decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    raw_resolution = plan.get("schema_resolution")
    resolution: dict[str, Any] = raw_resolution if isinstance(raw_resolution, dict) else {}
    kind = (
        "source_image_overlay"
        if str(decision.get("strategy") or "") == "source_image_overlay"
        else str(resolution.get("kind") or "").strip()
    )
    schema = get_schema(kind) if kind else None
    if not schema:
        chunks = [
            str(question.get("stem") or ""),
            str(question.get("section") or ""),
            str(question.get("section_raw") or ""),
        ]
        for subquestion in question.get("subquestions") or []:
            if not isinstance(subquestion, dict):
                continue
            chunks.append(str(subquestion.get("stem") or ""))
            for requirement in subquestion.get("requirements") or []:
                if isinstance(requirement, dict):
                    chunks.append(str(requirement.get("stem") or ""))
        return schema_prompt_catalog(capability_ids_for_text("\n".join(chunks)))
    return [
        {
            "schema_id": schema.get("schema_id"),
            "kind": schema.get("kind"),
            "name": schema.get("name"),
            "description": schema.get("description"),
            "required_fields": schema.get("required_fields", []),
            "optional_fields": schema.get("optional_fields", []),
        }
    ]


def _question_capability_text(question: dict[str, Any]) -> str:
    chunks = [
        str(question.get("stem") or ""),
        str(question.get("section") or ""),
        str(question.get("section_raw") or ""),
    ]
    for subquestion in question.get("subquestions") or []:
        if not isinstance(subquestion, dict):
            continue
        chunks.append(str(subquestion.get("stem") or ""))
        for requirement in subquestion.get("requirements") or []:
            if isinstance(requirement, dict):
                chunks.append(str(requirement.get("stem") or ""))
    return "\n".join(chunks)


def _textbook_content_record(row: dict[str, Any]) -> dict[str, Any]:
    source_type = str(row.get("source_type") or "text_block").strip() or "text_block"
    has_asset = bool(str(row.get("asset_path") or "").strip())
    visual_summary = str(row.get("visual_summary") or "").strip()
    ocr_text = str(row.get("ocr_text") or "").strip()
    table_rows = row.get("table_rows") or ""
    table_html = str(row.get("table_html") or "").strip()
    caption = str(row.get("caption") or "").strip()
    text_model_can_read_visual = True
    visual_warning = ""
    if source_type in {"figure_block", "table_block", "equation_block"} and has_asset:
        text_model_can_read_visual = bool(visual_summary or ocr_text or table_rows or table_html or caption)
        if text_model_can_read_visual:
            visual_warning = "若答案模型是文本模型，只能使用该图表的 caption/OCR/table_html/visual_summary 等文字化信息，不能读取原始教材图片。"
        else:
            visual_warning = str(row.get("visual_unreadable_reason") or "该教材图表/公式有图片文件，但当前答案提示只包含文字化证据，不能读取原图细节。").strip()
    return {
        "textbook": row.get("citation_textbook") or row.get("textbook"),
        "source_type": source_type,
        "content": row.get("evidence_text", ""),
        "caption": caption,
        "ocr_text": ocr_text[:1200],
        "table_html": table_html[:3000],
        "table_rows": table_rows,
        "visual_summary": visual_summary[:1600],
        "surrounding_text_preview": str(row.get("surrounding_text_preview") or "")[:1200],
        "asset_available": has_asset,
        "text_model_can_read_visual": text_model_can_read_visual,
        "visual_warning": visual_warning,
    }


def build_answer_draft_prompt(
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    question_understanding: dict[str, Any] | None = None,
    *,
    include_textbook_evidence: bool = True,
) -> list[dict[str, Any]]:
    depth_profile = build_answer_depth_profile(question)
    capability_text = _question_capability_text(question)
    answer_policy_contributions = capability_policy_contributions(
        "answer_generation",
        {"question": question, "text": capability_text},
        text=capability_text,
    )
    domain_answer_rules = [
        str(rule)
        for contribution in answer_policy_contributions
        if isinstance(contribution, dict)
        for rule in contribution.get("hard_rules", [])
        if str(rule).strip()
    ]
    domain_symbolic_guidance = [
        str(contribution.get("symbolic_notations_guidance") or "").strip()
        for contribution in answer_policy_contributions
        if isinstance(contribution, dict) and str(contribution.get("symbolic_notations_guidance") or "").strip()
    ]
    understanding = question_understanding if isinstance(question_understanding, dict) else question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
    has_calc_unit = has_calculation_answer_unit(question)
    schema_hint = {
        "schema_version": "answer_book.answer_draft.v1",
        "question_id": question.get("question_id", ""),
        "answer": "最终答案，如 A、正确、某术语、某数值结论；不得写页码或证据编号。",
        "analysis": "解析正文。必须服务于本题作答，说明总体解题思路；计算题不要在 analysis 中堆放完整计算过程。",
        "analysis_segments": [
            {
                "text": "非计算题填写。按答案册行文逐句推理；把公式、判据、符号缩写或反应式直接写在句中位置，用 {f1}、{f2} 引用 formulas 数组中的对应公式。例如：合成氨反应为 {f1}，因此 S=3、R=1。",
                "formula_indices": [1]
            }
        ],
        "answer_units": [
            {
                "number": "多小问题必填，必须等于题目中实际作答单元编号，例如 1、2 或 2.1；无小问题输出空数组。",
                "question_type": "该作答单元的确认题型，例如 名词解释、简答题、计算题、作图题。",
                "answer": "该小问的简短结论；反应式、公式等必须在 analysis_segments 或 steps 中用 formula_indices 引用，不要把 LaTeX 直接写在这里。",
                "analysis_segments": [
                    {"text": "该小问的答案和解析；公式在对应位置用 {f1} 引用。", "formula_indices": [1]}
                ],
                "steps": [
                    {
                        "text": "计算小问按本小问逐步作答。",
                        "relation_formula_indices": [1],
                        "substitution_formula_indices": [2],
                        "result_formula_indices": [3],
                        "result_text": "本步结论。"
                    }
                ],
                "figure_specs": [{"kind": "figure_specs 模式下该小问自己的注册 schema kind", "caption": "该小问图题"}],
                "drawing_code_specs": [{"figure_id": "code 模式可选", "caption": "该小问图题", "code": "def draw(output_path: str) -> None: ..."}]
            }
        ],
        "option_analysis": {"A": "选择题填写；非选择题为空对象"},
        "steps": [
            {
                "subquestion_number": "多小问或二级作答单元计算题必填，例如 1 或 2.3；无小问时留空或省略。",
                "text": "计算题按答案册形式逐步写本步目标，例如：由气液平衡关系计算80摄氏度下气化焓。不要在 text 中写 {f1}、{f2}，公式只用下方索引字段引用。",
                "relation_formula_indices": [1],
                "substitution_formula_indices": [2],
                "result_formula_indices": [3],
                "result_text": "必要时用一句话写本步结论，含单位。"
            }
        ],
        "formulas": [
            {
                "latex": "\\Delta_r G_m=-nFE",
                "meaning": "该公式在本题中的作用、变量含义或适用条件",
                "display": True,
                "role": "relation"
            }
        ],
        "calculation_contract": {
            "requested_outputs": [
                {
                    "answer_unit_number": "计算作答单元编号",
                    "request_text": "原题要求的量；只能来自题干，不得根据教材内容扩展题意",
                    "basis": "温度、时刻、总体、边界或其他共同计算基准"
                }
            ],
            "result_quantities": [
                {
                    "quantity_id": "q1",
                    "answer_unit_number": "所属作答单元",
                    "name": "结果量名称",
                    "value": 0.5,
                    "unit": "单位或 fraction",
                    "basis": "与同一组结果共享的计算基准",
                    "formula_index": 3
                }
            ],
            "intermediate_quantities": [
                {
                    "quantity_id": "i1",
                    "answer_unit_number": "所属作答单元",
                    "name": "转变或分配前的父项量",
                    "value": 0.6,
                    "unit": "单位或 fraction",
                    "basis": "必须与子项相同的全局基准"
                }
            ],
            "partitions": [
                {
                    "answer_unit_number": "所属作答单元",
                    "basis": "共同计算基准",
                    "component_quantity_ids": ["q1", "q2"],
                    "expected_total": 1.0
                }
            ],
            "transitions": [
                {
                    "transition_id": "t1",
                    "answer_unit_number": "所属作答单元",
                    "basis": "父项和子项共同的全局基准",
                    "parent_quantity_id": "i1",
                    "product_quantity_ids": ["q1", "q2"],
                    "derived_quantity_id": "q2",
                    "local_fraction": 0.2
                }
            ]
        },
        "symbolic_notations": [
            "可选。只放专业符号、图示标签、坐标轴标签或单位说明；不要把完整关系式、判据式或反应式放在这里。"
            + (f" 当前学科补充：{' '.join(domain_symbolic_guidance)}" if domain_symbolic_guidance else "")
        ],
        "drawing_code_specs": [
            {
                "figure_id": "可选；不填则程序自动生成",
                "caption": "中文图题或图注",
                "code": "Python/Matplotlib code string；必须定义 draw(output_path: str) -> None",
                "notes": "简短说明作图假设"
            }
        ],
        "figure_specs": [
            {
                "kind": "custom_diagram 或 figure_schema_plan.schema_resolution.kind 中指定的专业 schema kind",
                "caption": "图题或图注",
                "required_labels": ["必须出现在图中的文字或对象标签"],
                "features": [{"label": "注册 schema 要求的结构化特征", "xy": [0.5, 0.5]}],
                "elements": [
                    {"type": "circle", "center": [0, 0], "radius": 0.8, "label": "对象标签"},
                    {"type": "arrow", "start": [0, 0.9], "end": [0, 0.1], "label": "箭头含义", "color": "#dc2626"},
                    {"type": "line", "start": [0, 0], "end": [0.8, 0], "label": "线段含义", "style": "dashed"},
                    {"type": "text", "xy": [0, -0.2], "text": "说明文字"}
                ]
            }
        ],
        "generated_images": [
            {
                "asset_id": "仅填写 generate_image 工具返回且已由你检查过的 asset_id",
                "caption": "面向答案册读者的中文图题",
                "placement": "analysis",
                "answer_unit_number": "可选；多小问时填写所属作答单元编号"
            }
        ],
        "mistake_notes": ["计算题或确有必要的题目填写本题专属易错点及注意事项"],
        "uncertainties": []
    }
    user_payload = {
        "task": "generate_question_analysis_draft",
        "answer_content_quality_requirements": (
            ANSWER_CONTENT_QUALITY_REQUIREMENTS
            if include_textbook_evidence
            else _question_only_quality_requirements(ANSWER_CONTENT_QUALITY_REQUIREMENTS)
        ),
        "hard_rules": [
            "Only return a JSON object.",
            "Do not output page numbers.",
            "Do not output evidence_id, candidate id, retrieval id, or platform internal fields.",
            "Do not decide textbook citation formatting; the program will merge confirmed textbook references later.",
            "Analysis must directly serve this question and must not become a generic lecture.",
            "The answer field and analysis conclusion must be consistent.",
            "For 名词解释 questions, put the complete definition-style answer in answer. The final document only renders 教材依据 and 答案 for this type, so do not rely on analysis, steps, or mistake_notes to carry required answer content.",
            "For choice questions, explain why the selected option is correct and why valuable distractors are wrong.",
            "For calculation questions, analysis is only a short setup. Put the actual solution process in steps.",
            "For calculation questions, steps must be answer-book style: each step must follow step goal -> relation formula -> substitution formula -> result formula/result text, with unit where applicable.",
            "For calculation questions, solve the governing equations before writing the JSON. Recompute every substitution numerically, verify the sign against the stated process direction, and make every equality chain arithmetically true.",
            "For calculation questions, return exactly one internally consistent solution. Never include an abandoned trial calculation, a conflicting alternative result, phrases such as 'standard answer disagrees', or a knowingly incorrect value followed by a correction note.",
            "For calculation questions, synchronize the same final value and unit across answer, answer_units[].answer, result formulas, steps[].result_text, and calculation_contract. Do not merely copy a value if it contradicts the displayed substitution formula.",
            "For calculation questions, step.text must only describe what is being calculated and why, e.g. '由气液平衡关系计算80摄氏度下气化焓。'; do not put {f1}, {f2}, formula text, substitution text, or result formula in step.text.",
            "For calculation questions, the final document will render every step as: step.text, original relation formula, '带入数值：' plus substitution formula, '求得：' plus result formula, then result_text.",
            "For calculation questions, use relation_formula_indices, substitution_formula_indices, and result_formula_indices to attach formulas to the exact step; formula_indices is only a backward-compatible fallback.",
            "For calculation questions, Do not list all formulas before substitution; do not put all formulas in one step; every relation, substitution, and result formula must be attached to the step where it is used.",
            "For multi-part calculation questions, every step must set subquestion_number to one of the actual answer unit numbers: question.subquestions[].number when there are no nested requirements, otherwise question.subquestions[].requirements[].number. The program will render titles from the original question; do not write 第1小问, 第(1)小问, 第2小问, or similar labels in step.text.",
            "If the question has two or more answer units, answer_units is required. Return exactly one object for every actual answer unit, with answer_units[].number matching the original unit number. Put each unit's answer, analysis_segments, and calculation steps inside that unit; do not mix different units in top-level analysis, analysis_segments, or steps.",
            "For a non-calculation answer unit, put the substantive answer in answer_units[].analysis_segments. For a calculation answer unit, put its calculation process in answer_units[].steps. A mixed question can therefore contain both kinds of units.",
            "answer_units must not contain textbook citations, page numbers, evidence_id, or copied textbook-reference formatting. Textbook evidence is merged once at question level by the program.",
            "If a subquestion has requirements, use the requirement number such as 2.1, 2.2, 2.3 as the actual answer unit. Calculation steps must use the calculation requirement number, not only the parent subquestion number.",
            "If requirements contain mixed types, answer each requirement according to its own question_type inside answer_units: 作图题 must produce drawing_code_specs when question.drawing_generation_mode is code, or figure_specs when it is figure_specs; 计算题 must produce formulas and unit steps; 名词解释 must put the complete definition in answer_units[].answer; 简答题/判断题 must use unit analysis_segments.",
            "Do not merge multiple requirements into one crowded paragraph. Use the original requirement number and stem as the answer organization.",
            "Never embed image pixels or base64 in JSON. When an image tool is available, you alone decide whether the answer needs an image; call it only when useful, inspect the returned image, and bind an accepted asset_id through generated_images. If you do not call it, return normal text JSON without generated_images.",
            "For 作图题 with question.drawing_generation_mode=code, the dedicated drawing-code generator is the primary figure path. The answer model may output drawing_code_specs only as an optional fallback; do not spend excessive tokens on code when clear drawing requirements and analysis are enough.",
            "For 作图题 with question.drawing_generation_mode=code, if you do output drawing_code_specs, each code item must define exactly one function draw(output_path: str) -> None and save a PNG to output_path using Matplotlib. Use Chinese explanatory text, preserve the standard technical terms and notation required by the question, and make the figure black-and-white printable by using line styles, markers, hatch, direct labels, offsets, or subplots instead of color.",
            "For multi-part calculation questions, each calculation-type answer unit should have at least one calculation/judgment step unless the unit itself clearly does not require a calculation answer.",
            "Never insert a newline inside Chinese ordinal labels such as 第(2)问, 第2小问, or 第3步; keep the whole label on one line.",
            "For calculation questions, use Arabic numerals and standard units/symbols in text, e.g. 55.56 mol, 1.043×10^-3 m^3, -2261 kJ; do not write numbers as Chinese words.",
            "For calculation questions, every important relation, substitution expression, and result expression must appear in formulas; steps should reference those formulas by 1-based indices.",
            "Keep formulas and calculation_contract only at the question-draft top level. answer_units may reference top-level formulas by index but must not contain their own formulas or calculation_contract fields.",
            "For calculation questions, calculation_contract is mandatory. Copy every requested numerical output from the question into requested_outputs, record every final numerical quantity once in result_quantities, set formula_index to the 1-based index of the matching result formula, and declare each exhaustive composition/fraction/probability distribution as a partition whose components share one basis and sum to expected_total (normally 1 or 100). This ledger is machine-checked and is not rendered to users.",
            "For any multi-stage split, reaction, transfer, precipitation, loss, or transformation, put the pre-change parent amount in intermediate_quantities and add a transitions item. The transition products must include both the derived amount and the retained/remainder amount on the same global basis and must sum to the parent. If local_fraction is given, derived_quantity_id must equal parent_quantity_id multiplied by that local fraction. Do not multiply by a different coexisting quantity. Composite groups remain intact unless the requested final partition explicitly decomposes that group.",
            "The scope of requested_outputs is controlled only by the question stem and confirmed answer units. Textbook evidence may support an answer but must never introduce an additional requested output. In particular, never treat a textbook discussion of a related quantity as if the question had asked for it.",
            "For term explanation, short-answer, essay, proof, derivation, fill-in, judgment, graphic, and comprehensive questions, follow the matching requirements in answer_content_quality_requirements.",
            "Strictly follow the confirmed question_type fields. Do not infer 作图题 from drawing-related words in the stem when the confirmed question_type is not 作图题.",
            "If a judgment, fill-in, short-answer, proof, or essay question uses a criterion, definition, inequality, proportional relation, or named equation, put that relation in formulas instead of translating it into long Chinese prose.",
            "Do not write formula paraphrases such as 'Gibbs free energy is less than zero', 'equals enthalpy minus temperature times entropy', or similar Chinese wording in analysis; use formulas and explain only the conclusion and variable meaning.",
            "For non-calculation questions, write analysis as a step-by-step reasoning chain; do not start analysis with a formula list or grouped formula dump. Include each formula only where the corresponding criterion, relation, or definition is used.",
            "For non-calculation questions with formulas, prefer analysis_segments over plain analysis: each segment must be normal answer-book prose with inline placeholders such as {f1}, {f2} at the exact position where the formula, reaction, criterion, symbol abbreviation, or inequality belongs.",
            "Formula placeholders and formula_indices are one-based: the first formula is {f1} / index 1. Never output {f0} or formula_indices containing 0.",
            "For non-calculation questions, formula_indices is only a backup declaration; the visible answer-book style depends on inline {fN} placeholders inside analysis_segments.text.",
            "For non-calculation questions, do not leave useful formulas detached from the reasoning. If a formula is necessary, reference it in the relevant analysis_segments item; if it is not necessary, omit it from formulas.",
            "Do not put simple professional labels, axis labels, peak labels, or unit strings into formulas only to satisfy formula rules. Keep them in normal text, symbolic_notations, figure_specs.required_labels, or drawing-code labels unless they are part of an actual relation or criterion.",
            "For 作图题, formulas should only contain relations that explain drawing logic. Pure labels, coordinates, phase names, crystal-plane labels, and axis labels belong to figure labels or symbolic_notations, not detached formulas.",
            "Only when the confirmed question_type or requirement question_type is 作图题, output drawing_code_specs or figure_specs according to drawing_generation_mode. generated_images is a separate main-model tool result and may be used for any question only when you judge that it materially improves the answer.",
            "For 作图题 with question.drawing_generation_mode=figure_specs, if question.figure_schema_plan.schema_resolution.status is schema_found, figure_specs.kind must use that planned registry kind unless render_decision.strategy is source_image_overlay; do not invent another kind.",
            "For a multipart question, each drawing answer unit must follow that unit's own figure_schema_plan and emit its own figure_specs item; never merge two independent drawing units into one crowded image.",
            "For a registered schema, populate the schema's required_fields exactly. Use generic elements only for custom_diagram; for microstructure_schematic use features, for generic_axis_curve use points, and for multi_curve_axis_plot use series.",
            *domain_answer_rules,
            "If question.figure_schema_plan.render_decision.strategy is source_image_overlay, output exactly one figure_spec with kind=source_image_overlay, source_image_index, caption, and annotations. Use normalized [0,1] image coordinates for line/arrow/rectangle/ellipse/point/text annotations. Never return a replacement base image or a local source_image path.",
            "For 作图题 with question.drawing_generation_mode=figure_specs, if no registry schema is available, use custom_diagram only when the required figure can be expressed with line/arrow/circle/ellipse/arc/text/point primitives; otherwise leave figure_specs empty and put the reason in uncertainties so the image-model fallback can be audited.",
            "If question_understanding contains images or tables, use it as the authoritative normalized question surface. Do not ignore image OCR, visual labels, axes, legends, table_rows, or table visual notes.",
            "Textbook evidence may include asset_available=true for a textbook figure/table/equation image. If no actual image is attached to this prompt, do not claim to have read the original textbook image; use only content, caption, ocr_text, table_html, table_rows, visual_summary, and surrounding_text_preview.",
            "If textbook evidence has text_model_can_read_visual=false, treat the visual detail as unavailable and put the limitation in uncertainties instead of inventing image content.",
            "For custom_diagram, use only these element types: line, arrow, circle, ellipse, arc, text, point. Every required visual item must appear as an element with a clear label or text.",
            "For custom_diagram coordinates, use a simple 2D coordinate system; arrows must use start and end coordinates that match the required direction.",
            "If a required figure cannot be specified confidently, leave the required drawing output empty and add the reason to uncertainties; never request a generic placeholder figure.",
            "Follow answer_depth_profile strictly: concise low-score questions must stay short; deep high-score or comprehensive questions must be split into sufficient steps/paragraphs.",
            "For multi-part questions, align answer and analysis with each sub-question; do not squeeze multiple requested parts into one crowded paragraph.",
            "Do not exceed answer_depth_profile.max_analysis_sentences unless the question is a calculation or comprehensive proof that requires formulas/steps.",
            "Put formulas in the formulas array; the program will convert them to final formula objects.",
            "If the question or textbook content is insufficient, write the reason in uncertainties instead of inventing content."
        ],
        "answer_depth_profile": depth_profile,
        "output_schema_example": schema_hint,
        "question": _prompt_question_record(question),
        "figure_schema_registry": _figure_schema_registry_for_prompt(question),
        "question_understanding": understanding,
        "textbook_content": [_textbook_content_record(row) for row in evidence],
    }
    if not include_textbook_evidence:
        user_payload["analysis_profile"] = "question_only"
        user_payload.pop("textbook_content", None)
        evidence_markers = ("textbook", "教材", "evidence_id", "citation", "page numbers")
        user_payload["hard_rules"] = [
            rule for rule in user_payload["hard_rules"]
            if not any(marker in rule.lower() for marker in evidence_markers)
        ]
        user_payload["hard_rules"].extend(
            [
                "Use only the confirmed question surface and established disciplinary knowledge; do not invent missing conditions.",
                "Do not output textbook references, citations, page numbers, evidence IDs, or a 教材依据 block.",
                "This profile does not run textbook indexing, retrieval, or evidence binding.",
            ]
        )
    if not has_calc_unit:
        user_payload["hard_rules"] = [
            rule
            for rule in user_payload["hard_rules"]
            if not (
                rule.startswith("For calculation questions")
                or rule.startswith("For multi-part calculation questions")
                or "Calculation steps must" in rule
                or "计算题按答案册" in rule
            )
        ]
    user_text = json.dumps(user_payload, ensure_ascii=False)
    raw_plan = question.get("figure_schema_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_decision = plan.get("render_decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    overlay_requires_pixels = str(decision.get("strategy") or "") == "source_image_overlay"
    image_parts = question_image_parts(question) if overlay_requires_pixels or not understanding else []
    user_content: str | list[dict[str, Any]]
    if image_parts:
        user_content = [{"type": "text", "text": user_text}, *image_parts]
    else:
        user_content = user_text
    return ensure_generation_image_label_language_requirement([
        {"role": "system", "content": SYSTEM_PROMPT if include_textbook_evidence else QUESTION_ONLY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ])


def build_answer_fragment_prompt(question: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    return build_answer_draft_prompt(question, evidence)
