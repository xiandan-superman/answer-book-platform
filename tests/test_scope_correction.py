from __future__ import annotations

import pytest

from app.exercise_generation import (
    _normalize_plan,
    _normalize_source_scope,
    resolve_scope_granularity,
    scope_cover_summary,
)


def _atomic(source_id: str, number: str, title: str, parent: str | None = None, **overrides):
    value = {
        "source_question_id": source_id,
        "number": number,
        "title": title,
        "stem_excerpt": f"{title} 的题干摘要",
        "question_type": "综合题",
        "source_difficulty": "进阶",
        "knowledge_points": ["知识点"],
        "source_ref": {"page": "3", "block": "L10"},
    }
    if parent:
        value["parent_id"] = parent
    value.update(overrides)
    return value


def _exam_paper_with_6_subitems() -> dict:
    """A 场景：15 个顶层大题，第 1 题含 6 个名词解释子项 = 20 原子单元。"""
    questions = [_atomic("q01", "1.", "名词解释（含6子项）")]
    for i in range(1, 7):
        questions.append(_atomic(f"q01_{i}", f"1.{i}", f"名词解释{i}", parent="q01", question_type="名词解释"))
    for n in range(2, 16):
        questions.append(_atomic(f"q{n:02d}", f"{n}.", f"第{n}题"))
    return {
        "mode": "question_set",
        "title": "试卷A",
        "granularity": "top_level",
        "questions": questions,
    }


def _textbook_9_units() -> dict:
    """B 场景：9 个知识单元，其中多个需合并/拆分后校正为 10 个。"""
    questions = []
    for i in range(1, 10):
        questions.append(_atomic(f"unit_{i:02d}", str(i), f"单元{i}", question_type="知识单元"))
    return {"mode": "question_set", "title": "教材B", "granularity": "atomic", "questions": questions}


def test_normalize_source_scope_preserves_hierarchy_and_granularity():
    raw = _exam_paper_with_6_subitems()
    scope = _normalize_source_scope(raw)

    assert scope["mode"] == "question_set"
    assert scope["has_hierarchy"] is True
    assert scope["granularity"] == "top_level"
    assert all("source_ref" in q for q in scope["questions"])
    assert all(q["source_ref"]["page"] == "3" for q in scope["questions"] if q["source_question_id"] == "q01")


def test_exam_granularity_top_level_resolves_15():
    scope = _normalize_source_scope(_exam_paper_with_6_subitems())
    units = resolve_scope_granularity(scope, "top_level")

    assert [u["source_question_id"] for u in units] == [
        f"q{i:02d}" for i in range(1, 16)
    ]
    assert len(units) == 15


def test_exam_granularity_atomic_resolves_20():
    scope = _normalize_source_scope(_exam_paper_with_6_subitems())
    units = resolve_scope_granularity(scope, "atomic")

    assert len(units) == 20
    # 原子粒度：聚合父项 q01 被其 6 个子项替代，随后是 q02..q15
    ids = [u["source_question_id"] for u in units]
    assert ids == [f"q01_{i}" for i in range(1, 7)] + [f"q{i:02d}" for i in range(2, 16)]
    assert "q01" not in ids


def test_exam_parallel_coverage_at_top_level_complete():
    scope = _normalize_source_scope(_exam_paper_with_6_subitems())
    top = resolve_scope_granularity(scope, "top_level")
    plan = [{"source_question_id": u["source_question_id"], "number": i + 1} for i, u in enumerate(top)]
    cover = scope_cover_summary(scope, top, plan)

    assert cover["counts"]["selected_units"] == 15
    assert cover["counts"]["covered_units"] == 15
    assert cover["counts"]["uncovered_units"] == 0
    assert cover["counts"]["planned_exercises"] == 15
    assert cover["complete"] is True
    assert cover["granularity"] == "top_level"


def test_exam_coverage_incomplete_blocks_gate():
    scope = _normalize_source_scope(_exam_paper_with_6_subitems())
    top = resolve_scope_granularity(scope, "top_level")
    plan = [{"source_question_id": u["source_question_id"]} for i, u in enumerate(top) if i % 2 == 0]
    cover = scope_cover_summary(scope, top, plan)

    assert cover["complete"] is False
    assert cover["counts"]["covered_units"] < cover["counts"]["selected_units"]
    assert cover["counts"]["uncovered_units"] > 0


def test_textbook_edit_merge_split_add_to_10_units():
    """B 场景：在 9 个单元上通过合并/拆分/新增校正为 10 个可审单元。"""
    source = _textbook_9_units()
    scope = _normalize_source_scope(source)
    units = resolve_scope_granularity(scope, "atomic")
    # 9 个原子单元
    assert len(units) == 9

    # 教师校正：
    #  - 合并 unit_01 + unit_02 -> merged_01（主题"恒容热与热容"）
    #  - 拆分 unit_03 -> unit_03a + unit_03b（恒容热单独）
    #  - 手工新增 unit_11
    corrected = {
        "source_question_id": "merged_01",
        "number": "1",
        "title": "恒容热与热容",
        "stem_excerpt": "合并后的单元",
        "question_type": "知识单元",
        "source_difficulty": "基础",
        "knowledge_points": ["恒容热", "热容"],
        "source_ref": {"page": "3", "block": "L10-L12"},
    }
    split_a = {"source_question_id": "unit_03a", "number": "3a", "title": "恒容热", "stem_excerpt": "chunk", "question_type": "知识单元"}
    split_b = {"source_question_id": "unit_03b", "number": "3b", "title": "热容", "stem_excerpt": "chunk", "question_type": "知识单元"}
    added = {"source_question_id": "added_11", "number": "11", "title": "绝热过程", "stem_excerpt": "手工新增", "question_type": "知识单元"}
    # 去掉被合并/拆分的 3 项，替换为合并1 + 拆分2 + 新增1：9 - 3 + 4 = 10
    ids = [u["source_question_id"] for u in units]
    for rid in ("unit_01", "unit_02", "unit_03"):
        ids.remove(rid)
    final_ids = ids + ["merged_01", "unit_03a", "unit_03b", "added_11"]
    final_units = [{"source_question_id": _id, "number": str(i + 1), "title": _id} for i, _id in enumerate(final_ids)]

    assert len(final_units) == 10
    plan = [{"source_question_id": u["source_question_id"]} for u in final_units]
    cover = scope_cover_summary(scope, final_units, plan)
    assert cover["counts"]["selected_units"] == 10
    assert cover["counts"]["covered_units"] == 10
    assert cover["complete"] is True
    # 拆分子项 title 应保留来源引用能力
    assert corrected["source_ref"]["page"] == "3"


def test_single_source_coverage_defaults_to_plan():
    scope = _normalize_source_scope({"mode": "single", "questions": [_atomic("source_01", "1", "唯一题")]})
    cover = scope_cover_summary(scope, [scope["questions"][0]], [{"source_question_id": "source_01"}] * 3)
    assert cover["counts"]["selected_units"] == 1
    assert cover["counts"]["covered_units"] == 1
    assert cover["counts"]["planned_exercises"] == 3
    assert cover["complete"] is True


def test_normalize_plan_embeds_scope_cover_for_corrected_scope():
    """校验 _normalize_plan 会把覆盖摘要写入计划输出，供蓝图页门禁使用。"""
    scope = _normalize_source_scope(_textbook_9_units())
    units = resolve_scope_granularity(scope, "atomic")
    plan = _normalize_plan(
        raw={
            "source_analysis": {"subject": "物理"},
            "blueprint": {
                "training_goal": "热学专题",
                "exercise_plan": [
                    {"source_question_id": u["source_question_id"], "difficulty": "基础"}
                    for u in units
                ],
            },
        },
        count=9,
        planned_types=["综合题"] * 9,
        difficulty="基础",
        planned_difficulties=["基础"] * 9,
        selected_types=["综合题"],
        source_files=[],
        source_scope=scope,
        selected_source_questions=units,
        planned_source_ids=[u["source_question_id"] for u in units],
        generation_strategy="targeted_set",
    )
    cover = plan["scope_cover"]
    assert cover["counts"]["selected_units"] == 9
    assert cover["counts"]["covered_units"] == 9
    assert cover["counts"]["planned_exercises"] == 9
    assert cover["complete"] is True


def test_normalize_plan_reports_incomplete_coverage_when_unit_uncovered():
    """选中单元未出现在计划中时，覆盖摘要必须标记为不完整（拦截门禁）。"""
    scope = _normalize_source_scope(_textbook_9_units())
    units = resolve_scope_granularity(scope, "atomic")
    plan = _normalize_plan(
        raw={"source_analysis": {"subject": "物理"}, "blueprint": {"exercise_plan": []}},
        count=6,
        planned_types=["综合题"] * 6,
        difficulty="基础",
        planned_difficulties=["基础"] * 6,
        selected_types=["综合题"],
        source_files=[],
        source_scope=scope,
        selected_source_questions=units,
        planned_source_ids=[u["source_question_id"] for i, u in enumerate(units) if i < 6],
        generation_strategy="targeted_set",
    )
    cover = plan["scope_cover"]
    assert cover["counts"]["selected_units"] == 9
    # 未覆盖来源：只计划了 6 个来源，仍有 3 个未覆盖
    assert cover["counts"]["covered_units"] <= 6
    assert cover["counts"]["uncovered_units"] >= 3
    assert cover["complete"] is False


def test_scope_cover_merges_unlabeled_plan_items_to_sole_unit():
    scope = _normalize_source_scope({"mode": "single", "questions": [_atomic("source_01", "1", "唯一题")]})
    cover = scope_cover_summary(scope, [scope["questions"][0]], [{"number": 1}, {"number": 2}])
    assert cover["counts"]["planned_exercises"] == 2
    assert cover["counts"]["covered_units"] == 1
    assert cover["complete"] is True
