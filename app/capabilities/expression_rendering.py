from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from functools import lru_cache
from typing import Any

from ..omml import normalize_latex, omml_from_latex
from .academic_expressions import classify_formula


class ExpressionPresentation(str, Enum):
    INLINE = "inline"
    DISPLAY = "display"


class ExpressionTypography(str, Enum):
    ALL_ITALIC = "all_italic"
    MATH_MIXED = "all_italic"
    CHEMISTRY_UPRIGHT = "all_italic"


@dataclass(frozen=True)
class ExpressionRenderPlan:
    expression_id: str
    question_id: str
    raw: str
    normalized: str
    render_latex: str
    expression_kind: str
    presentation: ExpressionPresentation
    typography: ExpressionTypography
    role: str
    location: str
    capability_id: str
    rule_id: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["presentation"] = self.presentation.value
        value["typography"] = self.typography.value
        return value


def build_expression_render_plan(
    latex: str,
    *,
    question_id: str = "",
    location: str = "formula",
    context: str = "",
    role: str = "relation",
    display: bool = True,
) -> ExpressionRenderPlan:
    expression = classify_formula(
        str(latex or "").strip(),
        question_id=question_id,
        location=location,
        context=context,
    )
    return ExpressionRenderPlan(
        expression_id=expression.expression_id,
        question_id=question_id,
        raw=expression.raw,
        normalized=expression.normalized,
        render_latex=normalize_latex(expression.raw),
        expression_kind=expression.kind,
        presentation=ExpressionPresentation.DISPLAY if display else ExpressionPresentation.INLINE,
        typography=ExpressionTypography.ALL_ITALIC,
        role=str(role or "relation").strip() or "relation",
        location=location,
        capability_id=expression.capability_id,
        rule_id=expression.rule_id,
    )


def render_expression_omml(
    latex: str,
    *,
    question_id: str = "",
    location: str = "formula",
    context: str = "",
    role: str = "relation",
    display: bool = True,
    expression_kind: str = "",
):
    if expression_kind:
        return omml_from_latex(normalize_latex(latex), expression_kind=expression_kind)
    plan = build_expression_render_plan(
        latex,
        question_id=question_id,
        location=location,
        context=context,
        role=role,
        display=display,
    )
    return omml_from_latex(plan.render_latex, expression_kind=plan.expression_kind)


@lru_cache(maxsize=4096)
def _preflight_render_latex(render_latex: str, expression_kind: str) -> str:
    try:
        rendered = omml_from_latex(render_latex, expression_kind=expression_kind)
    except Exception as exc:
        return str(exc)[:500]
    if not list(rendered):
        return "OMML renderer returned an empty math object"
    if any(
        str(node.tag).endswith("}t") and any(marker in str(node.text or "") for marker in ("$", "\\"))
        for node in rendered.iter()
    ):
        return "OMML renderer preserved unconverted LaTeX or JSON escape markers"
    return ""


def preflight_expression_render(plan: ExpressionRenderPlan) -> str:
    return _preflight_render_latex(plan.render_latex, plan.expression_kind)
