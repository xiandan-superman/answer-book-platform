from __future__ import annotations

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
