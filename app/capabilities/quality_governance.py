from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum

from .quality import QualityPolicy


class EvidenceClass(str, Enum):
    """How independently a rule result can be verified by the program."""

    DETERMINISTIC = "deterministic"
    REPAIRABLE = "repairable"
    HEURISTIC = "heuristic"
    MODEL_JUDGMENT = "model_judgment"
    UNKNOWN = "unknown"


class ActionCeiling(str, Enum):
    """The strongest unattended action a rule is ever allowed to request."""

    BLOCK = "block"
    REPAIR_THEN_BLOCK = "repair_then_block"
    REPAIR_THEN_WARN = "repair_then_warn"
    WARN_ONLY = "warn_only"
    OBSERVE_ONLY = "observe_only"


@dataclass(frozen=True)
class RuleGovernance:
    code: str
    evidence_class: EvidenceClass
    action_ceiling: ActionCeiling
    verification_strategy: str
    fallback_strategy: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        value["evidence_class"] = self.evidence_class.value
        value["action_ceiling"] = self.action_ceiling.value
        return value


def _rule(
    code: str,
    evidence_class: EvidenceClass,
    action_ceiling: ActionCeiling,
    verification_strategy: str,
    fallback_strategy: str,
) -> RuleGovernance:
    return RuleGovernance(
        code=code,
        evidence_class=evidence_class,
        action_ceiling=action_ceiling,
        verification_strategy=verification_strategy,
        fallback_strategy=fallback_strategy,
    )


# This catalog is deliberately small. A new rule must declare its evidence type
# and unattended action ceiling here; otherwise it remains observation-only.
RULE_GOVERNANCE: dict[str, RuleGovernance] = {
    rule.code: rule
    for rule in (
        _rule(
            "content_quality.missing_fragment",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "exact_structure_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.missing_answer",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "exact_structure_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.missing_analysis",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "required_block_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.missing_answer_unit_content",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "answer_unit_coverage_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.missing_answer_unit_steps",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "calculation_step_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.choice_missing_option_analysis",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "choice_option_coverage_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.calculation_missing_formula",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "calculation_formula_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.calculation_missing_steps",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "calculation_step_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.calculation_internal_inconsistency",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "numeric_equality_and_step_result_postcondition",
            "regenerate_answer",
        ),
        _rule(
            "content_quality.answer_analysis_comparative_contradiction",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "same_subject_same_property_opposite_direction_then_bounded_repair",
            "stop_delivery",
        ),
        _rule(
            "content_quality.composition_partition_missing_declared_component",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "declared_constituents_vs_same_level_numeric_partition_then_bounded_repair",
            "stop_delivery",
        ),
        _rule(
            "content_quality.internal_repair_provenance_leak",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "exact_user_facing_process_phrase_exclusion_then_local_sentence_removal",
            "stop_delivery",
        ),
        _rule(
            "content_quality.spatial_relation_improper_membership_inference",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "attachment_or_adjacency_is_not_taxonomic_membership_then_bounded_evidence_repair",
            "stop_delivery",
        ),
        _rule(
            "content_quality.missing_confirmed_evidence",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "selected_evidence_binding_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.uses_rejected_evidence",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "rejected_evidence_exclusion_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.unresolved_formula_placeholder",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "repair_then_exact_postcondition",
            "stop_delivery",
        ),
        _rule(
            "content_quality.missing_required_figure",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "regenerate_then_artifact_postcondition",
            "stop_delivery",
        ),
        _rule(
            "academic_expression.empty_expression",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "nonempty_expression_postcondition",
            "stop_delivery",
        ),
        _rule(
            "academic_expression.invalid_latex_structure",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "repair_then_balanced_latex_postcondition",
            "stop_delivery",
        ),
        _rule(
            "academic_expression.render_preflight_failed",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "word_omml_preflight_postcondition",
            "stop_delivery",
        ),
        _rule(
            "docx.unresolved_formula_placeholder",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "rebuild_then_exact_postcondition",
            "stop_delivery",
        ),
        _rule(
            "docx.raw_latex_marker",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "rebuild_then_exact_postcondition",
            "stop_delivery",
        ),
        _rule(
            "docx.invalid_math_object",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_BLOCK,
            "rebuild_then_exact_postcondition",
            "stop_delivery",
        ),
        _rule(
            "figure_size.artifact_missing",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "file_existence_postcondition",
            "stop_delivery",
        ),
        _rule(
            "figure_size.artifact_too_small",
            EvidenceClass.HEURISTIC,
            ActionCeiling.REPAIR_THEN_WARN,
            "resize_then_measure",
            "deliver_with_quality_warning",
        ),
        _rule(
            "render.blank_page",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "pixel_distribution_postcondition",
            "stop_delivery",
        ),
        _rule(
            "render.artifact_missing",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "file_existence_postcondition",
            "stop_delivery",
        ),
        _rule(
            "figure_visual_qa.image_missing",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "file_existence_postcondition",
            "stop_delivery",
        ),
        _rule(
            "figure_generation.program_check_failed",
            EvidenceClass.REPAIRABLE,
            ActionCeiling.REPAIR_THEN_WARN,
            "regenerate_then_program_check",
            "use_last_valid_or_omit_optional_figure",
        ),
        _rule(
            "figure_visual_qa.review_failed",
            EvidenceClass.MODEL_JUDGMENT,
            ActionCeiling.WARN_ONLY,
            "reuse_existing_vision_qa",
            "use_last_valid_or_deliver_with_warning",
        ),
    )
}


PREFIX_DEFAULTS: tuple[tuple[str, RuleGovernance], ...] = (
    (
        "docx.",
        _rule(
            "docx.*",
            EvidenceClass.DETERMINISTIC,
            ActionCeiling.BLOCK,
            "docx_xml_postcondition",
            "stop_delivery",
        ),
    ),
    (
        "selective_quality.",
        _rule(
            "selective_quality.*",
            EvidenceClass.MODEL_JUDGMENT,
            ActionCeiling.WARN_ONLY,
            "single_batched_model_review",
            "deliver_with_quality_warning",
        ),
    ),
    (
        "academic_expression.",
        _rule(
            "academic_expression.*",
            EvidenceClass.HEURISTIC,
            ActionCeiling.WARN_ONLY,
            "local_expression_rule",
            "deliver_with_quality_warning",
        ),
    ),
    (
        "content_quality.",
        _rule(
            "content_quality.*",
            EvidenceClass.HEURISTIC,
            ActionCeiling.WARN_ONLY,
            "local_rule_then_optional_batched_model_review",
            "deliver_with_quality_warning",
        ),
    ),
    (
        "figure_visual_qa.",
        _rule(
            "figure_visual_qa.*",
            EvidenceClass.MODEL_JUDGMENT,
            ActionCeiling.WARN_ONLY,
            "reuse_existing_vision_qa",
            "use_last_valid_or_deliver_with_warning",
        ),
    ),
    (
        "figure_generation.",
        _rule(
            "figure_generation.*",
            EvidenceClass.HEURISTIC,
            ActionCeiling.WARN_ONLY,
            "local_program_check",
            "use_last_valid_or_deliver_with_warning",
        ),
    ),
)


UNKNOWN_RULE_GOVERNANCE = _rule(
    "*",
    EvidenceClass.UNKNOWN,
    ActionCeiling.OBSERVE_ONLY,
    "none",
    "record_telemetry_only",
)


def governance_for(code: str) -> RuleGovernance:
    normalized = str(code or "").strip()
    exact = RULE_GOVERNANCE.get(normalized)
    if exact is not None:
        return exact
    for prefix, governance in PREFIX_DEFAULTS:
        if normalized.startswith(prefix):
            return replace(governance, code=normalized)
    return replace(UNKNOWN_RULE_GOVERNANCE, code=normalized or "unknown")


def unattended_status(governance: RuleGovernance) -> tuple[str, list[str]]:
    """Return a contract status that never depends on human-labelled samples."""

    ceiling = governance.action_ceiling
    if ceiling is ActionCeiling.BLOCK:
        return "enforceable_by_machine_contract", ["deterministic_postcondition"]
    if ceiling is ActionCeiling.REPAIR_THEN_BLOCK:
        return "repair_then_machine_enforceable", ["post_repair_deterministic_postcondition"]
    if ceiling is ActionCeiling.REPAIR_THEN_WARN:
        return "bounded_repair_advisory", ["quality_threshold_is_not_ground_truth"]
    if ceiling is ActionCeiling.WARN_ONLY:
        return "advisory_only", ["heuristic_or_model_judgment_cannot_block_unattended"]
    return "observation_only", ["rule_not_governed_for_unattended_enforcement"]


def build_unattended_policy(*, minimum_block_confidence: float = 0.98) -> QualityPolicy:
    blocking = {
        code
        for code, governance in RULE_GOVERNANCE.items()
        if governance.action_ceiling in {ActionCeiling.BLOCK, ActionCeiling.REPAIR_THEN_BLOCK}
    }
    warning = set(RULE_GOVERNANCE) - blocking
    return QualityPolicy(
        blocking_codes=frozenset(blocking),
        warning_codes=frozenset(warning),
        minimum_block_confidence=minimum_block_confidence,
    )
