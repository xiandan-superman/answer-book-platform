"""Program gates own structure and delivery, not subject-matter interpretation."""

MACHINE_CONTENT_CODES = frozenset({
    "missing_fragment", "missing_draft", "missing_answer", "missing_analysis",
    "missing_answer_unit_content", "missing_answer_unit_steps",
    "unresolved_formula_placeholder", "missing_confirmed_evidence", "uses_rejected_evidence",
    "choice_missing_option_analysis", "missing_answer_summary", "calculation_missing_formula",
    "calculation_missing_steps", "calculation_missing_subquestion_steps",
    "calculation_invalid_subquestion_number", "calculation_steps_missing_formula_refs",
    "missing_required_figure", "calculation_internal_inconsistency",
    # This is an existing model verdict, not a local keyword judgment.
    "high_risk_correctness_unresolved",
})


def machine_content_rule(code: str) -> bool:
    return code not in RETIRED_CONTENT_CODES


RETIRED_CONTENT_CODES = frozenset(['answer_analysis_comparative_contradiction', 'calculation_answer_missing_unit', 'calculation_formula_dumped_in_analysis', 'calculation_formula_dumped_in_steps', 'calculation_missing_mistake_notes', 'calculation_missing_substitution', 'calculation_step_text_contains_subquestion_heading', 'calculation_steps_not_sequential', 'citation_leaked_into_answer', 'composition_partition_missing_declared_component', 'forbidden_process_text', 'formula_absence_after_retry', 'generic_analysis_phrase', 'incomplete_numeric_slot', 'internal_repair_provenance_leak', 'noncalculation_unintegrated_formulas', 'short_analysis', 'spatial_relation_improper_membership_inference', 'xrd_figure_text_label_mismatch', 'xrd_unsupported_peak_spacing_trend'])


def current_content_report(report: dict) -> dict:
    """Reinterpret identified retired findings without rewriting saved task data.

    Unknown legacy findings remain visible until re-audited, never silently passed.
    """
    result = dict(report)
    removed = False
    for field in ("issues", "warnings"):
        original = report.get(field, [])
        result[field] = [item for item in original if not (
            isinstance(item, dict) and str(item.get("code", "")).removeprefix("content_quality.") in RETIRED_CONTENT_CODES
        )]
        removed = removed or len(result[field]) != len(original)
    if removed:
        result["issue_count"] = len(result["issues"])
        result["warning_count"] = len(result["warnings"])
        result["ok"] = not result["issues"]
    return result
