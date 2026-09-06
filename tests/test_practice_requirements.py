import pytest

from app.practice_requirements import practice_user_focus
from app.practice_store import _compact_request


@pytest.mark.parametrize("mode", ["exam", "knowledge"])
def test_original_requirements_survive_plan_generation_review_and_recovery(mode):
    focus = "保留原要求\r\n" + "完整约束；" * 300 + "\n只对填空题适用。"
    request = _compact_request({"source_mode": mode, "focus": focus})
    plan = {"focus": practice_user_focus(request)}
    generated = {"focus": practice_user_focus({}, plan)}
    assert practice_user_focus({}, generated) == focus
    assert practice_user_focus({"focus": ""}, generated) == focus
    assert practice_user_focus({"focus": "新要求"}, generated) == "新要求"
    assert request["focus"] == focus


def test_missing_historical_requirements_are_not_invented():
    assert practice_user_focus({}, {}) == ""


def test_disk_history_roundtrip_preserves_requirements(tmp_path, monkeypatch):
    from app import practice_store

    monkeypatch.setattr(practice_store, "PRACTICE_HISTORY_DIR", tmp_path)
    focus = "原始要求\r\n" * 250 + "末尾要求仍须保留"
    record = practice_store.save_practice_record({"exercises": [], "focus": focus}, request={"focus": focus})
    loaded = practice_store.load_practice_record(record["history_id"])
    assert loaded["request"]["focus"] == focus
    assert loaded["data"]["focus"] == focus


def test_comprehensive_explicit_empty_constraints_survive_recovery():
    from app.exercise_generation import _required_constraints_for_plan_item
    sources = [{"source_question_id": "s1", "required_constraints": {"essential_formulas": ["unrelated"], "applicable_boundaries": ["boundary"]}}]
    for strategy in ("targeted_set", "knowledge_overall"):
        selected = _required_constraints_for_plan_item(["s1"], sources, {}, strategy)
        assert not any(selected.values())
        assert _required_constraints_for_plan_item(["s1"], sources, selected, strategy) == selected
        assert _required_constraints_for_plan_item(["s1"], sources, None, strategy)["essential_formulas"] == ["unrelated"]
    assert _required_constraints_for_plan_item(["s1"], sources, {}, "per_question")["essential_formulas"] == ["unrelated"]


def test_verbatim_fill_is_allowed_but_duplicate_fill_questions_are_rejected():
    from app.exercise_generation import practice_diversity_issues
    source = "材料的组织结构由晶体内部原子排列及其相互作用决定，材料的宏观物理性质与组织结构紧密相关。"
    stem = source.replace("组织结构", "____", 1)
    practice = {"generation_strategy": "knowledge_overall", "selected_source_questions": [{"source_question_id": "s1", "source_content": source}], "exercises": [{"number": 1, "source_question_id": "s1", "question_type": "填空题", "stem": stem}]}
    assert not practice_diversity_issues(practice)
    practice["exercises"].append({**practice["exercises"][0], "number": 2})
    assert any(x["code"] == "set_diversity_collision" for x in practice_diversity_issues(practice))


def test_full_planning_schema_and_refinement_schema_match_their_consumers():
    import pytest
    from pydantic import ValidationError

    from app.model_output_contracts import PracticePlanningOutput, PracticePlanningRefinementOutput
    item = {"source_refs": ["S2"], "target_skill": "保留模型选择"}
    assert PracticePlanningOutput.model_validate({"blueprint": {"requirements_contract": {"wording": "unconstrained", "cross_source": "default"}, "exercise_plan": [item]}}).blueprint.exercise_plan == [item]
    with pytest.raises(ValidationError):
        PracticePlanningOutput.model_validate({"exercise_plan": [item]})
    assert PracticePlanningRefinementOutput.model_validate({"plan_items": [item]}).plan_items == [item]


def test_legacy_top_level_plan_is_not_silently_replaced_by_defaults():
    import pytest

    from app.exercise_generation import _normalize_plan
    item = {"source_refs": ["S2"], "target_skill": "指定的技能", "design_intent": "原句挖空", "required_knowledge_points": ["B"], "required_constraints": {}}
    kwargs = dict(count=1, planned_types=["填空题"], difficulty="基础", selected_types=["填空题"], source_files=[], selected_source_questions=[{"source_question_id": "s1", "knowledge_points": ["A"]}, {"source_question_id": "s2", "knowledge_points": ["B"]}], generation_strategy="targeted_set")
    plan = _normalize_plan({"exercise_plan": [item], "blueprint": {"training_goal": "保持原句"}}, **kwargs)
    recovered = plan["blueprint"]["exercise_plan"][0]
    assert recovered["source_question_id"] == "s2"
    assert recovered["target_skill"] == "指定的技能"
    assert recovered["design_intent"] == "原句挖空"
    assert not any(recovered["required_constraints"].values())
    with pytest.raises(ValueError, match="不一致"):
        _normalize_plan({"exercise_plan": [item], "blueprint": {"exercise_plan": [{**item, "target_skill": "另一项"}]}}, **kwargs)


def test_request_boundary_selects_full_plan_or_refinement_schema(monkeypatch):
    from app import exercise_generation
    from app.model_output_contracts import PracticePlanningOutput, PracticePlanningRefinementOutput
    expected = iter([
        (PracticePlanningOutput, {"blueprint": {"requirements_contract": {"wording": "unconstrained", "cross_source": "default"}, "exercise_plan": [{"source_refs": ["S2"]}]}}),
        (PracticePlanningRefinementOutput, {"plan_items": [{"plan_item_id": "p1"}]}),
    ])

    def completion(_client, _messages, *, response_model, **_kwargs):
        model, payload = next(expected)
        assert response_model is model
        return response_model.model_validate(payload)

    monkeypatch.setattr(exercise_generation, "structured_completion", completion)
    common = dict(model="fake", temperature=0, thinking=None, prompt_contract_id="practice.planning")
    full = exercise_generation._call_practice_json(object(), [], task_stage="planning", **common)
    refined = exercise_generation._call_practice_json(object(), [], **common)
    assert full["blueprint"]["exercise_plan"][0]["source_refs"] == ["S2"]
    assert refined["plan_items"][0]["plan_item_id"] == "p1"
