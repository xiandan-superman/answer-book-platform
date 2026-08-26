from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree

from app.capabilities.text_expression_rendering import build_text_expression_render_plans
from app.docx_v4 import build_docx_from_fragments
from app.expression_promotion import promote_inline_mathematical_expressions
from app.practice_export import build_practice_question_docx


NASA_UNIT_CONVERSION = (
    "At 36,089 ft altitude, convert the altitude to miles using 1 mile = 5,280 ft."
)
NASA_IDEAL_GAS = "Explain which quantities are connected by the ideal-gas equation pV=nRT."
NAMESPACES = {
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def _document_xml(content: bytes) -> etree._Element:
    with ZipFile(BytesIO(content)) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def _docx_texts(root: etree._Element) -> tuple[list[str], list[str]]:
    prose = root.xpath("//w:t/text()", namespaces=NAMESPACES)
    formulas = [
        "".join(node.xpath(".//m:t/text()", namespaces=NAMESPACES))
        for node in root.xpath("//m:oMath", namespaces=NAMESPACES)
    ]
    return prose, formulas


def _fragment(text: str) -> dict:
    return {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q_english_equation",
        "display_number": "1",
        "section": "简答题",
        "question_type": "简答题",
        "answer": "见解析",
        "evidence_ids": [],
        "formulas": [],
        "blocks": [{"label": "解析", "segments": [{"type": "text", "text": text}]}],
    }


def test_text_equation_planner_rejects_broad_english_prose_and_keeps_local_formula() -> None:
    first = build_text_expression_render_plans(NASA_UNIT_CONVERSION)
    second = build_text_expression_render_plans(NASA_IDEAL_GAS)
    mixed = build_text_expression_render_plans("理想气体满足 pV=nRT，其中 p 为压强。")
    short_english = build_text_expression_render_plans("At x=1.")
    thermodynamic = build_text_expression_render_plans("ΔrGmθ=ΔrHmθ-TΔrSmθ")
    partial = build_text_expression_render_plans("由(∂ΔrGmθ/∂T)p=-ΔrHmθ/T²可知。")
    reaction = build_text_expression_render_plans("反应式为 2H₂+O₂→2H₂O。")

    assert first == []
    assert [(plan.raw, plan.start, plan.end) for plan in second] == [("pV=nRT", 65, 71)]
    assert [(plan.raw, plan.start, plan.end) for plan in mixed] == [("pV=nRT", 7, 13)]
    assert [(plan.raw, plan.start, plan.end) for plan in short_english] == [("x=1", 3, 6)]
    assert [(plan.raw, plan.rule_id) for plan in thermodynamic] == [
        ("ΔrGmθ=ΔrHmθ-TΔrSmθ", "core.text_equation")
    ]
    partial_equation = next(plan for plan in partial if plan.rule_id == "core.text_equation")
    assert partial_equation.raw == "(∂ΔrGmθ/∂T)p=-ΔrHmθ/T²"
    assert r"\partial" in partial_equation.render_latex
    assert [(plan.raw, plan.rule_id) for plan in reaction] == [
        ("2H₂+O₂→2H₂O", "core.text_reaction")
    ]


def test_true_exam_structure_promotion_keeps_english_prose_and_only_promotes_local_formula() -> None:
    first = promote_inline_mathematical_expressions(_fragment(NASA_UNIT_CONVERSION))
    second = promote_inline_mathematical_expressions(_fragment(NASA_IDEAL_GAS))

    assert first["formulas"] == []
    assert first["blocks"][0]["segments"] == [{"type": "text", "text": NASA_UNIT_CONVERSION}]
    assert [formula["latex"] for formula in second["formulas"]] == ["pV=nRT"]
    assert second["blocks"][0]["segments"] == [
        {"type": "text", "text": "Explain which quantities are connected by the ideal-gas equation "},
        {"type": "formula_ref", "formula_id": "f_q_english_equation_inline_math_01", "inline": True},
        {"type": "text", "text": "."},
    ]


@pytest.mark.parametrize("source_mode", ["exam", "knowledge"])
def test_practice_and_knowledge_word_exports_keep_prose_in_word_text(source_mode: str) -> None:
    content = build_practice_question_docx(
        {
            "source_mode": source_mode,
            "exercises": [
                {"number": 1, "question_type": "简答题", "stem": NASA_UNIT_CONVERSION},
                {"number": 2, "question_type": "简答题", "stem": NASA_IDEAL_GAS},
            ],
        }
    )
    prose, formulas = _docx_texts(_document_xml(content))

    assert NASA_UNIT_CONVERSION in prose
    assert "Explain which quantities are connected by the ideal-gas equation " in prose
    assert formulas == ["pV=nRT"]
    assert all("At36,089ftaltitude" not in value for value in formulas)
    assert all("Explainwhichquantities" not in value for value in formulas)


def test_true_exam_word_output_keeps_prose_in_word_text_and_local_formula_in_math(tmp_path: Path) -> None:
    fragments_path = tmp_path / "fragments.json"
    output_path = tmp_path / "answer_book.docx"
    fragment = promote_inline_mathematical_expressions(_fragment(NASA_UNIT_CONVERSION))
    second = promote_inline_mathematical_expressions(_fragment(NASA_IDEAL_GAS))
    second["question_id"] = "q_english_equation_2"
    fragments_path.write_text(json.dumps({"fragments": [fragment, second]}, ensure_ascii=False), encoding="utf-8")

    build_docx_from_fragments(fragments_path, output_path)
    with ZipFile(output_path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    prose, formulas = _docx_texts(root)

    assert NASA_UNIT_CONVERSION in prose
    assert "Explain which quantities are connected by the ideal-gas equation " in prose
    assert formulas == ["pV=nRT"]
