from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["openrouter"]


def test_openrouter_exposes_configured_text_and_vision_models() -> None:
    from app.settings import provider_model_supports_vision, provider_supports_image_generation

    provider = _provider()

    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.api_protocol == "responses"
    assert provider.responses_streaming
    assert not provider.responses_fallback_to_chat
    assert provider.api_key_env == "OPENROUTER_API_KEY"
    assert provider.default_model == "stealth/ox-alpha"
    assert provider.model_options == (
        "stealth/ox-alpha",
        "z-ai/glm-5.2:free",
        "minimax/minimax-m3:free",
    )
    assert provider.vision_model == "stealth/ox-alpha"
    assert provider.vision_model_options == (
        "stealth/ox-alpha",
        "minimax/minimax-m3:free",
    )
    assert provider_model_supports_vision(provider, "stealth/ox-alpha")
    assert not provider_model_supports_vision(provider, "z-ai/glm-5.2:free")
    assert provider_model_supports_vision(provider, "minimax/minimax-m3:free")
    assert not provider_supports_image_generation(provider)
    assert provider.image_model == ""


def test_openrouter_frontend_label_and_key_slot_are_available() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'openrouter: "OPENROUTER_API_KEY"' in source
    assert 'openrouter: "OpenRouter"' in source
