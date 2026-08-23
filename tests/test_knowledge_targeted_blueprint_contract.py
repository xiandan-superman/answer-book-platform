from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.exercise_generation import audit_practice_blueprint, plan_practice_set

GLOBAL_HEAT_POINTS = [
    "一维定态无内热源常导热系数平壁导热",
    "导热热阻的定义",
    "对流热阻的定义",
    "串联热阻网络",
    "定态串联路径热流率守恒与温降分配",
    "并联热阻网络判定条件",
]


def _item(number: int, points: list[str]) -> dict:
    return {
        "number": number,
        "plan_item_id": f"plan_item_{number:02d}",
        "question_type": "计算题" if number % 2 else "简答题",
        "difficulty": "基础" if number == 1 else "进阶",
        "target_skill": f"目标能力 {number}",
        "variation_type": f"知识应用 {number}",
        "design_intent": f"检验第 {number} 项理解。",
        "required_knowledge_points": points,
        "source_refs": [],
    }


def _plan(
    item_points: list[list[str]],
    *,
    global_points: list[str] | None = None,
    strategy: str = "knowledge_targeted",
    sources: list[dict] | None = None,
) -> dict:
    sources = sources or []
    items = [_item(index, points) for index, points in enumerate(item_points, start=1)]
    if sources:
        for item in items:
            source_id = sources[0]["source_question_id"]
            item["source_question_id"] = source_id
            item["source_refs"] = [source_id]
    return {
        "source_mode": "knowledge",
        "source_analysis": {"knowledge_points": global_points if global_points is not None else GLOBAL_HEAT_POINTS},
        "source_scope": {"mode": "single", "questions": sources},
        "selected_source_questions": sources,
        "blueprint": {
            "generation_strategy": strategy,
            "training_goal": "覆盖确认的全局知识目标",
            "progression": ["基础", "进阶"],
            "exercise_plan": items,
        },
    }


REAL_ARK_FAILURE_SHAPES = [
    [
        [
            "平壁一维定态无内热源常导热系数导热公式",
            "导热热阻定义",
            "对流热阻定义",
            "串联热阻网络",
            "定态串联路径热流率守恒",
            "温降与热阻分配",
        ],
        ["串联热阻网络", "并联热阻网络判定条件", "串联与并联热路径边界辨析"],
    ],
    [
        [
            "一维定态平壁导热",
            "导热热阻 R_cond=L/(kA)",
            "对流边界热阻 R_conv=1/(hA)",
            "串联热阻总热阻 R_total=ΣR_i",
            "定态串联路径热流率守恒",
        ],
        [
            "串联热阻网络",
            "并联热阻网络判定条件",
            "温降分配 ΔT_i=Q R_i",
        ],
    ],
]


@pytest.mark.parametrize("item_points", REAL_ARK_FAILURE_SHAPES)
def test_unbound_knowledge_targeted_accepts_both_real_ark_failure_shapes(
    item_points: list[list[str]],
) -> None:
    audit = audit_practice_blueprint(_plan(item_points))

    assert audit["status"] != "blocked", audit["errors"]
    assert audit["metrics"]["knowledge_targeted_global_point_count"] == len(GLOBAL_HEAT_POINTS)


@pytest.mark.parametrize("item_points", REAL_ARK_FAILURE_SHAPES)
def test_ark_named_planning_pipeline_accepts_both_real_failure_shapes(
    item_points: list[list[str]],
) -> None:
    raw = {
        "source_analysis": {
            "subject": "传热学",
            "knowledge_points": GLOBAL_HEAT_POINTS,
            "skills": ["热阻计算", "路径辨析"],
        },
        "blueprint": {
            "training_goal": "完成定态导热和热阻网络训练",
            "progression": ["串联计算", "串并联辨析"],
            "exercise_plan": [
                {
                    **_item(index, points),
                    "difficulty_levers": ["条件转换"],
                    "difficulty_rationale": "需要将物理路径映射为热阻网络。",
                }
                for index, points in enumerate(item_points, start=1)
            ],
        },
    }
    payload = {
        "source_mode": "knowledge",
        "knowledge_title": "一维定态导热与热阻网络",
        "question_text": "一维定态平壁导热、导热/对流热阻和串并联热阻网络。",
        "count": 2,
        "difficulty": "基础到进阶",
        "difficulty_counts": {"基础": 1, "进阶": 1, "挑战": 0},
        "question_types": ["计算题", "简答题"],
        "generation_strategy": "knowledge_targeted",
        "provider": "ark",
        "model": "doubao-seed-2-1-pro-260628",
        "thinking": "disabled",
    }
    provider = SimpleNamespace(name="ark")
    with (
        patch("app.exercise_generation._model_runtime", return_value=(provider, payload["model"])),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", return_value=raw),
    ):
        plan = plan_practice_set(payload)

    assert plan["generation"]["provider"] == "ark"
    assert plan["generation"]["model"] == "doubao-seed-2-1-pro-260628"
    assert plan["generation"]["stage"] == "planning"
    assert plan["blueprint_audit"]["status"] != "blocked"


def test_unbound_knowledge_targeted_accepts_partitioned_set_union_coverage() -> None:
    audit = audit_practice_blueprint(_plan([
        [GLOBAL_HEAT_POINTS[0], GLOBAL_HEAT_POINTS[1]],
        [GLOBAL_HEAT_POINTS[2], GLOBAL_HEAT_POINTS[3]],
        [GLOBAL_HEAT_POINTS[4], GLOBAL_HEAT_POINTS[5]],
    ]))

    assert audit["status"] != "blocked", audit["errors"]


def test_unbound_knowledge_targeted_rejects_whole_set_coverage_gap() -> None:
    audit = audit_practice_blueprint(_plan([[GLOBAL_HEAT_POINTS[0]], GLOBAL_HEAT_POINTS[1:5]]))

    assert audit["status"] == "blocked"
    assert any("整套必考知识点未覆盖全局目标" in error for error in audit["errors"])
    assert any(GLOBAL_HEAT_POINTS[5] in error for error in audit["errors"])


def test_unbound_knowledge_targeted_rejects_out_of_scope_point() -> None:
    audit = audit_practice_blueprint(_plan([
        GLOBAL_HEAT_POINTS[:3] + ["辐射换热角系数"],
        GLOBAL_HEAT_POINTS[3:],
    ]))

    assert audit["status"] == "blocked"
    assert any("材料范围外" in error and "辐射换热角系数" in error for error in audit["errors"])


def test_unbound_knowledge_targeted_does_not_alias_parallel_to_series() -> None:
    audit = audit_practice_blueprint(_plan(
        [["并联热阻网络"]],
        global_points=["串联热阻网络"],
    ))

    assert audit["status"] == "blocked"
    assert any("材料范围外" in error and "并联热阻网络" in error for error in audit["errors"])


def test_unbound_knowledge_targeted_rejects_empty_item() -> None:
    audit = audit_practice_blueprint(_plan([[], GLOBAL_HEAT_POINTS]))

    assert audit["status"] == "blocked"
    assert any("第1项缺少必考知识点组合" in error.replace(" ", "") for error in audit["errors"])


def test_unbound_knowledge_targeted_uses_existing_alias_normalization() -> None:
    audit = audit_practice_blueprint(_plan(
        [["复杂反应动力学的基本原理"]],
        global_points=["复合反应动力学"],
    ))

    assert audit["status"] != "blocked", audit["errors"]


def test_unbound_knowledge_targeted_deduplicates_repeated_points_and_allocations() -> None:
    audit = audit_practice_blueprint(_plan([
        [GLOBAL_HEAT_POINTS[0], GLOBAL_HEAT_POINTS[0], GLOBAL_HEAT_POINTS[1]],
        [GLOBAL_HEAT_POINTS[1], GLOBAL_HEAT_POINTS[2], GLOBAL_HEAT_POINTS[3]],
        [GLOBAL_HEAT_POINTS[4], GLOBAL_HEAT_POINTS[5], GLOBAL_HEAT_POINTS[5]],
    ]))

    assert audit["status"] != "blocked", audit["errors"]
    assert audit["metrics"]["knowledge_targeted_supported_declared_point_count"] == len(GLOBAL_HEAT_POINTS)


@pytest.mark.parametrize("strategy", ["parallel_exam", "per_question", "knowledge_targeted"])
def test_bound_source_modes_keep_strict_per_item_source_contract(strategy: str) -> None:
    source = {
        "source_question_id": "source_01",
        "title": "已绑定来源",
        "knowledge_points": ["导热热阻", "对流热阻"],
    }
    audit = audit_practice_blueprint(_plan(
        [["导热热阻"]],
        global_points=["导热热阻", "对流热阻"],
        strategy=strategy,
        sources=[source],
    ))

    assert audit["status"] == "blocked"
    assert any("必考知识点与绑定来源规则不一致" in error for error in audit["errors"])
