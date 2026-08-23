from __future__ import annotations

from pathlib import Path

import app.capabilities as capabilities
from app.exercise_generation import ensure_practice_blueprint_defaults, scope_cover_summary

ROOT = Path(__file__).resolve().parents[1]
assert capabilities is not None  # Load the registry before the package's legacy circular import path.


def _source(source_id: str, points: list[str]) -> dict:
    return {
        "source_question_id": source_id,
        "number": source_id,
        "title": source_id,
        "knowledge_points": points,
        "required_constraints": {
            "essential_definitions": [],
            "essential_formulas": [],
            "applicable_boundaries": [],
        },
    }


def _plan(items: list[dict]) -> dict:
    sources = [
        _source("source_01", ["热加工的定义"]),
        _source(
            "source_02",
            [
                "高层错能金属的位错运动特征",
                "动态回复过程的真应力-真应变曲线阶段划分",
            ],
        ),
    ]
    return {
        "selected_source_questions": sources,
        "source_scope": {
            "granularity": "top_level",
            "questions": sources,
        },
        "source_analysis": {"knowledge_points": []},
        "blueprint": {
            "generation_strategy": "knowledge_overall",
            "exercise_plan": items,
        },
    }


def _item(*, design_intent: str, definitions: list[str]) -> dict:
    return {
        "source_refs": ["source_01", "source_02"],
        "required_knowledge_points": ["热加工的定义"],
        "target_skill": "材料机理辨析",
        "variation_type": "对比归纳",
        "design_intent": design_intent,
        "required_constraints": {
            "essential_definitions": definitions,
            "essential_formulas": [],
            "applicable_boundaries": ["不扩展到静态回复与静态再结晶"],
        },
    }


def test_blueprint_reconciles_only_points_explicitly_adopted_by_design() -> None:
    plan = _plan(
        [
            _item(
                design_intent="比较高层错能金属的位错运动特征并解释其软化行为。",
                definitions=["高层错能金属的扩展位错较窄，螺型位错易交滑移。"],
            )
        ]
    )

    upgraded = ensure_practice_blueprint_defaults(plan)
    item = upgraded["blueprint"]["exercise_plan"][0]

    assert "高层错能金属的位错运动特征" in item["required_knowledge_points"]
    assert "动态回复过程的真应力-真应变曲线阶段划分" not in item["required_knowledge_points"]
    assert item["knowledge_point_reconciliation"]["added_from_blueprint_evidence"] == [
        "高层错能金属的位错运动特征"
    ]

    upgraded_again = ensure_practice_blueprint_defaults(upgraded)
    assert upgraded_again["blueprint"]["exercise_plan"][0]["knowledge_point_reconciliation"] == (
        item["knowledge_point_reconciliation"]
    )


def test_scope_cover_reports_source_and_knowledge_coverage_separately() -> None:
    plan = ensure_practice_blueprint_defaults(
        _plan(
            [
                _item(
                    design_intent="比较高层错能金属的位错运动特征并解释其软化行为。",
                    definitions=["高层错能金属的扩展位错较窄，螺型位错易交滑移。"],
                )
            ]
        )
    )
    cover = scope_cover_summary(
        plan["source_scope"],
        plan["selected_source_questions"],
        plan["blueprint"]["exercise_plan"],
    )

    assert cover["source_complete"] is True
    assert cover["complete"] is True  # compatibility: the hard gate is source coverage
    assert cover["content_complete"] is False
    assert cover["knowledge_points"]["covered_points"] == [
        "热加工的定义",
        "高层错能金属的位错运动特征",
    ]
    assert cover["knowledge_points"]["uncovered_points"] == [
        "动态回复过程的真应力-真应变曲线阶段划分"
    ]


def test_two_blueprint_items_can_reconcile_to_full_knowledge_coverage() -> None:
    plan = ensure_practice_blueprint_defaults(
        _plan(
            [
                _item(
                    design_intent="比较高层错能金属的位错运动特征并解释其软化行为。",
                    definitions=["高层错能金属的扩展位错较窄，螺型位错易交滑移。"],
                ),
                _item(
                    design_intent="识别动态回复过程的真应力-真应变曲线阶段划分。",
                    definitions=["动态回复曲线依次经历加工硬化、均匀应变、稳态流变阶段。"],
                ),
            ]
        )
    )
    cover = scope_cover_summary(
        plan["source_scope"],
        plan["selected_source_questions"],
        plan["blueprint"]["exercise_plan"],
    )

    assert cover["source_complete"] is True
    assert cover["knowledge_points"]["complete"] is True
    assert cover["knowledge_points"]["covered_count"] == 3
    assert cover["content_complete"] is True


def test_frontend_labels_dual_coverage_without_claiming_partial_is_complete() -> None:
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "范围覆盖" in index_html
    assert "本次纳入：" in app_js
    assert "本次未纳入：" in app_js
    assert "蓝图可继续生成，但只覆盖本次纳入的知识点范围。" in app_js
    assert "来源单元与必考知识点均覆盖完整，可生成。" in app_js
