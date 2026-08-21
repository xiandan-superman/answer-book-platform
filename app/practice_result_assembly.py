from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CleanValue = Callable[[Any, int], str]


def build_practice_result_groups(
    exercises: list[dict[str, Any]],
    *,
    selected_source_questions: list[dict[str, Any]],
    reviewed_exercise_plan: list[dict[str, Any]],
    clean_value: CleanValue,
) -> dict[str, list[dict[str, Any]]]:
    """Build stable source and sibling-variant indexes for result consumers."""

    source_lookup = {
        clean_value(item.get("source_question_id"), 80): item
        for item in selected_source_questions
        if isinstance(item, dict)
    }
    grouped: dict[str, list[str]] = {}
    for exercise in exercises:
        if not isinstance(exercise, dict):
            continue
        source_id = clean_value(exercise.get("source_question_id"), 80)
        exercise_id = clean_value(exercise.get("exercise_id"), 100)
        if exercise_id:
            grouped.setdefault(source_id, []).append(exercise_id)

    parent_plan_lookup = {
        clean_value(item.get("plan_item_id"), 80): item
        for item in reviewed_exercise_plan
        if isinstance(item, dict) and clean_value(item.get("plan_item_id"), 80)
    }
    variant_grouped: dict[str, list[str]] = {}
    for exercise in exercises:
        if not isinstance(exercise, dict):
            continue
        parent_id = clean_value(exercise.get("parent_plan_item_id"), 80)
        exercise_id = clean_value(exercise.get("exercise_id"), 100)
        if parent_id and exercise_id:
            variant_grouped.setdefault(parent_id, []).append(exercise_id)

    return {
        "exercise_groups": [
            {
                "source_question_id": source_id,
                "source_question": source_lookup.get(source_id) or {},
                "exercise_ids": exercise_ids,
            }
            for source_id, exercise_ids in grouped.items()
        ],
        "variant_groups": [
            {
                "parent_plan_item_id": parent_id,
                "blueprint_item": parent_plan_lookup.get(parent_id) or {},
                "exercise_ids": exercise_ids,
                "variant_count": len(exercise_ids),
            }
            for parent_id, exercise_ids in variant_grouped.items()
        ],
    }


@dataclass(frozen=True)
class PracticeGenerationMetadataContext:
    provider_name: str
    model: str
    expected_count: int
    batch_failures: dict[str, dict[str, Any]]
    batch_diagnostics: list[dict[str, Any]]
    diversity_repair: dict[str, Any]
    generation_run_id: str
    include_source_content: bool
    reference_images_attached: bool
    blueprint_multi_question: dict[str, Any]


def build_practice_generation_metadata(
    quality: dict[str, Any],
    context: PracticeGenerationMetadataContext,
) -> dict[str, Any]:
    """Assemble user-facing runtime facts without re-evaluating content quality."""

    batch_diagnostics = sorted(
        (row for row in context.batch_diagnostics if isinstance(row, dict)),
        key=lambda row: int(row.get("batch_start") or 0),
    )
    return {
        "provider": context.provider_name,
        "model": context.model,
        "stage": "generation",
        "status": "partial_success" if context.batch_failures else "completed",
        "generated_count": quality.get("generated_count", context.expected_count),
        "failed_count": quality.get("failed_count", 0),
        "batch_errors": [
            {"plan_item_id": plan_item_id, **error}
            for plan_item_id, error in sorted(context.batch_failures.items())
        ],
        "batch_diagnostics": batch_diagnostics,
        "prompt_char_count_total": sum(
            int(row.get("prompt_char_count") or 0) for row in batch_diagnostics
        ),
        "diversity_repair_call_count": len(context.diversity_repair.get("attempts") or []),
        "difficulty_review_mode": "non_blocking_observation",
        "difficulty_observation_count": len(quality.get("difficulty_observations") or []),
        "generation_run_id": context.generation_run_id,
        "include_source_content_in_generation": context.include_source_content,
        "model_route": "selected_primary",
        "reference_images_attached": context.reference_images_attached,
        "blueprint_multi_question": context.blueprint_multi_question,
    }
