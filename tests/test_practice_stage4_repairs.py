from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_cross_source_knowledge_set_triggers_semantic_review():
    practice = _complete_practice(quality={"blocking_issues": [], "failed_count": 0})

    assert exercise_generation._practice_semantic_review_should_run(
        practice,
        {"semantic_review_enabled": True},
    ) is True


def test_simple_single_source_item_still_skips_optional_semantic_review():
    practice = _complete_practice(
        source_mode="exam",
        generation_strategy="parallel_exam",
        quality={"blocking_issues": [], "failed_count": 0},
    )
    practice["blueprint"]["generation_strategy"] = "parallel_exam"
    practice["blueprint"]["exercise_plan"][0]["source_refs"] = ["source_01"]

    assert exercise_generation._practice_semantic_review_should_run(
        practice,
        {"semantic_review_enabled": True},
    ) is False


def test_semantic_reviewer_treats_false_judgment_proposition_as_valid_question_shape(monkeypatch):
    captured = {}
    practice = _complete_practice()

    def fake_call(_client, messages, **_kwargs):
        captured["prompt"] = messages[-1]["content"]
        return {"items": [{"number": 1, "status": "passed", "risks": []}], "set_summary": "通过"}

    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake"), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "_practice_generation_client", lambda _provider, _model: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)

    report = exercise_generation.review_practice_semantics(practice, {})

    assert report["status"] == "passed"
    assert "命题为假本身不是 fact_error" in captured["prompt"]
    assert "待判断命题" in captured["prompt"]
    assert "同时发生、均、总是、必然、无论、始终" in captured["prompt"]
    assert "以具体类别边界为准" in captured["prompt"]
    assert "各自相应的动态软化" in captured["prompt"]
    assert "高层错能金属的储存能不足以触发动态再结晶" in captured["prompt"]
    assert "低层错能金属在相应条件下更容易发生动态再结晶" in captured["prompt"]


def test_semantic_review_guard_rejects_cross_source_universal_premise(monkeypatch):
    practice = _complete_practice()
    practice["exercises"][0].update({
        "question_type": "简答题",
        "stem": "比较两类金属时，动态回复、动态再结晶诱导的软化过程同时存在。请说明主导机制。",
    })
    practice["blueprint"]["exercise_plan"][0]["question_type"] = "简答题"

    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake"), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "_practice_generation_client", lambda _provider, _model: object())
    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json",
        lambda *_args, **_kwargs: {
            "items": [{"number": 1, "status": "passed", "risks": []}],
            "set_summary": "模型未报告风险",
        },
    )

    report = exercise_generation.review_practice_semantics(practice, {})

    assert report["status"] == "warning"
    assert report["actionable_risk_count"] == 1
    assert report["items"][0]["risks"][0]["code"] == "cross_source_universal_premise"


def test_semantic_review_filters_fact_error_that_only_says_judgment_proposition_is_false(monkeypatch):
    practice = _complete_practice()
    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake"), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "_practice_generation_client", lambda _provider, _model: object())
    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json",
        lambda *_args, **_kwargs: {
            "items": [{
                "number": 1,
                "status": "risk",
                "risks": [{
                    "severity": "high",
                    "code": "fact_error",
                    "message": "待判断命题与绑定材料事实相反，属于错误表述。",
                    "evidence": "待判断命题把两类曲线特征写反。",
                    "suggested_action": "按材料修正命题。",
                }],
            }],
            "set_summary": "模型把错误命题本身当作风险",
        },
    )

    report = exercise_generation.review_practice_semantics(practice, {})

    assert report["status"] == "passed"
    assert report["items"][0]["risks"] == []


def test_semantic_review_filters_scope_critique_when_judgment_still_has_unique_truth_value(monkeypatch):
    practice = _complete_practice()
    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake"), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "_practice_generation_client", lambda _provider, _model: object())
    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json",
        lambda *_args, **_kwargs: {
            "items": [{
                "number": 1,
                "status": "risk",
                "risks": [{
                    "severity": "medium",
                    "code": "ambiguous_proposition_scope",
                    "message": "待判断命题未明确限定材料类别，存在泛化风险。",
                    "evidence": "待判断命题使用以动态回复为主导的金属。",
                    "suggested_action": "缩小表述范围。",
                }],
            }],
            "set_summary": "模型把可判假的范围表述当作生成缺陷",
        },
    )

    report = exercise_generation.review_practice_semantics(practice, {})

    assert report["status"] == "passed"
    assert report["items"][0]["risks"] == []


def test_semantic_review_filters_multi_clause_critique_when_whole_judgment_is_decidable(monkeypatch):
    practice = _complete_practice()
    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake"), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "_practice_generation_client", lambda _provider, _model: object())
    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json",
        lambda *_args, **_kwargs: {
            "items": [{
                "number": 1,
                "status": "risk",
                "risks": [{
                    "severity": "medium",
                    "code": "ambiguity_or_scope",
                    "message": "判断题包含多个并列陈述。",
                    "evidence": "局部真、局部假，但整体虽可判假。",
                    "suggested_action": "拆分命题。",
                }],
            }],
            "set_summary": "模型把可整体判断的复合命题误报为歧义",
        },
    )

    report = exercise_generation.review_practice_semantics(practice, {})

    assert report["status"] == "passed"
    assert report["items"][0]["risks"] == []


def test_semantic_review_keeps_judgment_risk_when_truth_value_is_not_unique(monkeypatch):
    practice = _complete_practice()
    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake"), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "_practice_generation_client", lambda _provider, _model: object())
    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json",
        lambda *_args, **_kwargs: {
            "items": [{
                "number": 1,
                "status": "risk",
                "risks": [{
                    "severity": "high",
                    "code": "fact_error",
                    "message": "题干缺少关键条件，命题真假不唯一，无法唯一判断。",
                    "evidence": "未说明材料类别。",
                    "suggested_action": "补充决定真值的条件。",
                }],
            }],
            "set_summary": "命题不可判定",
        },
    )

    report = exercise_generation.review_practice_semantics(practice, {})

    assert report["status"] == "warning"
    assert report["items"][0]["risks"][0]["message"].startswith("题干缺少关键条件")


def test_cross_source_guard_handles_simultaneous_wording_before_mechanism_names():
    exercise = {
        "question_type": "简答题",
        "stem": "热加工时变形同时发生的动态回复、动态再结晶属于动态软化过程，请比较两类金属。",
        "source_refs": ["source_01", "source_02"],
    }

    risks = exercise_generation._cross_source_universal_premise_risks(exercise, {})

    assert risks[0]["code"] == "cross_source_universal_premise"


def test_skipped_semantic_review_matches_review_candidate_release_state():
    practice = _complete_practice(
        semantic_review={
            "status": "skipped",
            "triggered": False,
            "reason": "review_not_completed",
            "items": [],
        }
    )

    quality = recompute_practice_quality(practice)

    assert quality["status"] == "passed"
    assert quality["release_level"] == "review_candidate"
    assert quality["checks"]["subject_matter_review_required"] is True
    assert any("语义质量审查未执行" in warning for warning in quality["warnings"])

    practice["quality"] = quality
    assert validate_practice_export(practice)["release_level"] == "review_candidate"


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


def test_frontend_renders_knowledge_source_refs_and_semantic_review_state():
    script = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    task_contract = (Path(__file__).parents[1] / "web" / "task-contract-ui.js").read_text(encoding="utf-8")

    assert 'data.source_mode === "knowledge" ? "来源知识单元" : "来源原题"' in script
    assert "item.source_refs" in script
    assert "semanticReviewIncomplete" in script
    assert 'data.quality?.release_level === "review_candidate"' in script
    assert "项需复核" in script
    assert "task.is_generation_task && task.status === \"completed_with_issues\"" not in task_contract
