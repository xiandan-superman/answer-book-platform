from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.exercise_generation import plan_practice_set


class KnowledgeGenerationTests(unittest.TestCase):
    def test_knowledge_mode_builds_targeted_blueprint_without_source_selection(self) -> None:
        captured: dict = {}

        def fake_call(_client, messages, **_kwargs):
            captured["prompt"] = messages[-1]["content"]
            return {
                "source_scope": {"mode": "question_set", "title": "教材章节", "questions": [{"title": "误识别题目"}]},
                "source_analysis": {
                    "subject": "材料科学",
                    "question_type": "",
                    "knowledge_points": ["晶胞", "点阵参数"],
                    "skills": ["空间关系分析"],
                    "difficulty": "研究生",
                    "solution_strategy": ["定义辨析", "参数计算"],
                    "common_errors": ["混淆晶格与晶胞"],
                    "uncertainties": [],
                },
                "blueprint": {
                    "training_goal": "掌握晶胞与点阵参数",
                    "progression": ["概念", "计算", "迁移"],
                    "design_notes": ["不机械复述教材"],
                    "exercise_plan": [
                        {
                            "number": index,
                            "question_type": "计算题",
                            "difficulty": "进阶",
                            "target_skill": "参数计算",
                            "variation_type": "知识应用",
                            "design_intent": "检验理解",
                            "source_question_id": "",
                        }
                        for index in range(1, 4)
                    ],
                },
            }

        provider = SimpleNamespace(name="deepseek")
        payload = {
            "source_mode": "knowledge",
            "knowledge_title": "晶胞 / 点阵参数",
            "question_text": "# 知识点名称\n\n晶胞 / 点阵参数",
            "count": 3,
            "difficulty": "进阶到挑战",
            "question_types": ["计算题"],
        }
        with (
            patch("app.exercise_generation._model_runtime", return_value=(provider, "deepseek-v4-flash")),
            patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
            patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
        ):
            plan = plan_practice_set(payload)

        self.assertEqual(plan["source_mode"], "knowledge")
        self.assertEqual(plan["knowledge_title"], "晶胞 / 点阵参数")
        self.assertEqual(plan["source_scope"]["mode"], "single")
        self.assertEqual(plan["source_scope"]["questions"], [])
        self.assertEqual(plan["blueprint"]["generation_strategy"], "knowledge_targeted")
        self.assertEqual(len(plan["blueprint"]["exercise_plan"]), 3)
        self.assertNotIn("requires_source_selection", plan)
        self.assertIn("不得把知识材料误判成真题", captured["prompt"])
        self.assertIn("题量：3", captured["prompt"])
        self.assertIn("整套题难度分布：进阶到挑战", captured["prompt"])
        self.assertIn("程序指定的逐题难度：进阶、进阶、挑战", captured["prompt"])
        self.assertEqual(
            [item["difficulty"] for item in plan["blueprint"]["exercise_plan"]],
            ["进阶", "进阶", "挑战"],
        )


if __name__ == "__main__":
    unittest.main()
