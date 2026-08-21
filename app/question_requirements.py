from __future__ import annotations

import re
from typing import Any

from .question_types import question_has_type

_DRAW_COMMAND_RE = re.compile(
    r"(?:画出|绘制|作图|画图|补全图|续画|描绘|绘出|"
    r"(?:画|作|绘制)(?:一幅|一个|出)?[^\n。；;]{0,20}示意图|"
    r"(?:请|需|要求|(?:在|于)[^\n。；;]{0,16}(?:图|坐标系)中)[^\n。；;]{0,6}标出|"
    r"标出.{0,20}(?:斑点|峰|相区|晶面|晶向|坐标|曲线)|"
    r"用图.{0,12}(?:表示|说明|表述))"
)
_SOURCE_IMAGE_CUE_RE = re.compile(r"(?:下图|上图|如图|图中|观察图|根据图|由图|所示图|附图)")


def _question_text(question: dict[str, Any]) -> str:
    chunks = [
        str(question.get("stem") or ""),
        str(question.get("section") or ""),
        str(question.get("section_raw") or ""),
    ]
    for key in ("subquestions", "requirements"):
        for child in question.get(key) or []:
            if isinstance(child, dict):
                chunks.append(_question_text(child))
    return "\n".join(chunks)


def answer_figure_required(question: dict[str, Any]) -> bool:
    """Whether the answer must create a figure, independent of source images."""

    text = _question_text(question)
    explicit_command = bool(_DRAW_COMMAND_RE.search(text))
    typed_as_drawing = question_has_type(question, "作图题")
    # Reconcile stale/model-authored flags against objective question facts.
    # A referenced source image plus a noun such as "示意图" means read the
    # supplied image, not redraw it. Explicit drawing language (including
    # "在图中标出") or a confirmed drawing type still wins.
    if (question.get("image_refs") or _SOURCE_IMAGE_CUE_RE.search(text)) and not explicit_command and not typed_as_drawing:
        return False
    if question.get("answer_figure_required") is True:
        return True
    if typed_as_drawing:
        return True
    understanding = question.get("question_understanding")
    if isinstance(understanding, dict) and understanding.get("needs_figure") is True:
        return True
    plan = question.get("figure_schema_plan")
    intent = plan.get("diagram_intent") if isinstance(plan, dict) else None
    if isinstance(intent, dict) and intent.get("needs_figure") is True:
        return True
    if explicit_command:
        return True
    # Legacy data used needs_figure for two meanings. Only treat it as an
    # answer drawing requirement when it is not already explained by a source
    # image attachment.
    return bool(question.get("needs_figure")) and not bool(question.get("image_refs"))


def source_image_required(question: dict[str, Any]) -> bool:
    """Whether the original question image is needed to understand/deliver it."""

    if question.get("source_image_required") is True:
        return True
    if question.get("image_refs"):
        return True
    return bool(_SOURCE_IMAGE_CUE_RE.search(_question_text(question)))


def delivery_figure_required(question: dict[str, Any]) -> bool:
    return answer_figure_required(question) or source_image_required(question)


def figure_requirement_summary(question: dict[str, Any]) -> dict[str, bool]:
    answer_required = answer_figure_required(question)
    source_required = source_image_required(question)
    return {
        "answer_figure_required": answer_required,
        "source_image_required": source_required,
        "delivery_figure_required": answer_required or source_required,
    }
