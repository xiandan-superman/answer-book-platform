"""Content capability contracts and the application-wide registry."""

from .academic_expressions import AcademicExpression, ExpressionKind, audit_academic_expressions
from .contracts import CapabilityManifest, ExpressionRule, KeywordRule
from .expression_rendering import (
    ExpressionPresentation,
    ExpressionRenderPlan,
    ExpressionTypography,
    build_expression_render_plan,
    render_expression_omml,
)
from .figure_semantics import (
    FigureRenderDecision,
    FigureSemanticContract,
    RenderStrategy,
    SourceImagePolicy,
    audit_figure_render_outcome,
    choose_figure_render_strategy,
)
from .quality import FindingSeverity, PolicyAction, QualityFinding, QualityPolicy
from .quality_budget import QualityExecutionBudget
from .quality_governance import ActionCeiling, EvidenceClass, RuleGovernance, governance_for
from .registry import CapabilityRegistry
from .rendering import RendererRegistry, assemble_renderer_registry, renderer_binding_issues
from .selective_review import collect_selective_review_candidates, review_selective_quality
from .shadow_quality import build_shadow_quality_report
from .text_expression_rendering import (
    TextExpressionRenderPlan,
    build_text_expression_render_plans,
    normalize_standard_state_latex,
    reaction_text_to_latex,
    repair_json_escaped_latex,
)

__all__ = [
    "CapabilityManifest",
    "CapabilityRegistry",
    "ActionCeiling",
    "EvidenceClass",
    "AcademicExpression",
    "ExpressionKind",
    "ExpressionRule",
    "ExpressionPresentation",
    "ExpressionRenderPlan",
    "ExpressionTypography",
    "TextExpressionRenderPlan",
    "FigureRenderDecision",
    "FigureSemanticContract",
    "FindingSeverity",
    "KeywordRule",
    "PolicyAction",
    "QualityFinding",
    "QualityPolicy",
    "QualityExecutionBudget",
    "RendererRegistry",
    "RenderStrategy",
    "RuleGovernance",
    "SourceImagePolicy",
    "build_shadow_quality_report",
    "assemble_renderer_registry",
    "build_expression_render_plan",
    "build_text_expression_render_plans",
    "collect_selective_review_candidates",
    "choose_figure_render_strategy",
    "audit_academic_expressions",
    "audit_figure_render_outcome",
    "governance_for",
    "normalize_standard_state_latex",
    "reaction_text_to_latex",
    "repair_json_escaped_latex",
    "review_selective_quality",
    "render_expression_omml",
    "renderer_binding_issues",
]
