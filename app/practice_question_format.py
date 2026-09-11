from __future__ import annotations

import re
from typing import Any

OBJECTIVE_ANSWER_SLOT = "（      ）"
OBJECTIVE_QUESTION_TYPES = frozenset({"选择题", "单选题", "多选题", "判断题"})
_TRAILING_EMPTY_ANSWER_SLOT_RE = re.compile(r"(?:（[ \t\u3000]*）|\([ \t\u3000]*\))\s*$")


def ensure_objective_answer_slot(stem: Any, question_type: Any) -> str:
    """Append one stable answer slot to choice and true/false question stems."""
    text = str(stem or "")
    if str(question_type or "").strip() not in OBJECTIVE_QUESTION_TYPES or not text.strip():
        return text
    normalized = text.rstrip()
    if match := _TRAILING_EMPTY_ANSWER_SLOT_RE.search(normalized):
        return normalized[: match.start()].rstrip() + OBJECTIVE_ANSWER_SLOT
    return normalized + OBJECTIVE_ANSWER_SLOT
