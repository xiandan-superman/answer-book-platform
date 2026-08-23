from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import exercise_generation
from app.exercise_generation import (
    _batch_output_contract,
    _batch_variation_issues,
    _complete_generated_figure,
    _difficulty_intent,
    _difficulty_plan,
    _exercise_figure_issues,
    _model_route,
    _model_runtime,
    _normalize_figures,
    _normalize_plan,
    _parse_practice_json,
    _semantic_batch_context,
    audit_generation_contract,
    audit_practice_blueprint,
    build_generation_contract,
    ensure_practice_blueprint_defaults,
    generate_practice_from_contract,
    generate_practice_from_plan,
    normalize_difficulty_counts,
    normalize_practice_set,
    plan_practice_set,
    practice_difficulty_observations,
    practice_diversity_issues,
    recompute_practice_quality,
    refine_blueprint_units,
    regenerate_plan_item,
    validate_practice_mode_contract,
    validate_reference_calculation_variation,
)


def _exercise(**overrides):
    value = {
        "question_type": "计算题",
        "difficulty": "基础",
        "target_skill": "建立方程",
        "variation_type": "同考点换情境",
        "stem": "已知条件，求未知量。",
        "options": [],
        "knowledge_points": ["一元一次方程"],
        "verification_note": "代入复算成立。",
    }
    value.update(overrides)
    return value


def test_drawing_question_does_not_implicitly_request_a_stem_figure():
    contract = _batch_output_contract([{"question_type": "作图题"}])
    assert "figures" not in contract["exercises"][0]


def test_compound_figure_requirements_accept_separate_visible_labels():
    figures = [{
        "x_label": "应变",
        "y_label": "应力",
        "series": [
            {"name": "上屈服点", "points": [[0, 2], [1, 1]]},
            {"name": "下屈服点", "points": [[1, 1], [2, 1]]},
            {"name": "0.40 wt% C 成分线", "points": [[0.4, 0], [0.4, 1000]]},
            {"name": "3.00 wt% C 成分线", "points": [[3.0, 0], [3.0, 1000]]},
        ],
        "nodes": [],
    }]
    visible = [row["name"] for row in figures[0]["series"]]

    assert exercise_generation._figure_element_present("上屈服点与下屈服点", figures, visible)
    assert exercise_generation._figure_element_present("0.40wt%C与3.00wt%C两条成分线", figures, visible)


def test_quality_warns_when_generated_stem_contradicts_confirmed_boundary():
    practice = {
        "requested_count": 1,
        "blueprint": {"exercise_plan": [{
            "plan_item_id": "plan_item_01",
            "question_type": "综合题",
            "required_knowledge_points": ["相图"],
            "required_constraints": {"applicable_boundaries": ["平衡冷却（缓慢冷却）条件"]},
        }]},
        "exercises": [{
            "number": 1,
            "plan_item_id": "plan_item_01",
            "question_type": "综合题",
            "difficulty": "挑战",
            "stem": "若冷却条件波动导致共晶反应未完全发生，分析最终组织。",
            "knowledge_points": ["相图"],
            "answerability_check_status": "reported",
            "generation_status": "completed",
        }],
    }

    quality = recompute_practice_quality(practice)

    assert quality["status"] == "passed"  # Keep the user's usable result.
    assert quality["checks"]["applicable_boundary_consistency"] is False
    assert quality["checks"]["subject_matter_review_required"] is True
    assert quality["boundary_issues"][0]["code"] == "applicable_boundary_contradiction"
    assert any("平衡冷却" in warning and "冷却条件波动" in warning for warning in quality["warnings"])


def test_simple_question_without_boundary_conflict_does_not_require_subject_review():
    practice = {
        "requested_count": 1,
        "blueprint": {"exercise_plan": [{
            "plan_item_id": "plan_item_01",
            "question_type": "简答题",
            "required_knowledge_points": ["状态函数"],
            "required_constraints": {"applicable_boundaries": ["恒温恒压可逆过程"]},
        }]},
        "exercises": [{
            "number": 1,
            "plan_item_id": "plan_item_01",
            "question_type": "简答题",
            "difficulty": "进阶",
            "stem": "在恒温恒压可逆过程中说明状态函数关系。",
            "knowledge_points": ["状态函数"],
            "answerability_check_status": "reported",
            "generation_status": "completed",
        }],
    }

    quality = recompute_practice_quality(practice)

    assert quality["checks"]["applicable_boundary_consistency"] is True
    assert quality["checks"]["subject_matter_review_required"] is False


def test_semantic_review_checks_complete_set_once_without_answer_content(monkeypatch):
    captured = {}
    practice = {
        "blueprint": {"exercise_plan": [
            {"plan_item_id": "p1", "target_skill": "概念辨析", "required_constraints": {}},
            {"plan_item_id": "p2", "target_skill": "综合判断", "required_constraints": {}},
        ]},
        "exercises": [
            {"number": 1, "plan_item_id": "p1", "question_type": "简答题", "difficulty": "基础", "stem": "说明概念。", "knowledge_points": ["概念"]},
            {
                "number": 2,
                "plan_item_id": "p2",
                "question_type": "综合题",
                "difficulty": "挑战",
                "stem": "分析综合情形。",
                "knowledge_points": ["概念"],
                "figures": [{"series": [{"name": "边界曲线", "points": [[0, 1], [1, 2], [2, 3]]}]}],
            },
        ],
    }

    def fake_call(_client, messages, **kwargs):
        captured["prompt"] = str(messages[-1]["content"])
        captured["kwargs"] = kwargs
        return {
            "items": [
                {"number": 1, "status": "passed", "risks": []},
                {"number": 2, "status": "passed", "risks": []},
            ],
            "set_summary": "通过",
        }

    monkeypatch.setattr(exercise_generation, "_primary_model_runtime", lambda _payload: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    report = exercise_generation.review_practice_semantics(practice, {})

    assert report["status"] == "passed"
    assert [item["number"] for item in report["items"]] == [1, 2]
    assert "说明概念" in captured["prompt"] and "分析综合情形" in captured["prompt"]
    assert '"sampled_points": [' in captured["prompt"]
    assert '0.0' in captured["prompt"] and '3.0' in captured["prompt"]
    assert '"series_names_are_visible_legend_labels": true' in captured["prompt"]
    assert "不输出答案" in captured["prompt"]
    assert captured["kwargs"]["thinking"] == "disabled"
    assert captured["kwargs"]["timeout_seconds"] == 180


def test_completed_semantic_review_clears_complex_review_requirement():
    practice = {
        "requested_count": 1,
        "blueprint": {"exercise_plan": [{"plan_item_id": "p1", "question_type": "综合题", "required_knowledge_points": ["相图"]}]},
        "exercises": [{
            "number": 1, "plan_item_id": "p1", "question_type": "综合题", "difficulty": "挑战",
            "stem": "比较两种相图路径。", "knowledge_points": ["相图"], "answerability_check_status": "reported",
        }],
        "semantic_review": {"status": "passed", "items": [{"number": 1, "status": "passed", "risks": []}]},
    }

    quality = recompute_practice_quality(practice)

    assert quality["checks"]["semantic_review_completed"] is True
    assert quality["checks"]["semantic_review_passed"] is True
    assert quality["checks"]["subject_matter_review_required"] is False


def test_actionable_semantic_risk_keeps_result_and_marks_review_required():
    practice = {
        "requested_count": 1,
        "blueprint": {"exercise_plan": [{"plan_item_id": "p1", "question_type": "综合题", "required_knowledge_points": ["相图"]}]},
        "exercises": [{
            "number": 1, "plan_item_id": "p1", "question_type": "综合题", "difficulty": "挑战",
            "stem": "比较两种相图路径。", "knowledge_points": ["相图"], "answerability_check_status": "reported",
        }],
        "semantic_review": {"status": "warning", "items": [{
            "number": 1, "status": "risk", "risks": [{"severity": "high", "code": "missing_condition", "message": "缺少关键条件。"}],
        }]},
    }

    quality = recompute_practice_quality(practice)

    assert quality["status"] == "passed"
    assert quality["checks"]["semantic_review_completed"] is True
    assert quality["checks"]["semantic_review_passed"] is False
    assert quality["checks"]["subject_matter_review_required"] is True
    assert any("缺少关键条件" in warning for warning in quality["warnings"])


def test_global_diversity_gate_detects_source_surface_reuse_in_comprehensive_set():
    source_stem = (
        "将一摩尔苯在正常沸点和饱和蒸气压下向真空蒸发为同温同压的蒸气，"
        "随后在低压下恒温可逆膨胀，计算整个过程的熵变。"
    )
    practice = {
        "generation_strategy": "targeted_set",
        "selected_source_questions": [{"source_question_id": "s1", "source_content": source_stem}],
        "exercises": [{
            "number": 1,
            "source_question_id": "s1",
            "stem": source_stem.replace("低压", "10.0 kPa"),
            "knowledge_points": ["熵变"],
            "generation_status": "completed",
        }],
    }

    issues = practice_diversity_issues(practice)

    assert any(issue["code"] == "source_surface_reuse" for issue in issues)


def test_per_source_slot_allocation_overrides_model_source_refs():
    selected = [
        {"source_question_id": "source_a", "knowledge_points": ["知识A"]},
        {"source_question_id": "source_b", "knowledge_points": ["知识B"]},
    ]
    raw = {"blueprint": {"exercise_plan": [
        {"source_refs": ["source_b"], "target_skill": "A", "variation_type": "变式A", "design_intent": "设计A"},
        {"source_refs": ["source_a"], "target_skill": "B", "variation_type": "变式B", "design_intent": "设计B"},
    ]}}

    plan = exercise_generation._normalize_plan(
        raw,
        count=2,
        planned_types=["简答题", "简答题"],
        difficulty="精确题数",
        planned_difficulties=["基础", "进阶"],
        selected_types=["简答题"],
        source_files=[],
        selected_source_questions=selected,
        planned_source_ids=["source_a", "source_b"],
        generation_strategy="knowledge_item_wise",
    )

    items = plan["blueprint"]["exercise_plan"]
    assert [item["source_refs"] for item in items] == [["source_a"], ["source_b"]]
    assert [item["required_knowledge_points"] for item in items] == [["知识A"], ["知识B"]]
    assert plan["blueprint"]["expected_source_counts"] == {"source_a": 1, "source_b": 1}


def test_knowledge_item_multiple_slots_partition_points_and_constraints_with_group_coverage():
    selected = [{
        "source_question_id": "source_a",
        "knowledge_points": ["晶向指数", "晶面指数", "晶向族"],
        "required_constraints": {
            "essential_definitions": ["晶向指数定义", "晶面指数定义"],
            "essential_formulas": ["r=ua+vb+wc"],
            "applicable_boundaries": ["坐标原点不能在待标定晶面上"],
        },
    }]
    rows = [
        {
            "required_knowledge_points": ["晶向指数", "晶向族"],
            "required_constraints": {
                "essential_definitions": ["晶向指数定义"],
                "essential_formulas": ["r=ua+vb+wc"],
                "applicable_boundaries": [],
            },
            "target_skill": "晶向作图",
            "variation_type": "作图",
            "design_intent": "使用晶向指数与晶向族",
        },
        {
            "required_knowledge_points": ["晶面指数"],
            "required_constraints": {
                "essential_definitions": ["晶面指数定义"],
                "essential_formulas": [],
                "applicable_boundaries": ["坐标原点不能在待标定晶面上"],
            },
            "target_skill": "晶面计算",
            "variation_type": "计算",
            "design_intent": "使用晶面指数",
        },
        {
            "required_knowledge_points": ["晶向指数", "晶面指数"],
            "required_constraints": {
                "essential_definitions": ["晶向指数定义", "晶面指数定义"],
                "essential_formulas": [],
                "applicable_boundaries": ["坐标原点不能在待标定晶面上"],
            },
            "target_skill": "晶向晶面辨析",
            "variation_type": "比较",
            "design_intent": "比较晶向指数与晶面指数",
        },
    ]

    plan = exercise_generation._normalize_plan(
        {"blueprint": {"exercise_plan": rows}},
        count=3,
        planned_types=["作图题", "计算题", "单选题"],
        difficulty="精确题数",
        planned_difficulties=["进阶", "进阶", "挑战"],
        selected_types=["作图题", "计算题", "单选题"],
        source_files=[],
        selected_source_questions=selected,
        planned_source_ids=["source_a", "source_a", "source_a"],
        generation_strategy="knowledge_item_wise",
    )

    items = plan["blueprint"]["exercise_plan"]
    assert items[0]["required_knowledge_points"] == ["晶向指数", "晶向族"]
    assert items[1]["required_knowledge_points"] == ["晶面指数"]
    assert items[1]["required_constraints"]["essential_formulas"] == []
    assert plan["blueprint_audit"]["status"] != "blocked"


def test_single_item_upgrade_preserves_partition_when_expected_source_count_is_multiple():
    source = {
        "source_question_id": "source_a",
        "knowledge_points": ["A", "B"],
        "required_constraints": {
            "essential_definitions": ["定义A", "定义B"],
            "essential_formulas": ["公式A"],
            "applicable_boundaries": ["边界B"],
        },
    }
    plan = exercise_generation.ensure_practice_blueprint_defaults({
        "selected_source_questions": [source],
        "blueprint": {
            "generation_strategy": "knowledge_item_wise",
            "expected_source_counts": {"source_a": 3},
            "exercise_plan": [{
                "source_question_id": "source_a",
                "source_refs": ["source_a"],
                "question_type": "简答题",
                "required_knowledge_points": ["B"],
                "required_constraints": {
                    "essential_definitions": ["定义B"],
                    "essential_formulas": [],
                    "applicable_boundaries": ["边界B"],
                },
            }],
        },
    })

    item = plan["blueprint"]["exercise_plan"][0]
    assert item["required_knowledge_points"] == ["B"]
    assert item["required_constraints"]["essential_formulas"] == []


def test_regeneration_surface_gate_rejects_same_question_and_accepts_real_change():
    current = {"stem": "根据以下判断选出正确项。", "options": [{"label": "A", "text": "陈述一"}, {"label": "B", "text": "陈述二"}]}
    same = {"stem": "根据以下判断选出正确项。", "options": [{"label": "A", "text": "陈述一"}, {"label": "B", "text": "陈述二"}]}
    changed = {"stem": "某实验记录给出三个相互制约的标定步骤，判断唯一自洽的修正方案。", "options": [{"label": "A", "text": "方案一"}, {"label": "B", "text": "方案二"}]}

    assert exercise_generation._regenerated_exercise_substantively_changed(current, same) is False
    assert exercise_generation._regenerated_exercise_substantively_changed(current, changed) is True


def test_blueprint_audit_blocks_cross_source_design_leakage():
    plan = {
        "source_mode": "knowledge",
        "selected_source_questions": [
            {"source_question_id": "distillation", "title": "精馏", "knowledge_points": ["精馏原理"]},
            {"source_question_id": "crystal", "title": "晶体", "knowledge_points": ["晶面指数标定步骤"]},
        ],
        "blueprint": {
            "generation_strategy": "knowledge_item_wise",
            "exercise_plan": [{
                "number": 1,
                "plan_item_id": "p1",
                "source_question_id": "distillation",
                "source_refs": ["distillation"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "精馏原理",
                "variation_type": "直接辨析",
                "design_intent": "考查精馏原理",
                "difficulty_levers": ["条件直接程度"],
                "difficulty_rationale": "学生容易混淆晶面指数。",
                "required_knowledge_points": ["精馏原理"],
            }],
        },
    }

    audit = exercise_generation.audit_practice_blueprint(plan)

    assert audit["status"] == "blocked"
    assert any("未绑定来源" in error for error in audit["errors"])
    assert audit["findings"] == [{
        "code": "cross_source_design_leak",
        "item_number": "1",
        "plan_item_id": "p1",
        "bound_source_refs": ["distillation"],
        "design_fields": {
            "target_skill": "精馏原理",
            "variation_type": "直接辨析",
            "design_intent": "考查精馏原理",
            "difficulty_rationale": "学生容易混淆晶面指数。",
            "difficulty_levers": "条件直接程度",
        },
        "matches": [{
            "anchor": "晶面指数",
            "matched_fields": ["difficulty_rationale"],
            "foreign_sources": [{
                "source_id": "crystal",
                "source_title": "晶体",
                "knowledge_point": "晶面指数标定步骤",
            }],
        }],
    }]


def test_blueprint_audit_accepts_bound_scope_expressed_with_synonyms_and_constraints():
    source_quality_law = {
        "source_question_id": "source_07",
        "title": "质量作用定律与反应级数",
        "knowledge_points": ["质量作用定律", "反应级数", "基元反应与复合反应"],
        "required_constraints": {
            "essential_definitions": ["质量作用定律只适用于基元反应"],
            "essential_formulas": [],
            "applicable_boundaries": ["复合反应速率方程不遵循质量作用定律"],
        },
    }
    plan = {
        "source_mode": "knowledge",
        "selected_source_questions": [
            {
                "source_question_id": "source_01",
                "title": "概念辨析",
                "knowledge_points": ["质量作用定律适用范围"],
            },
            source_quality_law,
        ],
        "blueprint": {
            "generation_strategy": "knowledge_item_wise",
            "exercise_plan": [{
                "number": 1,
                "plan_item_id": "p1",
                "source_question_id": "source_07",
                "source_refs": ["source_07"],
                "question_type": "单选题",
                "difficulty": "进阶",
                "target_skill": "辨析基元反应与复合反应中的速率方程",
                "variation_type": "概念判断",
                "design_intent": "考查质量作用定律适用范围",
                "difficulty_levers": ["条件识别或转换要求"],
                "difficulty_rationale": "需要判断定律的适用边界。",
                "required_knowledge_points": source_quality_law["knowledge_points"],
                "required_constraints": source_quality_law["required_constraints"],
            }, {
                "number": 2,
                "plan_item_id": "p2",
                "source_question_id": "source_01",
                "source_refs": ["source_01"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "说明适用条件",
                "variation_type": "直接说明",
                "design_intent": "考查定律边界。",
                "difficulty_levers": ["条件直接程度"],
                "difficulty_rationale": "需要准确说明条件。",
                "required_knowledge_points": ["质量作用定律适用范围"],
            }],
        },
    }

    audit = exercise_generation.audit_practice_blueprint(plan)

    assert audit["status"] != "blocked", audit
    assert not any(finding.get("code") == "cross_source_design_leak" for finding in audit["findings"])


def test_blueprint_audit_warns_for_future_bridge_without_expanding_assessed_scope():
    plan = {
        "selected_source_questions": [
            {"source_question_id": "source_01", "title": "半衰期", "knowledge_points": ["半衰期定义"]},
            {"source_question_id": "source_02", "title": "反应级数", "knowledge_points": ["反应级数的确定"]},
            {"source_question_id": "source_03", "title": "活化能", "knowledge_points": ["活化能计算"]},
        ],
        "blueprint": {
            "generation_strategy": "targeted_set",
            "exercise_plan": [{
                "number": 1,
                "plan_item_id": "p1",
                "source_question_id": "source_01",
                "source_refs": ["source_01", "source_02"],
                "coverage_role": "综合",
                "question_type": "计算题",
                "difficulty": "进阶",
                "target_skill": "根据半衰期判断反应级数",
                "variation_type": "数据比较",
                "design_intent": "比较一级与二级反应，为后续活化能计算奠定基础。",
                "difficulty_levers": ["知识综合与迁移程度"],
                "difficulty_rationale": "需要选择正确的半衰期关系。",
                "required_knowledge_points": ["半衰期定义", "反应级数的确定"],
            }],
        },
    }

    audit = exercise_generation.audit_practice_blueprint(plan)

    assert audit["status"] == "warning"
    assert not audit["errors"]
    assert any(finding.get("code") == "cross_source_context_reference" for finding in audit["findings"])


def test_blueprint_refinement_context_contains_only_bound_knowledge_points():
    plan = {
        "source_analysis": {"subject": "化学", "knowledge_points": ["半衰期", "活化能计算"]},
        "selected_source_questions": [
            {"source_question_id": "source_01", "knowledge_points": ["半衰期"]},
            {"source_question_id": "source_02", "knowledge_points": ["活化能计算"]},
        ],
    }

    context = exercise_generation._blueprint_refinement_context(
        plan,
        [{"source_question_id": "source_01", "source_refs": ["source_01"]}],
    )

    assert context["bound_knowledge_points"] == ["半衰期"]
    assert "task_knowledge_points" not in context


def test_blueprint_audit_cross_source_leak_gets_one_item_local_repair(monkeypatch):
    plan = {
        "source_mode": "knowledge",
        "selected_source_questions": [
            {"source_question_id": "source_a", "title": "精馏", "knowledge_points": ["精馏原理"]},
            {"source_question_id": "source_b", "title": "晶体", "knowledge_points": ["晶面指数标定步骤"]},
        ],
        "blueprint": {
            "generation_strategy": "knowledge_item_wise",
            "exercise_plan": [{
                "number": 1,
                "plan_item_id": "p1",
                "source_question_id": "source_a",
                "source_refs": ["source_a"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "精馏原理",
                "variation_type": "直接辨析",
                "design_intent": "考查精馏原理",
                "difficulty_levers": ["条件直接程度"],
                "difficulty_rationale": "学生容易混淆晶面指数。",
                "required_knowledge_points": ["精馏原理"],
            }, {
                "number": 2,
                "plan_item_id": "p2",
                "source_question_id": "source_b",
                "source_refs": ["source_b"],
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "标定晶面指数",
                "variation_type": "步骤说明",
                "design_intent": "考查晶面指数标定步骤",
                "difficulty_levers": ["条件直接程度"],
                "difficulty_rationale": "需要按顺序说明标定步骤。",
                "required_knowledge_points": ["晶面指数标定步骤"],
            }],
        },
    }
    initial = exercise_generation.audit_practice_blueprint(plan)

    def fake_refine(_plan, batch, **_kwargs):
        return [{**batch[0], "difficulty_rationale": "学生需要准确说明精馏原理。"}]

    monkeypatch.setattr(exercise_generation, "_refine_blueprint_batch", fake_refine)
    repair = exercise_generation.repair_blueprint_audit_findings(plan, {}, initial)
    final = exercise_generation.audit_practice_blueprint(plan)

    assert repair["attempted_item_ids"] == ["p1"]
    assert repair["repaired_item_ids"] == ["p1"]
    assert repair["call_count"] == 1
    assert final["status"] != "blocked", final


def test_mode_contract_blocks_wrong_per_source_question_counts():
    plan = {
        "selected_source_questions": [
            {"source_question_id": "source_a"},
            {"source_question_id": "source_b"},
        ],
        "blueprint": {
            "generation_strategy": "knowledge_item_wise",
            "expected_source_counts": {"source_a": 3, "source_b": 3},
            "exercise_plan": [
                {"source_refs": ["source_a"], "variation_type": f"变式{i}", "target_skill": f"能力{i}"}
                for i in range(2)
            ] + [
                {"source_refs": ["source_b"], "variation_type": f"变式{i}", "target_skill": f"能力{i}"}
                for i in range(2, 6)
            ],
        },
    }

    contract = validate_practice_mode_contract(plan)

    assert contract["status"] == "failed"
    assert any("逐来源题数" in error and "source_a应为3题、实际2题" in error for error in contract["errors"])


def test_global_diversity_gate_uses_declared_solution_signature():
    shared_signature = {
        "scenario_family": "两能级粒子体系",
        "asked_quantity": "高能级粒子数占比",
        "solution_family": "玻尔兹曼分布结合斯特林近似",
        "cognitive_operation": "计算",
    }
    practice = {
        "generation_strategy": "targeted_set",
        "exercises": [
            {
                "number": 1, "stem": "研究密闭容器中粒子的能级布居。", "knowledge_points": ["统计热力学"],
                "diversity_signature": shared_signature, "generation_status": "completed",
            },
            {
                "number": 2, "stem": "一束原子进入外场后测得布居概率。", "knowledge_points": ["统计热力学"],
                "diversity_signature": shared_signature, "generation_status": "completed",
            },
        ],
    }

    issues = practice_diversity_issues(practice)

    assert any(issue["code"] == "set_diversity_collision" for issue in issues)


def test_difficulty_intent_is_a_rotating_choice_pool_not_a_checklist():
    first = _difficulty_intent({"difficulty": "进阶", "variation_type": "条件应用"}, set_position=0)
    second = _difficulty_intent({"difficulty": "进阶", "variation_type": "条件应用"}, set_position=1)
    challenge = _difficulty_intent({"difficulty": "挑战", "structural_change": "比较与优化"}, set_position=3)

    assert first["candidate_mechanisms"][0] != second["candidate_mechanisms"][0]
    assert challenge["candidate_mechanisms"][0] == "比较评价或优化"
    assert "一种主要机制" in challenge["selection_rule"]
    assert "不要把候选项全部堆叠" in challenge["selection_rule"]


def test_missing_difficulty_hint_is_a_blueprint_warning_not_a_blocker():
    audit = audit_practice_blueprint({
        "blueprint": {
            "generation_strategy": "parallel_exam",
            "exercise_plan": [{
                "plan_item_id": "plan_item_01",
                "question_type": "简答题",
                "difficulty": "挑战",
                "target_skill": "判断边界",
                "variation_type": "评价",
                "design_intent": "评价模型的适用边界。",
            }],
        },
    })

    assert audit["status"] != "blocked"
    assert any("不因此阻断生成" in warning for warning in audit["warnings"])


def test_blueprint_warns_when_image_source_loses_all_image_dependency():
    plan = ensure_practice_blueprint_defaults({
        "source_scope": {"questions": [{"source_question_id": "source_1", "source_content": "题干⟦IMAGE_REF:1;MEMBER:word/media/image1.png⟧", "knowledge_points": ["曲线分析"]}]},
        "selected_source_questions": [{"source_question_id": "source_1", "source_content": "题干⟦IMAGE_REF:1;MEMBER:word/media/image1.png⟧", "knowledge_points": ["曲线分析"]}],
        "blueprint": {
            "training_goal": "训练读图",
            "progression": ["读取曲线"],
            "generation_strategy": "per_question",
            "exercise_plan": [{
                "plan_item_id": "plan_item_01",
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "解释曲线",
                "variation_type": "改变子问结构",
                "design_intent": "比较两个阶段。",
                "source_question_id": "source_1",
                "source_refs": ["source_1"],
                "required_knowledge_points": ["曲线分析"],
                "stem_figure_required": False,
            }],
        },
    })

    audit = audit_practice_blueprint(plan)

    assert audit["status"] == "warning"
    assert any("包含原图" in warning and "source_1" in warning for warning in audit["warnings"])


def test_blueprint_warns_when_variation_declares_source_boundary_change():
    plan = ensure_practice_blueprint_defaults({
        "selected_source_questions": [{
            "source_question_id": "source_1",
            "knowledge_points": ["相图"],
            "required_constraints": {"applicable_boundaries": ["平衡冷却条件"]},
        }],
        "blueprint": {
            "training_goal": "训练相图分析",
            "progression": ["组织判断"],
            "generation_strategy": "per_question",
            "exercise_plan": [{
                "plan_item_id": "plan_item_01",
                "question_type": "综合题",
                "difficulty": "挑战",
                "target_skill": "预测组织",
                "variation_type": "改变边界条件：从平衡冷却改为非平衡冷却",
                "design_intent": "改为快速冷却后判断组织。",
                "source_question_id": "source_1",
                "source_refs": ["source_1"],
                "required_knowledge_points": ["相图"],
            }],
        },
    })

    audit = audit_practice_blueprint(plan)

    assert audit["status"] == "warning"
    assert any("改变已有适用边界" in warning for warning in audit["warnings"])


def test_blueprint_warns_when_other_semantic_fields_contradict_source_boundary():
    plan = ensure_practice_blueprint_defaults({
        "selected_source_questions": [{
            "source_question_id": "source_1",
            "knowledge_points": ["相图"],
            "required_constraints": {"applicable_boundaries": ["平衡冷却条件"]},
        }],
        "blueprint": {
            "training_goal": "训练相图分析",
            "progression": ["组织判断"],
            "generation_strategy": "per_question",
            "exercise_plan": [{
                "plan_item_id": "plan_item_01",
                "question_type": "综合题",
                "difficulty": "挑战",
                "target_skill": "非平衡冷却下的组织预测",
                "variation_type": "改变未知量",
                "design_intent": "保持平衡冷却条件。",
                "difficulty_rationale": "结合非平衡冷却动力学进行判断。",
                "source_question_id": "source_1",
                "source_refs": ["source_1"],
                "required_knowledge_points": ["相图"],
            }],
        },
    })

    audit = audit_practice_blueprint(plan)

    assert audit["status"] == "warning"
    assert any("改变已有适用边界" in warning for warning in audit["warnings"])


def test_blueprint_does_not_treat_boundary_conclusion_or_misconception_as_boundary_change():
    plan = ensure_practice_blueprint_defaults({
        "selected_source_questions": [{
            "source_question_id": "source_1",
            "knowledge_points": ["二元系反应扩散的相区分布规律"],
            "required_constraints": {"applicable_boundaries": [
                "二元系反应扩散无两相区的结论仅适用于恒温恒压的平衡态过程",
                "结论不适用于三元及以上体系和非平衡扩散过程",
            ]},
        }],
        "blueprint": {
            "training_goal": "辨析二元系反应扩散规律",
            "progression": ["先判断标准结论"],
            "generation_strategy": "per_question",
            "exercise_plan": [{
                "plan_item_id": "plan_item_01",
                "question_type": "判断题",
                "difficulty": "基础",
                "target_skill": "准确识别经典结论的适用边界",
                "variation_type": "嵌入混淆二元与多元、平衡与非平衡过程的常见误区，但保持原适用条件",
                "design_intent": "聚焦恒温恒压平衡条件下不存在连续两相区的结论，为后续非平衡场景分析打基础。",
                "difficulty_rationale": "学生容易混淆非平衡扩散与平衡反应扩散。",
                "source_question_id": "source_1",
                "source_refs": ["source_1"],
                "required_knowledge_points": ["二元系反应扩散的相区分布规律"],
            }],
        },
    })

    audit = audit_practice_blueprint(plan)

    assert not any("改变已有适用边界" in warning for warning in audit["warnings"])
    assert not any(
        finding.get("code") == "applicable_boundary_change_declared"
        for finding in audit["findings"]
    )


def test_difficulty_evidence_is_preserved_and_drift_never_blocks_the_set():
    normalized = normalize_practice_set(
        {"exercises": [{
            "plan_item_id": "plan_item_01",
            "question_type": "简答题",
            "difficulty": "挑战",
            "target_skill": "边界判断",
            "variation_type": "评价",
            "stem": "评价某模型在给定边界下是否适用。",
            "knowledge_points": ["模型边界"],
            "verification_note": "条件充分。",
            "difficulty_evidence": {
                "primary_mechanism": "直接条件与明确路径",
                "student_bottleneck": "识别直接给出的条件。",
            },
        }]},
        requested_count=1,
        subject="",
        planned_types=["简答题"],
        planned_plan_ids=["plan_item_01"],
        planned_difficulties=["挑战"],
    )
    practice = {**normalized, "generation_strategy": "targeted_set"}

    observations = practice_difficulty_observations(practice)
    quality = recompute_practice_quality(practice)

    assert normalized["exercises"][0]["difficulty_evidence"]["primary_mechanism"] == "直接条件与明确路径"
    assert any(item["code"] == "possible_difficulty_drift" for item in observations)
    assert quality["status"] == "passed"
    assert quality["checks"]["difficulty_alignment"] is False
    assert any("本次不阻断成题" in warning for warning in quality["warnings"])


def test_repeated_difficulty_mechanism_is_only_a_portfolio_warning():
    practice = {
        "exercises": [
            {
                "number": index,
                "difficulty": "挑战",
                "stem": f"题干{label}",
                "question_type": "简答题",
                "generation_status": "completed",
                "difficulty_evidence": {
                    "primary_mechanism": "模型建立",
                    "student_bottleneck": f"建立{label}情境的模型。",
                },
            }
            for index, label in enumerate(("甲", "乙", "丙", "丁"), start=1)
        ],
    }

    quality = recompute_practice_quality(practice)

    assert quality["status"] == "passed"
    assert any(item["code"] == "difficulty_mechanism_concentration" for item in quality["difficulty_observations"])


def test_explicit_stem_figure_requires_renderable_figure_content():
    plan_item = {
        "question_type": "简答题",
        "stem_figure_required": True,
        "figure_design": {"required_elements": ["入口", "出口"]},
    }
    contract = _batch_output_contract([plan_item])
    assert "figures" in contract["exercises"][0]
    issues = _exercise_figure_issues(
        {"stem": "根据图示作答。", "figures": [{"description": "入口与出口示意", "series": []}]},
        plan_item,
    )
    assert any(issue["code"] == "unrenderable_stem_figure" for issue in issues)


def test_node_edge_diagram_is_normalized_as_renderable():
    figures = _normalize_figures([{
        "nodes": [
            {"id": "a", "label": "入口", "x": 0.1, "y": 0.5},
            {"id": "b", "label": "出口", "x": 0.9, "y": 0.5},
        ],
        "edges": [{"from": "a", "to": "b", "directed": True}],
    }])
    assert figures[0]["nodes"][1]["label"] == "出口"
    assert figures[0]["edges"] == [{"from": "a", "to": "b", "label": "", "directed": True}]


def test_chart_nodes_keep_data_coordinates_instead_of_being_clamped_to_canvas():
    figures = _normalize_figures([{
        "x_label": "V",
        "y_label": "P",
        "series": [{"name": "过程曲线", "points": [[1, 2], [3, 1]]}],
        "nodes": [{"id": "end", "label": "终态点", "x": 3, "y": 1}],
    }])

    assert figures[0]["nodes"][0]["x"] == 3
    assert figures[0]["nodes"][0]["y"] == 1


def test_figure_gate_understands_axes_and_separate_terminal_nodes_structurally():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {
            "required_elements": ["P-V坐标系", "终态点1和终态点2"],
        },
    }
    exercise = {
        "stem": "根据图中两个终态点作答。",
        "figures": [{
            "x_label": "V",
            "y_label": "P",
            "series": [
                {"name": "过程一", "points": [[1, 2], [2, 1]]},
                {"name": "过程二", "points": [[1, 2], [3, 1]]},
            ],
            "nodes": [
                {"id": "e1", "label": "终态点1", "x": 2, "y": 1},
                {"id": "e2", "label": "终态点2", "x": 3, "y": 1},
            ],
        }],
    }

    assert _exercise_figure_issues(exercise, plan_item) == []


def test_figure_gate_understands_separately_stored_compound_stage_labels():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {"required_elements": ["标注II、III阶段"]},
    }
    exercise = {
        "stem": "根据图中 II、III 阶段作答。",
        "figures": [{
            "series": [{"name": "应力-应变曲线", "points": [[0, 0], [1, 1], [2, 1], [3, 2], [4, 2.5]]}],
            "nodes": [
                {"id": "stage_ii", "label": "II阶段", "x": 1.5, "y": 1},
                {"id": "stage_iii", "label": "III阶段", "x": 3.2, "y": 2.1},
            ],
        }],
    }

    assert _exercise_figure_issues(exercise, plan_item) == []


def test_figure_gate_rejects_inconsistent_same_final_pressure_geometry():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {"relationship_constraints": ["两条路径终态压力相同"]},
    }
    exercise = {
        "stem": "根据图示比较两条路径。",
        "figures": [{
            "series": [
                {"name": "路径一", "points": [[1, 2], [2, 1]]},
                {"name": "路径二", "points": [[1, 2], [3, 0.8]]},
            ],
        }],
    }

    issues = _exercise_figure_issues(exercise, plan_item)
    assert any(issue["code"] == "figure_relationship_constraint_failed" for issue in issues)


def test_figure_gate_rejects_crossed_phase_boundaries_and_off_boundary_staged_path():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {
            "required_elements": ["气相线", "液相线", "逐级汽化与冷凝路径"],
            "relationship_constraints": [
                "气相线与液相线围成气液两相区",
                "逐级路径在等温联络线与相界之间交替",
            ],
        },
    }
    exercise = {
        "stem": "根据图中气、液相线及逐级路径作答。",
        "figures": [{
            "series": [
                {"name": "液相线", "points": [[0, 110], [0.25, 96], [0.5, 85], [0.75, 96], [1, 110]]},
                {"name": "气相线", "points": [[0, 110], [0.25, 90], [0.5, 85], [0.75, 102], [1, 110]]},
                {"name": "逐级汽化与冷凝路径", "points": [[0.2, 100], [0.2, 80], [0.1, 80], [0.1, 90], [0.05, 90]]},
            ],
        }],
    }

    codes = {issue["code"] for issue in _exercise_figure_issues(exercise, plan_item)}
    assert "figure_phase_boundaries_cross" in codes
    assert "figure_staged_path_off_boundary" in codes


def test_figure_gate_accepts_non_crossing_phase_boundaries_and_boundary_staged_path():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {
            "required_elements": ["气相线", "液相线", "逐级汽化与冷凝路径"],
            "relationship_constraints": [
                "气相线与液相线围成气液两相区",
                "逐级路径在等温联络线与相界之间交替",
            ],
        },
    }
    exercise = {
        "stem": "根据图中气、液相线及逐级路径作答。",
        "figures": [{
            "series": [
                {"name": "液相线", "points": [[0, 100], [0.2, 90], [0.3, 85], [0.5, 80], [1, 100]]},
                {"name": "气相线", "points": [[0, 100], [0.2, 95], [0.3, 90], [0.5, 80], [1, 100]]},
                {"name": "逐级汽化与冷凝路径", "points": [[0.2, 100], [0.2, 90], [0.3, 90], [0.3, 85], [0.4, 85]]},
            ],
        }],
    }

    codes = {issue["code"] for issue in _exercise_figure_issues(exercise, plan_item)}
    assert "figure_phase_boundaries_cross" not in codes
    assert "figure_staged_path_off_boundary" not in codes


def test_program_stabilizes_generated_phase_envelope_and_step_path():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {
            "required_elements": ["气相线", "液相线", "初始组成点", "恒沸组成点", "逐级汽化与冷凝路径"],
            "relationship_constraints": [
                "气相线与液相线围成气液两相区",
                "逐级路径在等温联络线与相界之间交替",
            ],
        },
    }
    exercise = {
        "stem": "根据图中气、液相线及逐级路径作答。",
        "figures": [{
            "series": [
                {"name": "液相线", "points": [[0, 110], [0.25, 98], [0.5, 85], [0.75, 98], [1, 110]]},
                {"name": "气相线", "points": [[0, 110], [0.25, 90], [0.4, 82], [0.5, 85], [0.75, 102], [1, 110]]},
                {"name": "逐级汽化与冷凝路径", "points": [[0.25, 100], [0.25, 88], [0.15, 88], [0.15, 92], [0.08, 92]]},
            ],
            "nodes": [
                {"id": "azeotrope", "label": "恒沸组成点", "x": 0.5, "y": 85},
                {"id": "initial", "label": "初始组成点 O", "x": 0.25, "y": 100},
            ],
        }],
    }

    _complete_generated_figure(exercise, plan_item)

    assert _exercise_figure_issues(exercise, plan_item) == []
    path = exercise["figures"][0]["series"][2]["points"]
    assert len(path) >= 5
    assert exercise["figures"][0]["nodes"][1]["y"] == path[0][1]


def test_figure_gate_rejects_under_sampled_curve_and_misaligned_state_nodes():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {"required_elements": ["绝热可逆膨胀线", "初态点"]},
    }
    exercise = {
        "stem": "根据图示判断过程状态。",
        "figures": [{
            "series": [{"name": "绝热可逆膨胀线", "points": [[1, 2], [2, 1]]}],
            "nodes": [{"id": "start", "label": "初态点", "x": 1, "y": 1}],
        }],
    }

    codes = {issue["code"] for issue in _exercise_figure_issues(exercise, plan_item)}
    assert "figure_curve_under_sampled" in codes
    assert "figure_node_coordinate_mismatch" in codes


def test_program_completes_chart_axes_state_nodes_and_terminal_pressure_line():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {
            "required_elements": ["P-V图", "初态点", "终态点1和终态点2", "终压水平线"],
        },
    }
    exercise = {
        "figures": [{
            "series": [
                {"name": "路径一", "points": [[1, 2], [2, 1]]},
                {"name": "路径二", "points": [[1, 2], [3, 1]]},
            ],
            "nodes": [
                {"id": "bad", "label": "初态点", "x": 0, "y": 0},
            ],
        }],
    }

    _complete_generated_figure(exercise, plan_item)

    figure = exercise["figures"][0]
    assert (figure["x_label"], figure["y_label"]) == ("V", "P")
    assert next(node for node in figure["nodes"] if node["label"] == "初态点")["x"] == 1
    assert {node["label"] for node in figure["nodes"]} >= {"终态点1", "终态点2"}
    assert any(row["name"] == "终压水平线" for row in figure["series"])


def test_figure_gate_rejects_labels_that_reveal_requested_unlabelled_identification():
    plan_item = {
        "stem_figure_required": True,
        "figure_design": {
            "required_elements": ["上屈服点与下屈服点", "屈服平台", "加工硬化起始段"],
            "relationship_constraints": ["上屈服点后应力下降到下屈服点"],
            "question_dependency": "学生必须根据曲线中未标注的阶段位置识别屈服平台。",
        },
    }
    exercise = {
        "stem": "根据图1识别曲线各阶段。",
        "figures": [{
            "title": "拉伸曲线",
            "description": "包含上下屈服点、屈服平台与加工硬化段的曲线。",
            "series": [{"name": "拉伸曲线", "points": [[0, 0], [1, 2], [2, 1.5], [3, 1.5], [4, 2.5]]}],
            "nodes": [
                {"id": "n1", "label": "上屈服点", "x": 1, "y": 2},
                {"id": "n2", "label": "屈服平台", "x": 3, "y": 1.5},
            ],
        }],
    }

    issues = _exercise_figure_issues(exercise, plan_item)

    assert "figure_reveals_requested_identification" in {issue["code"] for issue in issues}


def test_multimodal_primary_model_wins_over_separate_vision_fallback(monkeypatch):
    providers = {
        "primary": SimpleNamespace(name="primary", supports_vision=True, vision_model="gpt-5.6-sol", vision_model_options=("gpt-5.6-sol",)),
        "fallback": SimpleNamespace(name="fallback", supports_vision=True, vision_model="qwen-vl", vision_model_options=("qwen-vl",)),
    }
    monkeypatch.setattr(exercise_generation, "get_provider", lambda name=None: providers[name or "primary"])
    monkeypatch.setattr(exercise_generation, "resolve_provider_model", lambda provider, model=None: model or provider.vision_model)
    payload = {"provider": "primary", "model": "gpt-5.6-sol", "vision_provider": "fallback", "vision_model": "qwen-vl"}

    provider, model = _model_runtime(payload, True)

    assert (provider.name, model) == ("primary", "gpt-5.6-sol")
    assert _model_route(payload, True, provider, model) == "primary_multimodal"


def test_model_capability_table_routes_nondefault_multimodal_primary_directly(monkeypatch):
    providers = {
        "primary": SimpleNamespace(
            name="primary",
            supports_vision=True,
            vision_model="qwen3.7-flash",
            vision_model_options=(),
            model_capabilities={"qwen3.7-plus": ("text", "vision")},
            model_option_labels={"qwen3.7-plus": "Qwen3.7-Plus 多模态旗舰"},
        ),
        "fallback": SimpleNamespace(
            name="fallback",
            supports_vision=True,
            vision_model="qwen-vl",
            vision_model_options=("qwen-vl",),
            model_capabilities={},
            model_option_labels={},
        ),
    }
    monkeypatch.setattr(exercise_generation, "get_provider", lambda name=None: providers[name or "primary"])
    monkeypatch.setattr(exercise_generation, "resolve_provider_model", lambda provider, model=None: model or provider.vision_model)
    payload = {
        "provider": "primary",
        "model": "qwen3.7-plus",
        "vision_provider": "fallback",
        "vision_model": "qwen-vl",
    }

    provider, model = _model_runtime(payload, True)

    assert (provider.name, model) == ("primary", "qwen3.7-plus")
    assert _model_route(payload, True, provider, model) == "primary_multimodal"


def test_nonvisual_primary_uses_configured_vision_fallback(monkeypatch):
    providers = {
        "primary": SimpleNamespace(name="primary", supports_vision=False, vision_model="", vision_model_options=()),
        "fallback": SimpleNamespace(name="fallback", supports_vision=True, vision_model="qwen-vl", vision_model_options=("qwen-vl",)),
    }
    monkeypatch.setattr(exercise_generation, "get_provider", lambda name=None: providers[name or "primary"])
    monkeypatch.setattr(exercise_generation, "resolve_provider_model", lambda provider, model=None: model or provider.vision_model or "text-only")
    payload = {"provider": "primary", "model": "text-only", "vision_provider": "fallback", "vision_model": "qwen-vl"}

    provider, model = _model_runtime(payload, True)

    assert (provider.name, model) == ("fallback", "qwen-vl")
    assert _model_route(payload, True, provider, model) == "vision_fallback"


def test_source_analysis_prompt_keeps_required_constraints_as_literal_json(monkeypatch):
    """The JSON example in the analysis prompt must not be parsed as an f-string field."""
    captured: dict[str, str] = {}

    def fake_call(_client, messages, **_kwargs):
        captured["prompt"] = messages[-1]["content"]
        return {
            "source_scope": {"mode": "single", "title": "验证材料", "questions": []},
            "source_analysis": {"subject": "验证学科"},
        }

    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda _payload, _has_images: (SimpleNamespace(name="test"), "test-model"))
    monkeypatch.setattr(exercise_generation, "_model_route", lambda *_args: "text_only")

    result = exercise_generation.analyze_practice_source({"question_text": "用于验证提示词格式的足够长测试材料。"})

    assert '"required_constraints": {"essential_definitions"' in captured["prompt"]
    assert result["source_scope"]["questions"][0]["source_question_id"] == "source_01"


def test_source_analysis_attaches_docx_reference_images_even_when_text_first(monkeypatch):
    captured: dict[str, object] = {}
    reference_image = "data:image/png;base64,ZmFrZQ=="

    monkeypatch.setattr(
        exercise_generation,
        "parse_practice_sources",
        lambda _payload: {
            "text": "足够长的原生题目文字，解析器因此保持 text-first。",
            "images": [],
            "reference_images": [reference_image],
            "file_names": ["含图题.docx"],
            "file_diagnostics": [],
            "analysis_mode": "text",
        },
    )
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())

    def fake_runtime(_payload, has_images):
        captured["has_images"] = has_images
        return SimpleNamespace(name="vision-test"), "vision-model"

    def fake_call(_client, messages, **_kwargs):
        captured["content"] = messages[-1]["content"]
        return {
            "source_scope": {"mode": "single", "title": "含图题", "questions": []},
            "source_analysis": {"subject": "材料科学"},
        }

    monkeypatch.setattr(exercise_generation, "_model_runtime", fake_runtime)
    monkeypatch.setattr(exercise_generation, "_model_route", lambda *_args: "primary_multimodal")
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    result = exercise_generation.analyze_practice_source({"question_text": "占位"})

    assert captured["has_images"] is True
    assert isinstance(captured["content"], list)
    assert any(part.get("image_url", {}).get("url") == reference_image for part in captured["content"])
    assert result["generation"]["input_mode"] == "mixed"
    assert result["generation"]["reference_image_count"] == 1


def test_source_analysis_repairs_empty_constraints_per_question_without_global_leakage(monkeypatch):
    calls: list[str] = []

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        if "只补齐这些题目的约束" in prompt:
            return {"constraints": [
                {
                    "source_question_id": "source_01",
                    "required_constraints": {
                        "essential_definitions": ["可逆电池的定义"],
                        "essential_formulas": ["ΔG=-zFE"],
                        "applicable_boundaries": ["恒温恒压可逆过程"],
                    },
                },
                {
                    "source_question_id": "source_02",
                    "required_constraints": {
                        "essential_definitions": ["加工硬化的定义"],
                        "essential_formulas": [],
                        "applicable_boundaries": ["塑性变形阶段"],
                    },
                },
            ]}
        return {
            "source_scope": {"mode": "question_set", "title": "混合材料", "questions": [
                {
                    "source_question_id": "source_01",
                    "number": "1",
                    "title": "电化学题",
                    "source_content": "用可逆电池测量反应 Gibbs 自由能。",
                    "knowledge_points": ["电化学热力学"],
                    "required_constraints": {},
                },
                {
                    "source_question_id": "source_02",
                    "number": "2",
                    "title": "材料变形题",
                    "source_content": "说明金属塑性变形中的加工硬化。",
                    "knowledge_points": ["加工硬化"],
                    "required_constraints": {},
                },
            ]},
            "source_analysis": {
                "subject": "混合学科",
                "essential_formulas": ["不得直接复制给全部题目的全局公式"],
            },
        }

    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    monkeypatch.setattr(
        exercise_generation,
        "_model_runtime",
        lambda _payload, _has_images: (SimpleNamespace(name="test"), "test-model"),
    )
    monkeypatch.setattr(exercise_generation, "_model_route", lambda *_args: "text_only")

    result = exercise_generation.analyze_practice_source({"question_text": "混合题目材料，长度足够用于分析。"})

    assert len(calls) == 2
    questions = result["source_scope"]["questions"]
    assert questions[0]["required_constraints"]["essential_formulas"] == ["ΔG=-zFE"]
    assert questions[1]["required_constraints"]["essential_formulas"] == []
    assert questions[1]["required_constraints"]["essential_definitions"] == ["加工硬化的定义"]
    assert result["source_constraint_gate"] == {
        "status": "passed",
        "repair_attempted": True,
        "incomplete_source_question_ids": [],
        "repair_error": "",
    }


def test_planning_blocks_explicitly_incomplete_source_constraints(monkeypatch):
    monkeypatch.setattr(
        exercise_generation,
        "parse_practice_sources",
        lambda _payload: {
            "text": "原题",
            "images": [],
            "reference_images": [],
            "file_names": [],
            "file_diagnostics": [],
            "analysis_mode": "text",
        },
    )
    payload = {
        "source_mode": "exam",
        "selected_source_questions": [{
            "source_question_id": "source_01",
            "title": "约束缺失题",
            "source_content": "原题内容",
            "knowledge_points": ["知识点"],
            "required_constraints": {},
            "constraint_status": "incomplete",
        }],
    }

    with pytest.raises(ValueError, match="逐题生成约束尚未补齐"):
        exercise_generation.plan_practice_set(payload)


def test_long_mixed_source_is_chunked_and_local_ids_are_merged(monkeypatch):
    calls: list[object] = []
    images = [f"data:image/png;base64,image{index}" for index in range(1, 10)]
    first = "第一章知识内容" * 1400 + "\n⟦IMAGE_REF:1;MEMBER:word/media/image1.png⟧"
    second = "第二章知识内容" * 1400 + "\n⟦IMAGE_REF:9;MEMBER:word/media/image9.png⟧"
    monkeypatch.setattr(
        exercise_generation,
        "parse_practice_sources",
        lambda _payload: {
            "text": f"{first}\n\n{second}",
            "images": [],
            "reference_images": images,
            "file_names": ["长讲义.docx"],
            "file_diagnostics": [],
            "analysis_mode": "text",
        },
    )
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(
        exercise_generation,
        "_model_runtime",
        lambda _payload, _has_images: (SimpleNamespace(name="vision-test"), "vision-model"),
    )
    monkeypatch.setattr(exercise_generation, "_model_route", lambda *_args: "vision_fallback")

    def fake_call(_client, messages, **_kwargs):
        content = messages[-1]["content"]
        calls.append(content)
        index = len(calls)
        return {
            "source_scope": {"mode": "single", "title": f"第{index}段", "questions": [{
                "source_question_id": "source_01",
                "number": "1",
                "title": f"知识单元{index}",
                "source_content": f"第{index}段内容",
                "question_type": "知识单元",
                "knowledge_points": [f"知识点{index}"],
                "required_constraints": {
                    "essential_definitions": [f"定义{index}"],
                    "essential_formulas": [],
                    "applicable_boundaries": [f"边界{index}"],
                },
            }]},
            "source_analysis": {"subject": "测试学科", "knowledge_points": [f"知识点{index}"]},
        }

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    result = exercise_generation.analyze_practice_source({"source_mode": "knowledge"})

    assert len(calls) == 2
    assert [item["source_question_id"] for item in result["source_scope"]["questions"]] == ["source_01", "source_02"]
    assert result["source_analysis"]["knowledge_points"] == ["知识点1", "知识点2"]
    assert result["generation"]["chunked_analysis"] is True
    assert result["generation"]["analysis_chunk_count"] == 2


def test_normalize_practice_set_assigns_program_owned_fields():
    raw = {
        "source_analysis": {
            "subject": "数学",
            "question_type": "计算题",
            "knowledge_points": ["方程"],
            "skills": ["建模"],
        },
        "blueprint": {
            "training_goal": "练习根据条件建立方程",
            "progression": ["识别数量关系", "独立建模"],
        },
        "exercises": [_exercise(plan_item_id="plan_item_01"), _exercise(difficulty="进阶", plan_item_id="plan_item_02")],
    }

    result = normalize_practice_set(raw, requested_count=2, subject="数学")

    assert result["schema_version"] == "answer_book.practice_set.v1"
    assert [item["exercise_id"] for item in result["exercises"]] == ["practice_01", "practice_02"]
    assert [item["number"] for item in result["exercises"]] == [1, 2]
    assert result["quality"]["status"] == "passed"


def test_normalize_practice_set_preserves_comprehensive_knowledge_point_contract():
    points = [f"知识点{i}" for i in range(1, 15)]
    result = normalize_practice_set(
        {
            "exercises": [
                _exercise(plan_item_id="plan_item_01", knowledge_points=points)
            ]
        },
        requested_count=1,
        subject="材料科学",
    )

    assert result["exercises"][0]["knowledge_points"] == points


def test_normalize_practice_set_repairs_bare_braced_latex_before_results_are_saved():
    result = normalize_practice_set(
        {"exercises": [_exercise(plan_item_id="plan_item_01", stem=r"比较 \mathrm{Ar(g)} 与 \mathrm{H_2O(l)}。 ")]},
        requested_count=1,
        subject="物理化学",
    )

    exercise = result["exercises"][0]
    assert exercise["stem"] == r"比较 $\mathrm{Ar(g)}$ 与 $\mathrm{H_2O(l)}$。"
    assert exercise["generation_status"] == "completed"
    assert result["quality"]["status"] == "passed"


def test_normalize_practice_set_owns_generated_question_hierarchy_and_line_wraps():
    result = normalize_practice_set(
        {
            "exercises": [
                _exercise(
                    plan_item_id="plan_item_01",
                    stem="第 9 题\n请根据下列条件\n完成分析。\n\n（1）、写出关系式。\n2. 说明适用条件。\n①、比较两种情况。",
                )
            ]
        },
        requested_count=1,
        subject="物理化学",
    )

    assert result["exercises"][0]["stem"] == "请根据下列条件 完成分析。\n\n(1) 写出关系式。\n\n(2) 说明适用条件。\n\n① 比较两种情况。"


def test_normalize_practice_set_blocks_unrepairable_bare_latex_from_results():
    result = normalize_practice_set(
        {"exercises": [_exercise(plan_item_id="plan_item_01", stem=r"请说明 \Delta H 的物理意义。 ")]},
        requested_count=1,
        subject="物理化学",
    )

    exercise = result["exercises"][0]
    assert exercise["generation_status"] == "failed"
    assert exercise["generation_error"]["code"] == "unrenderable_markup"
    assert result["quality"]["status"] == "blocked"


def test_blueprint_refinement_isolates_a_failed_item_after_batch_retry(monkeypatch):
    plan = {
        "blueprint": {
            "exercise_plan": [
                {"plan_item_id": "plan_item_01", "target_skill": "原能力一"},
                {"plan_item_id": "plan_item_02", "target_skill": "原能力二"},
            ]
        }
    }
    monkeypatch.setattr(
        exercise_generation,
        "build_blueprint_generation_units",
        lambda _plan: [{"call_batches": [{"batch_id": "unit_01", "plan_item_ids": ["plan_item_01", "plan_item_02"]}]}],
    )

    def fake_refine(_plan, batch, **_kwargs):
        if len(batch) > 1 or batch[0]["plan_item_id"] == "plan_item_02":
            raise RuntimeError("模型只返回了部分蓝图项")
        return [{**batch[0], "target_skill": "已独立修复"}]

    monkeypatch.setattr(exercise_generation, "_refine_blueprint_batch", fake_refine)
    report = refine_blueprint_units(plan, {"blueprint_concurrency": 1})

    assert plan["blueprint"]["exercise_plan"][0]["target_skill"] == "已独立修复"
    assert plan["blueprint"]["exercise_plan"][1]["target_skill"] == "原能力二"
    assert report["units"][0]["call_batches"][0]["status"] == "partial_after_isolation"
    assert report["failures"][0]["plan_item_ids"] == ["plan_item_02"]


def test_normalize_options_relabels_duplicate_model_option_labels():
    result = normalize_practice_set(
        {"exercises": [_exercise(plan_item_id="plan_item_01", question_type="单选题", options=[
            {"label": "A", "text": "第一项"},
            {"label": "A", "text": "第二项"},
            {"label": "C", "text": "第三项"},
        ])]},
        requested_count=1,
        subject="物理化学",
    )
    assert [item["label"] for item in result["exercises"][0]["options"]] == ["A", "B", "C"]


def test_normalize_options_removes_labels_repeated_inside_option_text():
    result = normalize_practice_set(
        {"exercises": [_exercise(plan_item_id="plan_item_01", question_type="单选题", options=[
            {"label": "A", "text": "A. 第一项"},
            {"label": "B", "text": "（B）第二项"},
        ])]},
        requested_count=1,
        subject="物理化学",
    )
    assert result["exercises"][0]["options"] == [
        {"label": "A", "text": "第一项"},
        {"label": "B", "text": "第二项"},
    ]


def test_normalize_practice_set_lifts_markdown_pipe_table_out_of_stem():
    result = normalize_practice_set(
        {"exercises": [_exercise(plan_item_id="plan_item_01", stem="""根据下表回答问题：

| x | T/K |
|---|-----|
| 0.00 | 600 |
| 0.50 | 420 |

写出结论。""")]},
        requested_count=1,
        subject="材料科学",
    )
    item = result["exercises"][0]
    assert "| x |" not in item["stem"]
    assert item["tables"] == [{
        "table_id": "t1",
        "location": "stem",
        "title": "",
        "headers": ["x", "T/K"],
        "rows": [["0.00", "600"], ["0.50", "420"]],
    }]


def test_normalize_practice_set_reports_incomplete_generation():
    result = normalize_practice_set(
        {"source_analysis": {}, "blueprint": {}, "exercises": [_exercise(plan_item_id="plan_item_01")]},
        requested_count=3,
        subject="物理",
    )

    assert result["quality"]["status"] == "blocked"
    assert any("请求生成 3 题" in issue for issue in result["quality"]["blocking_issues"])
    assert all("解析" not in issue for issue in result["quality"]["blocking_issues"])


def test_normalize_practice_set_drops_model_answers_and_solutions():
    result = normalize_practice_set(
        {"exercises": [_exercise(plan_item_id="plan_item_01", answer="2", solution_steps=["计算过程"])]},
        requested_count=1,
        subject="数学",
    )
    assert "answer" not in result["exercises"][0]
    assert "solution_steps" not in result["exercises"][0]


def test_normalize_practice_set_rejects_empty_exercises():
    with pytest.raises(ValueError, match="exercises"):
        normalize_practice_set({"exercises": []}, requested_count=3, subject="化学")


def test_difficulty_plan_has_expected_progression_and_counts():
    assert _difficulty_plan("同等难度", 5) == ["基础", "基础", "进阶", "进阶", "挑战"]
    assert _difficulty_plan("进阶为主", 10).count("进阶") == 7
    assert _difficulty_plan("进阶为主", 10).count("挑战") == 3
    assert _difficulty_plan("进阶到挑战", 5) == ["进阶", "进阶", "进阶", "挑战", "挑战"]


def _direct_contract_payload():
    selected = [
        {"source_question_id": "source_1", "title": "来源一", "stem_excerpt": "材料一"},
        {"source_question_id": "source_2", "title": "来源二", "stem_excerpt": "材料二"},
    ]
    return {
        "source_mode": "exam",
        "generation_strategy": "targeted_set",
        "strategy_count": 5,
        "count": 5,
        "difficulty_counts": {"基础": 2, "进阶": 2, "挑战": 1},
        "source_scope": {"mode": "question_set", "questions": selected},
        "selected_source_questions": selected,
    }


def test_exact_difficulty_counts_must_equal_strategy_total():
    assert normalize_difficulty_counts(_direct_contract_payload(), 5) == {"基础": 2, "进阶": 2, "挑战": 1}
    invalid = {**_direct_contract_payload(), "difficulty_counts": {"基础": 2, "进阶": 1, "挑战": 1}}
    with pytest.raises(ValueError, match="合计必须等于总题量 5"):
        normalize_difficulty_counts(invalid, 5)


def test_direct_contract_has_stable_slots_and_exact_quotas():
    contract = build_generation_contract(_direct_contract_payload())
    assert [item["slot_id"] for item in contract["slots"]] == [f"generation_slot_{index:02d}" for index in range(1, 6)]
    assert [item["difficulty"] for item in contract["slots"]] == ["基础", "基础", "进阶", "进阶", "挑战"]
    assert contract["source_coverage"]["complete"] is True
    audit = audit_generation_contract(contract)
    assert audit["status"] == "passed"
    assert audit["errors"] == []


def test_direct_generation_uses_program_contract_without_planning_call(monkeypatch):
    captured = {}

    def fake_generate(payload):
        captured.update(payload)
        return {"exercises": [{"stem": "题干"}], "generation": {}}

    monkeypatch.setattr(exercise_generation, "generate_practice_from_plan", fake_generate)
    payload = {**_direct_contract_payload(), "blueprint_review_enabled": False, "generation_run_id": "run_new"}
    result = generate_practice_from_contract(payload)

    assert captured["plan"]["blueprint"]["exercise_plan"][0]["plan_item_id"] == "generation_slot_01"
    assert result["blueprint_review_enabled"] is False
    assert result["generation"]["generation_run_id"] == "run_new"


def test_normalize_practice_set_aligns_by_plan_index_not_model_order():
    raw = {"source_analysis": {}, "blueprint": {}, "exercises": [
        _exercise(number=2, plan_item_id="plan_item_02", stem="第二题"),
        _exercise(number=1, plan_item_id="plan_item_01", stem="第一题"),
    ]}
    result = normalize_practice_set(
        raw, requested_count=2, subject="数学", planned_types=["简答题", "计算题"],
        planned_source_ids=["source_1", "source_2"],
    )
    assert [item["question_type"] for item in result["exercises"]] == ["计算题", "简答题"]
    assert [item["source_question_id"] for item in result["exercises"]] == ["source_2", "source_1"]


def test_practice_parser_accepts_unescaped_newline_from_model():
    content = '{"source_analysis":{"difficulty":"先分析\n再判断"},"exercises":[]}'

    parsed = _parse_practice_json(content)

    assert parsed["source_analysis"]["difficulty"] == "先分析\n再判断"


def test_practice_parser_repairs_latex_backslashes_and_think_block():
    parsed = _parse_practice_json(
        '<think>internal reasoning</think>{"stem":"计算 $\\mathbf{a}_1$ 与 \\mu 的关系"}'
    )

    assert parsed["stem"] == "计算 $\\mathbf{a}_1$ 与 \\mu 的关系"


@pytest.mark.parametrize(
    ("mode", "count", "expected"),
    [
        ("基础", 1, ["基础"]),
        ("挑战", 1, ["挑战"]),
        ("基础为主", 5, ["基础", "基础", "基础", "基础", "进阶"]),
        ("基础到进阶", 5, ["基础", "基础", "基础", "进阶", "进阶"]),
        ("进阶为主", 5, ["进阶", "进阶", "进阶", "进阶", "挑战"]),
        ("进阶到挑战", 5, ["进阶", "进阶", "进阶", "挑战", "挑战"]),
    ],
)
def test_difficulty_plan_distinguishes_single_question_and_set_distribution(mode, count, expected):
    assert _difficulty_plan(mode, count) == expected


def test_normalized_blueprint_uses_program_owned_difficulty_distribution():
    raw = {
        "source_analysis": {},
        "blueprint": {
            "exercise_plan": [
                {"difficulty": "挑战", "target_skill": "能力", "variation_type": "应用", "design_intent": "意图"}
                for _ in range(3)
            ]
        },
    }
    result = _normalize_plan(
        raw,
        count=3,
        planned_types=["简答题"] * 3,
        difficulty="基础到进阶",
        planned_difficulties=["基础", "基础", "进阶"],
        selected_types=["简答题"],
        source_files=[],
    )

    assert [item["difficulty"] for item in result["blueprint"]["exercise_plan"]] == ["基础", "基础", "进阶"]


def test_blueprint_difficulty_design_is_level_aware_without_dropping_bound_points():
    source = {
        "source_question_id": "source_1",
        "title": "来源一",
        "knowledge_points": ["状态函数", "熵变"],
    }
    plan = _normalize_plan(
        {"blueprint": {"exercise_plan": [{}, {}, {}]}},
        count=3,
        planned_types=["填空题", "简答题", "计算题"],
        difficulty="基础到挑战",
        planned_difficulties=["基础", "进阶", "挑战"],
        selected_types=["填空题", "简答题", "计算题"],
        source_files=[],
        source_scope={"mode": "single", "questions": [source]},
        selected_source_questions=[source],
        planned_source_ids=["source_1", "source_1", "source_1"],
        generation_strategy="parallel_exam",
    )

    items = plan["blueprint"]["exercise_plan"]
    assert items[0]["difficulty_levers"] == ["条件直接程度", "提示和解题支架程度"]
    assert "条件识别或转换要求" in items[1]["difficulty_levers"]
    assert "隐含关系识别" in items[2]["difficulty_levers"]
    assert all(item["required_knowledge_points"] == ["状态函数", "熵变"] for item in items)
    assert all("步" not in item["difficulty_rationale"] for item in items)


def test_comprehensive_plan_has_full_coverage_and_count_scaled_cross_source_gate():
    selected = [
        {"source_question_id": f"source_{index}", "title": f"来源{index}", "stem_excerpt": f"材料{index}"}
        for index in range(1, 4)
    ]
    raw = {"blueprint": {"exercise_plan": [
        {"target_skill": f"能力{index}", "variation_type": f"变化{index}", "design_intent": "形成递进"}
        for index in range(5)
    ]}}

    plan = _normalize_plan(
        raw,
        count=5,
        planned_types=["综合题"] * 5,
        difficulty="基础到进阶",
        selected_types=["综合题"],
        source_files=[],
        source_scope={"mode": "question_set", "questions": selected},
        selected_source_questions=selected,
        generation_strategy="targeted_set",
    )

    contract = validate_practice_mode_contract(plan)
    assert contract["status"] == "passed"
    assert contract["mode"] == "comprehensive"
    assert contract["metrics"]["covered_source_count"] == 3
    assert contract["metrics"]["multi_source_count"] >= 1
    assert any(len(item["source_refs"]) >= 2 for item in plan["blueprint"]["exercise_plan"])


def test_comprehensive_plan_warns_for_incomplete_coverage_without_blocking():
    selected = [
        {"source_question_id": "source_1", "knowledge_points": ["知识点A"]},
        {"source_question_id": "source_2", "knowledge_points": ["知识点B"]},
        {"source_question_id": "source_3", "knowledge_points": ["知识点C"]},
    ]
    plan = {
        "source_scope": {"mode": "question_set", "questions": selected},
        "selected_source_questions": selected,
        "scope_cover": {"complete": False, "counts": {"selected_units": 3}},
        "blueprint": {
            "generation_strategy": "targeted_set",
            "training_goal": "优先训练核心知识点",
            "progression": ["核心概念", "知识连接"],
            "exercise_plan": [
                {
                    "plan_item_id": "plan_item_01",
                    "number": 1,
                    "question_type": "综合题",
                    "difficulty": "基础",
                    "difficulty_levers": ["条件直接程度"],
                    "difficulty_rationale": "直接给出两个来源的关键条件。",
                    "target_skill": "关联知识点",
                    "variation_type": "跨来源连接",
                    "design_intent": "优先连接两个核心来源。",
                    "source_refs": ["source_1", "source_2"],
                    "coverage_role": "综合",
                    "required_knowledge_points": ["知识点A", "知识点B"],
                },
                {
                    "plan_item_id": "plan_item_02",
                    "number": 2,
                    "question_type": "简答题",
                    "difficulty": "进阶",
                    "difficulty_levers": ["条件识别或转换要求"],
                    "difficulty_rationale": "要求辨析同一来源中的条件转换。",
                    "target_skill": "解释关键条件",
                    "variation_type": "条件辨析",
                    "design_intent": "巩固核心来源的适用边界。",
                    "source_refs": ["source_1"],
                    "coverage_role": "铺垫",
                    "required_knowledge_points": ["知识点A"],
                },
                {
                    "plan_item_id": "plan_item_03",
                    "number": 3,
                    "question_type": "填空题",
                    "difficulty": "挑战",
                    "difficulty_levers": ["隐含关系识别"],
                    "difficulty_rationale": "识别同一来源的隐含条件。",
                    "target_skill": "判断适用条件",
                    "variation_type": "边界判断",
                    "design_intent": "补强核心来源的适用条件。",
                    "source_refs": ["source_2"],
                    "coverage_role": "迁移",
                    "required_knowledge_points": ["知识点B"],
                },
            ],
        },
    }

    mode_contract = validate_practice_mode_contract(plan)
    audit = audit_practice_blueprint(plan)

    assert mode_contract["status"] == "warning"
    assert mode_contract["errors"] == []
    assert any("确认是否需要补充" in warning for warning in mode_contract["warnings"])
    assert audit["status"] == "warning"
    assert audit["errors"] == []
    assert any("确认是否需要补充" in warning for warning in audit["warnings"])


def test_smaller_comprehensive_generation_contract_allows_partial_source_coverage():
    contract = {
        "total_count": 1,
        "generation_strategy": "targeted_set",
        "difficulty_counts": {"基础": 1, "进阶": 0, "挑战": 0},
        "slots": [{"slot_id": "generation_slot_01", "difficulty": "基础"}],
        "source_coverage": {
            "complete": False,
            "counts": {"selected_units": 3},
        },
    }

    audit = audit_generation_contract(contract)
    assert audit["status"] == "warning"
    assert audit["errors"] == []


def test_single_source_gate_rejects_identical_same_source_variants():
    plan = {
        "source_scope": {"questions": [{"source_question_id": "source_1"}]},
        "selected_source_questions": [{"source_question_id": "source_1"}],
        "blueprint": {
            "generation_strategy": "per_question",
            "exercise_plan": [
                {"source_question_id": "source_1", "source_refs": ["source_1"], "variation_type": "换数字", "target_skill": "计算"},
                {"source_question_id": "source_1", "source_refs": ["source_1"], "variation_type": "换数字", "target_skill": "计算"},
            ],
        },
    }

    contract = validate_practice_mode_contract(plan)

    assert contract["status"] == "failed"
    assert "缺少能力或变化方式差异" in contract["errors"][0]


def test_reference_calculation_variation_rejects_numeric_only_rewrite():
    source = {
        "question_type": "计算题",
        "stem_excerpt": "1 mol 理想气体从 T1=300 K、p1=200 kPa 变到 T2=300 K、p2=100 kPa，求不同途径的 Q、W 和熵变。",
    }
    generated = {
        "stem": "1 mol 理想气体从 T1=400 K、p1=240 kPa 变到 T2=400 K、p2=120 kPa，求不同途径的 Q、W 和熵变。",
    }
    report = validate_reference_calculation_variation(
        source,
        generated,
        {"question_type": "计算题", "structural_change": "改变未知量"},
    )

    assert report["status"] == "failed"
    assert report["same_after_number_mask"] is True


def test_reference_calculation_variation_accepts_changed_problem_structure():
    source = {
        "question_type": "计算题",
        "stem_excerpt": "1 mol 理想气体从 T1=300 K、p1=200 kPa 变到 T2=300 K、p2=100 kPa，求不同途径的 Q、W 和熵变。",
    }
    generated = {
        "stem": "将上述气体置于带活塞的绝热装置中，先自由膨胀再经恒压压缩；求各阶段温度、边界功，并判断总熵变的方向。",
    }
    report = validate_reference_calculation_variation(
        source,
        generated,
        {"question_type": "计算题", "structural_change": "改变求解路径"},
    )

    assert report["status"] == "passed"
    assert report["normalized_similarity"] < 0.97


def test_batch_variation_gate_reports_numeric_only_calculation_item():
    plan = {
        "source_scope": {"questions": [{
            "source_question_id": "source_1",
            "question_type": "计算题",
            "stem_excerpt": "1 mol 理想气体从 T1=300 K、p1=200 kPa 变到 T2=300 K、p2=100 kPa，求不同途径的 Q、W 和熵变。",
        }]},
    }
    batch_plan = [{
        "question_type": "计算题",
        "source_question_id": "source_1",
        "source_refs": ["source_1"],
        "structural_change": "改变未知量",
    }]
    issues = _batch_variation_issues([{
        "batch_index": 1,
        "stem": "1 mol 理想气体从 T1=400 K、p1=240 kPa 变到 T2=400 K、p2=120 kPa，求不同途径的 Q、W 和熵变。",
    }], batch_plan, plan)

    assert len(issues) == 1
    assert issues[0]["status"] == "failed"


def test_generation_retries_when_reference_calculation_only_changes_numbers(monkeypatch):
    source = {
        "source_question_id": "source_1",
        "question_type": "计算题",
        "stem_excerpt": "1 mol 理想气体从 T1=300 K、p1=200 kPa 变到 T2=300 K、p2=100 kPa，求不同途径的 Q、W 和熵变。",
    }
    plan = {
        "source_mode": "exam",
        "source_analysis": {},
        "source_scope": {"mode": "single", "questions": [source]},
        "selected_source_questions": [source],
        "blueprint": {"generation_strategy": "parallel_exam", "exercise_plan": [{
            "plan_item_id": "plan_item_01",
            "source_question_id": "source_1",
            "source_refs": ["source_1"],
            "question_type": "计算题",
            "difficulty": "进阶",
            "target_skill": "建立方程",
            "variation_type": "改变求解路径",
            "structural_change": "改变求解路径",
            "design_intent": "改变路径",
        }]},
    }
    numeric_only = _exercise(
        stem="1 mol 理想气体从 T1=400 K、p1=240 kPa 变到 T2=400 K、p2=120 kPa，求不同途径的 Q、W 和熵变。",
        difficulty="进阶",
    )
    structural = _exercise(
        stem="将上述气体置于带活塞的绝热装置中，先自由膨胀再经恒压压缩；求各阶段温度、边界功，并判断总熵变的方向。",
        difficulty="进阶",
    )
    calls = []

    def fake_call(*_args, **_kwargs):
        calls.append(True)
        return {"exercises": [{"batch_index": 1, **(numeric_only if len(calls) == 1 else structural)}]}

    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda payload, has_images: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    result = generate_practice_from_plan({
        "plan": plan,
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "generation_batch_size": 1,
        "generation_concurrency": 1,
        "question_text": "reference",
    })

    assert len(calls) == 2
    assert result["exercises"][0]["stem"] == structural["stem"]


def test_semantic_batch_context_expands_multiple_sources_without_ids():
    plan = {
        "source_scope": {"title": "综合训练", "questions": [
            {"source_question_id": "source_1", "title": "热力学第一定律", "stem_excerpt": "能量守恒", "knowledge_points": ["内能"]},
            {"source_question_id": "source_2", "title": "熵变", "stem_excerpt": "可逆过程", "knowledge_points": ["熵"]},
        ]},
    }
    context = _semantic_batch_context(
        plan,
        [{"source_question_id": "source_1", "source_refs": ["source_1", "source_2"], "coverage_role": "综合"}],
        knowledge_mode=True,
    )

    assert context["items"][0]["coverage_role"] == "综合"
    assert [item["title"] for item in context["items"][0]["sources"]] == ["热力学第一定律", "熵变"]
    assert "source_1" not in str(context)


def test_formal_generation_injects_only_item_bound_sources_and_type_fields(monkeypatch):
    prompts: list[str] = []
    source_one = {
        "source_question_id": "source_1",
        "title": "填空来源",
        "source_content": "SOURCE_ONE_FULL",
        "knowledge_points": ["概念A"],
    }
    source_two = {
        "source_question_id": "source_2",
        "title": "计算来源",
        "source_content": "SOURCE_TWO_FULL",
        "knowledge_points": ["公式B"],
        "required_constraints": {"essential_formulas": ["B=mc^2"]},
    }
    plan = {
        "source_mode": "exam",
        "source_analysis": {"subject": "材料科学"},
        "source_scope": {"mode": "question_set", "questions": [source_one, source_two]},
        "selected_source_questions": [source_one, source_two],
        "blueprint": {
            "generation_strategy": "parallel_exam",
            "training_goal": "按绑定来源练习",
            "exercise_plan": [
                {
                    "plan_item_id": "plan_item_01", "source_question_id": "source_1", "source_refs": ["source_1"],
                    "question_type": "填空题", "difficulty": "基础", "target_skill": "识别概念A",
                    "variation_type": "直接辨析", "design_intent": "保留概念A", "required_knowledge_points": ["概念A"],
                },
                {
                    "plan_item_id": "plan_item_02", "source_question_id": "source_2", "source_refs": ["source_2"],
                    "question_type": "计算题", "difficulty": "挑战", "target_skill": "应用公式B",
                    "variation_type": "逆向求解", "design_intent": "保留公式B", "required_knowledge_points": ["公式B"],
                },
            ],
        },
    }

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[1]["content"])
        prompts.append(prompt)
        if "SOURCE_ONE_FULL" in prompt:
            return {"exercises": [{"batch_index": 1, **_exercise(question_type="填空题", difficulty="基础", knowledge_points=["概念A"])}]}
        return {"exercises": [{"batch_index": 1, **_exercise(question_type="计算题", difficulty="挑战", knowledge_points=["公式B"])}]}

    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda _payload, _has_images: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    generate_practice_from_plan({
        "plan": plan,
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "question_text": "UNBOUND_RAW_MATERIAL",
        "generation_batch_size": 1,
        "generation_concurrency": 1,
    })

    fill_prompt = next(prompt for prompt in prompts if "SOURCE_ONE_FULL" in prompt)
    calculation_prompt = next(prompt for prompt in prompts if "SOURCE_TWO_FULL" in prompt)
    assert "SOURCE_ONE_FULL" in fill_prompt and "SOURCE_TWO_FULL" not in fill_prompt
    assert "SOURCE_TWO_FULL" in calculation_prompt and "SOURCE_ONE_FULL" not in calculation_prompt
    assert "UNBOUND_RAW_MATERIAL" not in "".join(prompts)
    assert '"options"' not in fill_prompt and '"figures"' not in fill_prompt and "\\\\mathrm" not in fill_prompt
    assert '"formulas"' in calculation_prompt
    assert '"difficulty_intent"' in fill_prompt
    assert '"candidate_mechanisms"' in fill_prompt and '"selection_rule"' in calculation_prompt
    assert '"difficulty_evidence"' in fill_prompt
    assert "整套差异合同" in fill_prompt and "same_source_peer_designs" in calculation_prompt
    assert "verification_note 仅记录题干条件充分性检查" in fill_prompt
    assert "不得包含答案、结论、推导或解题过程" in calculation_prompt


def test_blueprint_default_upgrade_refreshes_difficulty_design_when_level_changes():
    plan = {
        "source_scope": {"questions": []},
        "blueprint": {"exercise_plan": [{
            "plan_item_id": "plan_item_01",
            "difficulty": "挑战",
            "difficulty_design_level": "基础",
            "question_type": "计算题",
            "target_skill": "建立热力学模型",
            "structural_change": "逆向求解",
            "variation_type": "逆向求解",
            "design_intent": "通过逆向求解检验建模能力",
            "difficulty_levers": ["条件直接程度", "提示和解题支架程度"],
            "difficulty_rationale": "条件表达更直接，并提供必要提示或支架。",
        }]},
    }

    upgraded = ensure_practice_blueprint_defaults(plan)
    item = upgraded["blueprint"]["exercise_plan"][0]

    assert item["difficulty_design_level"] == "挑战"
    assert "隐含关系识别" in item["difficulty_levers"]
    assert "条件表达更直接" not in item["difficulty_rationale"]


def test_regenerate_plan_item_uses_one_call_and_enforces_hard_change(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, _provider):
            pass

        def chat_text(self, messages, **kwargs):
            calls.append({"messages": messages, "kwargs": kwargs})
            return SimpleNamespace(content='{"plan_item":{"question_type":"计算题","difficulty":"进阶","target_skill":"两步建模","variation_type":"跨条件推导","design_intent":"改变情境并补充边界条件"},"applied_changes":[{"constraint_id":"must_change/1","evidence":"题型改为计算题"}]}')

    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda payload, has_images: (SimpleNamespace(name="fake"), "fake-model"))
    plan = {
        "source_scope": {"questions": [
            {"source_question_id": "source_1", "title": "能量原题", "stem_excerpt": "已知状态量求结果"},
            {"source_question_id": "source_2", "title": "熵变原题", "stem_excerpt": "可逆过程的熵变"},
        ]},
        "blueprint": {"exercise_plan": [
            {"number": 1, "plan_item_id": "plan_item_01", "source_question_id": "source_1", "source_refs": ["source_1", "source_2"], "coverage_role": "综合", "question_type": "简答题", "difficulty": "进阶", "target_skill": "概念说明", "variation_type": "直接问答", "design_intent": "检查概念"},
            {"number": 2, "plan_item_id": "plan_item_02", "source_question_id": "source_1", "question_type": "计算题", "target_skill": "已有能力", "variation_type": "参数变化"},
        ]},
    }

    result = regenerate_plan_item({"plan": plan, "plan_index": 0, "revision_spec": {"must_change": ["question_type", "target_skill"], "forbid": ["背诵"], "note": "增强推导"}})

    assert len(calls) == 1
    assert result["hard_checks"]["status"] == "passed"
    assert result["request_evidence"]["call_count"] == 1
    assert result["request_evidence"]["occupied_summary_count"] == 1
    assert result["plan_item"]["source_question_id"] == "source_1"
    assert result["plan_item"]["source_refs"] == ["source_1", "source_2"]
    assert "增强推导" in calls[0]["messages"][1]["content"]
    assert "能量原题" in calls[0]["messages"][1]["content"]
    assert "熵变原题" in calls[0]["messages"][1]["content"]
    assert "plan_item_id" not in str(result["request_evidence"]["prompt_snapshot"])
    assert "source_1" not in str(result["request_evidence"]["prompt_snapshot"])


def test_regenerate_plan_item_refreshes_difficulty_design_when_level_changes(monkeypatch):
    class FakeClient:
        def __init__(self, _provider):
            pass

        def chat_text(self, _messages, **_kwargs):
            return SimpleNamespace(content='{"plan_item":{"question_type":"计算题","difficulty":"挑战","target_skill":"两步建模","variation_type":"逆向求解","design_intent":"改变求解方向"}}')

    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda _payload, _has_images: (SimpleNamespace(name="fake"), "fake-model"))
    plan = {"source_scope": {"questions": []}, "blueprint": {"exercise_plan": [{
        "number": 1,
        "plan_item_id": "plan_item_01",
        "source_question_id": "source_1",
        "question_type": "计算题",
        "difficulty": "基础",
        "difficulty_design_level": "基础",
        "target_skill": "建立方程",
        "variation_type": "直接求解",
        "structural_change": "改变未知量",
        "design_intent": "基础计算",
        "difficulty_levers": ["条件直接程度", "提示和解题支架程度"],
        "difficulty_rationale": "条件表达更直接，并提供必要提示或支架。",
    }]}}

    result = regenerate_plan_item({"plan": plan, "plan_index": 0, "revision_spec": {"must_change": ["difficulty"], "forbid": []}})

    assert result["plan_item"]["difficulty_design_level"] == "挑战"
    assert "隐含关系识别" in result["plan_item"]["difficulty_levers"]
    assert "条件表达更直接" not in result["plan_item"]["difficulty_rationale"]


def test_regenerate_plan_item_rejects_unchanged_required_field(monkeypatch):
    class FakeClient:
        def __init__(self, _provider):
            pass

        def chat_text(self, messages, **kwargs):
            return SimpleNamespace(content='{"plan_item":{"question_type":"简答题","difficulty":"进阶","target_skill":"概念说明","variation_type":"新变化","design_intent":"新意图"}}')

    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda payload, has_images: (SimpleNamespace(name="fake"), "fake-model"))
    plan = {"source_scope": {"questions": []}, "blueprint": {"exercise_plan": [{"number": 1, "plan_item_id": "plan_item_01", "source_question_id": "source_1", "question_type": "简答题", "difficulty": "进阶", "target_skill": "概念说明", "variation_type": "旧变化", "design_intent": "旧意图"}]}}

    with pytest.raises(ValueError, match="要求改变“题型”"):
        regenerate_plan_item({"plan": plan, "plan_index": 0, "revision_spec": {"must_change": ["question_type"]}})


def test_confirmed_comprehensive_planning_uses_compact_catalog_and_one_default_call(monkeypatch):
    captured = {}
    scope_rows = [
        {"source_question_id": "source_1", "title": "来源一", "stem_excerpt": "片段一", "source_content": "CATALOG_SOURCE_CONTENT_ONE", "knowledge_points": ["知识一"]},
        {"source_question_id": "source_2", "title": "来源二", "stem_excerpt": "片段二", "source_content": "CATALOG_SOURCE_CONTENT_TWO", "knowledge_points": ["知识二"]},
    ]

    def fake_call(_client, messages, **kwargs):
        captured["prompt"] = messages[-1]["content"]
        return {"blueprint": {"training_goal": "综合掌握", "exercise_plan": [
            {"target_skill": f"能力{index}", "variation_type": f"变化{index}", "design_intent": "形成覆盖", "source_refs": [f"S{1 + index % 2}"]}
            for index in range(5)
        ]}}

    monkeypatch.setattr(exercise_generation, "_primary_model_runtime", lambda payload: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda payload, has_images: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    plan = plan_practice_set({
        "source_mode": "knowledge",
        "generation_strategy": "knowledge_overall",
        "strategy_count": 5,
        "question_text": "FULL_MATERIAL_SHOULD_NOT_REPEAT" * 500,
        "source_scope": {"mode": "question_set", "title": "知识目录", "questions": scope_rows},
        "source_analysis": {"subject": "物理化学", "knowledge_points": ["知识一", "知识二"]},
        "selected_source_questions": scope_rows,
        "question_types": ["综合题"],
    })

    assert "FULL_MATERIAL_SHOULD_NOT_REPEAT" not in captured["prompt"]
    assert captured["prompt"].count("CATALOG_SOURCE_CONTENT_ONE") == 1
    assert captured["prompt"].count("CATALOG_SOURCE_CONTENT_TWO") == 1
    assert "用户确认的知识范围：来源目录中的 S1、S2（共 2 项）" in captured["prompt"]
    assert plan["planning_evidence"]["default_call_count"] == 1
    assert plan["planning_evidence"]["material_char_count"] == 0
    assert plan["planning_evidence"]["source_catalog_count"] == 2
    assert plan["generation"]["model_route"] == "selected_primary"
    assert plan["mode_contract"]["status"] == "passed"


def test_large_blueprint_uses_global_allocation_then_retryable_unit_batches(monkeypatch):
    calls = []
    failed_once = {"value": False}

    def fake_call(_client, messages, **kwargs):
        prompt = messages[-1]["content"]
        calls.append(prompt)
        if "只做全局槽位分配" in prompt:
            return {
                "blueprint": {
                    "training_goal": "掌握动力学方法",
                    "progression": ["基础到迁移"],
                    "design_notes": ["保持差异"],
                    "exercise_plan": [
                        {"source_refs": ["S1"], "target_skill": "初始能力", "variation_type": "初始变化"}
                        for _ in range(6)
                    ],
                }
            }
        ids = __import__("re").findall(r"plan_item_\d+", prompt)
        ids = list(dict.fromkeys(ids))
        if not failed_once["value"]:
            failed_once["value"] = True
            raise RuntimeError("temporary provider failure")
        return {
            "plan_items": [
                {
                    "plan_item_id": item_id,
                    "target_skill": f"能力{item_id[-2:]}",
                    "variation_type": f"变化{item_id[-2:]}",
                    "structural_change": "改变求解路径",
                    "design_intent": "以不同路径检验指定能力。",
                    "difficulty_levers": ["方法选择与组合要求"],
                    "difficulty_rationale": "要求选择合适的方法并完成组合。",
                }
                for item_id in ids
            ]
        }

    selected = [{
        "source_question_id": "source_01",
        "number": "1",
        "title": "动力学原题",
        "stem_excerpt": "反应动力学",
        "knowledge_points": ["稳态近似"],
    }]
    monkeypatch.setattr(exercise_generation, "_primary_model_runtime", lambda payload: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda payload, has_images: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    plan = plan_practice_set({
        "source_mode": "exam",
        "generation_strategy": "targeted_set",
        "strategy_count": 6,
        "question_types": ["简答题"],
        "question_text": "动力学原题材料。",
        "selected_source_questions": selected,
        "source_scope": {"mode": "question_set", "questions": selected},
        "source_analysis": {"subject": "物理化学", "knowledge_points": ["稳态近似"]},
        "blueprint_concurrency": 1,
    })

    evidence = plan["planning_evidence"]
    assert evidence["adaptive_blueprint"] is True
    assert evidence["global_allocation_call_count"] == 1
    assert evidence["default_call_count"] == 4  # 1 global + 2 batches + 1 automatic retry
    assert plan["blueprint_refinement"]["units"][0]["call_batches"][0]["status"] == "completed_after_retry"
    assert plan["blueprint_audit"]["status"] == "passed"
    assert len({exercise_generation._blueprint_signature(item) for item in plan["blueprint"]["exercise_plan"]}) == 6


def test_blueprint_units_keep_logical_knowledge_work_together_but_bound_call_size():
    plan = {
        "source_mode": "knowledge",
        "blueprint": {
            "exercise_plan": [
                {"plan_item_id": f"plan_item_{index:02d}", "question_type": "简答题", "source_refs": ["knowledge_a"]}
                for index in range(1, 7)
            ] + [
                {"plan_item_id": "plan_item_07", "question_type": "作图题", "source_refs": ["knowledge_b"]},
                {"plan_item_id": "plan_item_08", "question_type": "计算题", "source_refs": ["knowledge_b"]},
                {"plan_item_id": "plan_item_09", "question_type": "综合题", "source_refs": ["knowledge_a", "knowledge_b"]},
            ]
        },
    }

    units = exercise_generation.build_blueprint_generation_units(plan)
    by_id = {unit["unit_id"]: unit for unit in units}
    assert len(units) == 3
    assert [batch["size"] for batch in by_id["unit_source_knowledge_a"]["call_batches"]] == [5, 1]
    assert [batch["size"] for batch in by_id["unit_source_knowledge_b"]["call_batches"]] == [2]
    assert by_id["unit_cross_knowledge_a_knowledge_b"]["plan_item_ids"] == ["plan_item_09"]


def test_blueprint_required_knowledge_points_preserve_all_selected_source_points():
    selected = [
        {
            "source_question_id": "source_1",
            "title": "来源一",
            "stem_excerpt": "题干一",
            "question_type": "简答题",
            "knowledge_points": ["热力学第一定律", "状态函数"],
        },
        {
            "source_question_id": "source_2",
            "title": "来源二",
            "stem_excerpt": "题干二",
            "question_type": "计算题",
            "knowledge_points": ["熵变", "可逆过程"],
        },
    ]

    comprehensive = _normalize_plan(
        {"source_analysis": {"knowledge_points": ["热力学第一定律", "状态函数", "熵变", "可逆过程"]}, "blueprint": {"exercise_plan": [{}, {}, {}, {}, {}]}},
        count=5,
        planned_types=["简答题"] * 5,
        difficulty="基础到进阶",
        selected_types=["简答题"],
        source_files=[],
        source_scope={"mode": "question_set", "questions": selected},
        selected_source_questions=selected,
        generation_strategy="targeted_set",
    )
    comprehensive_points = {
        point
        for item in comprehensive["blueprint"]["exercise_plan"]
        for point in item["required_knowledge_points"]
    }
    assert comprehensive_points == {"热力学第一定律", "状态函数", "熵变", "可逆过程"}
    assert all(
        set(item["required_knowledge_points"]) == {
            point
            for source_id in item["source_refs"]
            for source in selected
            if source["source_question_id"] == source_id
            for point in source["knowledge_points"]
        }
        for item in comprehensive["blueprint"]["exercise_plan"]
    )

    for strategy, source_ids in (
        ("parallel_exam", ["source_1", "source_2"]),
        ("per_question", ["source_1", "source_1", "source_2", "source_2"]),
    ):
        plan = _normalize_plan(
            {"blueprint": {"exercise_plan": [{} for _ in source_ids]}},
            count=len(source_ids),
            planned_types=["简答题"] * len(source_ids),
            difficulty="基础到进阶",
            selected_types=["简答题"],
            source_files=[],
            source_scope={"mode": "question_set", "questions": selected},
            selected_source_questions=selected,
            planned_source_ids=source_ids,
            generation_strategy=strategy,
        )
        for item, source_id in zip(plan["blueprint"]["exercise_plan"], source_ids):
            expected = next(source["knowledge_points"] for source in selected if source["source_question_id"] == source_id)
            assert item["required_knowledge_points"] == expected


def test_comprehensive_blueprint_keeps_its_knowledge_point_subset_and_bound_constraints():
    selected = [
        {
            "source_question_id": "source_1",
            "title": "来源一",
            "stem_excerpt": "题干一",
            "question_type": "简答题",
            "knowledge_points": ["A", "B"],
            "required_constraints": {
                "essential_definitions": ["A/B 的定义"],
                "essential_formulas": ["公式一"],
                "applicable_boundaries": ["边界一"],
            },
        },
        {
            "source_question_id": "source_2",
            "title": "来源二",
            "stem_excerpt": "题干二",
            "question_type": "计算题",
            "knowledge_points": ["C", "D"],
            "required_constraints": {
                "essential_definitions": ["C/D 的定义"],
                "essential_formulas": ["公式二"],
                "applicable_boundaries": ["边界二"],
            },
        },
    ]
    raw_plan = [
        {"source_refs": ["S1", "S2"], "required_knowledge_points": ["A", "C"]},
        {"source_refs": ["S1"], "required_knowledge_points": ["B"]},
        {"source_refs": ["S2"], "required_knowledge_points": ["D"]},
        {"source_refs": ["S1"], "required_knowledge_points": ["A"]},
        {"source_refs": ["S1", "S2"], "required_knowledge_points": ["B", "D"]},
    ]

    plan = _normalize_plan(
        {"source_analysis": {"knowledge_points": ["A", "B", "C", "D"]}, "blueprint": {"exercise_plan": raw_plan}},
        count=5,
        planned_types=["简答题"] * 5,
        difficulty="基础到进阶",
        selected_types=["简答题"],
        source_files=[],
        source_scope={"mode": "question_set", "questions": selected},
        selected_source_questions=selected,
        generation_strategy="targeted_set",
    )

    first = plan["blueprint"]["exercise_plan"][0]
    assert first["required_knowledge_points"] == ["A", "C"]
    assert first["required_constraints"] == {
        "essential_definitions": ["A/B 的定义", "C/D 的定义"],
        "essential_formulas": ["公式一", "公式二"],
        "applicable_boundaries": ["边界一", "边界二"],
    }
    assert plan["blueprint_audit"]["status"] != "blocked"


def test_comprehensive_blueprint_preserves_its_own_constraints_before_source_fallback():
    selected = [
        {
            "source_question_id": "source_1",
            "title": "来源一",
            "knowledge_points": ["A", "B"],
            "required_constraints": {
                "essential_definitions": ["A/B 的定义"],
                "essential_formulas": ["公式一"],
                "applicable_boundaries": ["边界一"],
            },
        },
        {
            "source_question_id": "source_2",
            "title": "来源二",
            "knowledge_points": ["C", "D"],
            "required_constraints": {
                "essential_definitions": ["C/D 的定义"],
                "essential_formulas": ["公式二"],
                "applicable_boundaries": ["边界二"],
            },
        },
    ]
    own_constraints = {
        "essential_definitions": ["A 与 C 的共同定义"],
        "essential_formulas": ["公式 AC"],
        "applicable_boundaries": ["仅适用 AC 情境"],
    }

    comprehensive = _normalize_plan(
        {"blueprint": {"exercise_plan": [{
            "source_refs": ["S1", "S2"],
            "required_knowledge_points": ["A", "C"],
            "required_constraints": own_constraints,
        }]}},
        count=1,
        planned_types=["综合题"],
        difficulty="挑战",
        selected_types=["综合题"],
        source_files=[],
        source_scope={"mode": "question_set", "questions": selected},
        selected_source_questions=selected,
        generation_strategy="targeted_set",
    )
    fallback = _normalize_plan(
        {"blueprint": {"exercise_plan": [{"source_refs": ["S1", "S2"]}]}},
        count=1,
        planned_types=["综合题"],
        difficulty="挑战",
        selected_types=["综合题"],
        source_files=[],
        source_scope={"mode": "question_set", "questions": selected},
        selected_source_questions=selected,
        generation_strategy="targeted_set",
    )
    parallel = _normalize_plan(
        {"blueprint": {"exercise_plan": [{
            "source_refs": ["S1"],
            "required_constraints": own_constraints,
        }]}},
        count=1,
        planned_types=["简答题"],
        difficulty="进阶",
        selected_types=["简答题"],
        source_files=[],
        source_scope={"mode": "single", "questions": [selected[0]]},
        selected_source_questions=[selected[0]],
        generation_strategy="parallel_exam",
    )

    assert comprehensive["blueprint"]["exercise_plan"][0]["required_constraints"] == own_constraints
    assert fallback["blueprint"]["exercise_plan"][0]["required_constraints"]["essential_formulas"] == ["公式一", "公式二"]
    assert parallel["blueprint"]["exercise_plan"][0]["required_constraints"]["essential_formulas"] == ["公式一"]


def test_formal_generation_switch_omits_source_content_and_upgrades_legacy_blueprint(monkeypatch):
    captured_prompts = []
    selected_runtime_calls = []

    def fake_call(_client, messages, **_kwargs):
        captured_prompts.append(messages[-1]["content"])
        return {"exercises": [{"batch_index": 1, **_exercise(
            question_type="简答题",
            difficulty="进阶",
            knowledge_points=["内能", "熵变"],
        )}]}

    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (selected_runtime_calls.append(True) or SimpleNamespace(name="fake", supports_vision=False), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    def legacy_plan() -> dict:
        source = {
            "source_question_id": "source_1",
            "title": "私有原题标题",
            "stem_excerpt": "PRIVATE_SOURCE_STEM",
            "question_type": "简答题",
            "source_difficulty": "进阶",
            "knowledge_points": ["内能", "熵变"],
            "required_constraints": {"essential_definitions": ["仅来源一的定义"]},
        }
        return {
            "source_mode": "exam",
            "source_analysis": {"knowledge_points": ["内能", "熵变"], "essential_definitions": ["其它来源的定义"]},
            "source_scope": {"mode": "single", "questions": [source]},
            "selected_source_questions": [source],
            "blueprint": {
                "training_goal": "热力学训练",
                "generation_strategy": "parallel_exam",
                "exercise_plan": [{
                    "plan_item_id": "plan_item_01",
                    "source_question_id": "source_1",
                    "source_refs": ["source_1"],
                    "question_type": "简答题",
                    "difficulty": "进阶",
                    "target_skill": "热力学推理",
                    "variation_type": "改变边界条件",
                    "design_intent": "保持知识组合",
                }],
            },
        }

    disabled = generate_practice_from_plan({
        "plan": legacy_plan(),
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "include_source_content_in_generation": False,
        "question_text": "PRIVATE_RAW_MATERIAL",
        "generation_batch_size": 1,
        "generation_concurrency": 1,
    })
    disabled_prompt = captured_prompts[-1]
    assert "PRIVATE_SOURCE_STEM" not in str(disabled_prompt)
    assert "PRIVATE_RAW_MATERIAL" not in str(disabled_prompt)
    assert "其它来源的定义" not in str(disabled_prompt)
    assert "仅来源一的定义" in str(disabled_prompt)
    assert "抽象知识与边界约束" in str(disabled_prompt)
    assert disabled["blueprint"]["exercise_plan"][0]["required_knowledge_points"] == ["内能", "熵变"]
    assert disabled["include_source_content_in_generation"] is False
    assert disabled["generation"]["include_source_content_in_generation"] is False
    assert disabled["generation"]["model_route"] == "selected_primary"
    assert selected_runtime_calls

    enabled = generate_practice_from_plan({
        "plan": legacy_plan(),
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "include_source_content_in_generation": True,
        "question_text": "PRIVATE_RAW_MATERIAL",
        "generation_batch_size": 1,
        "generation_concurrency": 1,
    })
    assert "PRIVATE_SOURCE_STEM" in str(captured_prompts[-1])
    assert "PRIVATE_RAW_MATERIAL" in str(captured_prompts[-1])
    assert enabled["include_source_content_in_generation"] is True


def test_enabled_generation_uses_selected_primary_and_confirmed_material_without_visual_fallback(monkeypatch):
    captured = {}
    source = {
        "source_question_id": "source_1",
        "title": "来源一",
        "stem_excerpt": "摘要",
        "question_type": "简答题",
        "knowledge_points": ["内能"],
    }
    plan = {
        "source_mode": "exam",
        "source_scope": {"mode": "single", "questions": [source]},
        "selected_source_questions": [source],
        "blueprint": {"generation_strategy": "parallel_exam", "exercise_plan": [{
            "plan_item_id": "plan_item_01", "source_question_id": "source_1", "source_refs": ["source_1"],
            "question_type": "简答题", "difficulty": "进阶", "target_skill": "热力学推理",
            "variation_type": "改变边界条件", "design_intent": "读取图示并保持知识组合", "requires_figure": True,
        }]},
    }

    monkeypatch.setattr(
        exercise_generation,
        "parse_practice_sources",
        lambda _payload: {
            "text": "FULL_SOURCE_TEXT_WITH_FORMULA_AND_TABLE",
            "images": [],
            "reference_images": ["data:image/png;base64,ZmFrZQ=="],
            "file_names": [],
        },
    )
    def fake_runtime(_payload):
        captured["selected_primary"] = True
        return SimpleNamespace(name="fake", supports_vision=False), "fake-model"

    monkeypatch.setattr(exercise_generation, "_primary_model_runtime", fake_runtime)
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())

    def fake_call(_client, messages, **_kwargs):
        captured["content"] = messages[-1]["content"]
        return {"exercises": [{"batch_index": 1, **_exercise(
            question_type="简答题",
            difficulty="进阶",
            knowledge_points=["内能"],
            stem="根据图示说明内能变化。",
            figures=[{"series": [{"name": "内能", "points": [[0, 0], [1, 1]]}]}],
        )}]}

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    generate_practice_from_plan({"plan": plan, "source_mode": "exam", "generation_strategy": "parallel_exam"})

    assert captured["selected_primary"] is True
    assert isinstance(captured["content"], str)
    assert "FULL_SOURCE_TEXT_WITH_FORMULA_AND_TABLE" in captured["content"]


def test_generation_rejects_output_missing_required_knowledge_points(monkeypatch):
    calls = []
    source = {
        "source_question_id": "source_1",
        "title": "热力学来源",
        "stem_excerpt": "完整来源题干",
        "question_type": "简答题",
        "knowledge_points": ["内能", "熵变"],
    }
    plan = {
        "source_mode": "exam",
        "source_scope": {"mode": "single", "questions": [source]},
        "selected_source_questions": [source],
        "blueprint": {"generation_strategy": "parallel_exam", "exercise_plan": [{
            "plan_item_id": "plan_item_01",
            "source_question_id": "source_1",
            "source_refs": ["source_1"],
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": "热力学推理",
            "variation_type": "改变边界条件",
            "design_intent": "保持知识组合",
        }]},
    }

    def fake_call(*_args, **_kwargs):
        calls.append(True)
        return {"exercises": [{"batch_index": 1, **_exercise(
            question_type="简答题",
            difficulty="进阶",
            knowledge_points=["内能"],
        )}]}

    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda _payload, _has_images: (SimpleNamespace(name="fake"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    result = generate_practice_from_plan({
        "plan": plan,
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "question_text": "原始题目材料",
        "generation_batch_size": 1,
        "generation_concurrency": 1,
    })

    assert len(calls) == 2
    assert result["exercises"][0]["generation_status"] == "failed"
    assert result["quality"]["status"] == "blocked"


def test_single_question_regeneration_uses_source_switch_without_leaking_source_text(monkeypatch):
    captured = {}
    source = {
        "source_question_id": "source_1",
        "title": "私有来源",
        "stem_excerpt": "PRIVATE_REGEN_SOURCE_STEM",
        "question_type": "简答题",
        "knowledge_points": ["内能", "熵变"],
    }
    practice = {
        "source_mode": "exam",
        "source_analysis": {"knowledge_points": ["内能", "熵变"]},
        "source_scope": {"mode": "single", "questions": [source]},
        "selected_source_questions": [source],
        "blueprint": {"exercise_plan": [{
            "plan_item_id": "plan_item_01",
            "source_question_id": "source_1",
            "required_knowledge_points": ["内能", "熵变"],
        }]},
        "exercises": [{
            "exercise_id": "practice_01",
            "plan_item_id": "plan_item_01",
            "source_question_id": "source_1",
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": "热力学推理",
            "variation_type": "改变边界条件",
            "stem": "当前题目",
            "knowledge_points": ["内能", "熵变"],
            "verification_note": "条件充分。",
        }],
    }

    def fake_call(_client, messages, **_kwargs):
        captured["prompt"] = messages[-1]["content"]
        return {"exercises": [_exercise(
            question_type="简答题",
            difficulty="进阶",
            knowledge_points=["内能", "熵变"],
        )]}

    monkeypatch.setattr(exercise_generation, "_model_runtime", lambda _payload, has_images: (SimpleNamespace(name=f"fake-{has_images}"), "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    result = exercise_generation.regenerate_practice_exercise({
        "practice": practice,
        "exercise_index": 0,
        "source_mode": "exam",
        "include_source_content_in_generation": False,
        "question_text": "PRIVATE_REGEN_RAW_MATERIAL",
    })

    assert "PRIVATE_REGEN_SOURCE_STEM" not in str(captured["prompt"])
    assert "PRIVATE_REGEN_RAW_MATERIAL" not in str(captured["prompt"])
    assert result["exercise"]["knowledge_points"] == ["内能", "熵变"]
    assert result["include_source_content_in_generation"] is False


def test_single_variant_regeneration_keeps_parent_blueprint_identity(monkeypatch):
    parent_item = {
        "plan_item_id": "plan_item_01",
        "question_type": "简答题",
        "difficulty": "进阶",
        "target_skill": "理解状态函数",
        "variation_type": "概念应用",
        "design_intent": "从多种情境理解状态函数。",
        "required_knowledge_points": ["状态函数"],
    }
    exercises = [
        {
            "exercise_id": f"practice_{index:02d}",
            "plan_item_id": f"variant_plan_01_{index:02d}",
            "parent_plan_item_id": "plan_item_01",
            "variant_id": f"plan_item_01::variant::{index}",
            "variant_index": index,
            "variant_count": 3,
            "variant_mode": "progressive",
            "variant_role": role,
            "source_question_id": "",
            "question_type": "简答题",
            "difficulty": difficulty,
            "target_skill": "理解状态函数",
            "variation_type": role,
            "stem": (
                "判断封闭容器的内能变化是否只取决于始末态。"
                if index == 1 else
                "比较两条路径下焓变的测量结果并解释路径无关性。"
                if index == 2 else
                "设计一项实验来区分状态函数与过程量。"
            ),
            "options": [],
            "knowledge_points": ["状态函数"],
            "verification_note": "条件充分。",
            "formulas": [],
            "tables": [],
            "figures": [],
            "generation_status": "completed",
            "generation_error": {},
        }
        for index, (role, difficulty) in enumerate(
            (("基础巩固", "基础"), ("条件转换", "进阶"), ("综合迁移", "挑战")),
            start=1,
        )
    ]
    practice = {
        "source_mode": "knowledge",
        "source_analysis": {"knowledge_points": ["状态函数"]},
        "blueprint": {
            "training_goal": "理解状态函数",
            "exercise_plan": [parent_item],
            "multi_question": {
                "enabled": True,
                "variants_per_item": 3,
                "mode": "progressive",
                "base_item_count": 1,
                "total_count": 3,
            },
        },
        "blueprint_multi_question": {
            "enabled": True,
            "variants_per_item": 3,
            "mode": "progressive",
            "base_item_count": 1,
            "total_count": 3,
        },
        "requested_count": 3,
        "exercises": exercises,
    }

    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake", supports_vision=False), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json",
        lambda *_args, **_kwargs: {"exercises": [_exercise(
            question_type="简答题",
            difficulty="基础",
            knowledge_points=["状态函数"],
            stem="重新生成后的条件转换型状态函数题目。",
        )]},
    )

    result = exercise_generation.regenerate_practice_exercise({
        "practice": practice,
        "exercise_index": 1,
        "source_mode": "knowledge",
        "include_source_content_in_generation": False,
    })

    regenerated = result["exercise"]
    assert regenerated["exercise_id"] == "practice_02"
    assert regenerated["plan_item_id"] == "variant_plan_01_02"
    assert regenerated["parent_plan_item_id"] == "plan_item_01"
    assert regenerated["variant_index"] == 2
    assert regenerated["variant_role"] == "条件转换"
    assert regenerated["difficulty"] == "进阶"
    assert result["quality"]["status"] == "passed"
