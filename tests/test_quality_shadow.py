from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _write(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class QualityShadowTests(unittest.TestCase):
    def test_content_quality_entries_keep_code_subject_and_severity(self) -> None:
        from app.capabilities.audit_adapters import findings_from_report

        findings = findings_from_report(
            {
                "issues": [
                    {
                        "question_id": "q1",
                        "code": "missing_required_figure",
                        "message": "缺少题目要求的图片。",
                        "severity": "issue",
                    }
                ],
                "warnings": [],
            },
            source="content_quality",
        )
        self.assertEqual(1, len(findings))
        self.assertEqual("content_quality.missing_required_figure", findings[0].code)
        self.assertEqual("q1", findings[0].subject_id)
        self.assertEqual("error", findings[0].to_dict()["severity"])

    def test_legacy_docx_and_render_messages_receive_stable_codes(self) -> None:
        from app.capabilities.audit_adapters import findings_from_report, legacy_issue_code

        findings = findings_from_report(
            {
                "issues": [
                    "paragraph 3 raw latex marker in normal text: \\frac",
                    "page-1.png appears blank or nearly uniform",
                ],
                "warnings": [],
            },
            source="artifact",
            code_resolver=legacy_issue_code,
        )
        self.assertEqual(
            {"artifact.raw_latex_marker", "artifact.blank_page"},
            {finding.code for finding in findings},
        )

    def test_shadow_report_never_enforces_policy(self) -> None:
        from app.capabilities.shadow_quality import build_shadow_quality_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            _write(
                stage / "content_quality_audit.json",
                {
                    "ok": False,
                    "issues": [
                        {
                            "question_id": "q1",
                            "code": "missing_required_figure",
                            "message": "缺少题目要求的图片。",
                            "severity": "issue",
                        }
                    ],
                    "warnings": [],
                },
            )
            report = build_shadow_quality_report(stage)
            saved = json.loads((stage / "quality_shadow_report.json").read_text(encoding="utf-8"))

        self.assertEqual("shadow", report["mode"])
        self.assertFalse(report["enforced"])
        self.assertEqual(1, report["would_block_count"])
        self.assertEqual(1, report["finding_count"])
        self.assertFalse(saved["enforced"])

    def test_shadow_report_aggregates_cross_domain_findings(self) -> None:
        from app.capabilities.shadow_quality import build_shadow_quality_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            _write(
                stage / "content_quality_audit.json",
                {
                    "ok": True,
                    "issues": [],
                    "warnings": [{"question_id": "q1", "code": "short_analysis", "message": "解析可能过短。"}],
                },
            )
            _write(
                stage / "docx_audit.json",
                {"ok": False, "issues": ["paragraph 2 unresolved formula placeholder in normal text: {f1}"], "warnings": []},
            )
            _write(
                stage / "figure_generation_audit.json",
                {
                    "items": [
                        {
                            "question_id": "q2",
                            "figure_id": "q2_fig_01",
                            "program_check_issues": [],
                            "risk_notes": ["专业准确性需复核。"],
                        }
                    ]
                },
            )
            report = build_shadow_quality_report(stage)

        self.assertEqual(3, report["finding_count"])
        self.assertEqual({"content_quality", "docx", "figure_generation"}, set(report["available_sources"]))
        self.assertEqual(1, report["would_block_count"])
        self.assertEqual(2, report["would_warn_count"])

    def test_missing_audits_produce_empty_observation_not_failure(self) -> None:
        from app.capabilities.shadow_quality import build_shadow_quality_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            report = build_shadow_quality_report(Path(raw_tmp))

        self.assertEqual([], report["available_sources"])
        self.assertEqual(0, report["finding_count"])
        self.assertFalse(report["enforced"])

    def test_unattended_governance_caps_model_judgment_at_warning(self) -> None:
        from app.capabilities.shadow_quality import build_shadow_quality_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            _write(
                stage / "figure_visual_qa.json",
                {
                    "enabled": True,
                    "items": [
                        {
                            "question_id": "q1",
                            "figure_id": "f1",
                            "qa": {"ok": False, "summary": "图形语义可能不完整"},
                        }
                    ],
                    "skipped": [],
                },
            )
            report = build_shadow_quality_report(stage)

        self.assertEqual("unattended", report["governance_mode"])
        self.assertFalse(report["human_review_required"])
        self.assertEqual(0, report["would_block_count"])
        self.assertEqual("warn", report["findings"][0]["action"])
        self.assertEqual("warn_only", report["findings"][0]["governance"]["action_ceiling"])

    def test_unattended_governance_keeps_exact_missing_artifact_blockable(self) -> None:
        from app.capabilities.shadow_quality import build_shadow_quality_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            _write(
                stage / "render_audit.json",
                {"ok": False, "issues": ["rendered artifact does not exist"], "warnings": []},
            )
            report = build_shadow_quality_report(stage)

        self.assertEqual(1, report["would_block_count"])
        self.assertEqual("deterministic", report["findings"][0]["governance"]["evidence_class"])


if __name__ == "__main__":
    unittest.main()
