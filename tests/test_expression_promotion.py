from __future__ import annotations


def test_inline_reactions_are_promoted_without_changing_surrounding_prose() -> None:
    from app.expression_promotion import promote_inline_reactions
    from app.v4_schema import validate_v4_answer_fragment

    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "answer": "见解析",
        "evidence_ids": [],
        "formulas": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {
                        "type": "text",
                        "text": "包晶转变：L+δ→γ；共晶转变：L→γ+Fe₃C，产物为莱氏体。",
                    }
                ],
            }
        ],
    }

    promoted = promote_inline_reactions(fragment)

    assert not validate_v4_answer_fragment(promoted)
    assert [item["latex"] for item in promoted["formulas"]] == [
        r"L+\delta\to\gamma",
        r"L\to\gamma+\mathrm{Fe}_{3}\mathrm{C}",
    ]
    segments = promoted["blocks"][0]["segments"]
    assert "包晶转变：" == segments[0]["text"]
    assert segments[1]["type"] == "formula_ref"
    assert segments[3]["type"] == "formula_ref"
    assert segments[-1]["text"] == "，产物为莱氏体。"


def test_program_evidence_block_is_not_rewritten() -> None:
    from app.expression_promotion import promote_inline_reactions

    fragment = {
        "question_id": "q1",
        "formulas": [],
        "blocks": [{"label": "教材依据", "segments": [{"type": "text", "text": "L→α+β"}]}],
    }

    promoted = promote_inline_reactions(fragment)

    assert promoted["formulas"] == []
    assert promoted["blocks"][0]["segments"][0]["text"] == "L→α+β"


def test_grouped_phase_reaction_is_preserved_as_one_expression() -> None:
    from app.expression_promotion import reaction_text_to_latex

    assert reaction_text_to_latex("L→(α+β)_共") == r"L\to(\alpha+\beta)_{共}"


def test_phase_subscript_labels_remain_domain_text_not_formula_leaks() -> None:
    from app.formula_audit import symbolic_formula_like_matches

    assert symbolic_formula_like_matches("初生α_初、二次相β_II和Fe3C_II。") == []
    assert symbolic_formula_like_matches("变量x_i需要转为公式。") == ["_"]


def test_physical_chemistry_mistake_note_equations_are_promoted() -> None:
    from app.expression_promotion import promote_inline_mathematical_expressions
    from app.v4_schema import validate_v4_answer_fragment

    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "calc_s01_03_02",
        "answer": "见解析",
        "evidence_ids": [],
        "formulas": [],
        "blocks": [
            {
                "label": "易错点及注意事项",
                "segments": [
                    {
                        "type": "text",
                        "text": (
                            "为1-2型电解质，平均离子活度需按ν₊=1、ν₋=2计算，"
                            "b±=(1¹·2²)^(1/3)b；最大电功W=-FE，不要误乘以n=2。"
                        ),
                    }
                ],
            }
        ],
    }

    promoted = promote_inline_mathematical_expressions(fragment)

    assert not validate_v4_answer_fragment(promoted)
    latex = [str(item["latex"]) for item in promoted["formulas"]]
    assert any("b" in item and "1/3" in item for item in latex)
    assert any("W=-FE" in item.replace(" ", "") for item in latex)


def test_grouped_inline_symbol_absorbs_both_prose_braces() -> None:
    from app.expression_promotion import promote_inline_mathematical_expressions

    fragment = {
        "question_id": "q_viscometer",
        "answer_summary": "",
        "formulas": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": "纯溶剂流经时间 {t_0} 的测定，再测定 {t_i} 。"}
                ],
            }
        ],
    }

    promoted = promote_inline_mathematical_expressions(fragment)

    assert [item["latex"] for item in promoted["formulas"]] == ["{t_0}", "{t_i}"]
    visible_text = "".join(
        str(item.get("text") or "")
        for item in promoted["blocks"][0]["segments"]
        if item.get("type") == "text"
    )
    assert "{" not in visible_text
    assert "}" not in visible_text


def test_legacy_split_group_wrapper_is_rejoined_from_program_provenance() -> None:
    from app.expression_promotion import promote_inline_mathematical_expressions

    fragment = {
        "question_id": "q_legacy_viscometer",
        "answer_summary": "",
        "formulas": [
            {
                "formula_id": "f1",
                "latex": "t_0}",
                "source_note": "程序在结构校验前从解析正文中提升的数学关系。",
            }
        ],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": "流经时间 {"},
                    {"type": "formula_ref", "formula_id": "f1", "inline": True},
                    {"type": "text", "text": "的测定"},
                ],
            }
        ],
    }

    promoted = promote_inline_mathematical_expressions(fragment)

    assert promoted["formulas"][0]["latex"] == "{t_0}"
    assert promoted["blocks"][0]["segments"][0]["text"] == "流经时间 "


def test_legacy_promoted_formula_continuations_are_rejoined_by_notation_class() -> None:
    from app.expression_promotion import promote_inline_mathematical_expressions

    fragment = {
        "question_id": "q_legacy_continuations",
        "formulas": [
            {"formula_id": "f1", "latex": r"\Delta S=15.44 J \cdot K", "display": False},
            {"formula_id": "f2", "latex": "Q=3.2", "display": False},
        ],
        "blocks": [
            {
                "label": "解题步骤",
                "segments": [
                    {"type": "formula_ref", "formula_id": "f1", "inline": True},
                    {"type": "text", "text": "⁻¹；继续说明。"},
                    {"type": "formula_ref", "formula_id": "f2", "inline": True},
                    {"type": "text", "text": "×10⁻⁵。"},
                ],
            }
        ],
    }

    promoted = promote_inline_mathematical_expressions(fragment)
    formulas = {item["formula_id"]: item["latex"] for item in promoted["formulas"]}
    segments = promoted["blocks"][0]["segments"]

    assert formulas[segments[0]["formula_id"]].endswith("K^{-1}")
    assert segments[1]["text"] == "；继续说明。"
    assert formulas[segments[2]["formula_id"]].endswith(r"\times 10^{-5}")
    assert segments[3]["text"] == "。"


def test_operandless_differential_operators_remain_prose_not_formula_objects() -> None:
    from app.expression_promotion import promote_inline_mathematical_expressions

    fragment = {
        "question_id": "q_operator_semantics",
        "formulas": [
            {
                "formula_id": "f_old",
                "latex": r"\delta",
                "role": "relation",
                "source_note": "程序在结构校验前从解析正文中提升的数学关系。",
            },
            {
                "formula_id": "f_orphan",
                "latex": "x=y",
                "role": "relation",
                "source_note": "程序在结构校验前从解析正文中提升的数学关系。",
            },
        ],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": r"微小量用公式：\partial表示；"},
                    {"type": "formula_ref", "formula_id": "f_old", "inline": True},
                    {"type": "text", "text": "表示变化。"},
                ],
            }
        ],
    }

    promoted = promote_inline_mathematical_expressions(fragment)
    text = "".join(
        str(segment.get("text") or "")
        for segment in promoted["blocks"][0]["segments"]
        if segment.get("type") == "text"
    )

    assert "∂表示" in text
    assert "δ表示" in text
    assert not any(_formula["latex"] in {r"\delta", r"\partial"} for _formula in promoted["formulas"])
    assert "f_orphan" not in {_formula["formula_id"] for _formula in promoted["formulas"]}


def test_physical_quantity_units_are_not_split_into_formula_prefixes() -> None:
    from app.expression_promotion import promote_inline_mathematical_expressions

    fragment = {
        "question_id": "q_physical_units",
        "formulas": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {
                        "type": "text",
                        "text": "压力由10 kPa升至50.5 kPa，对照组为2 MPa，物质的量为1 mol。",
                    }
                ],
            }
        ],
    }

    promoted = promote_inline_mathematical_expressions(fragment)

    assert promoted["blocks"][0]["segments"] == [
        {
            "type": "text",
            "text": "压力由10 kPa升至50.5 kPa，对照组为2 MPa，物质的量为1 mol。",
        }
    ]
    assert not any(
        str(formula.get("latex") or "") in {"kP", "MP", "mo"}
        for formula in promoted["formulas"]
    )
