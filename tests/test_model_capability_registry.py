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
    validate_provider_registry_sync,
)

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_CONFIG = ROOT / "config" / "providers.example.json"
GENERATED_DOC = ROOT / "docs" / "MODEL_CAPABILITY_REGISTRY.md"


def _providers() -> dict:
    return json.loads(PROVIDER_CONFIG.read_text(encoding="utf-8"))


def test_shipped_provider_config_and_registry_are_exactly_synchronized() -> None:
    ensure_provider_registry_sync(_providers())


def test_adding_model_without_capability_record_is_rejected() -> None:
    providers = _providers()
    providers["providers"]["bigmodel"]["model_options"].append("new-model-without-record")

    errors = validate_provider_registry_sync(providers)

    assert any("新增模型未登记" in error and "new-model-without-record" in error for error in errors)


def test_deleting_model_without_removing_capability_record_is_rejected() -> None:
    providers = _providers()
    providers["providers"]["bigmodel"]["model_options"] = []
    providers["providers"]["bigmodel"]["default_model"] = ""
    providers["providers"]["bigmodel"]["vision_model"] = ""
    providers["providers"]["bigmodel"]["vision_model_options"] = []

    errors = validate_provider_registry_sync(providers)

    assert any("已删除模型仍留在能力表" in error and "glm-5.3-flash" in error for error in errors)


def test_deleting_provider_without_removing_capability_record_is_rejected() -> None:
    providers = _providers()
    del providers["providers"]["bigmodel"]

    errors = validate_provider_registry_sync(providers)

    assert "已删除服务商仍留在能力表：bigmodel" in errors


def test_incomplete_model_record_is_rejected() -> None:
    registry = deepcopy(load_model_capability_registry())
    del registry["providers"]["bigmodel"]["models"]["glm-5.3-flash"]["task_support"]

    errors = validate_provider_registry_sync(_providers(), registry)

    assert any("glm-5.3-flash 缺少字段：task_support" in error for error in errors)


def test_registry_drives_input_and_task_eligibility() -> None:
    assert model_accepts_input("google_ai", "gemini-3.7-flash", "image") is True
    assert model_accepts_input("deepseek", "deepseek-v4-flash", "image") is False
    assert model_task_support("google_ai", "gemini-3.7-flash", "source_analysis") == "allowed"
    assert model_is_eligible_for_automatic_task("google_ai", "gemini-3.7-flash", "source_analysis") is False
    assert get_model_capability("missing", "missing") is None
    assert get_native_tool_route("bailian", "qwen3.7-plus")["protocol"] == "responses"
    assert get_native_tool_route("bailian", "qwen-vl-max") is None


def test_public_tool_profile_cannot_drift_from_verified_registry() -> None:
    providers = _providers()
    providers["providers"]["bailian"]["model_profiles"]["qwen-vl-max"][
        "supports_tool_calls"
    ] = True

    errors = validate_provider_registry_sync(providers)

    assert any("公开配置误声明工具能力" in error and "qwen-vl-max" in error for error in errors)


def test_generated_markdown_matches_registry() -> None:
    assert GENERATED_DOC.read_text(encoding="utf-8") == render_model_capability_markdown()


def test_invalid_registry_file_has_readable_error(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="模型能力注册表无法读取"):
        load_model_capability_registry(path)
