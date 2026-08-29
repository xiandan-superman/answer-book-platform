from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .paths import CONFIG_DIR

MODEL_CAPABILITY_REGISTRY_PATH = CONFIG_DIR / "model_capabilities.json"
REQUIRED_MODEL_FIELDS = {
    "kind",
    "evidence_grade",
    "native_inputs",
    "native_outputs",
    "structured_output",
    "thinking",
    "task_support",
    "last_verified_at",
}
VALID_EVIDENCE_GRADES = {"A", "B", "C", "D"}
VALID_MODEL_KINDS = {"text_generation", "image_generation"}
VALID_TASK_SUPPORT = {"recommended", "allowed", "limited", "forbidden", "unknown"}
SUPPORTED_NATIVE_TOOL_PROTOCOLS = {
    "responses",
    "responses_api",
    "chat_completions",
    "openai_compatible",
}


@lru_cache(maxsize=1)
def _load_default_model_capability_registry() -> dict[str, Any]:
    return _load_model_capability_registry_path(MODEL_CAPABILITY_REGISTRY_PATH)


def _load_model_capability_registry_path(target: Path) -> dict[str, Any]:
    if not target.exists():
        raise ValueError(f"模型能力注册表不存在：{target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"模型能力注册表无法读取：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
        raise ValueError("模型能力注册表格式错误：缺少 providers 对象")
    return payload


def load_model_capability_registry(path: Path | None = None) -> dict[str, Any]:
    return _load_model_capability_registry_path(path) if path is not None else _load_default_model_capability_registry()


def configured_provider_models(provider: dict[str, Any]) -> set[str]:
    models: set[str] = set()
    if bool(provider.get("supports_text_generation", True)):
        models.update(str(item).strip() for item in provider.get("model_options", []) if str(item).strip())
        default_model = str(provider.get("default_model", "") or "").strip()
        if default_model:
            models.add(default_model)
        vision_model = str(provider.get("vision_model", "") or "").strip()
        if vision_model:
            models.add(vision_model)
        models.update(str(item).strip() for item in provider.get("vision_model_options", []) if str(item).strip())
    if bool(provider.get("supports_image_generation", True)):
        image_model = str(provider.get("image_model", "") or "").strip()
        if image_model:
            models.add(image_model)
        models.update(str(item).strip() for item in provider.get("image_model_options", []) if str(item).strip())
    return models


def validate_provider_registry_sync(
    provider_config: dict[str, Any],
    registry: dict[str, Any] | None = None,
) -> list[str]:
    capability_registry = registry or load_model_capability_registry()
    configured_providers = provider_config.get("providers", {})
    registered_providers = capability_registry.get("providers", {})
    native_tool_routes = capability_registry.get("native_tool_routes", {})
    errors: list[str] = []

    if not isinstance(native_tool_routes, dict):
        errors.append("模型能力注册表的 native_tool_routes 必须是对象")
        native_tool_routes = {}

    for provider_name, provider in configured_providers.items():
        configured = configured_provider_models(provider)
        registered_provider = registered_providers.get(provider_name)
        if not isinstance(registered_provider, dict):
            errors.append(f"服务商 {provider_name} 缺少能力登记")
            continue
        registered_models = registered_provider.get("models", {})
        if not isinstance(registered_models, dict):
            errors.append(f"服务商 {provider_name} 的能力登记缺少 models 对象")
            continue
        registered = {str(item).strip() for item in registered_models if str(item).strip()}
        missing = sorted(configured - registered)
        stale = sorted(registered - configured)
        if missing:
            errors.append(f"服务商 {provider_name} 新增模型未登记：{', '.join(missing)}")
        if stale:
            errors.append(f"服务商 {provider_name} 已删除模型仍留在能力表：{', '.join(stale)}")
        for model_name, record in registered_models.items():
            if not isinstance(record, dict):
                errors.append(f"模型 {provider_name}/{model_name} 的能力记录必须是对象")
                continue
            missing_fields = sorted(REQUIRED_MODEL_FIELDS - set(record))
            if missing_fields:
                errors.append(f"模型 {provider_name}/{model_name} 缺少字段：{', '.join(missing_fields)}")
            if record.get("kind") not in VALID_MODEL_KINDS:
                errors.append(f"模型 {provider_name}/{model_name} 的 kind 无效")
            if record.get("evidence_grade") not in VALID_EVIDENCE_GRADES:
                errors.append(f"模型 {provider_name}/{model_name} 的 evidence_grade 无效")
            task_support = record.get("task_support")
            if not isinstance(task_support, dict) or not task_support:
                errors.append(f"模型 {provider_name}/{model_name} 必须登记 task_support")
            elif any(value not in VALID_TASK_SUPPORT for value in task_support.values()):
                errors.append(f"模型 {provider_name}/{model_name} 的 task_support 含无效状态")

        registered_tool_routes = native_tool_routes.get(provider_name, {})
        if not isinstance(registered_tool_routes, dict):
            errors.append(f"服务商 {provider_name} 的 native_tool_routes 必须是对象")
            registered_tool_routes = {}
        configured_profiles = provider.get("model_profiles", {})
        declared_tool_models = {
            str(model_name).strip()
            for model_name, profile in configured_profiles.items()
            if isinstance(profile, dict) and profile.get("supports_tool_calls") is True
        }
        registered_tool_models = {str(model_name).strip() for model_name in registered_tool_routes}
        if declared_tool_models != registered_tool_models:
            missing_public = sorted(registered_tool_models - declared_tool_models)
            stale_public = sorted(declared_tool_models - registered_tool_models)
            if missing_public:
                errors.append(
                    f"服务商 {provider_name} 已登记工具模型未同步公开配置：{', '.join(missing_public)}"
                )
            if stale_public:
                errors.append(
                    f"服务商 {provider_name} 公开配置误声明工具能力：{', '.join(stale_public)}"
                )
        for model_name, route in registered_tool_routes.items():
            if model_name not in registered_models:
                errors.append(f"工具能力登记引用未知模型：{provider_name}/{model_name}")
                continue
            if not isinstance(route, dict):
                errors.append(f"工具能力登记必须是对象：{provider_name}/{model_name}")
                continue
            protocol = str(route.get("protocol") or "").strip().lower()
            if protocol not in SUPPORTED_NATIVE_TOOL_PROTOCOLS:
                errors.append(f"工具能力协议无效：{provider_name}/{model_name}")
            profile = configured_profiles.get(model_name, {})
            configured_protocol = str(
                (profile.get("api_protocol") if isinstance(profile, dict) else "")
                or provider.get("api_protocol")
                or "chat_completions"
            ).strip().lower()
            if configured_protocol != protocol:
                errors.append(
                    f"工具能力协议与公开配置不一致：{provider_name}/{model_name} "
                    f"({protocol} != {configured_protocol})"
                )
            native_inputs = {
                str(item).strip().lower()
                for item in registered_models[model_name].get("native_inputs", [])
            }
            if "image" not in native_inputs:
                errors.append(f"自主生图工具模型必须能回看图片：{provider_name}/{model_name}")

    for provider_name in sorted(set(registered_providers) - set(configured_providers)):
        errors.append(f"已删除服务商仍留在能力表：{provider_name}")
    return errors


def ensure_provider_registry_sync(provider_config: dict[str, Any]) -> None:
    errors = validate_provider_registry_sync(provider_config)
    if errors:
        details = "\n- ".join(errors)
        raise ValueError(f"服务商配置与模型能力注册表不同步：\n- {details}")


def get_model_capability(provider_name: str, model_name: str) -> dict[str, Any] | None:
    registry = load_model_capability_registry()
    provider = registry.get("providers", {}).get(str(provider_name or "").strip(), {})
    record = provider.get("models", {}).get(str(model_name or "").strip()) if isinstance(provider, dict) else None
    return dict(record) if isinstance(record, dict) else None


def provider_has_capability_registry(provider_name: str) -> bool:
    registry = load_model_capability_registry()
    return str(provider_name or "").strip() in registry.get("providers", {})


def get_native_tool_route(provider_name: str, model_name: str) -> dict[str, Any] | None:
    """Return an explicitly verified native-tool route for one provider/model pair."""

    registry = load_model_capability_registry()
    provider_routes = registry.get("native_tool_routes", {}).get(
        str(provider_name or "").strip(), {}
    )
    route = (
        provider_routes.get(str(model_name or "").strip())
        if isinstance(provider_routes, dict)
        else None
    )
    return dict(route) if isinstance(route, dict) else None


def model_accepts_input(provider_name: str, model_name: str, input_type: str) -> bool | None:
    """Return an explicit registry decision, or None for an unregistered custom model."""

    record = get_model_capability(provider_name, model_name)
    if record is None:
        return None
    return str(input_type or "").strip().lower() in {
        str(item).strip().lower() for item in record.get("native_inputs", [])
    }


def model_task_support(provider_name: str, model_name: str, task_stage: str) -> str:
    record = get_model_capability(provider_name, model_name)
    if record is None:
        return "unknown"
    stage = str(task_stage or "").strip()
    support = record.get("task_support", {})
    aliases = {
        "planning": "blueprint",
        "knowledge_planning": "blueprint",
        "evidence_selection": "review",
        "answer_generation": "answer",
        "semantic_review": "review",
        "figure_schema": "blueprint",
        "drawing_code": "generation",
    }
    return str(support.get(stage, support.get(aliases.get(stage, ""), "unknown")))


def model_is_eligible_for_automatic_task(provider_name: str, model_name: str, task_stage: str) -> bool:
    record = get_model_capability(provider_name, model_name)
    if record is None or record.get("evidence_grade") != "A":
        return False
    return model_task_support(provider_name, model_name, task_stage) in {"recommended", "allowed"}


def render_model_capability_markdown(registry: dict[str, Any] | None = None) -> str:
    payload = registry or load_model_capability_registry()
    lines = [
        "# 当前模型能力登记表（自动生成）",
        "",
        "> 数据源：`config/model_capabilities.json`。请勿手工编辑本表；运行 `python3 scripts/sync_model_capability_docs.py` 重新生成。",
        "",
        "能力等级：A＝真实任务流程已验证；B＝接口能力已验证、任务基线待补；C＝配置或通道声明；D＝未知/过期。",
        "",
        "| 服务商 | 模型 | 类型 | 原生输入 → 输出 | 原生工具回路 | 结构化输出 | 推理 | 任务质量输入预算 | 任务适配 | 证据 | 最后验证 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    task_labels = payload.get("task_labels", {})
    for provider_name, provider in payload.get("providers", {}).items():
        display_name = str(provider.get("display_name") or provider_name)
        for model_name, record in provider.get("models", {}).items():
            provider_tool_routes = payload.get("native_tool_routes", {}).get(provider_name, {})
            tool_route = (
                provider_tool_routes.get(model_name)
                if isinstance(provider_tool_routes, dict)
                else None
            )
            tool_summary = (
                f"已验证（{tool_route.get('protocol')}，{tool_route.get('last_verified_at')}）"
                if tool_route
                else "未登记/禁用"
            )
            inputs = "、".join(record.get("native_inputs", [])) or "无"
            outputs = "、".join(record.get("native_outputs", [])) or "无"
            tasks = "；".join(
                f"{task_labels.get(task, task)}:{status}"
                for task, status in record.get("task_support", {}).items()
            )
            quality_limits = record.get("quality_limits") if isinstance(record.get("quality_limits"), dict) else {}
            quality_budget = "；".join(
                f"{task_labels.get(stage, stage)}:{limits.get('recommended_input_tokens')} tokens"
                for stage, limits in quality_limits.items()
                if isinstance(limits, dict) and limits.get("recommended_input_tokens")
            ) or "待验证"
            lines.append(
                "| "
                + " | ".join(
                    [
                        display_name,
                        f"`{model_name}`",
                        str(record.get("kind", "unknown")),
                        f"{inputs} → {outputs}",
                        tool_summary,
                        str(record.get("structured_output", "unknown")),
                        str(record.get("thinking", "unknown")),
                        quality_budget,
                        tasks,
                        str(record.get("evidence_grade", "D")),
                        str(record.get("last_verified_at", "unknown")),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## 同步规则", "", "新增、删除或更换服务商/模型时，必须同时修改能力注册表并重新生成本表；自动测试会拒绝任何缺失或遗留记录。", ""])
    return "\n".join(lines)
