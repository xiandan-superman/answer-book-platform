from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["deepseek"]


def test_official_deepseek_catalog_exposes_only_v41_stable_alias() -> None:
    from app.settings import provider_model_supports_vision

    provider = _provider()

    assert provider.base_url == "https://api.deepseek.com"
    assert provider.api_key_env == "DEEPSEEK_API_KEY"
    assert provider.default_model == "deepseek-flash"
    assert provider.model_options == ("deepseek-flash",)
    assert provider.vision_model_options == ("deepseek-flash",)
    assert provider.allow_custom_model is False
    assert provider_model_supports_vision(provider, "deepseek-flash") is True
    assert provider.model_profiles["deepseek-flash"]["supported_api_protocols"] == [
        "responses",
        "chat_completions",
    ]


def test_retired_official_deepseek_model_ids_are_not_user_selectable() -> None:
    provider = _provider()

    for retired in ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
        assert retired not in provider.model_options
        assert retired not in provider.model_profiles
        assert retired not in provider.model_capabilities


def test_frontend_exposes_official_deepseek_key_and_label() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'deepseek: "DEEPSEEK_API_KEY"' in source
    assert 'deepseek: "DeepSeek 官方"' in source


@pytest.mark.parametrize(
    ("thinking", "expected"),
    [("low", "low"), ("high", "high"), ("xhigh", "max")],
)
def test_responses_maps_ui_reasoning_to_verified_deepseek_effort(thinking: str, expected: str) -> None:
    from app.llm_client import ResponsesAPIClient

    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            if getattr(self, "consumed", False):
                return b""
            self.consumed = True
            return json.dumps({
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
            }).encode()

    provider = replace(_provider(), api_key="test-secret", responses_streaming=False)
    client = ResponsesAPIClient(provider)
    client._urlopen = lambda request, timeout: requests.append(request) or Response()

    client.chat_text([{"role": "user", "content": "Reply OK"}], thinking=thinking)

    payload = json.loads(requests[0].data)
    assert payload["reasoning"] == {"effort": expected}


def test_responses_uses_verified_deepseek_disabled_thinking_envelope() -> None:
    from app.llm_client import ResponsesAPIClient

    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            if getattr(self, "consumed", False):
                return b""
            self.consumed = True
            return json.dumps({
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
            }).encode()

    provider = replace(_provider(), api_key="test-secret", responses_streaming=False)
    client = ResponsesAPIClient(provider)
    client._urlopen = lambda request, timeout: requests.append(request) or Response()

    client.chat_text([{"role": "user", "content": "Reply OK"}], thinking="disabled")

    payload = json.loads(requests[0].data)
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning" not in payload
