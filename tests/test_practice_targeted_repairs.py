from __future__ import annotations

from app import exercise_generation


def test_format_repair_regenerates_only_the_question_that_failed_word_preflight(monkeypatch) -> None:
    practice = {
        "exercises": [
            {"number": 1, "plan_item_id": "p1", "stem": "安全题干", "options": [], "tables": []},
            {"number": 2, "plan_item_id": "p2", "stem": "坏公式", "options": [], "tables": []},
            {"number": 3, "plan_item_id": "p3", "stem": "另一道安全题", "options": [], "tables": []},
        ]
    }
    regenerated_indexes = []

    monkeypatch.setattr(
        exercise_generation,
        "preflight_practice_inline_expressions",
        lambda data: ["无法生成 Word 公式对象"]
        if (data.get("exercises") or [{}])[0].get("stem") == "坏公式"
        else [],
    )

    def regenerate(payload):
        regenerated_indexes.append(payload["exercise_index"])
        return {"exercise": {**payload["practice"]["exercises"][payload["exercise_index"]], "stem": "修复后的公式"}}

    monkeypatch.setattr(exercise_generation, "regenerate_practice_exercise", regenerate)

    report = exercise_generation._selectively_repair_practice_format(practice, {})

    assert regenerated_indexes == [1]
    assert practice["exercises"][0]["stem"] == "安全题干"
    assert practice["exercises"][1]["stem"] == "修复后的公式"
    assert practice["exercises"][2]["stem"] == "另一道安全题"
    assert report["status"] == "repaired"
    assert report["attempts"][0]["status"] == "repaired_by_targeted_regeneration"


def test_generation_entry_repairs_only_cross_source_blueprint_item_before_generation(monkeypatch) -> None:
    plan = {
        "source_mode": "knowledge",
        "selected_source_questions": [
            {"source_question_id": "source_a", "knowledge_points": ["精馏原理"]},
            {"source_question_id": "source_b", "knowledge_points": ["晶面指数"]},
        ],
        "blueprint": {
            "generation_strategy": "knowledge_item_wise",
            "training_goal": "边界测试",
            "progression": ["基础"],
            "exercise_plan": [{
                "number": 1,
                "plan_item_id": "p1",
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
            }, {
                "number": 2,
                "plan_item_id": "p2",
                "source_question_id": "source_b",
                "source_refs": ["source_b"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "晶面指数",
                "variation_type": "直接说明",
                "design_intent": "说明晶面指数。",
                "difficulty_levers": ["条件直接程度"],
                "difficulty_rationale": "按绑定范围作答。",
                "required_knowledge_points": ["晶面指数"],
            }],
        },
    }
    repair_targets = []

    def repair(current_plan, _payload, _audit, **kwargs):
        repair_targets.extend(kwargs["target_item_ids"])
        current_plan["blueprint"]["exercise_plan"][0]["design_intent"] = "仅考查精馏原理"
        return {
            "enabled": True,
            "attempted_item_ids": ["p1"],
            "repaired_item_ids": ["p1"],
            "unresolved_item_ids": [],
            "call_count": 1,
        }

    monkeypatch.setattr(exercise_generation, "repair_blueprint_audit_findings", repair)
    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (type("Provider", (), {"name": "fake", "supports_vision": False})(), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "_practice_generation_client", lambda *_args: object())
    def generate_batch(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        knowledge_point = "晶面指数" if "source_b" in prompt and "source_a" not in prompt else "精馏原理"
        return {"exercises": [{
            "batch_index": 1,
            "question_type": "简答题",
            "difficulty": "基础",
            "target_skill": knowledge_point,
            "variation_type": "直接说明",
            "stem": f"说明{knowledge_point}。",
            "options": [],
            "knowledge_points": [knowledge_point],
            "verification_note": "条件完整。",
            "formulas": [],
            "tables": [],
            "figures": [],
        }]}

    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json_with_transport_retry",
        generate_batch,
    )

    result = exercise_generation.generate_practice_from_plan({
        "source_mode": "knowledge",
        "generation_strategy": "knowledge_item_wise",
        "include_source_content_in_generation": False,
        "semantic_review_enabled": False,
        "plan_drafts": {
            "p1": {
                "question_type": "简答题", "difficulty": "基础", "target_skill": "精馏原理",
                "variation_type": "直接说明", "stem": "说明精馏原理。", "options": [],
                "knowledge_points": ["精馏原理"], "verification_note": "条件完整。",
                "formulas": [], "tables": [], "figures": [],
            },
            "p2": {
                "question_type": "简答题", "difficulty": "基础", "target_skill": "晶面指数",
                "variation_type": "直接说明", "stem": "说明晶面指数。", "options": [],
                "knowledge_points": ["晶面指数"], "verification_note": "条件完整。",
                "formulas": [], "tables": [], "figures": [],
            },
        },
        "plan": plan,
    })

    assert repair_targets == ["p1"]
    assert result["exercises"][0]["generation_status"] == "completed"
    assert result["blueprint_audit"]["local_blocking_item_ids"] == []
    assert result["blueprint_audit_repair"]["repaired_item_ids"] == ["p1"]
