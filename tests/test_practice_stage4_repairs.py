from __future__ import annotations

from pathlib import Path

import app.capabilities as capabilities
from app import exercise_generation
from app.exercise_generation import normalize_practice_set, recompute_practice_quality
from app.practice_export import validate_practice_export

assert capabilities is not None


def _complete_practice(**overrides):
    practice = {
        "requested_count": 1,
        "source_mode": "knowledge",
        "generation_strategy": "knowledge_overall",
        "selected_source_questions": [
            {
                "source_question_id": "source_01",
                "title": "动态回复",
                "source_content": "高层错能金属的储存能不足以触发动态再结晶。",
            },
            {
                "source_question_id": "source_02",
                "title": "动态再结晶",
                "source_content": "低层错能金属在相应条件下更容易发生动态再结晶。",
            },
        ],
        "blueprint": {
            "generation_strategy": "knowledge_overall",
            "exercise_plan": [{
                "plan_item_id": "plan_item_01",
                "source_question_id": "source_01",
                "source_refs": ["source_01", "source_02"],
                "question_type": "判断题",
                "required_knowledge_points": ["动态回复", "动态再结晶"],
            }],
        },
        "exercises": [{
            "number": 1,
            "plan_item_id": "plan_item_01",
            "source_question_id": "source_01",
            "source_refs": ["source_01", "source_02"],
            "question_type": "判断题",
            "difficulty": "进阶",
            "stem": "低层错能金属在满足相应条件时始终只发生动态回复。",
            "knowledge_points": ["动态回复", "动态再结晶"],
            "verification_note": "材料边界足以唯一判断命题真值。",
            "generation_status": "completed",
        }],
    }
    practice.update(overrides)
    return practice


def test_historical_semantic_review_does_not_change_current_export_state():
    practice = _complete_practice(semantic_review={
        "status": "failed",
        "items": [{"number": 1, "status": "risk", "risks": [{"severity": "high", "message": "旧风险"}]}],
    })

    quality = recompute_practice_quality(practice)

    assert quality["release_level"] == "formal"
    assert "semantic_review_risks" not in quality
    assert validate_practice_export(practice)["release_level"] == "formal"


def test_normalization_preserves_all_blueprint_source_refs():
    result = normalize_practice_set(
        {"exercises": [{
            "plan_item_id": "plan_item_01",
            "source_question_id": "source_01",
            "source_refs": ["source_01", "source_02", "source_03"],
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": "比较机制",
            "variation_type": "跨单元综合",
            "stem": "比较三类材料条件下的机制差异。",
            "knowledge_points": ["动态回复", "动态再结晶"],
            "verification_note": "边界完整。",
        }]},
        requested_count=1,
        subject="材料科学",
        planned_types=["简答题"],
        planned_source_ids=["source_01"],
        planned_plan_ids=["plan_item_01"],
        planned_difficulties=["进阶"],
    )

    assert result["exercises"][0]["source_refs"] == ["source_01", "source_02", "source_03"]


def test_frontend_renders_knowledge_source_refs_without_semantic_review_state():
    script = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    task_contract = (Path(__file__).parents[1] / "web" / "task-contract-ui.js").read_text(encoding="utf-8")

    assert 'data.source_mode === "knowledge" ? "来源知识单元" : "来源原题"' in script
    assert "item.source_refs" in script
    assert "semanticReviewIncomplete" not in script
    assert 'quality.release_level === "review_candidate"' in script
    assert "项需复核" in script
    assert "task.is_generation_task && task.status === \"completed_with_issues\"" not in task_contract


def test_negative_boundary_is_not_misread_as_its_positive_substring():
    check = exercise_generation._exercise_boundary_issues
    for positive, negative in [("稳态", "非稳态"), ("平衡", "非平衡"), ("可逆", "不可逆")]:
        item = {"stem": f"分析{negative}过程。"}
        assert not check(item, {"required_constraints": {"applicable_boundaries": [f"适用于{negative}过程"]}})
        assert check(item, {"required_constraints": {"applicable_boundaries": [f"适用于{positive}过程"]}}) == []


def test_scientific_model_analysis_is_not_an_internal_draft():
    from app.practice_export import validate_practice_export
    assert validate_practice_export({"exercises": [{"stem": "结合刚球模型分析晶格常数与原子半径关系。"}]})["ok"]
    assert not validate_practice_export({"exercises": [{"stem": "自我纠错：我刚才给出的答案有误。"}]})["ok"]


def test_auto_mixed_types_include_fill_blank_without_forcing_drawing():
    assert set(exercise_generation._type_plan([], 10)) == {"简答题", "综合题", "判断题", "单选题", "填空题"}
    assert exercise_generation._type_plan(["判断题"], 2) == ["判断题", "判断题"]
