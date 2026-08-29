from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["ark_image"]


def test_ark_image_provider_is_user_selectable_image_only() -> None:
    from app.settings import provider_supports_image_generation, resolve_provider_model

    provider = _provider()

    assert provider.base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert provider.api_key_env == "ARK_API_KEY"
    assert provider.supports_text_generation is False
    assert provider.supports_vision is False
    assert provider_supports_image_generation(provider)
    assert provider.image_model == "doubao-seedream-5-0-260128"
    assert provider.image_model_options == (
        "doubao-seedream-5-0-260128",
        "doubao-seedream-5-0-lite-260128",
    )
    assert provider.image_model_option_labels == {
        "doubao-seedream-5-0-260128": "Seedream-5.0-pro",
        "doubao-seedream-5-0-lite-260128": "Doubao-Seedream-5.0-lite",
    }
    assert provider.model_options == ()
    assert provider.allow_custom_model is False
    try:
        resolve_provider_model(provider)
    except ValueError as exc:
        assert "not configured for text generation" in str(exc)
    else:
        raise AssertionError("Ark image provider must never enter a text-model route")


def test_ark_seedream_uses_native_single_png_request(tmp_path, monkeypatch) -> None:
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

    assert result.model == "doubao-seedream-5-0-260128"
    assert output.read_bytes() == b"png-bytes"
    assert requests[0][0] == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    assert requests[0][1] == {
        "model": "doubao-seedream-5-0-260128",
        "prompt": "draw a clean teaching diagram",
        "size": "2K",
        "output_format": "png",
        "watermark": False,
        "sequential_image_generation": "disabled",
        "response_format": "b64_json",
    }


def test_stale_local_config_cannot_restore_other_ark_models() -> None:
    from app.settings import list_providers

    raw = json.loads((ROOT / "config" / "providers.example.json").read_text(encoding="utf-8"))
    raw["providers"]["ark_image"].update(
        {
            "supports_text_generation": True,
            "supports_vision": True,
            "vision_model": "unrelated-vision-model",
            "model_options": ["unrelated-text-model"],
            "image_model": "unrelated-image-model",
            "image_model_options": ["unrelated-image-model"],
            "image_model_option_labels": {"unrelated-image-model": "Other"},
        }
    )

    with patch("app.settings.load_provider_config_file", return_value=raw):
        provider = list_providers()["ark_image"]

    assert provider.supports_text_generation is False
    assert provider.supports_vision is False
    assert provider.vision_model == ""
    assert provider.model_options == ()
    assert provider.image_model == "doubao-seedream-5-0-260128"
    assert provider.image_model_options == (
        "doubao-seedream-5-0-260128",
        "doubao-seedream-5-0-lite-260128",
    )


def test_frontend_exposes_ark_only_through_the_image_provider() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    hidden_block = source.split("const HIDDEN_USER_PROVIDER_NAMES", 1)[1].split("]);", 1)[0]
    assert '"ark",' in hidden_block
    assert '"ark_image",' not in hidden_block
    assert 'ark_image: "ARK_API_KEY"' in source
    assert 'ark_image: "火山方舟"' in source
    assert 'const labels = kind === "image" ? (cfg.image_model_option_labels || {})' in source
