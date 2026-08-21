from __future__ import annotations

from types import SimpleNamespace

from app import exercise_generation
from app.llm_client import LLMError


def _planned(index: int, target_skill: str) -> dict:
    return {
        "plan_item_id": f"plan_item_{index:02d}",
        "source_question_id": "",
        "question_type": "简答题",
        "difficulty": "进阶",
        "target_skill": target_skill,
        "variation_type": "综合应用",
        "design_intent": "检查容错",
        "knowledge_points": ["热力学"],
    }


def _generated(batch_index: int) -> dict:
    stems = {
        1: "说明密闭系统内能作为状态函数的物理意义。",
        2: "比较等温与绝热过程的能量传递特征。",
    }
    return {
        "batch_index": batch_index,
        "question_type": "简答题",
        "difficulty": "进阶",
        "target_skill": "成功题",
        "variation_type": "综合应用",
        "stem": stems.get(batch_index, "分析热力学过程的基本特征。"),
        "options": [],
        "knowledge_points": ["热力学"],
        "verification_note": "结构完整。",
        "formulas": [],
        "tables": [],
        "figures": [],
    }


def test_failed_batch_becomes_position_preserving_placeholders(monkeypatch) -> None:
    plan = {
        "source_mode": "knowledge",
        "knowledge_title": "热力学",
        "source_analysis": {"subject": "化学"},
        "source_scope": {"mode": "single", "questions": []},
        "blueprint": {
            "training_goal": "训练热力学分析",
            "generation_strategy": "knowledge_overall",
            "exercise_plan": [
                _planned(1, "成功批次一"),
                _planned(2, "成功批次二"),
                _planned(3, "失败批次一"),
                _planned(4, "失败批次二"),
            ],
        },
    }

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        if "失败批次" in prompt:
            raise LLMError("Provider HTTP 524: error code: 524")
        return {"exercises": [_generated(1), _generated(2)]}

    monkeypatch.setattr(
        exercise_generation,
        "_model_runtime",
        lambda _payload, _has_images: (SimpleNamespace(name="lingsuan"), "gpt-5.6-sol"),
    )
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    result = exercise_generation.generate_practice_from_plan(
        {
            "source_mode": "knowledge",
            "question_text": "热力学第一定律材料",
            "generation_strategy": "knowledge_overall",
            "plan": plan,
            "generation_batch_size": 2,
            "generation_concurrency": 1,
        }
    )

    assert [item["number"] for item in result["exercises"]] == [1, 2, 3, 4]
    assert [item["generation_status"] for item in result["exercises"]] == ["completed", "completed", "failed", "failed"]
    assert result["exercises"][2]["generation_error"]["code"] == "provider_http_524"
    assert "HTTP 524" in result["exercises"][2]["stem"]
    assert result["quality"]["generated_count"] == 2
    assert result["quality"]["failed_count"] == 2
    assert result["quality"]["total_count"] == 4
    assert result["quality"]["partial_success"] is True
    assert result["generation"]["status"] == "partial_success"


def test_quality_recalculation_does_not_count_failure_placeholder_as_generated() -> None:
    quality = exercise_generation.recompute_practice_quality(
        {
            "exercises": [
                {"stem": "有效题目", "question_type": "简答题"},
                {
                    "stem": "生成失败占位",
                    "question_type": "单选题",
                    "generation_status": "failed",
                    "generation_error": {"message": "上游模型响应超时（HTTP 524）。"},
                },
            ]
        }
    )

    assert quality["generated_count"] == 1
    assert quality["failed_count"] == 1
    assert quality["total_count"] == 2
    assert not any("有效选项少于" in warning for warning in quality["warnings"])


def test_drawing_question_without_system_figure_is_complete() -> None:
    quality = exercise_generation.recompute_practice_quality(
        {
            "requested_count": 1,
            "exercises": [
                {
                    "stem": "请画出体心立方晶胞并标注原子位置。",
                    "question_type": "作图题",
                    "knowledge_points": ["晶胞结构"],
                    "verification_note": "题干条件完整。",
                    "figures": [],
                }
            ],
        }
    )

    assert quality["status"] == "passed"
    assert quality["blocking_issues"] == []
    assert quality["checks"]["subject_matter_review_required"] is True
