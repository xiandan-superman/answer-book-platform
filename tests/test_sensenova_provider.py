from __future__ import annotations

import base64
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["sensenova"]


def test_sensenova_provider_exposes_supported_models_and_multimodal_routing() -> None:
    from app.settings import provider_model_supports_vision, provider_supports_image_generation

    provider = _provider()

    assert provider.base_url == "https://token.sensenova.cn/v1"
    assert provider.api_protocol == "chat_completions"
    assert provider.api_key_env == "SENSENOVA_API_KEY"
    assert provider.default_model == "sensenova-6.8-flash-lite"
    assert provider.model_options == (
        "sensenova-6.8-flash-lite",
        "deepseek-v4-flash",
        "glm-5.2",
    )
    assert provider.vision_model == "sensenova-6.8-flash-lite"
    assert provider.vision_model_options == ("sensenova-6.8-flash-lite",)
    assert provider_model_supports_vision(provider, "sensenova-6.8-flash-lite")
    assert not provider_model_supports_vision(provider, "deepseek-v4-flash")
    assert not provider_model_supports_vision(provider, "glm-5.2")
    assert provider_supports_image_generation(provider)
    assert provider.image_model == "sensenova-u1.5-lite"
    assert provider.image_model_options == ("sensenova-u1.5-lite",)

    all_models = (*provider.model_options, *provider.vision_model_options, *provider.image_model_options)
    assert "sensenova-u1-fast" not in all_models


def test_sensenova_u15_uses_image_endpoint_and_persists_base64_png(tmp_path, monkeypatch) -> None:
    from app.llm_client import OpenAICompatibleClient

    provider = replace(_provider(), api_key="test-key")
    client = OpenAICompatibleClient(provider)
    requests = []

    def fake_post(url, payload, timeout):
        requests.append((url, payload, timeout))
        return {"data": [{"b64_json": base64.b64encode(b"png-bytes").decode("ascii")}]}

    monkeypatch.setattr(client, "_post_json", fake_post)
    output = tmp_path / "generated.png"

    result = client.generate_image("draw a clean teaching diagram", output)

    assert result.model == "sensenova-u1.5-lite"
    assert output.read_bytes() == b"png-bytes"
    assert len(requests) == 1
    assert requests[0][0] == "https://token.sensenova.cn/v1/images/generations"
    assert requests[0][1] == {
        "model": "sensenova-u1.5-lite",
        "prompt": "draw a clean teaching diagram",
        "size": "2048x2048",
        "n": 1,
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False,
    }


def test_frontend_displays_and_saves_sensenova_key() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'sensenova: "SENSENOVA_API_KEY"' in source
    assert 'sensenova: "商汤日日新 · SenseNova"' in source
