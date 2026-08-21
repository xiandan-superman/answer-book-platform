from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.pipeline import _review_selective_quality_with_fallback
from app.pipeline_telemetry import PipelineRunTelemetry


class PipelineRuntimeStateTests(unittest.TestCase):
    def test_stop_persists_end_time_and_final_elapsed_time(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            status_path = Path(raw_tmp) / "pipeline_status.json"
            telemetry = PipelineRunTelemetry(
                task_id="runtime-test",
                status_path=status_path,
                quality_governance={"mode": "unattended"},
            )
            telemetry.stop()
            report = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertTrue(report["ended_at"])
        self.assertGreaterEqual(report["elapsed_seconds"], 0)

    def test_reviewer_fallback_runs_once_and_records_routing(self) -> None:
        primary = SimpleNamespace(name="primary", api_key="key")
        fallback = SimpleNamespace(name="fallback", api_key="key")
        degraded = {
            "status": "degraded",
            "selection": {"selected_count": 1},
            "remote_model_calls": 1,
            "remote_model_calls_this_run": 1,
        }
        completed = {
            "status": "completed",
            "selection": {"selected_count": 1},
            "remote_model_calls": 1,
            "remote_model_calls_this_run": 1,
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            report_path = Path(raw_tmp) / "review.json"
            with patch(
                "app.pipeline.review_selective_quality",
                side_effect=[degraded, completed],
            ) as review:
                report = _review_selective_quality_with_fallback(
                    report_json=report_path,
                    primary_provider=primary,
                    primary_model="p-model",
                    fallback_provider=fallback,
                    fallback_model="f-model",
                    max_provider_fallbacks=1,
                    academic_report={},
                    content_quality_report={},
                    structured_exam={"items": []},
                    fragments_data={"fragments": []},
                )

        self.assertEqual(2, review.call_count)
        self.assertEqual("completed", report["status"])
        self.assertTrue(report["fallback_routing"]["used"])
        self.assertEqual("fallback", report["fallback_routing"]["fallback_provider"])
        self.assertEqual(2, report["remote_model_calls_this_run"])

    def test_reviewer_fallback_is_disabled_by_default(self) -> None:
        primary = SimpleNamespace(name="primary", api_key="key")
        fallback = SimpleNamespace(name="fallback", api_key="key")
        degraded = {
            "status": "degraded",
            "selection": {"selected_count": 1},
            "remote_model_calls_this_run": 1,
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            with patch("app.pipeline.review_selective_quality", return_value=degraded) as review:
                report = _review_selective_quality_with_fallback(
                    report_json=Path(raw_tmp) / "review.json",
                    primary_provider=primary,
                    primary_model="p-model",
                    fallback_provider=fallback,
                    fallback_model="f-model",
                    academic_report={},
                    content_quality_report={},
                    structured_exam={"items": []},
                    fragments_data={"fragments": []},
                )

        self.assertEqual(1, review.call_count)
        self.assertEqual("degraded", report["status"])

    def test_previous_fallback_does_not_permanently_bypass_recovered_primary(self) -> None:
        primary = SimpleNamespace(name="primary", api_key="key")
        fallback = SimpleNamespace(name="fallback", api_key="key")
        completed = {"status": "completed", "selection": {"selected_count": 1}}
        with tempfile.TemporaryDirectory() as raw_tmp:
            report_path = Path(raw_tmp) / "review.json"
            report_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "fallback_routing": {
                            "used": True,
                            "primary_provider": "primary",
                            "fallback_provider": "fallback",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("app.pipeline.review_selective_quality", return_value=completed) as review:
                report = _review_selective_quality_with_fallback(
                    report_json=report_path,
                    primary_provider=primary,
                    primary_model="p-model",
                    fallback_provider=fallback,
                    fallback_model="f-model",
                    academic_report={},
                    content_quality_report={},
                    structured_exam={"items": []},
                    fragments_data={"fragments": []},
                )

        self.assertEqual(1, review.call_count)
        self.assertIs(primary, review.call_args.kwargs["provider"])
        self.assertEqual("completed", report["status"])


if __name__ == "__main__":
    unittest.main()
