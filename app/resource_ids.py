from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def validate_resource_id(value: Any, *, label: str = "任务", max_length: int = 160) -> str:
    """Return an opaque local-storage key after rejecting path syntax.

    Exam task IDs intentionally support Chinese and punctuation such as hyphens,
    so this validates the storage boundary instead of imposing an ASCII schema.
    """

    text = str(value or "").strip()
    if (
        not text
        or len(text) > max_length
        or text in {".", ".."}
        or "/" in text
        or "\\" in text
        or _CONTROL_CHARACTERS.search(text)
    ):
        raise ValueError(f"{label}编号无效。")
    return text


def bounded_resource_path(root: Path, value: Any, *, label: str = "任务") -> Path:
    """Resolve one opaque child while also rejecting a symlink escape."""

    root = root.resolve()
    target = (root / validate_resource_id(value, label=label)).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label}编号无效。") from exc
    return target
