from __future__ import annotations

from app.expression_promotion import promote_inline_mathematical_expressions
from app.formula_audit import audit_text_segments_no_formula
from app.answer_generation import _replace_formula_placeholders_in_text
from app.fragment_repair import _repair_formula_text_segments


def test_promoted_sentence_formula_stays_inline() -> None:
    fragment = {
        "question_id": "q1",
        "formulas": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [{"type": "text", "text": "从基体析出二次相β_II，继续冷却。"}],
            }
        ],
    }

    repaired = _repair_formula_text_segments(fragment)

    formula = repaired["formulas"][0]
    reference = repaired["blocks"][0]["segments"][1]
    assert formula["latex"] == r"\beta_{II}"
    assert formula["display"] is False
    assert reference["type"] == "formula_ref"
    assert reference["inline"] is True


def test_ratio_equivalence_is_promoted_before_generation_validation() -> None:
    fragment = {
        "question_id": "q_ratio",
        "formulas": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [{"type": "text", "text": "初生组织质量比为36.53:63.47≈0.575:1。"}],
            }
        ],
    }

    repaired = promote_inline_mathematical_expressions(fragment)

    assert repaired["formulas"][0]["latex"] == r"36.53:63.47\approx0.575:1"
    assert repaired["blocks"][0]["segments"][1]["type"] == "formula_ref"
    assert repaired["blocks"][0]["segments"][1]["inline"] is True


def test_compact_prose_reuses_declared_latex_formula() -> None:
    fragment = {
        "question_id": "q_crystal",
        "formulas": [
            {"formula_id": "f1", "latex": r"d_{100}=\frac{a}{2}", "role": "result"},
            {"formula_id": "f2", "latex": r"\mathbf{b}=\frac{a}{2}\langle111\rangle", "role": "result"},
        ],
        "blocks": [
            {
                "label": "解析",
                "segments": [{"type": "text", "text": "层间距写为d_{100}=a/2，位错为b=(a/2)<111>，也常简写为(a/2)<111>。"}],
            }
        ],
    }

    repaired = promote_inline_mathematical_expressions(fragment)

    refs = [item for item in repaired["blocks"][0]["segments"] if item.get("type") == "formula_ref"]
    assert [item["formula_id"] for item in refs] == ["f1", "f2", "f2"]
    assert len(repaired["formulas"]) == 2


def test_declared_symbolic_fraction_is_promoted_in_correction_note() -> None:
    fragment = {
        "question_id": "q_fraction",
        "formulas": [{"formula_id": "f1", "latex": r"d=\frac{a}{2}", "role": "result"}],
        "blocks": [{"label": "易错点", "segments": [{"type": "text", "text": "不应把a/2误写为a。"}]}],
    }

    repaired = promote_inline_mathematical_expressions(fragment)

    assert repaired["blocks"][0]["segments"][1]["formula_id"] == "f1"


def test_compact_thermodynamic_symbols_are_promoted_without_model_repair() -> None:
    fragment = {
        "question_id": "q_thermo",
        "formulas": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {
                        "type": "text",
                        "text": "由(∂ΔrGmθ/∂T)p=-ΔrHmθ/T²可知，ΔrCp,m决定趋势，并注意pV、lnKθT、p_外与Q/T。",
                    }
                ],
            }
        ],
    }

    repaired = promote_inline_mathematical_expressions(fragment)

    assert not audit_text_segments_no_formula(repaired["blocks"])
    latex = "|".join(str(item.get("latex") or "") for item in repaired["formulas"])
    assert r"\Delta_{\mathrm{r}}" in latex
    assert r"C_{p,\mathrm{m}}" in latex
    assert any(segment.get("type") == "formula_ref" for segment in repaired["blocks"][0]["segments"])


def test_split_formula_subscript_suffix_is_rejoined_without_mutating_original() -> None:
    fragment = {
        "question_id": "q_split_script",
        "formulas": [{"formula_id": "f1", "latex": r"\Delta C", "role": "relation", "display": False}],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "formula_ref", "formula_id": "f1", "inline": True},
                    {"type": "text", "text": "_{V,m}>0，则增大。"},
                ],
            }
        ],
    }

    repaired = promote_inline_mathematical_expressions(fragment)

    reference = repaired["blocks"][0]["segments"][0]
    assert reference["formula_id"] != "f1"
    assert repaired["formulas"][0]["latex"] == r"\Delta C"
    assert repaired["formulas"][-1]["latex"] == r"\Delta C_{V,m}"
    assert repaired["blocks"][0]["segments"][1]["text"].startswith(">0")


def test_split_partial_derivative_is_joined_before_generation_validation() -> None:
    fragment = {
        "question_id": "q_partial",
        "formulas": [
            {"formula_id": "f1", "latex": r"\Delta G_m", "role": "relation", "display": False},
            {"formula_id": "f2", "latex": "p", "role": "relation", "display": False},
        ],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": "由(∂("},
                    {"type": "formula_ref", "formula_id": "f1", "inline": True},
                    {"type": "text", "text": "/"},
                    {"type": "formula_ref", "formula_id": "f2", "inline": True},
                    {"type": "text", "text": ")T>0可知结论。"},
                ],
            }
        ],
    }

    repaired = promote_inline_mathematical_expressions(fragment)

    segments = repaired["blocks"][0]["segments"]
    assert segments[0]["type"] == "text" and segments[0]["text"] == "由"
    assert segments[1]["type"] == "formula_ref"
    joined = next(item for item in repaired["formulas"] if item["formula_id"] == segments[1]["formula_id"])
    assert joined["latex"] == r"\left(\frac{\partial \Delta G_m}{\partial p}\right)_{T}"
    assert segments[2]["text"] == ">0可知结论。"


def test_formula_prefix_and_plain_subscript_are_joined_across_segment_boundaries() -> None:
    fragment = {
        "question_id": "q_boundary",
        "formulas": [
            {"formula_id": "f1", "latex": "S_m", "role": "relation", "display": False},
            {"formula_id": "f2", "latex": r"\Delta G", "role": "relation", "display": False},
        ],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": "升温使 -TΔ"},
                    {"type": "formula_ref", "formula_id": "f1", "inline": True},
                    {"type": "text", "text": " 增大，不利于 "},
                    {"type": "formula_ref", "formula_id": "f2", "inline": True},
                    {"type": "text", "text": "_m 减小。"},
                ],
            }
        ],
    }

    repaired = promote_inline_mathematical_expressions(fragment)
    formulas = {item["formula_id"]: item["latex"] for item in repaired["formulas"]}
    refs = [item for item in repaired["blocks"][0]["segments"] if item.get("type") == "formula_ref"]

    assert formulas[refs[0]["formula_id"]] == r"-T\Delta S_m"
    assert formulas[refs[1]["formula_id"]] == r"\Delta G_m"
    assert repaired["blocks"][0]["segments"][0]["text"] == "升温使 "
    assert repaired["blocks"][0]["segments"][-1]["text"] == " 减小。"


def test_three_segment_split_formula_subscript_suffix_is_rejoined() -> None:
    fragment = {
        "question_id": "q_split_script_three_segments",
        "formulas": [
            {"formula_id": "f1", "latex": r"\Delta_{rC}", "role": "relation", "display": False},
            {"formula_id": "f2", "latex": r"V,m}=0", "role": "relation", "display": False},
        ],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "formula_ref", "formula_id": "f1", "inline": True},
                    {"type": "text", "text": "_{"},
                    {"type": "formula_ref", "formula_id": "f2", "inline": True},
                    {"type": "text", "text": "，则不变。"},
                ],
            }
        ],
    }

    repaired = promote_inline_mathematical_expressions(fragment)

    reference = repaired["blocks"][0]["segments"][0]
    joined = next(item for item in repaired["formulas"] if item["formula_id"] == reference["formula_id"])
    assert joined["latex"] == r"\Delta_{\mathrm{r}} C_{V,m}=0"
    assert repaired["blocks"][0]["segments"][1]["text"] == ""
    assert repaired["blocks"][0]["segments"][2]["text"] == ""
    assert all(item["formula_id"] != "f2" for item in repaired["formulas"])


def test_formula_placeholder_with_mnemonic_suffix_uses_numeric_index() -> None:
    text = _replace_formula_placeholders_in_text(
        "结果见{f2_b}。",
        [{"latex": "x=a"}, {"latex": r"d=\frac{a}{2}"}],
    )

    assert "{f2_b}" not in text
    assert "d=" in text
