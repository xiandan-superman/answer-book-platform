from __future__ import annotations

import json

from app.fragment_repair import repair_answer_fragments_for_docx
from app.v4_schema import validate_v4_answer_fragment


def test_split_partial_derivative_is_repaired_and_schema_candidate_is_cleared(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    message = "模型生成内容存在审查问题，已保留当前候选内容进入正式文件并在审查记录中标记。"
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "answer": "小于零",
        "evidence_ids": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": "由题意可考察(∂("},
                    {"type": "formula_ref", "formula_id": "f1"},
                    {"type": "text", "text": "/"},
                    {"type": "formula_ref", "formula_id": "f2"},
                    {"type": "text", "text": ")T的符号。"},
                ],
            }
        ],
        "formulas": [
            {"formula_id": "f1", "latex": r"\Delta G_m", "role": "symbol", "display": False},
            {"formula_id": "f2", "latex": r"\partial p", "role": "symbol", "display": False},
        ],
        "warnings": [message],
        "_review_flags": [{"code": "answer_generation_review_candidate", "message": message}],
        "_review_candidate_issues": [
            "blocks[0].segments[0].text contains formula-like content; matched expression: ∂"
        ],
        "_meta": {"recovered_by": "review_candidate_preserved"},
    }
    path.write_text(json.dumps({"fragments": [fragment]}, ensure_ascii=False), encoding="utf-8")

    report = repair_answer_fragments_for_docx(path)
    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]

    assert report["ok"] is True
    assert report["changed"] is True
    assert validate_v4_answer_fragment(repaired) == []
    assert repaired["warnings"] == []
    assert repaired["_review_flags"] == []
    assert "_review_candidate_issues" not in repaired
    assert repaired["_meta"]["recovered_by"] == "deterministic_schema_repair"
    merged = [
        item
        for item in repaired["formulas"]
        if "partial_promoted" in item["formula_id"] or "partial_repair" in item["formula_id"]
    ]
    assert len(merged) == 1
    assert merged[0]["latex"] == r"\left(\frac{\partial \Delta G_m}{\partial p}\right)_{T}"


def test_semantic_candidate_is_not_cleared_by_schema_repair(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "answer": "候选答案",
        "evidence_ids": [],
        "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "仍需核对学科结论。"}]}],
        "formulas": [],
        "warnings": ["需复核"],
        "_review_flags": [{"code": "answer_generation_review_candidate", "message": "需复核"}],
        "_review_candidate_issues": ["missing_answer_units:2"],
    }
    path.write_text(json.dumps({"fragments": [fragment]}, ensure_ascii=False), encoding="utf-8")

    repair_answer_fragments_for_docx(path)
    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]

    assert repaired["_review_candidate_issues"] == ["missing_answer_units:2"]
    assert repaired["_review_flags"][0]["code"] == "answer_generation_review_candidate"


def test_echoed_word_math_metadata_and_transition_arrows_are_repaired(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    marker = '⟦MATHML:<math xmlns="http://www.w3.org/1998/Math/MathML"><mn>1000</mn><mi>K</mi></math>⟧'
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "answer": "见解析",
        "answer_summary": f"在1000 K{marker}保温后，序列为 G.P.区 → θ″ → θ′。",
        "evidence_ids": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [{"type": "text", "text": f"1000 K{marker}下按 G.P.区 → θ″ → θ′ 转变。"}],
            }
        ],
        "formulas": [],
    }
    path.write_text(json.dumps({"fragments": [fragment]}, ensure_ascii=False), encoding="utf-8")

    report = repair_answer_fragments_for_docx(path)
    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]

    assert report["ok"] is True
    assert report["changed"] is True
    assert "⟦MATHML:" not in repaired["answer_summary"]
    assert all("⟦MATHML:" not in str(segment.get("text") or "") for segment in repaired["blocks"][0]["segments"])
    arrow_formulas = [formula for formula in repaired["formulas"] if formula.get("latex") == r"\to"]
    assert len(arrow_formulas) == 2
    assert validate_v4_answer_fragment(repaired) == []


def test_explanatory_parentheses_are_not_swallowed_by_formula_repair(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "answer": "见解析",
        "evidence_ids": [],
        "blocks": [
            {
                "label": "教材依据",
                "segments": [
                    {"type": "text", "text": "热力学第一定律(ΔU=Q+W）：课本-p40"}
                ],
            }
        ],
        "formulas": [],
    }
    path.write_text(json.dumps({"fragments": [fragment]}, ensure_ascii=False), encoding="utf-8")

    report = repair_answer_fragments_for_docx(path)
    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]
    segments = repaired["blocks"][0]["segments"]

    assert report["ok"] is True
    assert segments[0] == {"type": "text", "text": "热力学第一定律("}
    assert segments[1]["type"] == "formula_ref"
    assert segments[2] == {"type": "text", "text": "）：课本-p40"}
    assert repaired["formulas"][0]["latex"] == r"\Delta U=Q+W"


def test_split_mathml_metadata_and_missing_formula_reference_preserve_candidate_text(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "answer": "见解析",
        "answer_summary": "体积自由能差{f2}为驱动力。",
        "evidence_ids": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": '在1000 K⟦MATHML:<math xmlns="http://www.w3.org/1998/Math/MathML"><mn>'},
                    {"type": "formula_ref", "formula_id": "bogus"},
                    {"type": "text", "text": "1000</mn><mi>K</mi></math>⟧保温，体积自由能差{f2}为驱动力。"},
                ],
            }
        ],
        "formulas": [{"formula_id": "bogus", "latex": "mn>1000", "role": "symbol", "display": False}],
    }
    path.write_text(json.dumps({"fragments": [fragment]}, ensure_ascii=False), encoding="utf-8")

    report = repair_answer_fragments_for_docx(path)
    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]
    text = "".join(str(segment.get("text") or "") for segment in repaired["blocks"][0]["segments"])

    assert report["ok"] is True
    assert text == "在1000 K保温，体积自由能差为驱动力。"
    assert "{f2}" not in repaired["answer_summary"]
    assert not any(segment.get("formula_id") == "bogus" for segment in repaired["blocks"][0]["segments"])
    assert any(flag.get("code") == "unresolved_formula_reference_removed" for flag in repaired["_review_flags"])
    assert validate_v4_answer_fragment(repaired) == []


def test_fragment_generation_never_promotes_mathml_markup_from_question_stem() -> None:
    from app.answer_generation import fragment_from_analysis_draft
    from app.expression_promotion import promote_inline_mathematical_expressions

    marker = '⟦MATHML:<math xmlns="http://www.w3.org/1998/Math/MathML"><mn>1000</mn><mi>K</mi></math>⟧'
    question = {
        "question_id": "q_mathml_stem",
        "number": "1",
        "question_type": "简答题",
        "stem": f"(1)说明机理\n(2)在1000 K{marker}保温后画图",
        "subquestions": [
            {"number": "1.1", "question_type": "简答题", "stem": f"说明1000 K{marker}下的机理"},
            {
                "number": "1.2",
                "question_type": "简答题",
                "stem": "画图",
                "requirements": [{"number": "1.2.1", "stem": f"标出1000 K{marker}条件"}],
            },
        ],
    }
    draft = {
        "answer": "见解析",
        "analysis": "机理如下。",
        "answer_units": [
            {"number": "1.1", "answer": "机理如下。", "analysis_segments": [{"text": "机理如下。"}]},
            {"number": "1.2", "answer": "见图。", "analysis_segments": [{"text": "绘制曲线。"}]},
        ],
    }

    fragment = promote_inline_mathematical_expressions(fragment_from_analysis_draft(draft, question, []))
    serialized = json.dumps(fragment, ensure_ascii=False)

    assert "MATHML" not in serialized
    assert "mn > 1000" not in serialized


def test_term_explanation_drops_unreferenced_pending_formula_block(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "question_type": "名词解释",
        "section": "一、名词解释",
        "answer": "相平衡条件：各相中同一组元的化学势相等。",
        "evidence_ids": [],
        "blocks": [
            {"label": "解析", "segments": [{"type": "text", "text": "给出核心定义。"}]},
            {"label": "待复核公式", "segments": [{"type": "formula_ref", "formula_id": "f1"}]},
        ],
        "formulas": [{"formula_id": "f1", "latex": "\\mu_A^a=\\mu_A^b", "role": "relation", "display": True}],
        "warnings": ["存在未融入解析正文的公式，已列入待复核公式。"],
    }
    path.write_text(json.dumps({"fragments": [fragment]}, ensure_ascii=False), encoding="utf-8")

    report = repair_answer_fragments_for_docx(path)
    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]

    assert report["ok"] is True
    assert "待复核公式" not in [block.get("label") for block in repaired["blocks"]]
    assert repaired.get("warnings", []) == []
    assert repaired["formulas"] == []
