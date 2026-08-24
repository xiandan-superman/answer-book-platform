from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.exam_extract import IMAGE_MARKER_PREFIX, _subquestion_entry, question_items, split_sections


class ExamExtractTests(unittest.TestCase):
    def test_inline_numbered_term_explanations_are_split_into_individual_items(self) -> None:
        section = split_sections(
            [
                "一、名词解释（5分/小题，共30分）",
                "1、再结晶温度 2、相平衡条件 3、上坡扩散 4、空间点阵",
                "5、堆垛层错 6、临界分切应力",
            ]
        )[0]

        items = question_items(section)

        self.assertEqual(["1", "2", "3", "4", "5", "6"], [item["number"] for item in items])
        self.assertEqual("临界分切应力", items[-1]["stem"])

    def test_parenthesized_inline_term_explanations_are_split_into_individual_items(self) -> None:
        section = split_sections(
            [
                "一、名词解释（每小题2分，共10分）",
                "(1) 扩散；(2) 位错；(3) 再结晶；(4) 相平衡；(5) 过冷度",
            ]
        )[0]

        items = question_items(section)

        self.assertEqual(["1", "2", "3", "4", "5"], [item["number"] for item in items])
        self.assertEqual("扩散", items[0]["stem"])
        self.assertEqual("过冷度", items[-1]["stem"])
        self.assertTrue(all(item["section_item_count"] == 5 for item in items))

    def test_parenthesized_numbers_are_not_split_outside_term_sections(self) -> None:
        section = split_sections(
            [
                "二、计算题（20分）",
                "1、已知表达式 (1) 与边界条件 (2)，计算结果。",
            ]
        )[0]

        items = question_items(section)

        self.assertEqual(1, len(items))
        self.assertIn("表达式 (1)", items[0]["stem"])

    def test_unknown_subject_heading_is_detected_by_document_structure(self) -> None:
        sections = split_sections(
            [
                "生命科学基础",
                "一、选择题",
                "1、细胞膜的主要作用是（ ）。",
            ]
        )

        self.assertEqual("生命科学基础", sections[0]["subject"])

    def test_missing_first_marker_is_recovered_before_explicit_second_part(self) -> None:
        section = split_sections(
            [
                "三、计算题",
                "1、理想气体由始态绝热膨胀至末态。",
                "求过程的W、ΔU、ΔH、ΔS；",
                "(2)ΔH的正负是否表示吸放热？ΔS的正负是否表示自发性？",
            ]
        )[0]

        item = question_items(section)[0]

        self.assertEqual(["1", "2"], [row["number"] for row in item["subquestions"]])
        self.assertTrue(item["subquestions"][0]["inferred_missing_marker"])
        self.assertIn("求过程", item["subquestions"][0]["stem"])
        self.assertEqual("计算题", item["subquestions"][0]["question_type"])

    def test_answer_precision_instruction_is_not_a_nested_requirement(self) -> None:
        entry = _subquestion_entry(
            "2",
            "(2)",
            "所求ΔH的正负是否表示吸放热？ΔS的正负是否表示自发性？(W、ΔU、ΔH、ΔS计算结果取四位有效数字)",
            "(2)所求ΔH的正负是否表示吸放热？ΔS的正负是否表示自发性？(W、ΔU、ΔH、ΔS计算结果取四位有效数字)",
        )

        self.assertEqual(["2.1", "2.2"], [row["number"] for row in entry["requirements"]])
        self.assertEqual(1, len(entry["response_constraints"]))
        self.assertIn("四位有效数字", entry["response_constraints"][0])

    def test_unnumbered_mixed_answer_actions_become_typed_requirements(self) -> None:
        section = split_sections(
            [
                "六、简答题",
                "6、根据相图写出三相反应；同时示意画出扩散层成分曲线，并标注各相。",
            ]
        )[0]

        item = question_items(section)[0]

        self.assertEqual(1, len(item["subquestions"]))
        self.assertTrue(item["subquestions"][0]["synthetic_parent"])
        requirements = item["subquestions"][0]["requirements"]
        self.assertEqual(["6.1", "6.2"], [row["number"] for row in requirements])
        self.assertEqual(["简答题", "作图题"], [row["question_type"] for row in requirements])
        self.assertIn("示意画出扩散层", requirements[1]["stem"])

    def test_leading_numeric_givens_do_not_become_an_answer_unit(self) -> None:
        section = split_sections(
            [
                "四、计算题",
                "5、Cu 的相对原子质量为63.55g/mol，密度为8.96g/cm3，NA=6.023×10^23/mol，计算铜原子的点阵常数和原子半径。",
            ]
        )[0]

        item = question_items(section)[0]

        self.assertFalse(item.get("subquestions"))

    def test_composite_section_trailing_figure_is_shared_by_all_subquestions(self) -> None:
        image = "/tmp/shared_phase_diagram.png"
        section = split_sections(
            [
                "十、综合题(本题共12分)",
                "1、简述合金2凝固过程，画出室温组织图，并计算相组成。(6分)",
                "2、比较两种铸态性能。(2分)",
                "3、分析T1时效曲线。(2分)",
                "4、比较T1与T2峰值时间。(2分)",
                f"{IMAGE_MARKER_PREFIX}{image}",
            ]
        )[0]

        items = question_items(section)

        self.assertEqual(1, len(items))
        parent = items[0]
        self.assertEqual([image], parent["image_refs"])
        self.assertEqual(["1", "2", "3", "4"], [item["number"] for item in parent["subquestions"]])
        self.assertEqual(
            ["作图题", "简答题", "简答题", "简答题"],
            [item["question_type"] for item in parent["subquestions"]],
        )
        self.assertEqual("shared_composite_question", parent["attachment_scope"]["kind"])
        self.assertEqual(
            ["简答题", "作图题", "计算题"],
            [item["question_type"] for item in parent["subquestions"][0]["requirements"]],
        )

    def test_dot_style_chinese_section_title_extracts_choice_items(self) -> None:
        paragraphs = [
            "一.选择题",
            "1、在298K恒压下，把某化学反应设计在可逆电池中进行可得电功 91.84kJ，该过程的∆H，∆S，∆G的值为………………………………( )",
            "A. -121.8 716.8 -91.84",
            "B. 121.8 716.8 -91.84",
            "C. 121.8 0.7168 -91.84",
            "D. -121.8 0.7168 -91.84",
            "2、已知纯液体A和B，其沸点分别为TA*=116℃，TB*=80℃，A和B可以形成双组分理想液态混合物，则……………………( )",
            "A. 在蒸馏塔的塔顶得到纯B B. 在蒸馏塔的塔底得到纯B",
            "C. 在蒸馏塔的塔中得到纯B D. 无法判断",
            "3、通电于含有活度相同的Fe2+、Ca2+、Zn2+、Cu2+的电解质溶液中，金属析出的顺序为…………( )",
            "A. Cu→Fe→Zn→Ca B. Ca→Zn→Fe→Cu",
            "C. Ca→Fe→Zn→Cu D. Ca→Cu→Zn→Fe",
            "二、简答题",
            "1、试确定下列体系的自由度数，并写明计算过程。",
        ]

        sections = split_sections(paragraphs)
        self.assertEqual(["一.选择题", "二、简答题"], [section["raw_title"] for section in sections])

        choice_items = question_items(sections[0])
        self.assertEqual(3, len(choice_items))
        self.assertEqual("一、选择题", choice_items[0]["section"])
        self.assertIn("A. -121.8", choice_items[0]["stem"])
        self.assertIn("D. 无法判断", choice_items[1]["stem"])

    def test_dot_style_chinese_list_without_question_type_does_not_split_section(self) -> None:
        paragraphs = [
            "一、简答题",
            "1、说明下列概念。",
            "一. 这只是题干中的中文编号，不是大题标题。",
            "二. 这仍属于第1题题干。",
        ]

        sections = split_sections(paragraphs)
        self.assertEqual(1, len(sections))
        items = question_items(sections[0])
        self.assertEqual(1, len(items))
        self.assertIn("这只是题干中的中文编号", items[0]["stem"])

    def test_unnumbered_single_question_preserves_major_question_number(self) -> None:
        section = split_sections(
            [
                "九、相图分析题（本题共14分）",
                "题九4图为某合金相图。",
                "(1)说明恒温转变。",
                "(2)计算室温组织组成。",
            ]
        )[0]

        items = question_items(section)

        self.assertEqual(1, len(items))
        self.assertEqual("9", items[0]["number"])
        self.assertEqual(["1", "2"], [row["number"] for row in items[0]["subquestions"]])

    def test_grouped_composite_parent_uses_major_number_not_first_subquestion_number(self) -> None:
        image = "/tmp/shared_composite.png"
        section = split_sections(
            [
                "十、综合题（本题共12分）",
                "1、说明过程。",
                "2、计算组成。",
                f"{IMAGE_MARKER_PREFIX}{image}",
            ]
        )[0]

        items = question_items(section)

        self.assertEqual(1, len(items))
        self.assertEqual("10", items[0]["number"])
        self.assertEqual(["1", "2"], [row["number"] for row in items[0]["subquestions"]])


if __name__ == "__main__":
    unittest.main()
