from __future__ import annotations

import json
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _providers():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()


def test_wawapi_routes_use_native_protocols_and_browser_client_identity() -> None:
    providers = _providers()
    expected = {
        "wawapi_openai": ("responses", "WAWAPI_OPENAI_API_KEY"),
        "wawapi_google": ("chat_completions", "WAWAPI_GOOGLE_API_KEY"),
        "wawapi_xai": ("responses", "WAWAPI_XAI_API_KEY"),
    }
    for name, (protocol, key_name) in expected.items():
        provider = providers[name]
        assert provider.base_url == "https://wawapii.com/v1"
        assert provider.api_protocol == protocol
        assert provider.api_key_env == key_name
        assert provider.user_agent.startswith("Mozilla/5.0 ")


def test_stale_wawapi_overlay_cannot_restore_blocked_client_identity() -> None:
    from app.settings import list_providers

    raw = json.loads((ROOT / "config" / "providers.example.json").read_text(encoding="utf-8"))
    for name in (
        "wawapi_openai",
        "wawapi_google",
        "wawapi_xai",
        "wawapi_image_openai",
        "wawapi_image_google",
        "wawapi_image_xai",
    ):
        raw["providers"][name]["base_url"] = "https://example.invalid/v1"
        raw["providers"][name]["user_agent"] = ""

    with patch("app.settings.load_provider_config_file", return_value=raw):
        providers = list_providers()

    for name in (
        "wawapi_openai",
        "wawapi_google",
        "wawapi_xai",
        "wawapi_image_openai",
        "wawapi_image_google",
        "wawapi_image_xai",
    ):
        assert providers[name].base_url == "https://wawapii.com/v1"
        assert providers[name].user_agent.startswith("Mozilla/5.0 ")


def test_wawapi_gemini_image_models_use_their_verified_text_protocols() -> None:
    provider = _providers()["wawapi_image_google"]

    assert provider.model_profiles["gemini-3-pro-image-preview"]["api_protocol"] == "chat_completions"
    assert provider.model_profiles["gemini-3.1-flash-image-preview"]["api_protocol"] == "responses"


def test_embedded_text_image_data_is_decoded() -> None:
    from app.llm_client import _embedded_image_bytes

    payload = b"fake-image-bytes" * 20
    content = f"![generated](data:image/png;base64,{base64.b64encode(payload).decode()})"

    assert _embedded_image_bytes(content) == payload


@pytest.mark.parametrize(
    ("model", "endpoint", "response"),
    [
        (
            "gemini-3-pro-image-preview",
            "/chat/completions",
            lambda text: {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]},
        ),
        (
            "gemini-3.1-flash-image-preview",
            "/responses",
            lambda text: {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]},
        ),
    ],
)
def test_wawapi_gemini_image_generation_uses_verified_embedded_data_route(
    tmp_path: Path, model: str, endpoint: str, response
) -> None:
    from dataclasses import replace

    from app.llm_client import OpenAICompatibleClient

    image_bytes = b"verified-image-bytes" * 20
    embedded = f"![generated](data:image/png;base64,{base64.b64encode(image_bytes).decode()})"
    requests = []

    class FakeResponse:
        def __init__(self):
            self._consumed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            if self._consumed:
                return b""
            self._consumed = True
            return json.dumps(response(embedded)).encode()

    provider = replace(_providers()["wawapi_image_google"], api_key="test-secret")
    client = OpenAICompatibleClient(provider)

    def fake_open(request, timeout):
        requests.append(request)
        return FakeResponse()

    client._urlopen = fake_open
    result = client.generate_image("draw a circle", tmp_path / "image.png", model=model)

    assert requests[0].full_url.endswith(endpoint)
    assert result.path.read_bytes() == image_bytes


def test_task_admission_accepts_only_verified_protocols_and_thinking_modes() -> None:
    from app.server import _validate_model_thinking_choice, _validate_requested_model_protocol

    dual_protocol = SimpleNamespace(
        api_protocol="responses",
        model_profiles={"gpt-5.6-sol": {"supported_api_protocols": ["responses", "chat_completions"]}},
    )
    assert _validate_requested_model_protocol(dual_protocol, "gpt-5.6-sol", "chat_completions") == "chat_completions"
    with pytest.raises(ValueError, match="未通过 anthropic_messages"):
        _validate_requested_model_protocol(dual_protocol, "gpt-5.6-sol", "anthropic_messages")

    restricted = SimpleNamespace(
        api_protocol="responses",
        model_profiles={"gpt-5.6-sol": {"supported_thinking_modes": ["low", "medium"]}},
    )
    with pytest.raises(ValueError, match="不支持推理强度 high"):
        _validate_model_thinking_choice(restricted, "gpt-5.6-sol", "high")
