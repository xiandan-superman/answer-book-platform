from __future__ import annotations

from app.calculation_consistency import (
    calculation_contract_issues,
    calculation_draft_consistency_issues,
    formula_numeric_consistency_issues,
    reconcile_calculation_reference_structure,
)


def test_formula_numeric_equality_accepts_rounding() -> None:
    issues = formula_numeric_consistency_issues(
        [{"latex": r"w=\frac{4.3-3.5}{4.3-2.11}=0.365"}]
    )
    assert issues == []


def test_formula_numeric_equality_preserves_grouped_fractional_exponent() -> None:
    issues = formula_numeric_consistency_issues(
        [
            {
                "latex": (
                    r"a_{\pm} = 0.200 \times \left( 1^1 \times 2^2 \right)^{1/3} "
                    r"\times 0.100 = 0.0317"
                )
            }
        ]
    )

    assert issues == []


def test_formula_numeric_equality_rejects_wrong_percentage() -> None:
    issues = formula_numeric_consistency_issues(
        [{"latex": r"w=\frac{60-40}{60-30}\times100\%=33.3\%"}]
    )
    assert any("numeric_equality_mismatch" in issue for issue in issues)


def test_formula_numeric_equality_understands_percentage_subtraction() -> None:
    assert formula_numeric_consistency_issues(
        [{"latex": r"w_b=1-71.43\%=28.57\%"}]
    ) == []


def test_substitution_expression_must_match_same_quantity_result_formula() -> None:
    issues = formula_numeric_consistency_issues(
        [
            {"latex": r"w=\frac{4.3-3.5}{4.3-2.11}", "role": "substitution"},
            {"latex": r"w=0.502", "role": "result"},
        ]
    )

    assert any(issue.startswith("formula_substitution_result_mismatch:1") for issue in issues)


def test_latex_units_do_not_hide_thermodynamic_arithmetic_error() -> None:
    formulas = [
        {
            "role": "result",
            "latex": (
                r"\Delta U_2=-2261\ \text{kJ}+55.56\ \text{mol}\times8.314\ \text{J}"
                r"\cdot\text{mol}^{-1}\cdot\text{K}^{-1}\times373.15\ \text{K}\times10^{-3}"
                r"=-2257\ \text{kJ}"
            ),
        }
    ]

    issues = calculation_draft_consistency_issues({"formulas": formulas})

    assert any(issue.startswith("formula_1_numeric_equality_mismatch:") for issue in issues), issues


def test_latex_unit_stripping_is_discipline_neutral() -> None:
    formulas = [
        {"role": "result", "latex": r"s=12\ \mathrm{m}+3\ \mathrm{m}=15\ \mathrm{m}"},
        {"role": "result", "latex": r"m=2\ \text{g}\times4=8\ \text{g}"},
    ]

    assert calculation_draft_consistency_issues({"formulas": formulas}) == []


def test_substitution_expression_accepts_rounded_same_quantity_result() -> None:
    assert formula_numeric_consistency_issues(
        [
            {"latex": r"w=\frac{4.3-3.5}{4.3-2.11}", "role": "substitution"},
            {"latex": r"w=0.365", "role": "result"},
        ]
    ) == []


def test_substitution_result_accepts_joule_to_kilojoule_conversion() -> None:
    assert formula_numeric_consistency_issues(
        [
            {"latex": r"W=-1\times96500\times0.667", "role": "substitution"},
            {"latex": r"W=-64.36\,\mathrm{kJ}", "role": "result"},
        ]
    ) == []


def test_step_result_rejects_same_number_with_wrong_si_prefix() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [{"latex": r"p=5.322\times10^5\,\mathrm{Pa}", "role": "result"}],
            "steps": [{"result_formula_indices": [1], "result_text": "p=532200 kPa"}],
        }
    )

    assert any(issue.startswith("step_result_mismatch") for issue in issues)


def test_contract_answer_accepts_equivalent_prefixed_unit() -> None:
    draft = {
        "formulas": [{"latex": r"W=-1.461\times10^4\,\mathrm{J}", "role": "result"}],
        "answer_units": [{"number": "1", "answer": "W=-14.61 kJ", "steps": []}],
        "calculation_contract": {
            "result_quantities": [
                {
                    "quantity_id": "w",
                    "answer_unit_number": "1",
                    "name": "W",
                    "value": -14610,
                    "unit": "J",
                    "formula_index": 1,
                }
            ]
        },
    }

    assert calculation_draft_consistency_issues(draft) == []


def test_reconcile_repairs_wrong_step_unit_from_referenced_result_formula() -> None:
    draft = {
        "formulas": [{"latex": r"p=5.322\times10^5\,\mathrm{Pa}", "role": "result"}],
        "answer_units": [
            {
                "number": "1",
                "answer": "p=532.2 kPa",
                "steps": [{"result_formula_indices": [1], "result_text": "p=532200 kPa"}],
            }
        ],
        "calculation_contract": {"result_quantities": []},
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert reconciled["answer_units"][0]["steps"][0]["result_text"] == "p=532200 Pa"


def test_step_result_must_match_referenced_result_formula() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [{"latex": r"w=0.0825", "role": "result"}],
            "answer_units": [
                {
                    "number": "2.3",
                    "steps": [
                        {
                            "subquestion_number": "2.3",
                            "result_formula_indices": [1],
                            "result_text": "w=0.061。",
                        }
                    ],
                }
            ],
        }
    )
    assert any(issue.startswith("step_result_mismatch:2.3") for issue in issues)


def test_reconcile_syncs_one_stale_step_number_to_unique_result_formula() -> None:
    draft = {
        "formulas": [{"latex": r"w_x=8.9\%", "role": "result"}],
        "answer_units": [
            {"number": "1", "steps": [{"result_formula_indices": [1], "result_text": "x为4.4%。"}]}
        ],
        "calculation_contract": {"result_quantities": [], "partitions": []},
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert reconciled["answer_units"][0]["steps"][0]["result_text"] == "x为8.9%。"


def test_step_with_multiple_result_formulas_matches_values_in_any_order() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [{"latex": "w_a=73.3", "role": "result"}, {"latex": "w_b=26.7", "role": "result"}],
            "answer_units": [
                {
                    "number": "1",
                    "steps": [
                        {
                            "result_formula_indices": [1, 2],
                            "result_text": "b相为26.7%，a相为73.3%。",
                        }
                    ],
                }
            ],
        }
    )
    assert issues == []


def test_step_result_accepts_decimal_formula_and_percentage_text() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [{"latex": "w=0.267", "role": "result"}],
            "steps": [{"result_formula_indices": [1], "result_text": "质量分数为26.7%。"}],
        }
    )
    assert issues == []


def test_step_result_ignores_negative_unit_exponents() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [
                {"latex": r"C_{V,m}=20.785\ \mathrm{J\,mol^{-1}\,K^{-1}}", "role": "result"}
            ],
            "steps": [{"result_formula_indices": [1], "result_text": "C_V,m=20.785 J·mol⁻¹·K⁻¹。"}],
        }
    )

    assert not any(issue.startswith("step_result_mismatch") for issue in issues)


def test_step_result_understands_scientific_notation_as_one_value() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [{"latex": r"W'=6.620\times10^{4}\ \mathrm{J}", "role": "result"}],
            "steps": [{"result_formula_indices": [1], "result_text": "W'=6.620×10⁴ J。"}],
        }
    )

    assert not any(issue.startswith("step_result_mismatch") for issue in issues)


def test_latex_thin_space_does_not_split_one_result_formula() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [
                {
                    "latex": r"E=0.625\,\mathrm{V}+0.1507\,\mathrm{V}=0.7757\,\mathrm{V}",
                    "role": "result",
                }
            ],
            "steps": [{"result_formula_indices": [1], "result_text": "E=0.7757 V"}],
        }
    )

    assert not any(issue.startswith("step_result_mismatch") for issue in issues)


def test_program_promoted_prose_suffix_is_not_a_numeric_ledger_entry() -> None:
    issues = formula_numeric_consistency_issues(
        [
            {
                "latex": "12.47=5.198+9.353",
                "role": "relation",
                "source_note": "程序在结构校验前从解析正文中提升的数学关系。",
            }
        ]
    )

    assert issues == []


def test_symbolic_fraction_result_matches_declared_latex_rhs() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": r"d_{100}=\frac{a}{2}", "role": "result"}],
            "calculation_contract": {
                "result_quantities": [
                    {"quantity_id": "d", "value": "a/2", "basis": "lattice parameter", "formula_index": 1}
                ],
                "partitions": [],
            },
        }
    )
    assert issues == []


def test_crystallographic_index_is_not_a_numeric_step_result() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [{"latex": r"d_{100}=a", "role": "result"}],
            "steps": [{"result_formula_indices": [1], "result_text": "d_{100}=a"}],
        }
    )
    assert not any(issue.startswith("step_result_mismatch") for issue in issues)


def test_calculation_contract_rejects_partition_that_does_not_sum_to_whole() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "w_P=0.041", "role": "result"}, {"latex": "w_L=0.563", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [{"answer_unit_number": "2.3", "request_text": "计算组成", "basis": "室温组织"}],
                "result_quantities": [
                    {"quantity_id": "p", "value": 0.041, "basis": "室温组织", "formula_index": 1},
                    {"quantity_id": "ld", "value": 0.563, "basis": "室温组织", "formula_index": 2},
                ],
                "partitions": [
                    {
                        "answer_unit_number": "2.3",
                        "basis": "室温组织",
                        "component_quantity_ids": ["p", "ld"],
                        "expected_total": 1.0,
                    }
                ],
            }
        },
        [{"number": "2.3", "stem": "计算室温组织组成和质量比"}],
    )
    assert any("partition_sum_mismatch" in issue for issue in issues)


def test_calculation_contract_accepts_consistent_percentage_partition() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "w_a=73.3", "role": "result"}, {"latex": "w_b=26.7", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [{"answer_unit_number": "1.3", "request_text": "计算相组成", "basis": "室温相"}],
                "result_quantities": [
                    {"quantity_id": "a", "value": 73.3, "basis": "室温相", "formula_index": 1},
                    {"quantity_id": "b", "value": 26.7, "basis": "室温相", "formula_index": 2},
                ],
                "partitions": [
                    {
                        "answer_unit_number": "1.3",
                        "basis": "室温相",
                        "component_quantity_ids": ["a", "b"],
                        "expected_total": 100,
                    }
                ],
            }
        },
        [{"number": "1.3", "stem": "计算室温相组成"}],
    )
    assert issues == []


def test_calculation_contract_rejects_mixed_partition_bases() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "w_a=0.4", "role": "result"}, {"latex": "w_b=0.6", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [{"answer_unit_number": "1", "request_text": "计算分数", "basis": "总体"}],
                "result_quantities": [
                    {"quantity_id": "a", "value": 0.4, "basis": "总体", "formula_index": 1},
                    {"quantity_id": "b", "value": 0.6, "basis": "子总体", "formula_index": 2},
                ],
                "partitions": [
                    {"answer_unit_number": "1", "basis": "总体", "component_quantity_ids": ["a", "b"], "expected_total": 1}
                ],
            }
        },
        [{"number": "1", "stem": "计算分数"}],
    )
    assert any("mixed_partition_basis" in issue for issue in issues)


def test_reconcile_normalizes_partition_label_when_component_bases_agree() -> None:
    draft = {
        "formulas": [
            {"latex": "w_A=36.5\\%", "role": "result"},
            {"latex": "w_B=63.5\\%", "role": "result"},
        ],
        "calculation_contract": {
            "requested_outputs": [],
            "result_quantities": [
                {"quantity_id": "a", "value": 0.365, "basis": "3.5%C合金整体", "formula_index": 1},
                {"quantity_id": "b", "value": 0.635, "basis": "3.5%C合金整体", "formula_index": 2},
            ],
            "partitions": [
                {"basis": "室温组织组成", "component_quantity_ids": ["a", "b"], "expected_total": 1}
            ],
        },
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert reconciled["calculation_contract"]["partitions"][0]["basis"] == "3.5%C合金整体"
    assert not any("mixed_partition_basis" in issue for issue in calculation_contract_issues(reconciled))


def test_calculation_contract_is_not_required_for_qualitative_unit() -> None:
    assert calculation_contract_issues(
        {},
        [{"number": "3", "stem": "分析固态相变过程并比较拉伸强度"}],
    ) == []


def test_calculation_contract_value_must_match_result_formula() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "w=0.744", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [{"answer_unit_number": "1", "request_text": "计算质量分数", "basis": "总体"}],
                "result_quantities": [
                    {"quantity_id": "w", "value": 0.8, "basis": "总体", "formula_index": 1}
                ],
                "partitions": [],
            },
        },
        [{"number": "1", "stem": "计算质量分数"}],
    )
    assert any("result_mismatch" in issue for issue in issues)


def test_calculation_draft_rejects_final_value_that_contradicts_worked_steps() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [
                {"latex": "E=0.758", "role": "result"},
                {"latex": "E=0.505", "role": "result"},
            ],
            "answer_units": [
                {
                    "number": "2",
                    "answer": "E=0.505 V",
                    "steps": [{"result_formula_indices": [1], "result_text": "E=0.758 V"}],
                }
            ],
            "calculation_contract": {
                "requested_outputs": [{"answer_unit_number": "2", "request_text": "求电动势", "basis": "25℃"}],
                "result_quantities": [
                    {
                        "quantity_id": "e",
                        "answer_unit_number": "2",
                        "name": "E",
                        "value": 0.505,
                        "basis": "25℃",
                        "formula_index": 2,
                    }
                ],
                "partitions": [],
            },
        },
    )

    assert any(issue.startswith("answer_step_result_mismatch:2:E=") for issue in issues)


def test_answer_unit_summary_must_match_named_contract_quantities() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [
                {"latex": r"Q=35.51", "role": "result"},
                {"latex": r"\Delta S=100.55", "role": "result"},
            ],
            "answer_units": [
                {
                    "number": "1",
                    "answer": "Q=37.70 kJ，ΔS=106.8 J·K⁻¹",
                    "steps": [],
                }
            ],
            "calculation_contract": {
                "result_quantities": [
                    {
                        "quantity_id": "q",
                        "answer_unit_number": "1",
                        "name": "整个过程的热Q",
                        "value": 35.51,
                        "formula_index": 1,
                    },
                    {
                        "quantity_id": "s",
                        "answer_unit_number": "1",
                        "name": "整个过程的熵变ΔS",
                        "value": 100.55,
                        "formula_index": 2,
                    },
                ],
                "partitions": [],
            },
        }
    )

    assert any(issue.startswith("answer_contract_result_mismatch:1:Q=") for issue in issues)
    assert any(issue.startswith("answer_contract_result_mismatch:1:ΔS=") for issue in issues)


def test_answer_unit_summary_accepts_rounded_named_contract_quantities() -> None:
    issues = calculation_draft_consistency_issues(
        {
            "formulas": [{"latex": r"w=0.267", "role": "result"}],
            "answer_units": [{"number": "1", "answer": "w=26.7%", "steps": []}],
            "calculation_contract": {
                "result_quantities": [
                    {
                        "quantity_id": "w",
                        "answer_unit_number": "1",
                        "name": "质量分数w",
                        "value": 0.267,
                        "formula_index": 1,
                    }
                ],
                "partitions": [],
            },
        }
    )

    assert not any(issue.startswith("answer_contract_result_mismatch") for issue in issues)


def test_calculation_contract_accepts_symbolic_result_bound_to_formula() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "d_{100}=a", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [
                    {"answer_unit_number": "1", "request_text": "用点阵常数表示面间距", "basis": "立方晶系"}
                ],
                "result_quantities": [
                    {"quantity_id": "d", "value": "a", "basis": "立方晶系", "formula_index": 1}
                ],
                "partitions": [],
            },
        },
        [{"number": "1", "stem": "计算面间距，用点阵常数表示"}],
    )

    assert issues == []


def test_calculation_contract_rejects_symbolic_result_not_bound_to_formula() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "d_{100}=a", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [],
                "result_quantities": [
                    {"quantity_id": "d", "value": "2a", "basis": "立方晶系", "formula_index": 1}
                ],
                "partitions": [],
            },
        }
    )

    assert "calculation_contract_invalid_quantity_value:d" in issues


def test_multiple_ledger_values_may_reference_one_multi_result_formula() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "w_a=0.75,\\quad w_b=0.25", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [{"answer_unit_number": "1", "request_text": "计算组成", "basis": "总体"}],
                "result_quantities": [
                    {"quantity_id": "a", "value": 0.75, "basis": "总体", "formula_index": 1},
                    {"quantity_id": "b", "value": 0.25, "basis": "总体", "formula_index": 1},
                ],
                "partitions": [
                    {"answer_unit_number": "1", "basis": "总体", "component_quantity_ids": ["a", "b"], "expected_total": 1}
                ],
            },
        },
        [{"number": "1", "stem": "计算组成"}],
    )
    assert issues == []


def test_unit_local_calculation_payload_is_hoisted_and_reindexed() -> None:
    from app.answer_generation import normalize_nested_calculation_payload

    normalized = normalize_nested_calculation_payload(
        {
            "formulas": [{"latex": "x=1", "role": "result"}],
            "calculation_contract": {"requested_outputs": [], "result_quantities": [], "partitions": []},
            "answer_units": [
                {
                    "number": "2.3",
                    "steps": [{"result_formula_indices": [1]}],
                    "formulas": [{"latex": "w=0.75", "role": "result"}],
                    "calculation_contract": {
                        "requested_outputs": [{"answer_unit_number": "2.3", "request_text": "计算组成", "basis": "总体"}],
                        "result_quantities": [
                            {"quantity_id": "w", "value": 0.75, "basis": "总体", "formula_index": 1}
                        ],
                        "partitions": [],
                    },
                }
            ],
        }
    )

    assert len(normalized["formulas"]) == 2
    assert normalized["answer_units"][0]["steps"][0]["result_formula_indices"] == [2]
    assert normalized["calculation_contract"]["result_quantities"][0]["formula_index"] == 2


def test_reconcile_mirrors_missing_ledger_result_and_connects_all_step_values() -> None:
    draft = {
        "formulas": [
            {"latex": r"w_a=\frac{4.3-3.5}{4.3-2.11}=36.5\%", "role": "result"}
        ],
        "answer_units": [
            {
                "number": "2.3",
                "steps": [
                    {
                        "result_formula_indices": [1],
                        "result_text": "a 为 36.5%，b 为 63.5%。",
                    }
                ],
            }
        ],
        "calculation_contract": {
            "requested_outputs": [],
            "result_quantities": [
                {"quantity_id": "a", "name": "a", "value": 36.5, "unit": "%", "formula_index": 1},
                {"quantity_id": "b", "name": "b", "value": 63.5, "unit": "%"},
            ],
            "partitions": [],
        },
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert len(reconciled["formulas"]) == 2
    assert reconciled["calculation_contract"]["result_quantities"][1]["formula_index"] == 2
    assert reconciled["answer_units"][0]["steps"][0]["result_formula_indices"] == [1, 2]


def test_reconcile_does_not_match_ledger_value_to_formula_operand() -> None:
    draft = {
        "formulas": [
            {"latex": r"w=\frac{4.3-3.5}{4.3-2.11}=0.365", "role": "result"}
        ],
        "calculation_contract": {
            "requested_outputs": [],
            "result_quantities": [
                {"quantity_id": "operand_like", "name": "x", "value": 3.5}
            ],
            "partitions": [],
        },
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert len(reconciled["formulas"]) == 2
    assert reconciled["calculation_contract"]["result_quantities"][0]["formula_index"] == 2


def test_reconcile_uses_minimal_exact_multi_result_formula_per_step() -> None:
    draft = {
        "formulas": [
            {"latex": "w_initial=0.502", "role": "result"},
            {"latex": r"w_P=0.502,\quad w_L=0.498", "role": "result"},
        ],
        "answer_units": [
            {
                "number": "1",
                "steps": [
                    {"result_formula_indices": [1, 2], "result_text": "初生相为0.502"},
                    {"result_formula_indices": [1, 2], "result_text": "P为0.502，L为0.498"},
                ],
            }
        ],
        "calculation_contract": {
            "requested_outputs": [],
            "result_quantities": [
                {"quantity_id": "p", "value": 0.502, "formula_index": 2},
                {"quantity_id": "l", "value": 0.498, "formula_index": 2},
            ],
            "partitions": [],
        },
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert reconciled["answer_units"][0]["steps"][0]["result_formula_indices"] == [1]
    assert reconciled["answer_units"][0]["steps"][1]["result_formula_indices"] == [2]


def test_transition_ledger_accepts_parent_split_on_one_global_basis() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [
                {"latex": "w_A=0.578", "role": "result"},
                {"latex": "w_X=0.089", "role": "result"},
                {"latex": "w_E=0.333", "role": "result"},
            ],
            "answer": "X 从初始 A 中析出，剩余 A 与复合组分 E 共同构成最终组成。",
            "calculation_contract": {
                "requested_outputs": [],
                "result_quantities": [
                    {"quantity_id": "a", "value": 0.578, "basis": "whole", "formula_index": 1},
                    {"quantity_id": "x", "value": 0.089, "basis": "whole", "formula_index": 2},
                    {"quantity_id": "e", "value": 0.333, "basis": "whole", "formula_index": 3},
                ],
                "intermediate_quantities": [
                    {"quantity_id": "a0", "value": 0.667, "basis": "whole"}
                ],
                "partitions": [
                    {"component_quantity_ids": ["a", "x", "e"], "expected_total": 1}
                ],
                "transitions": [
                    {
                        "transition_id": "t1",
                        "parent_quantity_id": "a0",
                        "product_quantity_ids": ["a", "x"],
                        "derived_quantity_id": "x",
                        "local_fraction": 0.1333,
                    }
                ],
            },
        }
    )
    assert issues == []


def test_transition_ledger_rejects_wrong_parent_multiplier_even_when_final_partition_sums() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [
                {"latex": "w_A=0.623", "role": "result"},
                {"latex": "w_X=0.044", "role": "result"},
                {"latex": "w_E=0.333", "role": "result"},
            ],
            "answer": "X 从 A 中析出，剩余量与 E 合计为整体。",
            "calculation_contract": {
                "requested_outputs": [],
                "result_quantities": [
                    {"quantity_id": "a", "value": 0.623, "basis": "whole", "formula_index": 1},
                    {"quantity_id": "x", "value": 0.044, "basis": "whole", "formula_index": 2},
                    {"quantity_id": "e", "value": 0.333, "basis": "whole", "formula_index": 3},
                ],
                "intermediate_quantities": [
                    {"quantity_id": "a0", "value": 0.667, "basis": "whole"}
                ],
                "partitions": [
                    {"component_quantity_ids": ["a", "x", "e"], "expected_total": 1}
                ],
                "transitions": [
                    {
                        "transition_id": "t1",
                        "parent_quantity_id": "a0",
                        "product_quantity_ids": ["a", "x"],
                        "derived_quantity_id": "x",
                        "local_fraction": 0.1333,
                    }
                ],
            },
        }
    )
    assert any("transition_derivation_mismatch" in issue for issue in issues)


def test_transition_ledger_accepts_whole_parent_to_one_product() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [{"latex": "w_P=0.365", "role": "result"}],
            "calculation_contract": {
                "requested_outputs": [],
                "result_quantities": [
                    {"quantity_id": "p", "value": 0.365, "basis": "whole", "formula_index": 1}
                ],
                "intermediate_quantities": [
                    {"quantity_id": "a0", "value": 0.365, "basis": "whole"}
                ],
                "partitions": [],
                "transitions": [
                    {
                        "transition_id": "t1",
                        "parent_quantity_id": "a0",
                        "product_quantity_ids": ["p"],
                        "derived_quantity_id": "p",
                        "local_fraction": 1.0,
                    }
                ],
            },
        }
    )
    assert issues == []


def test_reconcile_normalizes_stage_label_to_conserved_global_basis() -> None:
    draft = {
        "formulas": [{"latex": "w_E=0.333", "role": "result"}],
        "calculation_contract": {
            "requested_outputs": [],
            "result_quantities": [
                {"quantity_id": "e", "value": 0.333, "basis": "alloy whole", "formula_index": 1}
            ],
            "intermediate_quantities": [
                {"quantity_id": "liquid", "value": 0.333, "basis": "before reaction"}
            ],
            "partitions": [],
            "transitions": [
                {
                    "transition_id": "t1",
                    "basis": "before reaction",
                    "parent_quantity_id": "liquid",
                    "product_quantity_ids": ["e"],
                    "derived_quantity_id": "e",
                    "local_fraction": 1,
                }
            ],
        },
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert reconciled["calculation_contract"]["intermediate_quantities"][0]["basis"] == "alloy whole"
    assert reconciled["calculation_contract"]["transitions"][0]["basis"] == "alloy whole"
    assert calculation_contract_issues(reconciled) == []


def test_reconcile_syncs_two_stale_step_numbers_to_one_multi_result_formula() -> None:
    draft = {
        "formulas": [{"latex": "w_a=73.3\\%,\\quad w_b=26.7\\%", "role": "result"}],
        "answer_units": [
            {
                "number": "1",
                "steps": [
                    {"result_formula_indices": [1], "result_text": "a为80%，b为20%。"}
                ],
            }
        ],
        "calculation_contract": {"requested_outputs": [], "result_quantities": [], "partitions": []},
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert reconciled["answer_units"][0]["steps"][0]["result_text"] == "a为73.3%，b为26.7%。"


def test_reconcile_projects_valid_ledger_across_alternative_exhaustive_views() -> None:
    draft = {
        "answer": "相组成：α相约77.8%，β相约22.2%；组织组成：初生α相约66.7%，共晶体约33.3%。",
        "formulas": [
            {"latex": "w_a=0.733=73.3\\%", "role": "result"},
            {"latex": "w_b=0.267=26.7\\%", "role": "result"},
            {"latex": "w_p=0.667=66.7\\%", "role": "result"},
            {"latex": "w_e=0.333=33.3\\%", "role": "result"},
        ],
        "answer_units": [
            {
                "number": "1.3",
                "answer": "α相约77.8%，β相约22.2%；初生α相约66.7%，共晶体约33.3%。",
                "steps": [
                    {
                        "result_formula_indices": [1],
                        "result_text": "α相质量分数为77.8%，β相质量分数为22.2%。",
                    }
                ],
            }
        ],
        "calculation_contract": {
            "requested_outputs": [],
            "result_quantities": [
                {"quantity_id": "a", "answer_unit_number": "1.3", "name": "α相质量分数", "value": 0.733, "basis": "final state", "formula_index": 1},
                {"quantity_id": "b", "answer_unit_number": "1.3", "name": "β相质量分数", "value": 0.267, "basis": "final state", "formula_index": 2},
                {"quantity_id": "p", "answer_unit_number": "1.3", "name": "初生α相质量分数", "value": 0.667, "basis": "before transition", "formula_index": 3},
                {"quantity_id": "e", "answer_unit_number": "1.3", "name": "共晶体质量分数", "value": 0.333, "basis": "before transition", "formula_index": 4},
            ],
            "intermediate_quantities": [
                {"quantity_id": "whole", "name": "system total", "value": 1.0, "basis": "before transition"}
            ],
            "partitions": [
                {"basis": "final state", "component_quantity_ids": ["a", "b"], "expected_total": 1.0},
                {"basis": "before transition", "component_quantity_ids": ["p", "e"], "expected_total": 1.0},
            ],
            "transitions": [
                {"transition_id": "t1", "parent_quantity_id": "whole", "product_quantity_ids": ["a", "b"]},
                {"transition_id": "t2", "parent_quantity_id": "whole", "product_quantity_ids": ["p", "e"]},
            ],
        },
    }

    reconciled = reconcile_calculation_reference_structure(draft)

    assert calculation_contract_issues(reconciled) == []
    assert reconciled["calculation_contract"]["intermediate_quantities"][0]["basis"] == "system total"
    assert {item["basis"] for item in reconciled["calculation_contract"]["result_quantities"]} == {"system total"}
    unit = reconciled["answer_units"][0]
    assert unit["answer"] == "α相约73.3%，β相约26.7%；初生α相约66.7%，共晶体约33.3%。"
    assert unit["steps"][0]["result_formula_indices"] == [1, 2]
    assert unit["steps"][0]["result_text"] == "α相质量分数为73.3%，β相质量分数为26.7%。"


def test_contract_rejects_labeled_answer_value_that_disagrees_with_formula_ledger() -> None:
    draft = {
        "formulas": [{"latex": "w_a=0.733=73.3\\%", "role": "result"}],
        "answer_units": [{"number": "1", "answer": "α相质量分数为77.8%。"}],
        "calculation_contract": {
            "requested_outputs": [],
            "result_quantities": [
                {"quantity_id": "a", "answer_unit_number": "1", "name": "α相质量分数", "value": 0.733, "basis": "whole", "formula_index": 1}
            ],
            "partitions": [],
        },
    }

    assert "calculation_contract_answer_mismatch:a" in calculation_contract_issues(draft)


def test_multistage_three_way_partition_requires_transition_lineage() -> None:
    issues = calculation_contract_issues(
        {
            "formulas": [
                {"latex": "a=0.5", "role": "result"},
                {"latex": "b=0.2", "role": "result"},
                {"latex": "c=0.3", "role": "result"},
            ],
            "answer": "b 由父项析出，剩余 a，另有 c。",
            "calculation_contract": {
                "requested_outputs": [],
                "result_quantities": [
                    {"quantity_id": "a", "value": 0.5, "formula_index": 1},
                    {"quantity_id": "b", "value": 0.2, "formula_index": 2},
                    {"quantity_id": "c", "value": 0.3, "formula_index": 3},
                ],
                "partitions": [{"component_quantity_ids": ["a", "b", "c"], "expected_total": 1}],
            },
        }
    )
    assert "calculation_contract_missing_transition_lineage" in issues


def test_nested_latex_units_do_not_disable_substitution_result_check() -> None:
    from app.calculation_consistency import formula_numeric_consistency_issues

    formulas = [
        {
            "latex": (
                r"\Delta U=-2.26\times10^3\,\mathrm{kJ}"
                r"+55.56\,\mathrm{mol}\times8.314\,"
                r"\mathrm{J\cdot mol^{-1}\cdot K^{-1}}\times373.15\,\mathrm{K}\times10^{-3}"
            ),
            "role": "substitution",
        },
        {"latex": r"\Delta U=-2.26\times10^3\,\mathrm{kJ}", "role": "result"},
    ]

    issues = formula_numeric_consistency_issues(formulas)

    assert any(issue.startswith("formula_substitution_result_mismatch:1:") for issue in issues)


def test_rounded_large_terms_may_cancel_to_declared_zero() -> None:
    from app.calculation_consistency import formula_numeric_consistency_issues

    formulas = [
        {
            "latex": r"\Delta G=-2.260\times10^3-373.15\times(-6.06)",
            "role": "substitution",
        },
        {"latex": r"\Delta G\approx0\,\mathrm{kJ}", "role": "result"},
    ]

    assert formula_numeric_consistency_issues(formulas) == []


def test_top_level_scientific_answer_is_checked_against_contract_without_units() -> None:
    from app.calculation_consistency import calculation_draft_consistency_issues

    draft = {
        "answer": r"ΔU=-2260\times10^3\,\mathrm{kJ}；ΔH=-2260\,\mathrm{kJ}",
        "formulas": [
            {"latex": r"\Delta U=-2260\,\mathrm{kJ}", "role": "result"},
            {"latex": r"\Delta H=-2260\,\mathrm{kJ}", "role": "result"},
        ],
        "answer_units": [],
        "calculation_contract": {
            "result_quantities": [
                {"quantity_id": "du", "name": "ΔU", "value": -2260, "unit": "kJ", "formula_index": 1},
                {"quantity_id": "dh", "name": "ΔH", "value": -2260, "unit": "kJ", "formula_index": 2},
            ]
        },
    }

    issues = calculation_draft_consistency_issues(draft)

    assert any(issue.startswith("answer_contract_result_mismatch:top:ΔU=") for issue in issues)
