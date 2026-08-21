import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class KnowledgeCountControlTests(unittest.TestCase):
    """回归：范围确认界面按知识点生题时，出题配置需可修改生题数目。

    需求见 @arlotter CH：
    1. 知识点综合覆盖（knowledge_overall）可填入总题量，默认 5；
    2. 知识单元逐项扩展（knowledge_item_wise）可为每个知识点填入生题数，默认 1。
    """

    def setUp(self):
        self.html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")

    def test_html_has_total_count_control_default_5(self):
        self.assertIn('id="practiceTargetedCount"', self.html)
        self.assertIn('id="practiceTargetedCountRow"', self.html)
        # 综合覆盖默认总量按需求为 5
        self.assertIn('id="practiceTargetedCount" type="number" min="1" max="20" value="5"', self.html)

    def test_html_has_per_point_count_control_default_1(self):
        self.assertIn('id="practiceKnowledgePerCount"', self.html)
        self.assertIn('id="practiceKnowledgePerCountRow"', self.html)
        self.assertIn('id="practiceKnowledgePerCount" type="number" min="1" max="3" value="1"', self.html)

    def test_js_shows_total_count_for_knowledge_overall_and_targeted(self):
        self.assertIn(
            '$("practiceTargetedCountRow")?.classList.toggle("hidden", !["targeted_set", "knowledge_overall"].includes(strategy));',
            self.js,
        )

    def test_js_shows_per_point_count_for_knowledge_item_wise(self):
        self.assertIn(
            '$("practiceKnowledgePerCountRow")?.classList.toggle("hidden", strategy !== "knowledge_item_wise");',
            self.js,
        )

    def test_js_maps_per_point_count_to_variants_per_question(self):
        self.assertIn('variants_per_question: strategy === "knowledge_item_wise" ? perPointCount :', self.js)
        self.assertIn('count: strategy === "knowledge_item_wise" ? perPointCount : totalCount', self.js)

    def test_js_listens_to_per_point_count_input(self):
        self.assertIn('$("practiceKnowledgePerCount")?.addEventListener("input", updatePracticeStrategySettings);', self.js)

    def test_preview_uses_per_point_count_for_item_wise(self):
        self.assertIn('else if (strategy === "knowledge_item_wise") n = Math.min(30, selectedCount * perPoint);', self.js)


if __name__ == "__main__":
    unittest.main()
