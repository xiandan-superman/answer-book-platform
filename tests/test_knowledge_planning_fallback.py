from __future__ import annotations

from app.knowledge_planning import fallback_knowledge_plan


def test_fallback_plan_reuses_visual_understanding_and_ignores_page_furniture() -> None:
    question = {
        "question_id": "q_visual",
        "section": "简答题",
        "stem": "某物质的图(示意图)如题四图所示，回答问题。",
        "question_understanding": {
            "question_requirements": [{"text": "判断边界斜率随外部条件如何变化。"}],
            "images": [
                {
                    "visual_description": "该物质的压强-温度相图，三条两相平衡线交于一点。",
                    "answer_relevant_observations": ["一条固液平衡线具有负斜率。"],
                }
            ],
        },
    }

    plan = fallback_knowledge_plan(question, "模型超时")

    assert plan["search_queries"][0].startswith("该物质的压强-温度相图")
    assert any("固液平衡线" in query for query in plan["search_queries"])
    assert "简答题" not in plan["key_terms"]
    assert "示意图" not in plan["key_terms"]
    assert "模型超时" in plan["warnings"]
