from __future__ import annotations

from app.exercise_generation import (
    _batch_needs_visual_reference,
    _batch_reference_images,
    _normalize_plan,
    _normalize_source_scope,
)
from app.practice_context_planner import (
    aggregate_source_evidence,
    apply_source_evidence_contract,
    build_context_plan,
    estimate_text_tokens,
    image_numbers_from_evidence_refs,
    model_stage_quality_limit,
)
from app.model_context_planner import context_plan_block_reason


def _visual_source() -> dict:
    return {
        "source_question_id": "source_01",
        "number": "1",
        "title": "曲线题",
        "stem_excerpt": "根据曲线回答",
        "source_content": "根据下图判断转折点。⟦IMAGE_REF:2;MEMBER:word/media/image2.png⟧",
        "content_refs": ["C01P0001"],
        "question_type": "综合题",
        "knowledge_points": ["相变动力学"],
        "required_constraints": {
            "essential_definitions": ["转折点定义"],
            "essential_formulas": [],
            "applicable_boundaries": [],
        },
    }


def test_source_evidence_contract_preserves_text_and_visual_evidence() -> None:
    source = apply_source_evidence_contract(_visual_source())

    assert source["evidence_refs"] == ["C01P0001", "image:2"]
    assert source["visual_evidence_refs"] == ["image:2"]
    assert source["visual_dependency"]["required"] is True
    assert source["visual_dependency"]["replaceable_by_summary"] is False


def test_normalized_source_scope_cannot_drop_visual_contract() -> None:
    scope = _normalize_source_scope({"mode": "single", "questions": [_visual_source()]})

    item = scope["questions"][0]
    assert item["evidence_refs"] == ["C01P0001", "image:2"]
    assert item["visual_evidence_refs"] == ["image:2"]


def test_blueprint_items_inherit_evidence_from_bound_source() -> None:
    source = apply_source_evidence_contract(_visual_source())
    plan = _normalize_plan(
        {
            "source_analysis": {"subject": "材料科学"},
            "blueprint": {"exercise_plan": [{"source_refs": ["S1"], "target_skill": "读图判断"}]},
        },
        count=1,
        planned_types=["综合题"],
        difficulty="进阶",
        planned_difficulties=["进阶"],
        selected_types=["综合题"],
        source_files=["source.docx"],
        source_scope={"mode": "single", "questions": [source]},
        selected_source_questions=[source],
        planned_source_ids=["source_01"],
        generation_strategy="per_question",
    )

    item = plan["blueprint"]["exercise_plan"][0]
    assert item["required_evidence_refs"] == ["C01P0001", "image:2"]
    assert item["visual_evidence_refs"] == ["image:2"]
    assert item["requires_source_visuals"] is True


def test_explicit_visual_evidence_selects_exact_images_without_keywords() -> None:
    semantic_sources = {"items": [{"visual_evidence_refs": ["image:2"]}]}
    images, numbers = _batch_reference_images(["first", "second", "third"], semantic_sources)

    assert images == ["second"]
    assert numbers == [2]
    assert _batch_needs_visual_reference(
        semantic_sources,
        [{"requires_source_visuals": True, "visual_evidence_refs": ["image:2"]}],
    ) is True


def test_context_plan_blocks_omitted_required_image() -> None:
    plan = build_context_plan(
        stage="generation",
        provider_name="bigmodel",
        model_name="glm-5.3-flash",
        text="生成一道题",
        image_evidence_refs=[],
        required_evidence_refs=["C01P0001", "image:2"],
        delivered_evidence_refs=["C01P0001"],
        item_ids=["plan_item_01"],
    )

    assert plan["evidence_complete"] is False
    assert plan["omitted_required_evidence_refs"] == ["image:2"]
    assert plan["content_evidence_complete"] is True
    assert plan["visual_evidence_complete"] is False


def test_context_plan_cannot_report_complete_when_text_evidence_is_missing() -> None:
    plan = build_context_plan(
        stage="generation",
        provider_name="bigmodel",
        model_name="glm-5.3-flash",
        text="生成一道题",
        image_evidence_refs=["image:2"],
        required_evidence_refs=["C01P0001", "image:2"],
        item_ids=["plan_item_01"],
    )

    assert plan["evidence_complete"] is False
    assert plan["omitted_required_evidence_refs"] == ["C01P0001"]
    assert plan["content_evidence_complete"] is False
    assert plan["visual_evidence_complete"] is True


def test_context_plan_counts_delivered_images_and_rejects_text_only_model() -> None:
    plan = build_context_plan(
        stage="generation",
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        text="生成一道题",
        image_evidence_refs=["image:2"],
        required_evidence_refs=["C01P0001", "image:2"],
        delivered_evidence_refs=["C01P0001"],
        item_ids=["plan_item_01"],
    )

    assert plan["evidence_complete"] is True
    assert plan["input_modalities"]["images"] == 1
    assert plan["unsupported_modalities"] == ["image"]
    assert "不支持" in context_plan_block_reason(plan)


def test_glm_stage_quality_limits_and_token_estimate_are_available() -> None:
    assert model_stage_quality_limit("bigmodel", "glm-5.3-flash", "generation") == 20000
    assert estimate_text_tokens("材料科学ABC") >= 5
    assert image_numbers_from_evidence_refs(["image:2", "image:2", "image:9"], maximum=3) == [2]


def test_aggregate_source_evidence_keeps_each_bound_source() -> None:
    first = apply_source_evidence_contract(_visual_source())
    second = apply_source_evidence_contract({
        **_visual_source(),
        "source_question_id": "source_02",
        "source_content": "另一张图。⟦IMAGE_REF:3;MEMBER:word/media/image3.png⟧",
        "content_refs": ["C01P0002"],
    })

    contract = aggregate_source_evidence(["source_01", "source_02"], [first, second])

    assert contract["required_evidence_refs"] == ["C01P0001", "image:2", "C01P0002", "image:3"]
    assert contract["visual_evidence_refs"] == ["image:2", "image:3"]
