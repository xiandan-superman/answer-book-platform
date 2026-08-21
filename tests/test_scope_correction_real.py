"""真实数据验收：用两个已保存分析结果的最小冻结快照验证范围校正与覆盖门禁。

原任务文件的名称和 SHA-256 保存在 fixture 的 provenance 中；快照不会进入用户任务列表。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.exercise_generation import (
    _aggregate_unit_content,
    _normalize_plan,
    _normalize_source_scope,
    resolve_scope_granularity,
)

ROOT = Path(__file__).resolve().parents[1]

SNAPSHOT = ROOT / "tests" / "fixtures" / "scope_correction_real_snapshot.json"


def _analyze_result(name: str) -> dict:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return data[name]


def test_real_A_flat_20_resolves_atomic_20():
    res = _analyze_result("A")
    scope = _normalize_source_scope(res["source_scope"])
    # 真实 A 是扁平 20 项（无 parent_id），原子粒度=20
    assert len(scope["questions"]) == 20
    assert scope["has_hierarchy"] is False
    assert len(resolve_scope_granularity(scope, "atomic")) == 20


def test_real_A_merge_six_noun_items_to_15_top_level_and_20_atomic():
    """用户把第1大题（一）的 6 个名词解释项 source_01..06 合并为顶层父项，
    从而把 20 项表达为 15 顶层 / 20 原子。"""
    res = _analyze_result("A")
    scope = _normalize_source_scope(res["source_scope"])
    units = scope["questions"]
    # 前 6 项属于第一道大题（"一.1".."一.6" 名词解释）
    noun_ids = [u["source_question_id"] for u in units[:6]]
    assert len(noun_ids) == 6

    parent = {**units[0], "parent_id": ""}
    children = [
        {**u, "source_question_id": f"{units[0]['source_question_id']}_{i+1}", "parent_id": units[0]["source_question_id"]}
        for i, u in enumerate(units[:6])
    ]
    others = units[6:]
    corrected = {
        "mode": "question_set",
        "title": scope["title"],
        "granularity": "top_level",
        "has_hierarchy": True,
        "questions": [*others, parent, *children],
    }
    assert len(resolve_scope_granularity(corrected, "top_level")) == 15
    assert len(resolve_scope_granularity(corrected, "atomic")) == 20


def test_real_A_top_level_parent_aggregates_all_children_content():
    """小王要求：top_level 解析必须实际应用聚合，返回的父项含全部 6 个子项的题干与知识点。"""
    res = _analyze_result("A")
    scope = _normalize_source_scope(res["source_scope"])
    units = scope["questions"]
    children = [{**u, "source_question_id": f"{units[0]['source_question_id']}_{i+1}", "parent_id": units[0]["source_question_id"]} for i, u in enumerate(units[:6])]
    corrected = {"mode": "question_set", "title": scope["title"], "granularity": "top_level", "has_hierarchy": True, "questions": [*units[6:], {**units[0], "parent_id": ""}, *children]}
    top = resolve_scope_granularity(corrected, "top_level")
    assert len(top) == 15
    parent = next(u for u in top if u["source_question_id"] == units[0]["source_question_id"])
    # 父项必须聚合全部 6 个子项知识点
    all_child_kps = {kp for u in children for kp in (u.get("knowledge_points") or [])}
    assert all_child_kps <= set(parent.get("knowledge_points") or [])
    # 父项题干必须包含全部子项摘要
    for u in children:
        assert (u.get("stem_excerpt") or "") in (parent.get("stem_excerpt") or "")
    # 原子粒度仍为 20
    assert len(resolve_scope_granularity(corrected, "atomic")) == 20


def test_real_A_aggregated_parent_survives_backend_normalization_no_truncation():
    """Cindy 关键点：真实 plan_practice_set 归一化路径必须不再把父项知识点截断到 8；
    经 _normalize_source_scope 后完整聚合父项（21 知识点）不被截断，覆盖仍完整。"""
    res = _analyze_result("A")
    units = _normalize_source_scope(res["source_scope"])["questions"]
    children = [{**u, "source_question_id": f"{units[0]['source_question_id']}_{i+1}", "parent_id": units[0]["source_question_id"]} for i, u in enumerate(units[:6])]
    aggregate = _aggregate_unit_content(children + units[6:], {**units[0], "parent_id": ""})
    # 父项应有全部子项知识点（数量 > 8）
    assert len(aggregate.get("knowledge_points") or []) > 8
    corrected = {"mode": "question_set", "title": "试卷A", "granularity": "top_level", "has_hierarchy": True, "questions": [*units[6:], aggregate, *children]}
    # 走与 plan_practice_set 相同的归一化（limit 现为 60），父项知识点不被截断
    normalized = _normalize_source_scope(corrected)
    normalized_parent = next(q for q in normalized["questions"] if q["source_question_id"] == units[0]["source_question_id"] and not q.get("parent_id"))
    children_all = [q for q in normalized["questions"] if q.get("parent_id") == units[0]["source_question_id"]]
    assert len(normalized_parent.get("knowledge_points") or []) > 8
    missing = {kp for c in children_all for kp in (c.get("knowledge_points") or [])} - set(normalized_parent.get("knowledge_points") or [])
    assert not missing, f"父项仍缺失子项知识点: {missing}"


def test_real_A_aggregated_parent_reports_complete_coverage():
    """父项正确聚合全部 6 个子项知识点后，15 顶层平行计划覆盖完整可交付。"""
    res = _analyze_result("A")
    scope = _normalize_source_scope(res["source_scope"])
    units = scope["questions"]
    children = [{**u, "source_question_id": f"{units[0]['source_question_id']}_{i+1}", "parent_id": units[0]["source_question_id"]} for i, u in enumerate(units[:6])]
    # 用聚合器构造正确父项：知识点/题干 = 全部 6 个子项并集
    questions_for_parent = [*children]
    parent = _aggregate_unit_content(questions_for_parent, {**units[0], "parent_id": ""})
    corrected = {"mode": "question_set", "title": scope["title"], "granularity": "top_level", "has_hierarchy": True, "questions": [*units[6:], parent, *children]}
    top = resolve_scope_granularity(corrected, "top_level")
    assert len(top) == 15

    plan = _normalize_plan(
        raw={"source_analysis": res.get("source_analysis") or {}, "blueprint": {"training_goal": "金属学专项", "exercise_plan": [{"source_question_id": u["source_question_id"]} for u in top]}},
        count=15,
        planned_types=["综合题"] * 15,
        difficulty="进阶",
        planned_difficulties=["进阶"] * 15,
        selected_types=["综合题"],
        source_files=res.get("source_files") or [],
        source_scope=corrected,
        selected_source_questions=top,
        planned_source_ids=[u["source_question_id"] for u in top],
        generation_strategy="parallel_exam",
    )
    cover = plan["scope_cover"]
    assert cover["counts"]["selected_units"] == 15
    assert cover["counts"]["covered_units"] == 15
    assert cover["complete"] is True


def test_real_A_incomplete_plan_blocks_gate():
    """A 只计划覆盖第一个父项下的若干子项，覆盖不完整，门禁拦截。"""
    res = _analyze_result("A")
    scope = _normalize_source_scope(res["source_scope"])
    units = scope["questions"]
    # 不校正，直接把 20 项当作 20 个选中单元；只计划前 10 项 -> 覆盖不完整
    selected = units
    plan = _normalize_plan(
        raw={"source_analysis": res.get("source_analysis") or {}, "blueprint": {"exercise_plan": []}},
        count=10,
        planned_types=["综合题"] * 10,
        difficulty="进阶",
        planned_difficulties=["进阶"] * 10,
        selected_types=["综合题"],
        source_files=res.get("source_files") or [],
        source_scope=scope,
        selected_source_questions=selected,
        planned_source_ids=[u["source_question_id"] for u in selected[:10]],
        generation_strategy="targeted_set",
    )
    cover = plan["scope_cover"]
    assert cover["counts"]["selected_units"] == 20
    assert cover["counts"]["planned_exercises"] == 10
    assert cover["complete"] is False
    assert cover["counts"]["uncovered_units"] >= 10


def test_real_B_9_to_10_by_split_unit():
    """B：现有 9 个单元，通过拆分一个多要点单元为 2 个子项校正为 10 个可审单元。"""
    res = _analyze_result("B")
    scope = _normalize_source_scope(res["source_scope"])
    assert len(scope["questions"]) == 9
    units = scope["questions"]
    # 拆分第 2 个单元（"功与热…"）为 2 个子项：功、热
    target = units[1]
    parent = {**target, "parent_id": ""}
    children = [
        {**target, "source_question_id": f"{target['source_question_id']}_s1", "parent_id": target["source_question_id"], "title": "功的定义、符号与计算", "stem_excerpt": target["stem_excerpt"]},
        {**target, "source_question_id": f"{target['source_question_id']}_s2", "parent_id": target["source_question_id"], "title": "热的定义、符号与计算", "stem_excerpt": target["stem_excerpt"]},
    ]
    corrected = {"mode": "question_set", "title": scope["title"], "granularity": "top_level", "has_hierarchy": True, "questions": [*units[:1], *units[2:], parent, *children]}
    atomic = resolve_scope_granularity(corrected, "atomic")
    # 顶层：9（父项替换回固定为 1）这里父项保留顶层，等价 9 顶层 / 10 原子
    assert len(atomic) == 10
    # 顶层 = 原始 9 个顶层（父项不变）
    assert len([u for u in corrected["questions"] if not u.get("parent_id")]) == 9

    plan = _normalize_plan(
        raw={"source_analysis": res.get("source_analysis") or {}, "blueprint": {"training_goal": "热力学第一定律", "exercise_plan": [{"source_question_id": u["source_question_id"]} for u in atomic]}},
        count=10,
        planned_types=["综合题"] * 10,
        difficulty="基础到进阶",
        planned_difficulties=["基础", "基础", "进阶", "进阶", "进阶", "基础", "基础", "进阶", "进阶", "进阶"],
        selected_types=["综合题"],
        source_files=res.get("source_files") or [],
        source_scope=corrected,
        selected_source_questions=atomic,
        planned_source_ids=[u["source_question_id"] for u in atomic],
        generation_strategy="knowledge_item_wise",
    )
    cover = plan["scope_cover"]
    assert cover["counts"]["selected_units"] == 10
    assert cover["counts"]["covered_units"] == 10
    assert cover["complete"] is True
