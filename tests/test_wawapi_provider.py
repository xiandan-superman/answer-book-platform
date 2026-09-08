from __future__ import annotations

import json
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


def test_task_admission_rejects_gpt_chat_and_unsupported_thinking_mode() -> None:
    from app.server import _validate_model_thinking_choice

    chat_gpt = SimpleNamespace(api_protocol="chat_completions", model_profiles={})
    with pytest.raises(ValueError, match="必须使用已验证的 Responses"):
        _validate_model_thinking_choice(chat_gpt, "gpt-5.6-sol", "low")

    restricted = SimpleNamespace(
        api_protocol="responses",
        model_profiles={"gpt-5.6-sol": {"supported_thinking_modes": ["low", "medium"]}},
    )
    with pytest.raises(ValueError, match="不支持推理强度 high"):
        _validate_model_thinking_choice(restricted, "gpt-5.6-sol", "high")
