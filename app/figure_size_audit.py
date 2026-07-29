from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document


EMU_PER_CM = 360000
MIN_FIGURE_WIDTH_CM = 8.0
MIN_FIGURE_HEIGHT_CM = 3.8
WIDE_FIGURE_ASPECT_RATIO = 2.4
MIN_WIDE_FIGURE_HEIGHT_CM = 4.4


def _cm(value: int) -> float:
    return round(float(value) / EMU_PER_CM, 2)


def audit_docx_figure_sizes(docx_path: Path) -> dict[str, Any]:
    """Audit the dimensions Word actually assigns to embedded figure images."""
    if not docx_path.exists():
        return {"ok": False, "issues": ["answer_book.docx does not exist"], "warnings": [], "figures": []}

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
    if not figures:
        warnings.append("no inline figure images were found in answer_book.docx")
    return {"ok": not issues, "issues": issues, "warnings": warnings, "figures": figures}
