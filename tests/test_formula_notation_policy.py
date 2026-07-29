from __future__ import annotations

import json

from app.answer_generation import fragment_from_analysis_draft
from app.drawing_code import drawing_domain_quality_rules
from app.formula_audit import symbolic_formula_like_matches
from app.prompts import build_answer_draft_prompt


def _block_labels(fragment: dict) -> list[str]:
    return [str(block.get("label") or "") for block in fragment.get("blocks", [])]


def test_graphic_unplaced_formulas_render_as_drawing_basis_not_pending_review():
    draft = {
        "question_id": "q_graphic_formula_policy",
        "answer": "画出(111)与(435)晶面并标出截距。",
        "analysis": "本题按米勒指数截距法作图。",
        "formulas": [
            {"latex": r"x:y:z=\frac{a}{h}:\frac{a}{k}:\frac{a}{l}", "meaning": "晶面作图截距关系", "role": "relation", "display": True},
            {"latex": r"x:y:z=\frac{a}{4}:\frac{a}{3}:\frac{a}{5}", "meaning": "(435)晶面截距标注", "role": "diagram_label", "display": True},
        ],
    }

    fragment = fragment_from_analysis_draft(
        draft,
        {"question_id": "q_graphic_formula_policy", "question_type": "作图题", "stem": "画出(111)和(435)晶面。"},
        [],
    )

    labels = _block_labels(fragment)
    assert "作图依据与符号" in labels
    assert "待复核公式" not in labels
    assert not any("待复核公式" in warning for warning in fragment.get("warnings", []))


def test_domain_notation_text_is_not_treated_as_formula_leak():
    text = "作图时标出(435)面截距 a/4、a/3、a/5，并标明(111)晶面。"

    assert symbolic_formula_like_matches(text) == []


def test_real_equation_text_is_still_treated_as_formula_leak():
    text = "菲克第一定律为 J=-D dρ/dx，应说明符号含义。"

    assert symbolic_formula_like_matches(text)


def test_answer_prompt_requires_latex_overbar_for_negative_crystal_indices():
    messages = build_answer_draft_prompt(
        {"question_id": "q_index", "question_type": "简答题", "stem": "写出晶面族{10-10}和晶向族<11-20>的规范表示。"},
        [],
    )
    user_payload = json.loads(str(messages[-1]["content"]))
    payload = "\n".join(user_payload["hard_rules"])

    assert "{10\\bar{1}0}" in payload
    assert "<11\\bar{2}0>" in payload
    assert "{10-10}" in payload
    assert "<11-20>" in payload
    assert "every negative index must use LaTeX overbar notation" in payload


def test_drawing_prompt_rules_require_latex_overbar_for_negative_crystal_indices():
    rules = drawing_domain_quality_rules({"stem": "画出六方晶系晶面族{10-10}和晶向族<11-20>。"})
    text = "\n".join(rules)

    assert "{10\\bar{1}0}" in text
    assert "<11\\bar{2}0>" in text
    assert "{10-10}" in text
    assert "<11-20>" in text
