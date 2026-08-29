from app.answer_generation import (
    _answer_model_candidates_for_question,
    _equivalent_tool_loop_model_candidates,
)
from app.llm_client import OpenAICompatibleClient
from app.settings import ProviderConfig


def _provider(*, supports_vision: bool = True) -> ProviderConfig:
    return ProviderConfig(
        name="test", type="openai_compatible", base_url="https://example.invalid", api_key="",
        default_model="text-default", model_options=("text-default", "text-selected", "text-backup"),
        allow_custom_model=False, model_hint="", temperature=0.1, max_tokens=1000,
        supports_vision=supports_vision, vision_model="vision-model" if supports_vision else "",
    )


def test_unprocessed_image_uses_vision_first_but_keeps_selected_model() -> None:
    assert _answer_model_candidates_for_question(_provider(), "text-selected", {"image_refs": ["question.png"]}) == [
        "vision-model", "text-selected", "text-default", "text-backup",
    ]


def test_processed_image_uses_selected_answer_model_first() -> None:
    question = {"image_refs": ["question.png"], "question_understanding": {"vision_used": True}}
    assert _answer_model_candidates_for_question(_provider(), "text-selected", question)[:2] == ["text-selected", "text-default"]


def test_no_declared_vision_route_fails_closed() -> None:
    assert _answer_model_candidates_for_question(_provider(supports_vision=False), "text-selected", {"image_refs": ["q.png"]}) == []


def test_image_tool_model_retry_keeps_only_equivalent_registered_candidate() -> None:
    provider = ProviderConfig(
        name="custom-test",
        type="openai_compatible",
        base_url="https://example.invalid",
        api_key="key",
        default_model="tool-model",
        model_options=("tool-model", "vision-without-tools", "text-only"),
        allow_custom_model=True,
        model_hint="",
        temperature=0.1,
        max_tokens=1000,
        supports_vision=True,
        vision_model="tool-model",
        vision_model_options=("tool-model", "vision-without-tools"),
        model_capabilities={
            "tool-model": ("text", "vision"),
            "vision-without-tools": ("text", "vision"),
            "text-only": ("text",),
        },
        model_profiles={
            "tool-model": {"supports_tool_calls": True},
            "vision-without-tools": {"supports_tool_calls": False},
            "text-only": {"supports_tool_calls": True},
        },
    )

    assert _equivalent_tool_loop_model_candidates(
        OpenAICompatibleClient(provider),
        provider,
        ["tool-model", "vision-without-tools", "text-only"],
    ) == ["tool-model"]
