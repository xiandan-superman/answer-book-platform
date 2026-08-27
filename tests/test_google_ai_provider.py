from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODELS = (
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ping":"pong"}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        }).encode("utf-8")


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["google_ai"]


def test_google_ai_exposes_requested_stable_gemini_models_with_vision() -> None:
    from app.settings import provider_model_supports_vision, provider_supports_image_generation

    provider = _provider()

    assert provider.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert provider.api_protocol == "chat_completions"
    assert provider.api_key_env == "GEMINI_API_KEY"
    assert provider.default_model == "gemini-3.7-flash"
    assert provider.model_options == MODELS
    assert provider.vision_model == "gemini-3.7-flash"
    assert provider.vision_model_options == MODELS
    assert all(provider_model_supports_vision(provider, model) for model in MODELS)
    assert not provider_supports_image_generation(provider)
    assert provider.image_model == ""
    assert provider.thinking_mode == "high"


@pytest.mark.parametrize(
    ("thinking", "expected_effort"),
    [("disabled", "low"), ("low", "low"), ("medium", "medium"), ("xhigh", "high")],
)
def test_google_ai_clamps_reasoning_and_omits_sampling(thinking: str, expected_effort: str) -> None:
    from app.llm_client import OpenAICompatibleClient

    requests = []
    client = OpenAICompatibleClient(replace(_provider(), api_key="test-secret"))

    def request(value, timeout):
        requests.append(value)
        return _Response()

    client._urlopen = request
    result = client.chat_json(
        [{"role": "user", "content": "Return JSON"}],
        model="gemini-3.7-flash",
        temperature=0.1,
        thinking=thinking,
    )

    payload = json.loads(requests[0].data)
    assert requests[0].full_url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert payload["reasoning_effort"] == expected_effort
    assert payload["response_format"] == {"type": "json_object"}
    assert "thinking" not in payload
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert result.content == '{"ping":"pong"}'


def test_google_ai_frontend_label_and_independent_key_slot_are_available() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'google_ai: "GEMINI_API_KEY"' in source
    assert 'google_ai: "Google AI Studio · Gemini"' in source
    assert 'lingsuan_google: "LINGSUAN_GOOGLE_API_KEY"' in source
