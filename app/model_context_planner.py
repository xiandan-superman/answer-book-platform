from __future__ import annotations

import math
from typing import Any, Iterable

from .model_capability_registry import get_model_capability, model_accepts_input, model_task_support


DEFAULT_QUALITY_INPUT_TOKENS = {
    "source_analysis": 18000,
    "knowledge_planning": 14000,
    "evidence_selection": 18000,
    "planning": 16000,
    "blueprint": 16000,
    "generation": 20000,
    "answer": 22000,
    "answer_generation": 22000,
    "review": 18000,
    "semantic_review": 20000,
    "format_repair": 12000,
    "figure_schema": 14000,
    "drawing_code": 14000,
    "image_generation": 4000,
    "general": 16000,
}


def _unique_strings(values: Iterable[Any], *, limit: int = 320) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def estimate_text_tokens(text: str) -> int:
    """Conservative local estimate: CJK is roughly one token per character."""

    value = str(text or "")
    non_ascii = sum(1 for character in value if ord(character) > 127)
    ascii_count = len(value) - non_ascii
    return max(1, non_ascii + math.ceil(ascii_count / 4))


def model_stage_quality_limit(provider_name: str, model_name: str, stage: str) -> int:
    record = get_model_capability(provider_name, model_name) or {}
    quality_limits = record.get("quality_limits") if isinstance(record.get("quality_limits"), dict) else {}
    stage_limits = quality_limits.get(stage) if isinstance(quality_limits.get(stage), dict) else {}
    try:
        configured = int(stage_limits.get("recommended_input_tokens") or 0)
    except (TypeError, ValueError):
        configured = 0
    return max(2000, configured or DEFAULT_QUALITY_INPUT_TOKENS.get(stage, DEFAULT_QUALITY_INPUT_TOKENS["general"]))


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    values: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") in {"text", "input_text", "output_text"}:
            values.append(str(part.get("text") or ""))
    return "\n".join(values)


def inspect_messages(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    text_parts: list[str] = []
    image_count = 0
    audio_count = 0
    video_count = 0
    file_count = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        text_parts.append(_message_text(content))
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "").strip().lower()
            if kind in {"image_url", "input_image", "image"}:
                image_count += 1
            elif kind in {"input_audio", "audio"}:
                audio_count += 1
            elif kind in {"input_video", "video"}:
                video_count += 1
            elif kind in {"input_file", "file"}:
                file_count += 1
    return {
        "text": "\n".join(part for part in text_parts if part),
        "image_count": image_count,
        "audio_count": audio_count,
        "video_count": video_count,
        "file_count": file_count,
    }


def build_model_context_plan(
    *,
    stage: str,
    provider_name: str,
    model_name: str,
    messages: Iterable[dict[str, Any]],
    required_evidence_refs: Iterable[Any] = (),
    delivered_evidence_refs: Iterable[Any] = (),
    item_ids: Iterable[Any] = (),
    fixed_overhead_tokens: int = 0,
) -> dict[str, Any]:
    message_list = list(messages)
    inspected = inspect_messages(message_list)
    required_refs = _unique_strings(required_evidence_refs)
    delivered_refs = _unique_strings(delivered_evidence_refs)
    omitted_refs = [item for item in required_refs if item not in delivered_refs]
    estimated_tokens = estimate_text_tokens(inspected["text"]) + max(0, int(fixed_overhead_tokens))
    quality_limit = model_stage_quality_limit(provider_name, model_name, stage)
    capability = get_model_capability(provider_name, model_name)
    quality_limits = capability.get("quality_limits") if isinstance(capability, dict) and isinstance(capability.get("quality_limits"), dict) else {}
    stage_limits = quality_limits.get(stage) if isinstance(quality_limits.get(stage), dict) else {}
    try:
        max_images = max(0, int(stage_limits.get("max_images") or 0))
    except (TypeError, ValueError):
        max_images = 0
    unsupported_modalities: list[str] = []
    for modality, count_key in (
        ("image", "image_count"),
        ("audio", "audio_count"),
        ("video", "video_count"),
        ("file", "file_count"),
    ):
        if inspected[count_key] and model_accepts_input(provider_name, model_name, modality) is False:
            unsupported_modalities.append(modality)
    support = model_task_support(provider_name, model_name, stage)
    return {
        "stage": stage,
        "provider": provider_name,
        "model": model_name,
        "item_ids": _unique_strings(item_ids, limit=120),
        "registered_model": capability is not None,
        "task_support": support,
        "estimated_input_tokens": estimated_tokens,
        "quality_input_token_limit": quality_limit,
        "quality_budget_ratio": round(estimated_tokens / quality_limit, 3),
        "over_quality_budget": estimated_tokens > quality_limit,
        "input_modalities": {
            "text": True,
            "images": inspected["image_count"],
            "audio": inspected["audio_count"],
            "video": inspected["video_count"],
            "files": inspected["file_count"],
        },
        "unsupported_modalities": unsupported_modalities,
        "maximum_images": max_images or None,
        "too_many_images": bool(max_images and inspected["image_count"] > max_images),
        "required_evidence_refs": required_refs,
        "delivered_evidence_refs": delivered_refs,
        "evidence_complete": not omitted_refs,
        "omitted_required_evidence_refs": omitted_refs,
    }


def context_plan_block_reason(plan: dict[str, Any], *, enforce_budget: bool = True) -> str:
    provider_model = f"{plan.get('provider')}/{plan.get('model')}"
    if plan.get("task_support") == "forbidden":
        return f"当前模型 {provider_model} 不适合执行“{plan.get('stage')}”任务，请更换支持该任务的模型。"
    unsupported = plan.get("unsupported_modalities") or []
    if unsupported:
        labels = {"image": "图片", "audio": "音频", "video": "视频", "file": "文件"}
        visible = "、".join(labels.get(item, str(item)) for item in unsupported)
        return f"当前模型 {provider_model} 不支持本次任务包含的{visible}输入，请更换兼容模型。"
    if plan.get("too_many_images"):
        return (
            f"本次任务包含 {plan.get('input_modalities', {}).get('images')} 张图片，超过当前模型在该任务中的平台质量上限 "
            f"{plan.get('maximum_images')} 张。请按题目或页码拆分后重试。"
        )
    omitted = plan.get("omitted_required_evidence_refs") or []
    if omitted:
        return f"本次调用缺少 {len(omitted)} 项必要材料，平台已停止调用以避免模型在证据不完整时猜测。"
    if enforce_budget and plan.get("over_quality_budget"):
        return (
            f"本次输入约 {plan.get('estimated_input_tokens')} tokens，超过当前模型在该任务中的质量预算 "
            f"{plan.get('quality_input_token_limit')} tokens。请缩小材料范围或拆分任务后重试。"
        )
    return ""
