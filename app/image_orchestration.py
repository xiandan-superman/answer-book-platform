from __future__ import annotations

import copy
import json
from typing import Any, Mapping

MAIN_MODEL_TOOL_LOOP = "main_model_tool_loop"
LEGACY_FIGURE_PIPELINE = "legacy_figure_pipeline"
IMAGE_ORCHESTRATION_MODES = frozenset({MAIN_MODEL_TOOL_LOOP, LEGACY_FIGURE_PIPELINE})

# This is a presentation default, never an image-necessity classifier.  Keep it
# model-visible so the main model can carry the same visual policy into every
# image prompt it authors while still overriding it for genuinely color-bound
# source material or an explicit user/question requirement.
DEFAULT_EDUCATIONAL_IMAGE_STYLE_RULE = (
    "Default every generated educational image to black, white, and grayscale on a white background. "
    "Do not use color to distinguish content; use labels, line styles, hatching, symbols, shapes, or numbering instead. "
    "Use color only when the user, the question, or source evidence explicitly makes color part of the required meaning."
)

GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT = (
    "如本次内容需要生成图片，图片中的标题、说明和自然语言标注应与题目语言保持一致；"
    "中文题目优先使用简体中文，避免无必要的英文标题；"
    "数学符号、字母标记、公式、单位和专业缩写可保留原样。"
)
GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT_FIELD = "image_label_language_requirement"


def _has_generation_image_label_language_requirement(value: Any) -> bool:
    if isinstance(value, dict):
        if GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT_FIELD in value:
            return True
        return any(_has_generation_image_label_language_requirement(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_generation_image_label_language_requirement(item) for item in value)
    if not isinstance(value, str):
        return False
    return (
        GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT in value
        or "图片中的标题、说明和自然语言标注应与题目语言保持一致" in value
        or "图片里的标题、说明和自然语言标注应与题目语言保持一致" in value
    )


def _add_generation_image_label_language_requirement(text: str) -> str:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        if not _has_generation_image_label_language_requirement(payload):
            payload[GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT_FIELD] = (
                GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT
            )
        return json.dumps(payload, ensure_ascii=False)
    return (
        f"{text.rstrip()}\n\n图片文字要求：{GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT}"
        if text.strip()
        else f"图片文字要求：{GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT}"
    )


def ensure_generation_image_label_language_requirement(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add the generation-only image-label rule at most once per request."""

    result = copy.deepcopy(messages)
    if _has_generation_image_label_language_requirement(result):
        return result
    for message in reversed(result):
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = _add_generation_image_label_language_requirement(content)
            return result
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in {"text", "input_text"}:
                    continue
                part["text"] = _add_generation_image_label_language_requirement(
                    str(part.get("text") or "")
                )
                return result
            content.insert(
                0,
                {
                    "type": "text",
                    "text": f"图片文字要求：{GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT}",
                },
            )
            return result
    result.append(
        {
            "role": "user",
            "content": f"图片文字要求：{GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT}",
        }
    )
    return result


def normalize_image_orchestration(value: Any, *, default: str = LEGACY_FIGURE_PIPELINE) -> str:
    """Return one explicit image route; never silently blend the two pipelines."""

    mode = str(value or "").strip()
    if not mode:
        mode = default
    if mode not in IMAGE_ORCHESTRATION_MODES:
        raise ValueError(f"Unsupported image_orchestration: {mode}")
    return mode


def image_orchestration_from_payload(
    payload: Mapping[str, Any],
    *,
    default: str = LEGACY_FIGURE_PIPELINE,
) -> str:
    return normalize_image_orchestration(payload.get("image_orchestration"), default=default)
