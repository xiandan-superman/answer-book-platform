"""Lossless recovery of positional formula keys in model output."""

from __future__ import annotations

import re
from typing import Any


def normalize_formula_entry(raw: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Recover one fN key only when it agrees with its original 1-based slot.

    The producer contract remains ``latex``. Never reorder entries, infer a
    missing equation, or choose between contradictory representations.
    """
    result = dict(raw)
    aliases = [key for key in raw if re.fullmatch(r"f\d+", key)]
    if not aliases:
        return result
    if len(aliases) != 1 or aliases[0] != f"f{index}":
        raise ValueError(f"公式第 {index} 项的 fN 键不唯一或与原引用位置不一致")
    alias = aliases[0]
    if raw.get("formula_id") not in (None, "", alias):
        raise ValueError(f"公式第 {index} 项的 formula_id 与 {alias} 不一致")
    value = raw[alias]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"公式第 {index} 项的 {alias} 必须是非空公式文本")
    if "latex" in raw and raw["latex"] != value:
        raise ValueError(f"公式第 {index} 项的 latex 与 {alias} 矛盾")
    result["latex"] = value
    del result[alias]
    return result
