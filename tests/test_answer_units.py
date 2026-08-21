from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


QUESTION = {
    "question_id": "calc_mixed_01",
    "section": "五、计算题",
    "question_type": "计算题",
    "stem": "电池问题。",
    "subquestions": [
        {"number": "1", "stem": "正确写出电池反应。", "question_type": "简答题"},
        {"number": "2", "stem": "计算待测溶液 pH。", "question_type": "计算题"},
    ],
}


def _formulas():
    return [
        {"latex": "A\\rightarrow B", "role": "reaction", "display": True, "meaning": "总反应"},
        {"latex": "E=E_0-k\\mathrm{pH}", "role": "relation", "display": True, "meaning": "关系式"},
        {"latex": "\\mathrm{pH}=2.00", "role": "result", "display": True, "meaning": "结果"},
    ]


class AnswerUnitTests(unittest.TestCase):
    def test_semantic_gate_rejects_answer_analysis_zero_polarity_contradiction(self) -> None:
        from app.answer_generation import semantic_generation_issues

        question = {
            "question_id": "q_sign",
            "question_type": "简答题",
            "subquestions": [
                {"number": "1", "stem": "判断趋势", "question_type": "简答题"},
                {"number": "2", "stem": "判断偏导符号", "question_type": "简答题"},
            ],
        }
        fragment = {
            "answer_units": [
                {"number": "1", "answer": "升高", "analysis_segments": [{"text": "由曲线判断为升高。"}]},
                {
                    "number": "2",
                    "answer": "该偏导大于零。",
                    "analysis_segments": [{"text": "体积差小于零，因此该偏导小于零。"}],
                },
            ],
            "formulas": [],
        }

        self.assertIn(
            "answer_analysis_zero_polarity_contradiction:2",
            semantic_generation_issues(question, fragment),
        )

    def test_semantic_gate_rejects_difference_sign_algebra_contradiction(self) -> None:
        from app.answer_generation import semantic_generation_issues

        question = {"question_id": "q_difference", "question_type": "简答题", "subquestions": []}
        fragment = {
            "answer_units": [
                {
                    "number": "1",
                    "answer": "该差值大于零。",
                    "analysis_segments": [{"text": "已知V_m(甲)<V_m(乙)，代入差值关系。"}],
                }
            ],
            "_draft": {
                "formulas": [
                    {"latex": r"D=V_{\mathrm{m}}(\text{甲})-V_{\mathrm{m}}(\text{乙})", "role": "relation"},
                    {"latex": "D>0", "role": "conclusion"},
                ]
            },
        }

        issues = semantic_generation_issues(question, fragment)
        self.assertTrue(any(issue.startswith("difference_sign_contradiction:") for issue in issues))

    def test_missing_unit_warning_requires_numeric_result_with_specific_declared_unit(self) -> None:
        from app.content_quality_audit import _calculation_has_high_confidence_missing_unit

        symbolic = {
            "answer": "(4) 面间距为a/2，柏氏矢量为(a/2)<111>。",
            "answer_units": [{"answer": "d=a/2"}],
            "calculation_contract": {
                "result_quantities": [{"name": "面间距", "value": "a/2", "unit": "长度"}]
            },
        }
        missing = {
            "answer": "计算结果为20。",
            "answer_units": [],
            "calculation_contract": {
                "result_quantities": [{"name": "应力", "value": 20, "unit": "MPa"}]
            },
        }
        present = {
            **missing,
            "answer": "计算结果为20 MPa。",
        }

        self.assertFalse(_calculation_has_high_confidence_missing_unit(symbolic, {}))
        self.assertTrue(_calculation_has_high_confidence_missing_unit(missing, {}))
        self.assertFalse(_calculation_has_high_confidence_missing_unit(present, {}))

    def test_single_leaf_question_promotes_single_answer_unit(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        fragment = fragment_from_analysis_draft(
            {
                "question_id": "qa_s01_06_02",
                "answer": "扩散系数随温度升高而增大。",
                "answer_units": [
                    {
                        "number": "2",
                        "question_type": "简答题",
                        "answer": "表面扩散最快，晶界扩散次之，晶内扩散最慢。",
                        "analysis_segments": [
                            {"text": "同一温度下比较三条曲线高低即可得到扩散系数大小顺序。", "formula_indices": []}
                        ],
                        "steps": [],
                    }
                ],
                "formulas": [],
            },
            {
                "question_id": "qa_s01_06_02",
                "section": "六、简答题",
                "number": "2",
                "question_type": "简答题",
                "stem": "从图中能得出哪些信息？",
                "subquestions": [],
            },
            [],
        )

        self.assertEqual(1, len(fragment["answer_units"]))
        analysis = next(block for block in fragment["blocks"] if block["label"] == "解析")
        analysis_text = "".join(seg.get("text", "") for seg in analysis["segments"])
        self.assertIn("同一温度下比较三条曲线高低", analysis_text)
        self.assertNotIn("第2小问", analysis_text)

    def test_pending_top_answer_is_backfilled_from_single_answer_unit(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        fragment = fragment_from_analysis_draft(
            {
                "question_id": "term_01",
                "answer": "待复核",
                "answer_units": [
                    {
                        "number": "1",
                        "question_type": "名词解释",
                        "answer": "点阵畸变：晶体点阵偏离理想周期排列的局部畸变。",
                        "analysis_segments": [],
                        "steps": [],
                    }
                ],
                "formulas": [],
            },
            {
                "question_id": "term_01",
                "section": "名词解释",
                "number": "1",
                "question_type": "名词解释",
                "stem": "点阵畸变。",
                "subquestions": [],
            },
            [],
        )

        self.assertEqual("点阵畸变：晶体点阵偏离理想周期排列的局部畸变。", fragment["answer"])
        self.assertEqual("点阵畸变：晶体点阵偏离理想周期排列的局部畸变。", fragment["answer_summary"])

    def test_unknown_top_answer_is_backfilled_from_multiple_answer_units(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        question = {
            "question_id": "term_multi_01",
            "section": "名词解释",
            "number": "1",
            "question_type": "名词解释",
            "stem": "解释下列概念。",
            "subquestions": [
                {"number": "1", "stem": "点阵畸变。", "question_type": "名词解释"},
                {"number": "2", "stem": "位错。", "question_type": "名词解释"},
            ],
        }
        fragment = fragment_from_analysis_draft(
            {
                "question_id": "term_multi_01",
                "answer": "未知",
                "answer_units": [
                    {"number": "1", "question_type": "名词解释", "answer": "点阵畸变：晶体点阵偏离理想周期排列的局部畸变。"},
                    {"number": "2", "question_type": "名词解释", "answer": "位错：晶体中的线缺陷。"},
                ],
                "formulas": [],
            },
            question,
            [],
        )

        self.assertEqual("(1)点阵畸变：晶体点阵偏离理想周期排列的局部畸变。；(2)位错：晶体中的线缺陷。", fragment["answer"])

    def test_explicit_answer_units_render_by_original_subquestion(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        fragment = fragment_from_analysis_draft(
            {
                "question_id": "calc_mixed_01",
                "answer": "见解析",
                "formulas": _formulas(),
                "answer_units": [
                    {
                        "number": "1",
                        "question_type": "简答题",
                        "answer": "",
                        "analysis_segments": [{"text": "配平后总反应为 {f1}。", "formula_indices": [1]}],
                        "steps": [],
                    },
                    {
                        "number": "2",
                        "question_type": "计算题",
                        "answer": "pH 为 2.00。",
                        "analysis_segments": [],
                        "steps": [
                            {
                                "text": "建立电动势与 pH 的关系。",
                                "relation_formula_indices": [2],
                                "result_formula_indices": [3],
                                "result_text": "待测溶液 pH 为 2.00。",
                            }
                        ],
                    },
                ],
                "mistake_notes": ["注意电池电动势的正负号。"],
            },
            QUESTION,
            [],
        )

        self.assertEqual(["1", "2"], [unit["number"] for unit in fragment["answer_units"]])
        analysis = next(block for block in fragment["blocks"] if block["label"] == "解析")
        steps = next(block for block in fragment["blocks"] if block["label"] == "解题步骤")
        self.assertIn("(1)正确写出电池反应", "".join(seg.get("text", "") for seg in analysis["segments"]))
        self.assertIn("(2)计算待测溶液 pH", "".join(seg.get("text", "") for seg in steps["segments"]))
        self.assertNotIn("待复核公式", [block["label"] for block in fragment["blocks"]])

    def test_legacy_mixed_draft_keeps_single_short_answer_unit(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        fragment = fragment_from_analysis_draft(
            {
                "question_id": "calc_mixed_01",
                "answer": "见解析",
                "analysis": "利用缓冲溶液建立校准关系。",
                "analysis_segments": [{"text": "电池总反应为 {f1}。", "formula_indices": [1]}],
                "steps": [
                    {
                        "subquestion_number": "2",
                        "text": "计算待测溶液的 pH。",
                        "relation_formula_indices": [2],
                        "result_formula_indices": [3],
                    }
                ],
                "formulas": _formulas(),
                "mistake_notes": ["注意电池电动势的正负号。"],
            },
            QUESTION,
            [],
        )

        units = {unit["number"]: unit for unit in fragment["answer_units"]}
        self.assertTrue(units["1"]["analysis_segments"])
        self.assertTrue(units["2"]["steps"])
        analysis = next(block for block in fragment["blocks"] if block["label"] == "解析")
        self.assertIn("(1)正确写出电池反应", "".join(seg.get("text", "") for seg in analysis["segments"]))

    def test_requirement_units_render_with_parent_and_circled_labels(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        question = {
            "question_id": "nested_01",
            "section": "六、简答题",
            "number": "3",
            "question_type": "简答题",
            "stem": "回答材料问题。",
            "subquestions": [
                {
                    "number": "1",
                    "stem": "围绕扩散现象回答。",
                    "question_type": "简答题",
                    "requirements": [
                        {"number": "1.1", "stem": "说明扩散驱动力。", "question_type": "简答题"},
                        {"number": "1.2", "stem": "说明温度影响。", "question_type": "简答题"},
                    ],
                }
            ],
        }
        fragment = fragment_from_analysis_draft(
            {
                "question_id": "nested_01",
                "answer": "见解析",
                "answer_units": [
                    {"number": "1.1", "question_type": "简答题", "answer": "浓度梯度是主要驱动力。"},
                    {"number": "1.2", "question_type": "简答题", "answer": "温度升高会加快扩散。"},
                ],
                "formulas": [],
            },
            question,
            [],
        )

        analysis = next(block for block in fragment["blocks"] if block["label"] == "解析")
        analysis_text = "".join(seg.get("text", "") for seg in analysis["segments"])
        self.assertIn("(1)围绕扩散现象回答", analysis_text)
        self.assertIn("①、说明扩散驱动力", analysis_text)
        self.assertIn("②、说明温度影响", analysis_text)
        self.assertNotIn("1.1", analysis_text)
        self.assertNotIn("第1小问", analysis_text)

    def test_inferred_actions_render_as_flat_first_level_subquestions(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        stem = "写出三相反应；同时画出扩层曲线。"
        question = {
            "question_id": "synthetic_parent_01",
            "section": "六、简答题",
            "number": "6",
            "question_type": "简答题",
            "stem": stem,
            "subquestions": [
                {
                    "number": "6",
                    "stem": stem,
                    "raw": stem,
                    "synthetic_parent": True,
                    "requirements": [
                        {"number": "6.1", "stem": "写出三相反应", "question_type": "简答题"},
                        {"number": "6.2", "stem": "画出扩层曲线", "question_type": "作图题"},
                    ],
                }
            ],
        }
        fragment = fragment_from_analysis_draft(
            {
                "question_id": "synthetic_parent_01",
                "answer": "见解析",
                "answer_units": [
                    {"number": "6.1", "question_type": "简答题", "answer": "反应内容。"},
                    {"number": "6.2", "question_type": "作图题", "answer": "作图内容。"},
                ],
                "formulas": [],
            },
            question,
            [],
        )

        analysis = next(block for block in fragment["blocks"] if block["label"] == "解析")
        analysis_text = "".join(seg.get("text", "") for seg in analysis["segments"])
        self.assertIn("(1)写出三相反应", analysis_text)
        self.assertIn("(2)画出扩层曲线", analysis_text)
        self.assertNotIn("①、", analysis_text)
        self.assertNotIn(f"(6){stem}", analysis_text)

    def test_audit_checks_unit_payload_not_only_visible_heading(self) -> None:
        from app.content_quality_audit import audit_content_quality

        fragment = {
            "question_id": "calc_mixed_01",
            "answer": "见解析",
            "answer_summary": "见解析",
            "evidence_ids": [],
            "formulas": _formulas(),
            "answer_units": [
                {"number": "1", "question_type": "简答题", "answer": "", "analysis_segments": [], "steps": []},
                {
                    "number": "2",
                    "question_type": "计算题",
                    "answer": "",
                    "analysis_segments": [],
                    "steps": [{"text": "计算 pH。", "relation_formula_indices": [2], "result_formula_indices": [3]}],
                },
            ],
            "blocks": [
                {"label": "解析", "segments": [{"type": "text", "text": "第1小问：\n第2小问：\n"}]},
                {"label": "解题步骤", "segments": [{"type": "text", "text": "第2小问：\n计算 pH。"}]},
                {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "注意符号。"}]},
            ],
        }
        draft = {
            "question_id": "calc_mixed_01",
            "answer": "见解析",
            "analysis": "解析。",
            "answer_units": fragment["answer_units"],
            "formulas": _formulas(),
        }

        report = audit_content_quality({"items": [QUESTION]}, {"fragments": [fragment]}, {"drafts": [draft]})

        self.assertIn("missing_answer_unit_content", {item["code"] for item in report["issues"]})

    def test_audit_accepts_valid_units_even_without_heading_text(self) -> None:
        from app.content_quality_audit import audit_content_quality

        units = [
            {
                "number": "1",
                "question_type": "简答题",
                "answer": "",
                "analysis_segments": [{"text": "总反应见对应关系式。", "formula_indices": [1]}],
                "steps": [],
            },
            {
                "number": "2",
                "question_type": "计算题",
                "answer": "",
                "analysis_segments": [],
                "steps": [{"text": "计算 pH。", "relation_formula_indices": [2], "result_formula_indices": [3]}],
            },
        ]
        fragment = {
            "question_id": "calc_mixed_01",
            "answer": "见解析",
            "answer_summary": "见解析",
            "evidence_ids": [],
            "formulas": _formulas(),
            "answer_units": units,
            "blocks": [
                {"label": "解析", "segments": [{"type": "text", "text": "已按作答单元组织。"}]},
                {"label": "解题步骤", "segments": [{"type": "text", "text": "计算 pH。"}]},
                {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "注意符号。"}]},
            ],
        }
        draft = {"question_id": "calc_mixed_01", "answer": "见解析", "analysis": "解析。", "answer_units": units, "formulas": _formulas()}

        report = audit_content_quality({"items": [QUESTION]}, {"fragments": [fragment]}, {"drafts": [draft]})

        self.assertNotIn("missing_answer_unit_content", {item["code"] for item in report["issues"]})
        self.assertNotIn("missing_answer_unit_steps", {item["code"] for item in report["issues"]})

    def test_formula_bearing_unit_is_preserved_in_multipart_answer_summary(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        question = {
            "question_id": "q_formula_unit",
            "number": "1",
            "question_type": "简答题",
            "stem": "(1)判断；(2)写出偏导符号。",
            "subquestions": [
                {"number": "1", "stem": "判断", "question_type": "简答题"},
                {"number": "2", "stem": "写出偏导符号", "question_type": "简答题"},
            ],
        }
        draft = {
            "question_id": "q_formula_unit",
            "answer": "见解析",
            "answer_units": [
                {"number": "1", "question_type": "简答题", "answer": "升高", "analysis_segments": [{"text": "据图判断。"}]},
                {"number": "2", "question_type": "简答题", "answer": "(∂G/∂p)_T<0；低温有利", "analysis_segments": [{"text": "由关系式判断。"}]},
            ],
            "formulas": [{"latex": r"\left(\frac{\partial G}{\partial p}\right)_T<0", "role": "result", "display": False}],
        }

        fragment = fragment_from_analysis_draft(draft, question, [])

        assert fragment["answer"] == "见解析"
        assert "(1)升高" in fragment["answer_summary"]
        assert "(2)(∂G/∂p)_T<0；低温有利" in fragment["answer_summary"]

    def test_audit_rejects_opposite_property_direction_in_answer_and_analysis(self) -> None:
        from app.content_quality_audit import audit_content_quality

        question = {
            "question_id": "q_compare",
            "question_type": "简答题",
            "subquestions": [{"number": "1", "stem": "比较两种工艺的拉伸性能", "question_type": "简答题"}],
        }
        unit = {
            "number": "1",
            "question_type": "简答题",
            "answer": "工艺A强度更高，但塑性略低。",
            "analysis_segments": [{"text": "工艺A组织细化，因此强度较高，塑性也较好。"}],
            "steps": [],
        }
        fragment = {
            "question_id": "q_compare",
            "answer": unit["answer"],
            "answer_summary": unit["answer"],
            "evidence_ids": [],
            "formulas": [],
            "answer_units": [unit],
            "blocks": [{"label": "解析", "segments": unit["analysis_segments"]}],
        }
        draft = {"question_id": "q_compare", "answer": unit["answer"], "analysis": "", "answer_units": [unit], "formulas": []}

        report = audit_content_quality({"items": [question]}, {"fragments": [fragment]}, {"drafts": [draft]})

        assert "answer_analysis_comparative_contradiction" in {item["code"] for item in report["issues"]}

    def test_audit_does_not_treat_inverse_subject_comparison_as_contradiction(self) -> None:
        from app.content_quality_audit import audit_content_quality

        question = {
            "question_id": "q_compare",
            "question_type": "简答题",
            "subquestions": [{"number": "1", "stem": "比较两种工艺的强度", "question_type": "简答题"}],
        }
        unit = {
            "number": "1",
            "question_type": "简答题",
            "answer": "工艺A强度更高。",
            "analysis_segments": [{"text": "工艺B强度较低，因此两处表述一致。"}],
            "steps": [],
        }
        fragment = {
            "question_id": "q_compare",
            "answer": unit["answer"],
            "answer_summary": unit["answer"],
            "evidence_ids": [],
            "formulas": [],
            "answer_units": [unit],
            "blocks": [{"label": "解析", "segments": unit["analysis_segments"]}],
        }
        draft = {"question_id": "q_compare", "answer": unit["answer"], "answer_units": [unit], "formulas": []}

        report = audit_content_quality({"items": [question]}, {"fragments": [fragment]}, {"drafts": [draft]})

        assert "answer_analysis_comparative_contradiction" not in {item["code"] for item in report["issues"]}

    def test_audit_rejects_declared_composition_item_missing_from_partition(self) -> None:
        from app.content_quality_audit import audit_content_quality

        question = {
            "question_id": "q_composition",
            "question_type": "计算题",
            "subquestions": [{"number": "1", "stem": "计算室温组织组成和质量比", "question_type": "计算题"}],
        }
        unit = {
            "number": "1",
            "question_type": "计算题",
            "answer": "室温组织为珠光体+二次渗碳体+变态莱氏体。",
            "analysis_segments": [{"text": "按同一总体计算各组成。"}],
            "steps": [{"text": "计算各组织质量分数。", "result_text": "珠光体36.5%，变态莱氏体63.5%。"}],
        }
        contract = {
            "requested_outputs": [{"answer_unit_number": "1", "request_text": "室温组织组成和质量比"}],
            "result_quantities": [
                {"quantity_id": "p", "name": "珠光体质量分数", "value": 0.365},
                {"quantity_id": "ld", "name": "变态莱氏体质量分数", "value": 0.635},
            ],
            "partitions": [{"component_quantity_ids": ["p", "ld"], "expected_total": 1}],
        }
        fragment = {
            "question_id": "q_composition",
            "answer": unit["answer"],
            "answer_summary": unit["answer"],
            "evidence_ids": [],
            "formulas": [{"formula_id": "f1", "latex": "w_P=0.365", "role": "result"}],
            "answer_units": [unit],
            "calculation_contract": contract,
            "blocks": [
                {"label": "解析", "segments": unit["analysis_segments"]},
                {"label": "解题步骤", "segments": [{"type": "text", "text": "计算各组织质量分数。"}, {"type": "formula_ref", "formula_id": "f1"}]},
            ],
        }
        draft = {**fragment, "calculation_contract": contract}

        report = audit_content_quality({"items": [question]}, {"fragments": [fragment]}, {"drafts": [draft]})

        assert "composition_partition_missing_declared_component" in {item["code"] for item in report["issues"]}

    def test_semantic_gate_requires_one_figure_output_per_drawing_unit(self) -> None:
        from app.answer_generation import semantic_generation_issues

        question = {
            "question_id": "q_draw",
            "question_type": "计算题",
            "figure_schema_plan": {"render_decision": {"strategy": "programmatic_renderer"}},
            "subquestions": [
                {
                    "number": "2",
                    "question_type": "作图题",
                    "requirements": [
                        {"number": "2.1", "question_type": "作图题", "stem": "画曲线"},
                        {"number": "2.2", "question_type": "作图题", "stem": "画组织"},
                    ],
                }
            ],
        }
        fragment = {
            "formulas": [{"formula_id": "f1"}],
            "answer_units": [
                {"number": "2.1", "answer": "见图", "figure_specs": [{"kind": "generic_axis_curve"}]},
                {"number": "2.2", "answer": "见图", "figure_specs": []},
            ],
            "_draft": {"formulas": []},
        }

        self.assertIn("missing_drawing_answer_units:2.2", semantic_generation_issues(question, fragment))

    def test_confirmed_calculation_unit_cannot_be_relabelled_or_lose_steps(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft, semantic_generation_issues

        question = {
            "question_id": "q_calc_unit",
            "section": "三、计算题",
            "question_type": "计算题",
            "subquestions": [
                {"number": "1", "stem": "计算功。", "question_type": "计算题"},
                {"number": "2", "stem": "说明含义。", "question_type": "简答题"},
            ],
        }
        fragment = fragment_from_analysis_draft(
            {
                "question_id": "q_calc_unit",
                "answer": "见各小问",
                "answer_units": [
                    {"number": "1", "question_type": "简答题", "answer": "W=1 J", "steps": []},
                    {
                        "number": "2",
                        "question_type": "简答题",
                        "answer": "状态函数含义。",
                        "analysis_segments": [{"text": "由定义判断。", "formula_indices": []}],
                    },
                ],
                "formulas": [{"latex": "W=1\\,\\mathrm{J}", "role": "result", "display": True}],
                "calculation_contract": {"requested_outputs": [], "result_quantities": [], "partitions": []},
            },
            question,
            [],
        )

        unit = next(row for row in fragment["answer_units"] if row["number"] == "1")
        self.assertEqual("计算题", unit["question_type"])
        self.assertIn("calculation_missing_subquestion_steps:1", semantic_generation_issues(question, fragment))

    def test_calculation_summary_lists_all_contract_results(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        fragment = fragment_from_analysis_draft(
            {
                "question_id": "q_multi_result",
                "answer": r"ΔU=-2088\,\mathrm{kJ},\Delta H=-2260\,\mathrm{kJ}",
                "analysis": "见计算。",
                "formulas": [
                    {"latex": r"\Delta U=-2088\,\mathrm{kJ}", "role": "result"},
                    {"latex": r"\Delta H=-2260\,\mathrm{kJ}", "role": "result"},
                ],
                "calculation_contract": {
                    "result_quantities": [
                        {"quantity_id": "du", "name": "ΔU", "value": -2088, "unit": "kJ", "formula_index": 1},
                        {"quantity_id": "dh", "name": "ΔH", "value": -2260, "unit": "kJ", "formula_index": 2},
                    ]
                },
            },
            {"question_id": "q_multi_result", "question_type": "计算题", "stem": "计算ΔU和ΔH。"},
            [],
        )

        self.assertEqual("见解析", fragment["answer"])
        self.assertEqual("ΔU=-2088 kJ；ΔH=-2260 kJ", fragment["answer_summary"])

    def test_semantic_gate_rejects_formula_indices_without_formula_objects(self) -> None:
        from app.answer_generation import semantic_generation_issues

        question = {"question_id": "q_calc", "question_type": "计算题"}
        fragment = {
            "formulas": [{"formula_id": "promoted_reaction"}],
            "_draft": {
                "formulas": [],
                "steps": [{"text": "计算结果", "result_formula_indices": [3]}],
            },
        }

        self.assertIn(
            "formula_reference_out_of_range:3:formula_count=0",
            semantic_generation_issues(question, fragment),
        )

    def test_duplicate_question_and_unit_figure_specs_collapse_per_drawing_leaf(self) -> None:
        from app.answer_generation import _draft_figure_specs

        specs = _draft_figure_specs(
            {
                "figure_specs": [
                    {"kind": "generic_axis_curve", "answer_unit_number": "2.1", "points": [[0, 1], [1, 0]]},
                    {"kind": "microstructure_schematic", "answer_unit_number": "2.2", "features": [{"label": "A"}]},
                ],
                "answer_units": [
                    {
                        "number": "2.1",
                        "figure_specs": [
                            {"kind": "generic_axis_curve", "points": [[0, 1], [1, 0]], "annotations": ["start"]}
                        ],
                    },
                    {
                        "number": "2.2",
                        "figure_specs": [
                            {"kind": "microstructure_schematic", "features": [{"label": "A"}]}
                        ],
                    },
                ],
            }
        )

        self.assertEqual(2, len(specs))
        by_unit = {item["answer_unit_number"]: item for item in specs}
        self.assertEqual(["start"], by_unit["2.1"]["annotations"])


if __name__ == "__main__":
    unittest.main()
