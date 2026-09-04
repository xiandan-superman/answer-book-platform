from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ModelOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def reject_control_characters(cls, value: Any) -> Any:
        def visit(item: Any, path: str = "$") -> None:
            if isinstance(item, str):
                bad = [f"U+{ord(char):04X}" for char in item if (ord(char) < 32 and char != "\n") or ord(char) == 127]
                if bad:
                    raise ValueError(f"illegal control characters at {path}: {bad[:4]}")
            elif isinstance(item, dict):
                for key, child in item.items():
                    visit(child, f"{path}.{key}")
            elif isinstance(item, list):
                for index, child in enumerate(item):
                    visit(child, f"{path}[{index}]")

        visit(value)
        return value


class PracticeGenerationOutput(_ModelOutput):
    exercises: list[dict[str, Any]] = Field(min_length=1)


class PracticeFigureRepairOutput(_ModelOutput):
    figures: list[dict[str, Any]]


class PracticeSemanticReviewOutput(_ModelOutput):
    items: list[dict[str, Any]] = Field(min_length=1)


class PracticeSourceAnalysisOutput(_ModelOutput):
    constraints: list[dict[str, Any]]


class PracticePlanningOutput(_ModelOutput):
    plan_items: list[dict[str, Any]] | None = None
    exercise_plan: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def require_plan(self) -> "PracticePlanningOutput":
        if not self.plan_items and not self.exercise_plan:
            raise ValueError("plan_items or exercise_plan must contain at least one item")
        return self


class GenericJsonObjectOutput(_ModelOutput):
    pass


class AnswerDraftOutput(_ModelOutput):
    schema_version: Literal["answer_book.answer_draft.v1"]
    question_id: str = Field(min_length=1)
    answer: str | list[Any] | dict[str, Any]
    analysis: str | list[Any] | dict[str, Any]
    formulas: list[dict[str, Any]] = Field(default_factory=list)


class AnswerDraftBatchOutput(_ModelOutput):
    items: list[AnswerDraftOutput] = Field(min_length=1)
