from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

# Initialize the capability package before llm_client; the application normally
# does this through its server bootstrap, while this focused test imports the
# exercise module directly.
import app.capabilities  # noqa: F401
from app import exercise_generation
from app.llm_client import LLMError, ResponsesAPIClient
from app.settings import ProviderConfig


def _provider(**overrides) -> ProviderConfig:
    values = {
        "name": "lingsuan",
        "type": "openai_compatible",
        "base_url": "https://lingsuan.top/v1",
        "api_key": "test-key",
        "default_model": "gpt-5.6-terra",
        "model_options": ("gpt-5.6-terra",),
        "allow_custom_model": True,
        "model_hint": "",
        "temperature": 0.1,
        "max_tokens": 24576,
        "api_protocol": "responses",
        "responses_streaming": True,
    }
    values.update(overrides)
    return ProviderConfig(**values)


def _result(content: str):
    return SimpleNamespace(content=content, raw={})


def _malformed_latex_json() -> str:
    return '{"exercises":[{"stem":"$' + "\\" + 'beta$"}]}'


def _valid_latex_json() -> str:
    return json.dumps({"exercises": [{"stem": "$\\beta$"}]}, ensure_ascii=False)


def test_lingsuan_gpt_practice_generation_is_forced_to_chat_completions():
    provider = _provider()

    client = exercise_generation._practice_generation_client(provider, "gpt-5.6-terra")

    assert client.config.api_protocol == "chat_completions"
    assert client.config.responses_streaming is False
    assert not isinstance(client, ResponsesAPIClient)
    assert provider.api_protocol == "responses"


def test_lingsuan_non_gpt_model_keeps_configured_protocol():
    provider = _provider(default_model="claude-opus-5")

    client = exercise_generation._practice_generation_client(provider, "claude-opus-5")

    assert isinstance(client, ResponsesAPIClient)
    assert client.config.api_protocol == "responses"


def test_control_character_in_responses_output_retries_through_chat(monkeypatch):
    primary_calls = []
    chat_calls = []
    fallback_configs = []

    class PrimaryResponsesClient:
        config = _provider()

        def chat_json(self, messages, **kwargs):
            primary_calls.append((messages, kwargs))
            return _result(_malformed_latex_json())

    class ChatFallbackClient:
        def __init__(self, config):
            self.config = config

        def chat_json(self, messages, **kwargs):
            chat_calls.append((messages, kwargs))
            return _result(_valid_latex_json())

    def client_factory(config):
        fallback_configs.append(config)
        return ChatFallbackClient(config)

    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", client_factory)

    parsed = exercise_generation._call_practice_json(
        PrimaryResponsesClient(),
        [{"role": "user", "content": "return JSON"}],
        model="gpt-5.6-terra",
        temperature=0.35,
        thinking=None,
    )

    assert parsed["exercises"][0]["stem"] == "$\\beta$"
    assert len(primary_calls) == 1
    assert len(chat_calls) == 1
    assert fallback_configs[0].api_protocol == "chat_completions"
    assert fallback_configs[0].responses_streaming is False
    assert chat_calls[0][1]["thinking"] == "disabled"


def test_invalid_chat_retry_is_rejected_instead_of_returned(monkeypatch):
    class PrimaryResponsesClient:
        config = _provider()

        def chat_json(self, _messages, **_kwargs):
            return _result(_malformed_latex_json())

    class InvalidChatFallbackClient:
        def __init__(self, config):
            self.config = config

        def chat_json(self, _messages, **_kwargs):
            return _result(_malformed_latex_json())

    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", InvalidChatFallbackClient)

    with pytest.raises(LLMError, match="Chat 修复后仍失败"):
        exercise_generation._call_practice_json(
            PrimaryResponsesClient(),
            [{"role": "user", "content": "return JSON"}],
            model="gpt-5.6-terra",
            temperature=0.35,
            thinking=None,
        )


def test_control_gate_rejects_formula_escape_controls_but_allows_newline():
    assert exercise_generation._practice_control_character_issues({"stem": "first\nsecond"}) == []

    issues = exercise_generation._practice_control_character_issues({
        "beta": "\beta",
        "frac": "\frac",
        "theta": "\theta",
        "rm": "\rm",
        "del": "x\x7fy",
    })

    assert {issue["code"] for issue in issues} == {
        "U+0008", "U+0009", "U+000C", "U+000D", "U+007F",
    }


def test_normalization_refuses_contaminated_data_before_persistence():
    with pytest.raises(ValueError, match="不能进入规范化或保存"):
        exercise_generation.normalize_practice_set(
            {"exercises": [{"stem": "$\beta$"}]},
            requested_count=1,
            subject="化学",
        )
