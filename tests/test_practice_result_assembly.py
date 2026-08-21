from __future__ import annotations

from app.practice_result_assembly import (
    PracticeGenerationMetadataContext,
    build_practice_generation_metadata,
    build_practice_result_groups,
)


def clean_value(value, limit):
    return str(value or "").strip()[:limit]


def test_result_groups_preserve_first_seen_source_and_variant_order() -> None:
    result = build_practice_result_groups(
        [
            {"exercise_id": "e2", "source_question_id": "s1", "parent_plan_item_id": "p1"},
            {"exercise_id": "e1", "source_question_id": "s2", "parent_plan_item_id": "p1"},
            {"exercise_id": "e3", "source_question_id": "s1", "parent_plan_item_id": ""},
        ],
        selected_source_questions=[
            {"source_question_id": "s1", "title": "source one"},
            {"source_question_id": "s2", "title": "source two"},
        ],
        reviewed_exercise_plan=[{"plan_item_id": "p1", "target_skill": "skill"}],
        clean_value=clean_value,
    )

    assert result["exercise_groups"] == [
        {
            "source_question_id": "s1",
            "source_question": {"source_question_id": "s1", "title": "source one"},
            "exercise_ids": ["e2", "e3"],
        },
        {
            "source_question_id": "s2",
            "source_question": {"source_question_id": "s2", "title": "source two"},
            "exercise_ids": ["e1"],
        },
    ]
    assert result["variant_groups"] == [
        {
            "parent_plan_item_id": "p1",
            "blueprint_item": {"plan_item_id": "p1", "target_skill": "skill"},
            "exercise_ids": ["e2", "e1"],
            "variant_count": 2,
        }
    ]


def test_generation_metadata_sorts_diagnostics_and_reports_partial_success() -> None:
    metadata = build_practice_generation_metadata(
        {"generated_count": 2, "failed_count": 1, "difficulty_observations": ["risk"]},
        PracticeGenerationMetadataContext(
            provider_name="provider",
            model="model",
            expected_count=3,
            batch_failures={"p2": {"code": "missing", "message": "failed"}},
            batch_diagnostics=[
                {"batch_start": 3, "prompt_char_count": 20},
                {"batch_start": 1, "prompt_char_count": 10},
            ],
            diversity_repair={"attempts": [{"plan_item_id": "p1"}]},
            generation_run_id="run-1",
            include_source_content=False,
            reference_images_attached=True,
            blueprint_multi_question={"enabled": True},
        ),
    )

    assert metadata["status"] == "partial_success"
    assert metadata["generated_count"] == 2
    assert metadata["batch_errors"][0]["plan_item_id"] == "p2"
    assert [row["batch_start"] for row in metadata["batch_diagnostics"]] == [1, 3]
    assert metadata["prompt_char_count_total"] == 30
    assert metadata["diversity_repair_call_count"] == 1
    assert metadata["difficulty_observation_count"] == 1
