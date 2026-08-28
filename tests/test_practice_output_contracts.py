from __future__ import annotations

import pytest

from app.practice_output_contracts import (
    validate_normalized_practice_plan,
    validate_normalized_practice_set,
)


def test_normalized_plan_contract_accepts_program_owned_core() -> None:
    validate_normalized_practice_plan({
        "schema_version": "answer_book.practice_plan.v1",
        "source_analysis": {},
        "blueprint": {
            "exercise_plan": [{
                "number": 1,
                "plan_item_id": "plan_item_01",
                "question_type": "综合题",
                "difficulty": "进阶",
                "required_knowledge_points": ["相平衡"],
                "required_constraints": {
                    "essential_definitions": [],
                    "essential_formulas": ["G=H-TS"],
                    "applicable_boundaries": [],
                },
            }],
        },
    })


def test_normalized_result_contract_reports_exact_nested_path() -> None:
    with pytest.raises(ValueError, match=r"exercises\.0\.number"):
        validate_normalized_practice_set({
            "schema_version": "answer_book.practice.v1",
            "requested_count": 1,
            "exercises": [{
                "exercise_id": "practice_01",
                "plan_item_id": "plan_item_01",
                "number": "1",
                "stem": "题干",
                "generation_status": "completed",
            }],
            "quality": {
                "status": "passed",
                "generated_count": 1,
                "failed_count": 0,
                "total_count": 1,
                "partial_success": False,
            },
        })
