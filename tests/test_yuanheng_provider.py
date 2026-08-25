from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["yuanheng"]


def test_yuanheng_exposes_only_verified_luna_and_terra_with_vision() -> None:
    from app.settings import provider_model_supports_vision, provider_supports_image_generation

    provider = _provider()

    assert provider.base_url == "https://cn.meta-api.vip/v1"
    assert provider.api_protocol == "responses"
    assert provider.responses_streaming
    assert not provider.responses_fallback_to_chat
    assert provider.api_key_env == "YUANHENG_API_KEY"
    assert provider.default_model == "gpt-5.6-luna"
    assert provider.model_options == ("gpt-5.6-luna", "gpt-5.6-terra")
    assert provider.vision_model == "gpt-5.6-luna"
    assert provider.vision_model_options == ("gpt-5.6-luna", "gpt-5.6-terra")
    assert provider_model_supports_vision(provider, "gpt-5.6-luna")
    assert provider_model_supports_vision(provider, "gpt-5.6-terra")
    assert not provider_supports_image_generation(provider)
    assert provider.image_model == ""


def test_yuanheng_frontend_label_and_key_slot_are_available() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'yuanheng: "YUANHENG_API_KEY"' in source
    assert 'yuanheng: "元衡 API"' in source
