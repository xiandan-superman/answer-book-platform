from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from .answer_quality_requirements import ANSWER_CONTENT_QUALITY_REQUIREMENTS
from .figure_schema_registry import get_schema, schema_prompt_catalog
from .question_scores import confirmed_score_from_question, normalize_score
from .question_types import has_calculation_answer_unit, question_has_type, question_kind
from .drawing_code import question_drawing_mode


SYSTEM_PROMPT = """你是专业考研真题解析教师，你输出的答案要倾向专业考研真题解析，要平衡学生理解和解析深度。
你只负责根据输入的真题和教材内容完成解析，并输出一个合法 JSON object。
不得描述流程，不得生成 Markdown，不得输出 JSON 之外的任何文字，不得要求用户补文件。
不得输出页码、evidence_id、候选证据编号、平台内部字段或最终排版内容。
解析内容必须满足输入中的《真题解析内容质量要求》。
"""


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
    drawing_mode = question_drawing_mode(question) if question_has_type(question, "作图题") else ""
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
        "stem": question.get("stem", ""),
        "subquestions": question.get("subquestions", []),
        "needs_figure": question_has_type(question, "作图题"),
        "drawing_generation_mode": drawing_mode,
        "figure_schema_plan": question.get("figure_schema_plan", {}) if question_has_type(question, "作图题") else {},
    }


def _figure_schema_registry_for_prompt(question: dict[str, Any]) -> list[dict[str, Any]]:
    if not question_has_type(question, "作图题"):
        return []
    if question_drawing_mode(question) == "code":
        return []
    plan = question.get("figure_schema_plan") if isinstance(question.get("figure_schema_plan"), dict) else {}
    resolution = plan.get("schema_resolution") if isinstance(plan.get("schema_resolution"), dict) else {}
    kind = str(resolution.get("kind") or "").strip()
    schema = get_schema(kind) if kind else None
    if not schema:
        return schema_prompt_catalog()
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


def build_answer_draft_prompt(question: dict[str, Any], evidence: list[dict[str, Any]], question_understanding: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    depth_profile = build_answer_depth_profile(question)
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
                ]
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
        "symbolic_notations": [
            "可选。只放专业符号、图示标签、晶面指数、晶向指数、晶面族、晶向族、相区名、坐标轴标签或单位说明；不要把完整关系式、判据式、反应式放在这里。涉及负指数时必须用 LaTeX 上横线，例如 {10\\bar{1}0}、<11\\bar{2}0>。"
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
                "elements": [
                    {"type": "circle", "center": [0, 0], "radius": 0.8, "label": "对象标签"},
                    {"type": "arrow", "start": [0, 0.9], "end": [0, 0.1], "label": "箭头含义", "color": "#dc2626"},
                    {"type": "line", "start": [0, 0], "end": [0.8, 0], "label": "线段含义", "style": "dashed"},
                    {"type": "text", "xy": [0, -0.2], "text": "说明文字"}
                ]
            }
        ],
        "mistake_notes": ["计算题或确有必要的题目填写本题专属易错点及注意事项"],
        "uncertainties": []
    }
    user_payload = {
        "task": "generate_question_analysis_draft",
        "answer_content_quality_requirements": ANSWER_CONTENT_QUALITY_REQUIREMENTS,
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
            "For drawing requirements, provide the textual answer context and precise drawing requirements; do not attempt to generate or embed final image pixels.",
            "For 作图题 with question.drawing_generation_mode=code, the dedicated drawing-code generator is the primary figure path. The answer model may output drawing_code_specs only as an optional fallback; do not spend excessive tokens on code when clear drawing requirements and analysis are enough.",
            "For 作图题 with question.drawing_generation_mode=code, if you do output drawing_code_specs, each code item must define exactly one function draw(output_path: str) -> None and save a PNG to output_path using Matplotlib. Use Chinese explanatory text, keep standard terms such as XRD/BCC/FCC/CsCl/hkl/2θ/a.u./[110]/(110), and make the figure black-and-white printable by using line styles, markers, hatch, direct labels, offsets, or subplots instead of color.",
            "For multi-part calculation questions, each calculation-type answer unit should have at least one calculation/judgment step unless the unit itself clearly does not require a calculation answer.",
            "Never insert a newline inside Chinese ordinal labels such as 第(2)问, 第2小问, or 第3步; keep the whole label on one line.",
            "For calculation questions, use Arabic numerals and standard units/symbols in text, e.g. 55.56 mol, 1.043×10^-3 m^3, -2261 kJ; do not write numbers as Chinese words.",
            "For calculation questions, every important relation, substitution expression, and result expression must appear in formulas; steps should reference those formulas by 1-based indices.",
            "For term explanation, short-answer, essay, proof, derivation, fill-in, judgment, graphic, and comprehensive questions, follow the matching requirements in answer_content_quality_requirements.",
            "Strictly follow the confirmed question_type fields. Do not infer 作图题 from words such as 画出、绘制、作图、示意图、图示、标出、衍射花样、晶胞 when the confirmed question_type is not 作图题.",
            "If a judgment, fill-in, short-answer, proof, or essay question uses a criterion, definition, inequality, proportional relation, or named equation, put that relation in formulas instead of translating it into long Chinese prose.",
            "Do not write formula paraphrases such as 'Gibbs free energy is less than zero', 'equals enthalpy minus temperature times entropy', or similar Chinese wording in analysis; use formulas and explain only the conclusion and variable meaning.",
            "For non-calculation questions, write analysis as a step-by-step reasoning chain; do not start analysis with a formula list or grouped formula dump. Include each formula only where the corresponding criterion, relation, or definition is used.",
            "For non-calculation questions with formulas, prefer analysis_segments over plain analysis: each segment must be normal answer-book prose with inline placeholders such as {f1}, {f2} at the exact position where the formula, reaction, criterion, symbol abbreviation, or inequality belongs.",
            "Formula placeholders and formula_indices are one-based: the first formula is {f1} / index 1. Never output {f0} or formula_indices containing 0.",
            "For non-calculation questions, formula_indices is only a backup declaration; the visible answer-book style depends on inline {fN} placeholders inside analysis_segments.text.",
            "For non-calculation questions, do not leave useful formulas detached from the reasoning. If a formula is necessary, reference it in the relevant analysis_segments item; if it is not necessary, omit it from formulas.",
            "Do not put simple professional labels into formulas only to satisfy formula rules. Miller indices such as (111), zone axes such as [110], phase labels such as α/γ/δ/Fe3C/L, axis labels such as w(C) or T, peak labels, and unit strings should stay in normal Chinese text, symbolic_notations, figure_specs.required_labels, or drawing_code_specs labels unless they are part of an actual relation or criterion.",
            "For crystallographic plane indices, direction indices, plane families, and direction families, every negative index must use LaTeX overbar notation. Write {10\\bar{1}0} and <11\\bar{2}0>; never write hyphen forms such as {10-10}, <11-20>, (1-10), or [11-2 0].",
            "For 作图题, formulas should only contain relations that explain drawing logic. Pure labels, coordinates, phase names, crystal-plane labels, and axis labels belong to figure labels or symbolic_notations, not detached formulas.",
            "Only when the confirmed question_type or requirement question_type is 作图题, output drawing_code_specs or figure_specs according to drawing_generation_mode; do not output drawing outputs for non-作图题 even if the stem contains drawing-related words.",
            "For 作图题 with question.drawing_generation_mode=figure_specs, if question.figure_schema_plan.schema_resolution.status is schema_found, figure_specs.kind must use that planned registry kind and fill only its professional parameters; do not invent another kind.",
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
    image_parts = [] if understanding else question_image_parts(question)
    user_content: str | list[dict[str, Any]]
    if image_parts:
        user_content = [{"type": "text", "text": user_text}, *image_parts]
    else:
        user_content = user_text
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def build_answer_fragment_prompt(question: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    return build_answer_draft_prompt(question, evidence)
