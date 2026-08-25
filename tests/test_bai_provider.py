from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["bai"]


def test_bai_provider_contains_only_verified_models() -> None:
    from app.settings import provider_model_supports_vision, provider_supports_image_generation

    provider = _provider()

    assert provider.base_url == "https://api.b.ai/v1"
    assert provider.api_protocol == "chat_completions"
    assert provider.api_key_env == "BAI_API_KEY"
    assert provider.default_model == "deepseek-v4-flash"
    assert provider.model_options == (
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
        "hy3",
        "mimo-v2.5",
    )
    assert provider.vision_model == "deepseek-v4-flash-vision-exp"
    assert provider.vision_model_options == ("deepseek-v4-flash-vision-exp",)
    assert not provider_model_supports_vision(provider, "deepseek-v4-flash")
    assert provider_model_supports_vision(provider, "deepseek-v4-flash-vision-exp")
    assert not provider_model_supports_vision(provider, "hy3")
    assert not provider_model_supports_vision(provider, "mimo-v2.5")
    assert not provider_supports_image_generation(provider)
    assert provider.image_model == ""
    assert "gpt-5.6-luna" not in provider.model_options


def test_bai_frontend_label_and_key_slot_are_available() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'bai: "BAI_API_KEY"' in source
    assert 'bai: "B.AI"' in source
