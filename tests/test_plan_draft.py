from __future__ import annotations

import unittest

from app.exercise_generation import _is_single_item_mode, _plan_item_by_id_or_index


def _plan_with(items):
    return {"blueprint": {"exercise_plan": items}, "source_analysis": {}}


def _draft(stem="草稿题干"):
    return {
        "plan_item_id": "plan_item_01",
        "question_type": "简答题",
        "difficulty": "基础",
        "stem": stem,
        "options": [],
        "answer": "草稿答案",
        "solution_steps": ["步骤1"],
        "knowledge_points": ["知识点"],
    }


class PlanDraftHelpersTests(unittest.TestCase):
    def test_single_item_modes_are_detected(self):
        for strategy in ["knowledge_item_wise", "per_question", "parallel_exam", "knowledge_targeted"]:
            self.assertTrue(_is_single_item_mode(strategy), strategy)

    def test_full_set_modes_are_not_single(self):
        for strategy in ["knowledge_overall", "targeted_set", "knowledge_overall"]:
            self.assertFalse(_is_single_item_mode(strategy), strategy)

    def test_lookup_by_plan_item_id(self):
        plan = _plan_with([{"plan_item_id": "plan_item_01", "difficulty": "基础"}, {"plan_item_id": "plan_item_02", "difficulty": "进阶"}])
        item, index = _plan_item_by_id_or_index(plan, "plan_item_02", 0)
        self.assertEqual(item["plan_item_id"], "plan_item_02")
        self.assertEqual(index, 1)

    def test_lookup_by_index_when_no_id(self):
        plan = _plan_with([{"plan_item_id": "plan_item_01"}, {"plan_item_id": "plan_item_02"}])
        item, index = _plan_item_by_id_or_index(plan, "", 1)
        self.assertEqual(item["plan_item_id"], "plan_item_02")
        self.assertEqual(index, 1)

    def test_lookup_raises_for_missing_id(self):
        plan = _plan_with([{"plan_item_id": "plan_item_01"}])
        with self.assertRaises(ValueError):
            _plan_item_by_id_or_index(plan, "nope", 0)

    def test_lookup_raises_for_out_of_range_index(self):
        plan = _plan_with([{"plan_item_id": "plan_item_01"}])
        with self.assertRaises(ValueError):
            _plan_item_by_id_or_index(plan, "", 5)

    def test_lookup_raises_for_empty_plan(self):
        plan = _plan_with([])
        with self.assertRaises(ValueError):
            _plan_item_by_id_or_index(plan, "", 0)

    def test_generate_from_plan_uses_adopted_draft_without_model(self, mocker=None):
        """已采用草案应直接进入正式结果，不再调用模型（防串题/防重复调用）。"""
        from unittest import mock

        import app.exercise_generation as eg

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

        # mock 掉材料解析、模型运行时、客户端构造与模型调用，验证 adopted draft 被注入并返回
        with mock.patch.object(eg, "parse_practice_sources", return_value={"text": "", "images": []}), \
             mock.patch.object(eg, "OpenAICompatibleClient", _FakeClient), \
             mock.patch.object(eg, "_model_runtime", return_value=(mock.Mock(), "m")), \
             mock.patch.object(eg, "_call_practice_json") as call_json:
            payload = {
                "source_mode": "knowledge",
                "generation_strategy": "knowledge_item_wise",
                "question_text": "",
                "source_files": [],
                "plan": {
                    "source_analysis": {"subject": "物理"},
                    "blueprint": {
                        "training_goal": "热学",
                        "generation_strategy": "knowledge_item_wise",
                        "exercise_plan": [{"plan_item_id": "plan_item_01", "question_type": "简答题", "difficulty": "基础", "source_question_id": "source_01"}],
                    },
                },
                "plan_drafts": {"plan_item_01": _draft()},
            }
            result = eg.generate_practice_from_plan(payload)
            # 已采用草案直接进入正式结果，不触发模型调用
            call_json.assert_not_called()
            exercises = result.get("exercises") or []
            self.assertEqual(len(exercises), 1)
            self.assertEqual(exercises[0]["plan_item_id"], "plan_item_01")
            self.assertIn("草稿题干", exercises[0].get("stem"))


if __name__ == "__main__":
    unittest.main()
