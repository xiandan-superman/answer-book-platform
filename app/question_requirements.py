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
_OPTIONAL_DRAW_FORMAT_RE = re.compile(
    r"(?:可|可以|也可|允许)(?:按|采用|用)?(?:画图|作图|绘图|图示)(?:的)?(?:格式|形式|方式)?"
)


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
    optional_format = bool(_OPTIONAL_DRAW_FORMAT_RE.search(text))
    # Do not mistake the words inside an optional-format phrase for a command;
    # a separate explicit draw instruction in the remaining text still wins.
    explicit_command = bool(_DRAW_COMMAND_RE.search(_OPTIONAL_DRAW_FORMAT_RE.sub("", text)))
    typed_as_drawing = question_has_type(question, "作图题")
    understanding = question.get("question_understanding")
    plan = question.get("figure_schema_plan")
    intent = plan.get("diagram_intent") if isinstance(plan, dict) else None

    # Explicit and model-authored positive intent is authoritative.  Objective
    # legacy heuristics below may resolve ambiguous old records, but must never
    # erase an image deliverable the responsible model has already formed.
    if question.get("answer_figure_required") is True:
        return True
    if typed_as_drawing:
        return True
    if isinstance(understanding, dict) and understanding.get("needs_figure") is True:
        return True
    if isinstance(intent, dict) and intent.get("needs_figure") is True:
        return True
    if explicit_command:
        return True

    # Phrases such as ``可按画图格式`` describe an allowed answer notation,
    # not a mandatory image deliverable when no responsible model has already
    # formed a positive image intent.
    if optional_format and not typed_as_drawing:
        return False
    # Reconcile ambiguous legacy flags against objective question facts.
    # A referenced source image plus a noun such as "示意图" means read the
    # supplied image, not redraw it, unless authoritative intent above says an
    # answer image is required.
    if (question.get("image_refs") or _SOURCE_IMAGE_CUE_RE.search(text)) and not explicit_command and not typed_as_drawing:
        return False
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
