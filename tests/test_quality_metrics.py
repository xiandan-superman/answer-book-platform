from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _task(tasks_dir: Path, task_id: str, findings: list[dict[str, object]], *, status: str = "completed") -> Path:
    task = tasks_dir / task_id
    stage = task / "stage_outputs"
    _write(task / "task.json", {"task_id": task_id, "status": status, "updated_at": "2026-08-12 10:00:00"})
    _write(
        stage / "quality_shadow_report.json",
        {
            "schema_version": "answer_book.quality_shadow.v1",
            "enforced": False,
            "finding_count": len(findings),
            "would_block_count": sum(1 for item in findings if item.get("action") == "block"),
            "would_warn_count": sum(1 for item in findings if item.get("action") == "warn"),
            "findings": findings,
        },
    )
    return task


def _finding(code: str, subject_id: str, *, action: str = "warn", confidence: float = 0.9) -> dict[str, object]:
    return {
        "code": code,
        "source": code.split(".", 1)[0],
        "message": f"{code} message",
        "severity": "warning",
        "confidence": confidence,
        "subject_id": subject_id,
        "evidence": {},
        "action": action,
    }


class QualityMetricsTests(unittest.TestCase):
    def test_aggregates_tasks_subjects_actions_and_duplicates(self) -> None:
        from app.capabilities.quality_metrics import build_quality_metrics_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            tasks = Path(raw_tmp) / "tasks"
            _task(
                tasks,
                "task-1",
                [
                    _finding("content_quality.short_analysis", "q1"),
                    _finding("content_quality.short_analysis", "q1"),
                    _finding("docx.raw_latex_marker", "", action="block", confidence=0.99),
                ],
            )
            _task(tasks, "task-2", [_finding("content_quality.short_analysis", "q2")], status="failed")
            report = build_quality_metrics_report(tasks, cache_path=None, use_cache=False)

        self.assertTrue(report["observation_only"])
        self.assertFalse(report["automatic_promotion_enabled"])
        self.assertEqual("unattended", report["governance_mode"])
        self.assertFalse(report["human_review_required"])
        self.assertEqual(2, report["task_count"])
        self.assertEqual(1, report["tasks_would_block"])
        self.assertEqual(4, report["finding_count"])
        rules = {item["code"]: item for item in report["rules"]}
        short = rules["content_quality.short_analysis"]
        self.assertEqual(3, short["occurrence_count"])
        self.assertEqual(2, short["affected_task_count"])
        self.assertEqual(2, short["affected_subject_count"])
        self.assertEqual(1, short["duplicate_count"])
        self.assertEqual({"warn": 3}, short["action_counts"])

    def test_incremental_cache_reuses_unchanged_and_prunes_deleted_tasks(self) -> None:
        from app.capabilities.quality_metrics import build_quality_metrics_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            tasks = root / "tasks"
            cache = root / "cache.json"
            first_task = _task(tasks, "task-1", [_finding("content_quality.short_analysis", "q1")])
            first = build_quality_metrics_report(tasks, cache_path=cache)
            second = build_quality_metrics_report(tasks, cache_path=cache)
            for child in (first_task / "stage_outputs").iterdir():
                child.unlink()
            (first_task / "stage_outputs").rmdir()
            (first_task / "task.json").unlink()
            first_task.rmdir()
            third = build_quality_metrics_report(tasks, cache_path=cache)

        self.assertEqual(1, first["cache"]["parsed_task_count"])
        self.assertEqual(1, second["cache"]["reused_task_count"])
        self.assertEqual(1, third["cache"]["pruned_task_count"])
        self.assertEqual(0, third["task_count"])

    def test_human_decisions_are_optional_telemetry_not_a_governance_dependency(self) -> None:
        from app.capabilities.quality_metrics import build_quality_metrics_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            tasks = Path(raw_tmp) / "tasks"
            task = _task(tasks, "task-1", [_finding("content_quality.missing_answer", "q1", action="block", confidence=0.99)])
            request = {
                "request_id": "content_quality_123",
                "stage": "content_quality",
                "items": [{"question_id": "q1", "code": "missing_answer", "message": "答案缺失"}],
            }
            _write(task / "review_decision_request.json", request)
            _write(task / "review_decision_response.json", {"request_id": "content_quality_123", "decision": "reject"})
            report = build_quality_metrics_report(tasks, cache_path=None, use_cache=False)

        rule = report["rules"][0]
        self.assertEqual(1, rule["human_review_count"])
        self.assertEqual(0, rule["human_allowed_count"])
        self.assertEqual(1, rule["human_rejected_count"])
        self.assertTrue(rule["human_review_is_optional_telemetry"])
        self.assertEqual("not_applicable_unattended", rule["promotion_status"])
        self.assertEqual("enforceable_by_machine_contract", rule["unattended_status"])
        self.assertFalse(report["automatic_promotion_enabled"])

    def test_frequent_heuristic_rule_cannot_promote_to_block(self) -> None:
        from app.capabilities.quality_metrics import build_quality_metrics_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            tasks = Path(raw_tmp) / "tasks"
            for index in range(12):
                _task(
                    tasks,
                    f"task-{index}",
                    [_finding("content_quality.short_analysis", f"q{index}", confidence=1.0)],
                )
            report = build_quality_metrics_report(tasks, cache_path=None, use_cache=False)

        rule = report["rules"][0]
        self.assertEqual(12, rule["occurrence_count"])
        self.assertEqual("heuristic", rule["evidence_class"])
        self.assertEqual("warn_only", rule["action_ceiling"])
        self.assertEqual("advisory_only", rule["unattended_status"])

    def test_unknown_rule_defaults_to_observation_only(self) -> None:
        from app.capabilities.quality_metrics import build_quality_metrics_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            tasks = Path(raw_tmp) / "tasks"
            _task(tasks, "task-1", [_finding("new_subject.unregistered_rule", "q1", confidence=1.0)])
            report = build_quality_metrics_report(tasks, cache_path=None, use_cache=False)

        rule = report["rules"][0]
        self.assertEqual("unknown", rule["evidence_class"])
        self.assertEqual("observe_only", rule["action_ceiling"])

    def test_missing_saved_shadow_is_evaluated_read_only(self) -> None:
        from app.capabilities.quality_metrics import build_quality_metrics_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            tasks = Path(raw_tmp) / "tasks"
            task = tasks / "task-1"
            _write(task / "task.json", {"task_id": "task-1", "status": "completed"})
            _write(
                task / "stage_outputs" / "content_quality_audit.json",
                {"issues": [], "warnings": [{"question_id": "q1", "code": "short_analysis", "message": "解析过短"}]},
            )
            report = build_quality_metrics_report(tasks, cache_path=None, use_cache=False)

        self.assertEqual(1, report["finding_count"])
        self.assertFalse((task / "stage_outputs" / "quality_shadow_report.json").exists())

    def test_stale_saved_shadow_is_recomputed_without_overwriting_task(self) -> None:
        from app.capabilities.quality_metrics import build_quality_metrics_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            tasks = Path(raw_tmp) / "tasks"
            task = _task(tasks, "task-1", [])
            shadow = task / "stage_outputs" / "quality_shadow_report.json"
            audit = task / "stage_outputs" / "content_quality_audit.json"
            _write(audit, {"issues": [], "warnings": [{"question_id": "q1", "code": "short_analysis", "message": "解析过短"}]})
            shadow_stat = shadow.stat()
            os.utime(audit, ns=(shadow_stat.st_atime_ns, shadow_stat.st_mtime_ns + 1_000_000))
            original_shadow = shadow.read_text(encoding="utf-8")
            report = build_quality_metrics_report(tasks, cache_path=None, use_cache=False)
            saved_shadow = shadow.read_text(encoding="utf-8")

        self.assertEqual(1, report["finding_count"])
        self.assertEqual(original_shadow, saved_shadow)


if __name__ == "__main__":
    unittest.main()
