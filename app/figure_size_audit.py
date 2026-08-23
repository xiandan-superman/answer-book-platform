from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document

from .question_requirements import delivery_figure_required


EMU_PER_CM = 360000
MIN_FIGURE_WIDTH_CM = 8.0
MIN_FIGURE_HEIGHT_CM = 3.8
WIDE_FIGURE_ASPECT_RATIO = 2.4
MIN_WIDE_FIGURE_HEIGHT_CM = 4.4


def _cm(value: int) -> float:
    return round(float(value) / EMU_PER_CM, 2)


def _required_figure_question_ids(structured_exam: dict[str, Any] | None) -> list[str]:
    if not isinstance(structured_exam, dict):
        return []
    required: list[str] = []
    for index, question in enumerate(structured_exam.get("items") or [], start=1):
        if not isinstance(question, dict) or not delivery_figure_required(question):
            continue
        required.append(str(question.get("question_id") or f"question_{index}"))
    return required


def audit_docx_figure_sizes(
    docx_path: Path,
    *,
    structured_exam: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit Word image dimensions with the exam's actual figure requirements."""
    requirement_known = isinstance(structured_exam, dict)
    required_question_ids = _required_figure_question_ids(structured_exam)
    if not docx_path.exists():
        return {
            "schema_version": "answer_book.figure_size_audit.v2",
            "ok": False,
            "applicable": True,
            "requirement_known": requirement_known,
            "required_question_ids": required_question_ids,
            "issues": ["answer_book.docx does not exist"],
            "warnings": [],
            "figures": [],
        }

    document = Document(str(docx_path))
    figures: list[dict[str, Any]] = []
    issues: list[str] = []
    warnings: list[str] = []
    for index, shape in enumerate(document.inline_shapes, start=1):
        width_cm = _cm(shape.width)
        height_cm = _cm(shape.height)
        aspect_ratio = round(width_cm / height_cm, 2) if height_cm else 0.0
        item = {
            "index": index,
            "width_cm": width_cm,
            "height_cm": height_cm,
            "aspect_ratio": aspect_ratio,
        }
        figures.append(item)
        if width_cm < MIN_FIGURE_WIDTH_CM or height_cm < MIN_FIGURE_HEIGHT_CM:
            issues.append(
                f"figure {index} is too small in Word ({width_cm} cm x {height_cm} cm; minimum {MIN_FIGURE_WIDTH_CM} cm x {MIN_FIGURE_HEIGHT_CM} cm)"
            )
        elif aspect_ratio >= WIDE_FIGURE_ASPECT_RATIO and height_cm < MIN_WIDE_FIGURE_HEIGHT_CM:
            issues.append(
                f"wide figure {index} is too short in Word ({width_cm} cm x {height_cm} cm); wide figures need at least {MIN_WIDE_FIGURE_HEIGHT_CM} cm height for labels"
            )
    if not figures and (not requirement_known or required_question_ids):
        warnings.append("no inline figure images were found in answer_book.docx")
    applicable = bool(figures or required_question_ids or not requirement_known)
    return {
        "schema_version": "answer_book.figure_size_audit.v2",
        "ok": not issues,
        "applicable": applicable,
        "skipped_reason": "" if applicable else "no_figure_required",
        "requirement_known": requirement_known,
        "required_question_ids": required_question_ids,
        "issues": issues,
        "warnings": warnings,
        "figures": figures,
    }
