from __future__ import annotations

from app import model_context_planner
from app.model_capability_registry import model_task_support
from app.model_context_planner import (
    build_model_context_plan,
    context_plan_block_reason,
    inspect_messages,
)


def _image_message(count: int) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请分析这些图片。"},
                *[
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{index}"}}
                    for index in range(count)
                ],
            ],
        }
    ]


def test_inspect_messages_counts_multimodal_inputs() -> None:
    inspected = inspect_messages(_image_message(2))

    assert inspected["image_count"] == 2
    assert "请分析这些图片" in inspected["text"]


def test_platform_context_plan_blocks_unsupported_image_input() -> None:
    plan = build_model_context_plan(
        stage="answer_generation",
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        messages=_image_message(1),
    )

    assert plan["unsupported_modalities"] == ["image"]
    assert "不支持" in context_plan_block_reason(plan)


def test_platform_context_plan_treats_stage_image_recommendation_as_advisory() -> None:
    plan = build_model_context_plan(
        stage="source_analysis",
        provider_name="bigmodel",
        model_name="glm-5.3-flash",
        messages=_image_message(9),
    )

    assert plan["recommended_images"] == 8
    assert plan["over_recommended_images"] is True
    assert plan["maximum_images"] is None
    assert plan["too_many_images"] is False
    assert plan["image_limit_authority"] == "stage_quality_advisory"
    assert context_plan_block_reason(plan) == ""


def test_platform_context_plan_blocks_only_registered_provider_hard_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        model_context_planner,
        "get_model_capability",
        lambda _provider, _model: {
            "native_inputs": ["text", "image"],
            "limits": {"max_images_per_request": 10},
            "quality_limits": {"source_analysis": {"max_images": 8}},
        },
    )
    plan = build_model_context_plan(
        stage="source_analysis",
        provider_name="test-provider",
        model_name="test-model",
        messages=_image_message(11),
    )

    assert plan["recommended_images"] == 8
    assert plan["maximum_images"] == 10
    assert plan["too_many_images"] is True
    assert plan["image_limit_authority"] == "provider_hard_limit"
    assert "硬上限 10 张" in context_plan_block_reason(plan)


def test_platform_context_plan_blocks_missing_required_evidence() -> None:
    plan = build_model_context_plan(
        stage="evidence_selection",
        provider_name="bigmodel",
        model_name="glm-5.3-flash",
        messages=[{"role": "user", "content": "选择教材证据"}],
        required_evidence_refs=["page:12", "figure:2"],
        delivered_evidence_refs=["page:12"],
    )

    assert plan["evidence_complete"] is False
    assert plan["omitted_required_evidence_refs"] == ["figure:2"]
    assert "必要材料" in context_plan_block_reason(plan)


def test_task_support_uses_platform_stage_aliases() -> None:
    assert model_task_support("deepseek", "deepseek-v4-flash", "answer_generation") == "allowed"
    assert model_task_support("deepseek", "deepseek-v4-flash", "knowledge_planning") == "allowed"
