from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.settings import list_providers, provider_model_supports_vision

ROOT = Path(__file__).resolve().parents[1]
MODELS = (
    "glm-5.2", "glm-5.3", "glm-5.3-flash",
    "kimi-k3", "qwen3.7-max", "qwen3.8-max", "deepseek-v4.1-flash",
)


def test_domestic_catalog_has_one_separate_key_and_only_verified_choices():
    raw = json.loads((ROOT / "config/providers.example.json").read_text())
    with patch("app.settings.load_provider_config_file", return_value=raw):
        provider = list_providers()["lingsuan_domestic"]
    assert provider.model_options == MODELS
    assert provider.api_key_env == "LINGSUAN_DOMESTIC_API_KEY"
    assert not provider.supports_image_generation
    assert not provider.allow_custom_model
    for model in MODELS:
        assert provider_model_supports_vision(provider, model) == (model == "deepseek-v4.1-flash")
        profile = provider.model_profiles[model]
        assert profile["supported_api_protocols"] == (["responses", "chat_completions"] if model == "deepseek-v4.1-flash" else ["chat_completions"])
        assert profile["supported_thinking_modes"] == ["auto"]


def test_all_lingsuan_groups_migrate_old_saved_endpoint_without_changing_keys():
    raw = json.loads((ROOT / "config/providers.example.json").read_text())
    names = [name for name in raw["providers"] if name.startswith("lingsuan_")]
    for old_url in ("https://lingsuan.top/v1", "https://lingsuan.org/v1"):
        for name in names:
            raw["providers"][name]["base_url"] = old_url
        with patch("app.settings.load_provider_config_file", return_value=raw):
            providers = list_providers()
        assert len(names) == 4
        for name in names:
            assert providers[name].base_url == "https://edge.lingsuan.org/v1"
            assert providers[name].api_key_env == raw["providers"][name]["api_key_env"]


def test_domestic_key_is_allowed_but_not_inherited_from_legacy_key():
    from app.api_key_config import ALLOWED_API_KEY_NAMES, _clean_keys

    assert "LINGSUAN_DOMESTIC_API_KEY" in ALLOWED_API_KEY_NAMES
    cleaned = _clean_keys({"LINGSUAN_API_KEY": "test-only-old", "LINGSUAN_DOMESTIC_API_KEY": "test-only-new"})
    assert cleaned == {"LINGSUAN_OPENAI_API_KEY": "test-only-old", "LINGSUAN_DOMESTIC_API_KEY": "test-only-new"}
    assert "LINGSUAN_DOMESTIC_API_KEY" not in _clean_keys({"LINGSUAN_API_KEY": "test-only-old"})


def test_domestic_group_is_visible_in_shared_api_navigation():
    source = (ROOT / "web/app.js").read_text()
    assert 'lingsuan_domestic: "灵算 · 国模分组"' in source
    assert '["lingsuan_openai", "lingsuan_google", "lingsuan_domestic"]' in source
