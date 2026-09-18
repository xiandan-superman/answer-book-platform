from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.model_capability_registry import (
    ensure_provider_registry_sync,
    get_model_capability,
    get_native_tool_route,
    load_model_capability_registry,
    model_accepts_input,
    model_is_eligible_for_automatic_task,
    model_task_support,
    render_model_capability_markdown,
    validate_protocol_verification_sync,
    validate_provider_registry_sync,
)
from app.settings import list_providers

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_CONFIG = ROOT / "config" / "providers.example.json"
GENERATED_DOC = ROOT / "docs" / "MODEL_CAPABILITY_REGISTRY.md"


def _providers() -> dict:
    return json.loads(PROVIDER_CONFIG.read_text(encoding="utf-8"))


def test_shipped_provider_config_and_registry_are_exactly_synchronized() -> None:
    ensure_provider_registry_sync(_providers())


def test_shipped_catalog_contains_only_approved_verified_channels() -> None:
    providers = _providers()["providers"]
    assert set(providers) == {
        "ark", "ark_image", "bailian", "deepseek",
        "lingsuan_openai", "lingsuan_google", "lingsuan_image", "lingsuan_domestic",
        "wawapi_openai", "wawapi_google", "wawapi_xai", "lingsuan_claude", "wawapi_claude",
        "wawapi_image_openai", "wawapi_image_google", "wawapi_image_xai",
        "topapi_google",
    }
    assert all(provider.get("allow_custom_model") is False for provider in providers.values())


def test_adding_model_without_capability_record_is_rejected() -> None:
    providers = _providers()
    providers["providers"]["bailian"]["model_options"].append("new-model-without-record")

    errors = validate_provider_registry_sync(providers)

    assert any("新增模型未登记" in error and "new-model-without-record" in error for error in errors)


def test_deleting_model_without_removing_capability_record_is_rejected() -> None:
    providers = _providers()
    providers["providers"]["bailian"]["model_options"] = []
    providers["providers"]["bailian"]["default_model"] = ""
    providers["providers"]["bailian"]["vision_model"] = ""
    providers["providers"]["bailian"]["vision_model_options"] = []

    errors = validate_provider_registry_sync(providers)

    assert any("已删除模型仍留在能力表" in error and "qwen3.7-plus" in error for error in errors)


def test_deleting_provider_without_removing_capability_record_is_rejected() -> None:
    providers = _providers()
    del providers["providers"]["bailian"]

    errors = validate_provider_registry_sync(providers)

    assert "已删除服务商仍留在能力表：bailian" in errors


def test_incomplete_model_record_is_rejected() -> None:
    registry = deepcopy(load_model_capability_registry())
    del registry["providers"]["bailian"]["models"]["qwen3.7-plus"]["task_support"]

    errors = validate_provider_registry_sync(_providers(), registry)

    assert any("qwen3.7-plus 缺少字段：task_support" in error for error in errors)


def test_registry_drives_input_and_task_eligibility() -> None:
    assert model_accepts_input("bailian", "qwen3.7-plus", "image") is True
    assert model_accepts_input("ark", "deepseek-v4-pro-ga-260813", "image") is None
    assert model_task_support("bailian", "qwen3.7-plus", "source_analysis") == "limited"
    assert model_is_eligible_for_automatic_task("bailian", "qwen3.7-plus", "source_analysis") is False
    assert get_model_capability("missing", "missing") is None
    assert get_native_tool_route("bailian", "qwen3.7-plus")["protocol"] == "responses"
    assert get_native_tool_route("bailian", "qwen-vl-max") is None


def test_public_provider_catalog_includes_registry_task_metadata_without_keys() -> None:
    public = list_providers()["bailian"].redacted()
    profile = public["model_profiles"]["qwen3.7-plus"]

    assert profile["kind"] == "text_generation"
    assert profile["native_inputs"] == ["text", "image"]
    assert profile["task_support"]["answer"] == "limited"
    assert "api_key" not in public


def test_new_model_without_protocol_probe_is_rejected_before_launch() -> None:
    providers = _providers()
    providers["providers"]["bailian"]["model_options"].append("new-unverified-model")

    errors = validate_protocol_verification_sync(providers)

    assert "模型 bailian/new-unverified-model 上线前未测试协议" in errors


def test_new_model_with_only_failed_probe_is_rejected_before_launch() -> None:
    from app.model_capability_registry import load_model_protocol_verification

    providers = _providers()
    providers["providers"]["bailian"]["model_options"].append("new-failed-model")
    verification = deepcopy(load_model_protocol_verification())
    verification["providers"]["bailian"]["models"]["new-failed-model"] = {
        "kind": "text_generation",
        "routes": {
            "responses": {
                "status": "failed_snapshot",
                "observations": [{"attempt": 1, "status": "failed"}],
            }
        },
    }

    errors = validate_protocol_verification_sync(providers, verification)

    assert "模型 bailian/new-failed-model 没有通过任何协议，禁止作为新模型上线" in errors


def test_public_tool_profile_is_not_gated_by_historical_tool_registry() -> None:
    providers = _providers()
    providers["providers"]["bailian"]["model_profiles"]["qwen3.7-plus"][
        "supports_tool_calls"
    ] = True

    errors = validate_provider_registry_sync(providers)

    assert not any("工具能力" in error and "qwen-vl-max" in error for error in errors)


def test_generated_markdown_matches_registry() -> None:
    assert GENERATED_DOC.read_text(encoding="utf-8") == render_model_capability_markdown()


def test_invalid_registry_file_has_readable_error(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="模型能力注册表无法读取"):
        load_model_capability_registry(path)
