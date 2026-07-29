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


if __name__ == "__main__":
    unittest.main()
