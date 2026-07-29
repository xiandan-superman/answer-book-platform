from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.exam_extract import question_items, split_sections


class ExamExtractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
