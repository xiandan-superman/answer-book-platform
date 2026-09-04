from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class QualityGovernanceTests(unittest.TestCase):
    def test_unattended_gate_blocks_exact_failures_and_warns_on_heuristics(self) -> None:
        from app.audit_review_gate import enforce_unattended_audit_report

        report = enforce_unattended_audit_report(
            {
                "ok": False,
                "issues": [
                    {"question_id": "q1", "code": "missing_answer", "message": "缺少答案"},
                    {"question_id": "q2", "code": "new_style_preference", "message": "表达可更简洁"},
                ],
                "warnings": [],
            },
            source="content_quality",
        )

        self.assertFalse(report["ok"])
        self.assertEqual(1, report["blocked_count"])
        self.assertEqual("missing_answer", report["issues"][0]["code"])
        self.assertEqual("warning", report["warnings"][0]["severity"])
        self.assertFalse(report["human_review_required"])

    def test_same_subject_property_contradiction_gets_one_bounded_repair_then_block(self) -> None:
        from app.capabilities.quality_governance import governance_for

        rule = governance_for("content_quality.answer_analysis_comparative_contradiction")

        self.assertEqual("repairable", rule.evidence_class.value)
        self.assertEqual("repair_then_block", rule.action_ceiling.value)

        composition = governance_for("content_quality.composition_partition_missing_declared_component")
        self.assertEqual("repair_then_block", composition.action_ceiling.value)

    def test_docx_unknown_issue_is_still_machine_blocked(self) -> None:
        from app.audit_review_gate import enforce_unattended_audit_report

        report = enforce_unattended_audit_report(
            {"ok": False, "issues": ["math object 2 has unexpected XML"], "warnings": []},
            source="docx",
        )

        self.assertFalse(report["ok"])
        self.assertEqual(1, report["blocked_count"])
    def test_rule_classes_have_non_escalating_action_ceilings(self) -> None:
        from app.capabilities.quality_governance import governance_for

        exact = governance_for("render.artifact_missing")
        heuristic = governance_for("content_quality.new_style_preference")
        model = governance_for("figure_visual_qa.new_visual_opinion")
        unknown = governance_for("chemistry.new_rule")

        self.assertEqual("block", exact.action_ceiling.value)
        self.assertEqual("warn_only", heuristic.action_ceiling.value)
        self.assertEqual("warn_only", model.action_ceiling.value)
        self.assertEqual("observe_only", unknown.action_ceiling.value)

    def test_quality_budget_is_bounded_even_for_extreme_environment_values(self) -> None:
        from app.capabilities.quality_budget import QualityExecutionBudget

        with patch.dict(
            os.environ,
            {
                "QUALITY_MAX_CONTENT_REPAIR_QUESTIONS": "999",
                "QUALITY_MAX_FIGURE_REPAIR_ROUNDS": "999",
                "QUALITY_MAX_FIGURE_REPAIR_CANDIDATES": "999",
            },
        ):
            budget = QualityExecutionBudget.from_environment()

        self.assertEqual(20, budget.max_content_repair_questions)
        self.assertEqual(2, budget.max_figure_repair_rounds)
        self.assertEqual(2, budget.max_figure_repair_candidates_per_target)
        self.assertEqual(8, budget.max_selective_review_candidates)
        self.assertEqual(1, budget.max_selective_review_batches)
        self.assertEqual(1, budget.max_selective_review_attempts_per_batch)
        self.assertEqual(0, budget.max_selective_review_provider_fallbacks)
        self.assertEqual(1, budget.max_answer_generation_repair_rounds)
        self.assertFalse(budget.post_content_selective_review_enabled)
        self.assertEqual(1, budget.max_prefigure_correctness_repair_rounds)

    def test_quality_budget_scales_exam_calls_to_180_percent_of_estimate(self) -> None:
        from app.capabilities.quality_budget import QualityExecutionBudget

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUALITY_MAX_MODEL_CALLS_PER_RUN", None)
            os.environ.pop("QUALITY_MODEL_CALL_HEADROOM_PERCENT", None)
            budget = QualityExecutionBudget.from_environment(
                question_count=33,
                task_kind="exam",
                textbook_evidence_enabled=True,
            )

        self.assertEqual(122, budget.estimated_model_calls_per_run)
        self.assertEqual(220, budget.max_model_calls_per_run)
        self.assertEqual(180, budget.model_call_headroom_percent)
        self.assertTrue(budget.model_call_budget_dynamic)

    def test_quality_budget_keeps_floor_for_small_tasks(self) -> None:
        from app.capabilities.quality_budget import QualityExecutionBudget

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUALITY_MAX_MODEL_CALLS_PER_RUN", None)
            budget = QualityExecutionBudget.from_environment(
                question_count=10,
                task_kind="exam",
                textbook_evidence_enabled=False,
            )

        self.assertEqual(16, budget.estimated_model_calls_per_run)
        self.assertEqual(120, budget.max_model_calls_per_run)

    def test_explicit_model_call_limit_overrides_dynamic_budget(self) -> None:
        from app.capabilities.quality_budget import QualityExecutionBudget

        with patch.dict(os.environ, {"QUALITY_MAX_MODEL_CALLS_PER_RUN": "140"}):
            budget = QualityExecutionBudget.from_environment(
                question_count=100,
                task_kind="exam",
                textbook_evidence_enabled=True,
            )

        self.assertEqual(140, budget.max_model_calls_per_run)
        self.assertFalse(budget.model_call_budget_dynamic)


if __name__ == "__main__":
    unittest.main()
