from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_AUTOMATIC_MODE_SUFFIX = re.compile(
    r"(?:[\s_·—-]*(?:按题生题|按题出题|知识点生题|知识点出题|专项练习))+$"
)


def clean_task_title(value: Any, *, limit: int = 80) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def friendly_material_title(value: Any) -> str:
    """Turn an automatic filename title into a concise user-facing label."""
    raw = clean_task_title(Path(str(value or "")).stem)
    if not raw:
        return ""
    without_mode = _AUTOMATIC_MODE_SUFFIX.sub("", raw).strip(" _·—-") or raw
    parts = [part.strip() for part in re.split(r"_+", without_mode) if part.strip()]
    return " · ".join(parts)[:80]


def title_matches_material_name(title: Any, material_name: Any) -> bool:
    title_text = clean_task_title(title)
    material_stem = clean_task_title(Path(str(material_name or "")).stem)
    return bool(title_text and material_stem and title_text == material_stem)


def short_model_label(model: Any, provider: Any = "") -> str:
    text = clean_task_title(model, limit=120)
    lowered = text.lower()
    for tier in ("terra", "sol", "luna"):
        if re.search(rf"(?:^|[-_\s]){tier}(?:$|[-_\s])", lowered):
            return tier.title()
    if "gemini" in lowered:
        return "Gemini"
    if "deepseek" in lowered:
        return "DeepSeek"
    if "sensenova" in lowered:
        return "SenseNova"
    if lowered == "hy3" or lowered.startswith("hy3-"):
        return "Hy3"
    if "mimo" in lowered:
        return "MiMo"
    if "claude" in lowered:
        return "Claude"
    if "qwen" in lowered:
        return "Qwen"
    if "doubao" in lowered or "seedream" in lowered:
        return "豆包"
    gpt = re.search(r"gpt[-_\s]?([\d.]+)", lowered)
    if gpt:
        return f"GPT-{gpt.group(1).rstrip('.')}"
    if text:
        return re.sub(r"[-_]+", " ", text)[:24]
    provider_text = clean_task_title(provider).lower()
    provider_labels = {
        "deepseek": "DeepSeek",
        "bailian": "百炼",
        "ark": "方舟",
        "zhipu": "智谱",
        "sensenova": "SenseNova",
        "bai": "B.AI",
    }
    return provider_labels.get(provider_text, "")


def build_display_task_title(
    task_kind_label: Any,
    content: Any,
    *,
    model: Any = "",
    provider: Any = "",
    model_label: Any = "",
) -> str:
    parts = [clean_task_title(task_kind_label)]
    concise_model = clean_task_title(model_label) or short_model_label(model, provider)
    if concise_model:
        parts.append(concise_model)
    parts.append(clean_task_title(content) or "未命名内容")
    return " · ".join(part for part in parts if part)
