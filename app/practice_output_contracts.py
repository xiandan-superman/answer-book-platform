from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class RequiredConstraintsContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    essential_definitions: list[str]
    essential_formulas: list[str]
    applicable_boundaries: list[str]


class BlueprintPlanItemContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    number: int
    plan_item_id: str
    question_type: str
    difficulty: str
    required_knowledge_points: list[str]
    required_constraints: RequiredConstraintsContract


class BlueprintContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    exercise_plan: list[BlueprintPlanItemContract]


class NormalizedPracticePlanContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    schema_version: Literal["answer_book.practice_plan.v1"]
    source_analysis: dict[str, Any]
    blueprint: BlueprintContract


class NormalizedExerciseContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    exercise_id: str
    plan_item_id: str
    number: int
    stem: str
    generation_status: Literal["completed", "failed"]


class PracticeQualityContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    status: str
    generated_count: int
    failed_count: int
    total_count: int
    partial_success: bool


class NormalizedPracticeSetContract(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    schema_version: str
    requested_count: int
    exercises: list[NormalizedExerciseContract]
    quality: PracticeQualityContract


def _contract_error_message(label: str, exc: ValidationError) -> str:
    issues = []
    for error in exc.errors(include_url=False)[:8]:
        location = ".".join(str(part) for part in error["loc"]) or "root"
        issues.append(f"{location}: {error['msg']}")
    return f"{label}结构契约校验失败：" + "；".join(issues)


def validate_normalized_practice_plan(data: dict[str, Any]) -> None:
    try:
        NormalizedPracticePlanContract.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_contract_error_message("练习蓝图", exc)) from exc


def validate_normalized_practice_set(data: dict[str, Any]) -> None:
    try:
        NormalizedPracticeSetContract.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_contract_error_message("练习结果", exc)) from exc
