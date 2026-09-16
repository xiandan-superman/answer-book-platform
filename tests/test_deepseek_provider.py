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


def test_retired_text_routes_cannot_be_restored_by_local_config(tmp_path) -> None:
    from app.settings import load_provider_config_file

    public = json.loads((ROOT / "config" / "providers.example.json").read_text())
    overrides = {"providers": {}}
    for name in ("ark", "lingsuan_domestic"):
        retired = public["providers"][name]["retired_models"]
        overrides["providers"][name] = {
            "model_options": retired,
            "default_model": retired[0],
            "model_profiles": {model: {"kind": "text_generation"} for model in retired},
        }
    (tmp_path / "providers.local.json").write_text(json.dumps(overrides))
    with patch("app.settings.LOCAL_CONFIG_DIR", tmp_path):
        config = load_provider_config_file()["providers"]
    for name in overrides["providers"]:
        retired = set(public["providers"][name]["retired_models"])
        assert not retired.intersection(config[name]["model_options"])
        assert not retired.intersection(config[name]["model_profiles"])
        assert config[name]["default_model"] not in retired
    assert "deepseek-flash" in config["deepseek"]["model_options"]


def test_lingsuan_v41_uses_official_multimodal_capabilities() -> None:
    from app.model_capability_registry import get_model_capability
    from app.settings import list_providers, provider_model_supports_vision

    provider = list_providers()["lingsuan_domestic"]
    model = "deepseek-v4.1-flash"
    assert model in provider.model_options
    assert provider_model_supports_vision(provider, model)
    assert set(provider.model_profiles[model]["supported_api_protocols"]) == {"responses", "chat_completions"}
    capability = get_model_capability(provider.name, model)
    assert capability["capability_source"] == "https://api-docs.deepseek.com/guides/vision"
    assert capability["native_inputs"] == ["text", "image"]


def test_removed_bailian_models_stay_removed_with_stale_override(tmp_path) -> None:
    from app.settings import load_provider_config_file

    retired = ["qwen3.6-plus", "qwen3.6-flash", "qwen3-vl-flash", "qwen-vl-max", "qwen-vl-plus"]
    (tmp_path / "providers.local.json").write_text(json.dumps({"providers": {"bailian": {
        "model_options": retired + ["qwen3.7-plus"], "vision_model": "qwen-vl-max",
        "model_profiles": {model: {} for model in retired},
    }}}))
    with patch("app.settings.LOCAL_CONFIG_DIR", tmp_path):
        provider = load_provider_config_file()["providers"]["bailian"]
    assert not set(retired).intersection(provider["model_options"])
    assert not set(retired).intersection(provider["model_profiles"])
    assert provider["vision_model"] not in retired
    assert "qwen3.7-plus" in provider["model_options"]
