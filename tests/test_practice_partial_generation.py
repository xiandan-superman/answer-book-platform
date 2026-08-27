from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import exercise_generation, practice_store
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
    assert "模型服务在规定时间内没有返回完整结果" in result["exercises"][2]["stem"]
    assert "HTTP 524" not in result["exercises"][2]["stem"]
    assert result["quality"]["generated_count"] == 2
    assert result["quality"]["failed_count"] == 2
    assert result["quality"]["total_count"] == 4
    assert result["quality"]["partial_success"] is True
    assert result["generation"]["status"] == "partial_success"


def test_blueprint_item_audit_failure_skips_only_that_item_and_preserves_healthy_result(monkeypatch) -> None:
    sources = [
        {"source_question_id": "source_a", "title": "精馏", "knowledge_points": ["精馏原理"]},
        {"source_question_id": "source_b", "title": "晶体", "knowledge_points": ["晶面指数标定步骤"]},
    ]
    plan = {
        "source_mode": "knowledge",
        "knowledge_title": "局部门禁隔离",
        "source_analysis": {"subject": "化学"},
        "source_scope": {"mode": "question_set", "questions": sources},
        "selected_source_questions": sources,
        "blueprint": {
            "training_goal": "分别训练两个知识单元",
            "progression": ["概念", "步骤"],
            "generation_strategy": "knowledge_item_wise",
            "exercise_plan": [
                {
                    "number": 1,
                    "plan_item_id": "plan_item_01",
                    "source_question_id": "source_a",
                    "source_refs": ["source_a"],
                    "question_type": "简答题",
                    "difficulty": "基础",
                    "target_skill": "精馏原理",
                    "variation_type": "直接说明",
                    "design_intent": "需要计算晶面指数",
                    "difficulty_levers": ["条件直接程度"],
                    "difficulty_rationale": "按绑定范围作答。",
                    "required_knowledge_points": ["精馏原理"],
                },
                {
                    "number": 2,
                    "plan_item_id": "plan_item_02",
                    "source_question_id": "source_b",
                    "source_refs": ["source_b"],
                    "question_type": "简答题",
                    "difficulty": "基础",
                    "target_skill": "晶面指数标定",
                    "variation_type": "步骤说明",
                    "design_intent": "考查晶面指数标定步骤",
                    "difficulty_levers": ["条件直接程度"],
                    "difficulty_rationale": "按顺序作答。",
                    "required_knowledge_points": ["晶面指数标定步骤"],
                },
            ],
        },
    }
    calls = []

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        assert "需要计算晶面指数" not in prompt
        return {"exercises": [{
            "batch_index": 1,
            "question_type": "简答题",
            "difficulty": "基础",
            "target_skill": "晶面指数标定",
            "variation_type": "步骤说明",
            "stem": "说明晶面指数标定的基本步骤。",
            "options": [],
            "knowledge_points": ["晶面指数标定步骤"],
            "verification_note": "条件完整。",
            "formulas": [],
            "tables": [],
            "figures": [],
        }]}

    monkeypatch.setattr(exercise_generation, "_primary_model_runtime", lambda _payload: (SimpleNamespace(name="fake", supports_vision=False), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    result = exercise_generation.generate_practice_from_plan({
        "source_mode": "knowledge",
        "generation_strategy": "knowledge_item_wise",
        "question_text": "来源材料",
        "plan": plan,
        "generation_batch_size": 2,
        "generation_concurrency": 1,
    })

    assert len(calls) == 1
    assert [item["generation_status"] for item in result["exercises"]] == ["failed", "completed"]
    assert result["exercises"][0]["generation_error"]["code"] == "blueprint_audit_failed"
    assert result["exercises"][0]["audit_status"] == "audit_failed"
    assert result["exercises"][0]["review_status"] == "needs_review"
    assert result["exercises"][1]["stem"] == "说明晶面指数标定的基本步骤。"
    assert result["generation"]["status"] == "partial_success"
    assert result["blueprint_audit"]["local_blocking_item_ids"] == ["plan_item_01"]
    assert any(row["status"] == "blueprint_audit_failed" and row["model_call_count"] == 0 for row in result["generation"]["batch_diagnostics"])


def test_global_blueprint_contract_error_blocks_before_generation_runtime(monkeypatch) -> None:
    plan = {
        "source_mode": "knowledge",
        "blueprint": {
            "generation_strategy": "knowledge_overall",
            "exercise_plan": [
                {"plan_item_id": "duplicate", "question_type": "简答题", "difficulty": "基础", "target_skill": "A", "variation_type": "A", "design_intent": "A"},
                {"plan_item_id": "duplicate", "question_type": "非法题型", "difficulty": "基础", "target_skill": "B", "variation_type": "B", "design_intent": "B"},
            ],
        },
    }
    runtime_calls = []
    monkeypatch.setattr(exercise_generation, "_primary_model_runtime", lambda _payload: runtime_calls.append(True))

    with pytest.raises(ValueError, match="确认后的蓝图未通过生成门禁"):
        exercise_generation.generate_practice_from_plan({
            "source_mode": "knowledge",
            "generation_strategy": "knowledge_overall",
            "plan": plan,
        })

    assert runtime_calls == []


def test_audit_failed_item_can_be_repaired_reviewed_generated_and_saved_locally(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_store, "PRACTICE_HISTORY_DIR", tmp_path / "history")
    sources = [
        {"source_question_id": "source_a", "title": "精馏", "knowledge_points": ["精馏原理"]},
        {"source_question_id": "source_b", "title": "晶体", "knowledge_points": ["晶面指数标定步骤"]},
    ]
    blueprint = {
        "training_goal": "局部复审",
        "progression": ["概念", "步骤"],
        "generation_strategy": "knowledge_item_wise",
        "exercise_plan": [
            {
                "number": 1,
                "plan_item_id": "plan_item_01",
                "source_question_id": "source_a",
                "source_refs": ["source_a"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "精馏原理",
                "variation_type": "直接说明",
                "design_intent": "需要计算晶面指数",
                "difficulty_levers": ["条件直接程度"],
                "difficulty_rationale": "按绑定范围作答。",
                "required_knowledge_points": ["精馏原理"],
            },
            {
                "number": 2,
                "plan_item_id": "plan_item_02",
                "source_question_id": "source_b",
                "source_refs": ["source_b"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "晶面指数标定",
                "variation_type": "步骤说明",
                "design_intent": "考查晶面指数标定步骤",
                "difficulty_levers": ["条件直接程度"],
                "difficulty_rationale": "按顺序作答。",
                "required_knowledge_points": ["晶面指数标定步骤"],
            },
        ],
    }
    practice = {
        "source_mode": "knowledge",
        "source_analysis": {"subject": "化学"},
        "source_scope": {"mode": "question_set", "questions": sources},
        "selected_source_questions": sources,
        "blueprint": blueprint,
        "requested_count": 2,
        "exercises": [
            exercise_generation._failed_exercise_placeholder(
                blueprint["exercise_plan"][0],
                index=1,
                error={
                    "code": "blueprint_audit_failed",
                    "message": "蓝图项仍包含未绑定主题，已暂停本题生成并等待复核。",
                    "retryable": True,
                },
            ),
            {
                "number": 2,
                "exercise_id": "practice_02",
                "plan_item_id": "plan_item_02",
                "source_question_id": "source_b",
                "source_refs": ["source_b"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "晶面指数标定",
                "variation_type": "步骤说明",
                "stem": "说明晶面指数标定步骤。",
                "options": [],
                "knowledge_points": ["晶面指数标定步骤"],
                "generation_status": "completed",
            },
        ],
    }
    saved = practice_store.save_practice_record(practice, request={"source_mode": "knowledge"})
    generation_calls = []

    monkeypatch.setattr(
        exercise_generation,
        "_refine_blueprint_batch",
        lambda _plan, batch, **_kwargs: [{**batch[0], "design_intent": "仅考查精馏原理"}],
    )
    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake", supports_vision=False), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())

    def fake_generation(_client, _messages, **_kwargs):
        generation_calls.append(True)
        return {"exercises": [{
            "question_type": "简答题",
            "difficulty": "基础",
            "target_skill": "精馏原理",
            "variation_type": "条件说明",
            "stem": "说明精馏过程中回流比对分离效果的作用。",
            "options": [],
            "knowledge_points": ["精馏原理"],
            "verification_note": "条件完整。",
            "formulas": [],
            "tables": [],
            "figures": [],
        }]}

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_generation)
    response = exercise_generation.regenerate_practice_exercise({
        "practice": saved["data"],
        "exercise_index": 0,
        "source_mode": "knowledge",
        "include_source_content_in_generation": False,
        "semantic_review_enabled": False,
    })

    assert generation_calls == [True]
    assert response["practice_updates"]["blueprint_audit_repair"]["call_count"] == 1
    assert response["practice_updates"]["blueprint_audit_repair"]["repaired_item_ids"] == ["plan_item_01"]
    assert response["practice_updates"]["blueprint_audit"]["local_blocking_item_ids"] == []
    updated = practice_store.update_practice_exercise(
        saved["history_id"],
        0,
        response["exercise"],
        semantic_review=response["semantic_review"],
        practice_updates=response["practice_updates"],
        expected_edit_version=saved["data"]["exercises"][0]["_edit_version"],
    )

    assert updated["data"]["exercises"][0]["generation_status"] == "completed"
    assert updated["data"]["blueprint"]["exercise_plan"][0]["design_intent"] == "仅考查精馏原理"
    assert updated["data"]["blueprint_audit"]["local_blocking_item_ids"] == []
    assert updated["revision_count"] == 1


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
    assert quality["checks"]["subject_matter_review_required"] is False
