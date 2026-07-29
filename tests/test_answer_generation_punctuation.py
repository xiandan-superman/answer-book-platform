from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class AnswerGenerationPunctuationTests(unittest.TestCase):
    def test_joins_sentence_lists_without_period_semicolon_boundary(self) -> None:
        from app.answer_generation import _list_text

        value = ["第一项已经结束。", "第二项也已经结束。"]

        self.assertEqual("第一项已经结束；第二项也已经结束。", _list_text(value))

    def test_preserves_plain_model_prose_without_global_punctuation_rewrite(self) -> None:
        from app.answer_generation import _list_text

        prose = "原始正文中的。；组合不属于程序列表拼接，必须保留。"

        self.assertEqual(prose, _list_text(prose))

    def test_option_analysis_and_warnings_keep_independent_boundary_rules(self) -> None:
        from app.answer_generation import _option_analysis_text, _warning_items

        options = {"A": "A 项说明。", "B": "B 项说明；"}

        self.assertEqual("A：A 项说明；B：B 项说明。", _option_analysis_text(options))
        self.assertEqual(["风险一。", "风险二；"], _warning_items(["风险一。", "风险二；"], []))

    def test_fragment_uses_normalized_sentence_list_for_mistake_notes(self) -> None:
        from app.answer_generation import fragment_from_analysis_draft

        fragment = fragment_from_analysis_draft(
            {
                "question_id": "q1",
                "answer": "结论。",
                "analysis": "解析正文。",
                "mistake_notes": ["第一项。", "第二项。"],
            },
            {"question_id": "q1", "question_type": "简答题", "stem": "说明原因。"},
            [],
        )

        block = next(item for item in fragment["blocks"] if item["label"] == "易错点及注意事项")
        self.assertEqual("第一项；第二项。", block["segments"][0]["text"])


if __name__ == "__main__":
    unittest.main()
