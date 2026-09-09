import inspect
from types import SimpleNamespace

import pytest

from app.exercise_generation import (
    _batch_prompt_contract,
    _exercise_output_contract_for_plan_item,
    _main_model_practice_image_rules,
    practice_plan_requires_image_tools,
)
from app.image_orchestration import (
    LEGACY_FIGURE_PIPELINE,
    MAIN_MODEL_TOOL_LOOP,
    normalize_image_orchestration,
)
from app.pipeline import _isolated_image_routes
from app.task_store import TaskRecord, create_task


def test_missing_values_and_old_records_migrate_to_the_main_model_route() -> None:
    assert normalize_image_orchestration("") == MAIN_MODEL_TOOL_LOOP
    record = TaskRecord("t", "e.docx", "books", "p", "m", "created", "now", "now")
    assert record.image_orchestration == MAIN_MODEL_TOOL_LOOP


def test_new_platform_tasks_default_to_the_main_model_image_route() -> None:
    assert inspect.signature(create_task).parameters["image_orchestration"].default == MAIN_MODEL_TOOL_LOOP


def test_unknown_route_is_rejected_instead_of_guessed() -> None:
    with pytest.raises(ValueError, match="Unsupported image_orchestration"):
        normalize_image_orchestration("blend_both")


def test_unknown_route_exposes_stable_public_error_contract() -> None:
    with pytest.raises(ValueError) as exc_info:
        normalize_image_orchestration("disabled")
    assert exc_info.value.public_error_code == "invalid_image_orchestration"
    assert "不支持的 image_orchestration" in exc_info.value.public_message


def test_main_model_route_cannot_receive_legacy_dependencies() -> None:
    image = SimpleNamespace(name="image")
    answer = SimpleNamespace(name="answer")
    routes = _isolated_image_routes(
        MAIN_MODEL_TOOL_LOOP,
        image_provider=image,
        image_model="gpt-image-2",
        code_provider=answer,
        code_model="gpt-5.6-sol",
    )
    assert routes["answer_image_provider"] is image
    assert routes["legacy_image_provider"] is None
    assert routes["legacy_code_provider"] is None


def test_legacy_route_is_formally_retired() -> None:
    with pytest.raises(ValueError, match="Unsupported image_orchestration"):
        normalize_image_orchestration(LEGACY_FIGURE_PIPELINE)


def test_practice_prompt_exposes_generated_images_only_in_main_model_mode() -> None:
    legacy = _batch_prompt_contract([], main_model_image_tools=False)
    main = _batch_prompt_contract([], main_model_image_tools=True)
    assert "generated_images" not in legacy["item_schema"]
    assert "generated_images" in main["item_schema"]


def test_main_model_practice_contract_hides_legacy_figures_even_when_blueprint_requires_image() -> None:
    item = {"question_type": "简答题", "stem_figure_required": True}
    legacy = _batch_prompt_contract([item], main_model_image_tools=False)
    main = _batch_prompt_contract([item], main_model_image_tools=True)
    single = _exercise_output_contract_for_plan_item(item, main_model_image_tools=True)

    assert "figures" in legacy["conditional_fields"]
    assert "figures" not in main["conditional_fields"]
    assert "figures" not in single
    assert "generated_images" in single


def test_practice_main_model_rules_preserve_confirmed_intent_and_default_to_monochrome() -> None:
    rules = "\n".join(
        _main_model_practice_image_rules([{"stem_figure_required": True}])
    )

    assert "不得在后续生成、重试或修复中撤销" in rules
    assert "black, white, and grayscale" in rules
    assert "Do not use color to distinguish content" in rules


def test_practice_image_tools_are_required_only_by_explicit_blueprint_contract() -> None:
    text_plan = {"blueprint": {"exercise_plan": [{"question_type": "简答题"}]}}
    figure_plan = {"blueprint": {"exercise_plan": [{"stem_figure_required": True}]}}

    assert practice_plan_requires_image_tools(text_plan) is False
    assert practice_plan_requires_image_tools(figure_plan) is True
