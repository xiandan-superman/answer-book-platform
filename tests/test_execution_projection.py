from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from app.execution_projection import (
    build_execution_projection_report,
    project_exam_task,
    project_practice_job,
)
from app.server import _practice_job_task_row, _stage_progress_percent


def _finding_codes(projection: dict) -> set[str]:
    return {str(item["code"]) for item in projection["findings"]}


def test_failed_practice_terminal_progress_is_not_output_completion() -> None:
    record = {
        "job_id": "generation-failed",
        "operation": "generate_from_plan",
        "status": "failed",
        "current_stage": "failed",
        "completed_count": 0,
        "total_count": 5,
    }

    projection = project_practice_job(record)

    assert projection["lifecycle"]["outcome"] == "failed"
    assert projection["presentation_progress"] == {
        "reported_percent": 100,
        "semantic": "lifecycle_terminated",
        "effective_stage": "failed",
        "represents_output_completion": False,
    }
    assert projection["work_completion"]["completion_percent"] == 0
    assert "terminal_progress_not_output_completion" in _finding_codes(projection)
    assert not any(item["contradiction"] for item in projection["findings"])
    assert _practice_job_task_row(record)["progress_percent"] == 100


def test_completed_practice_uses_durable_result_when_counters_are_stale() -> None:
    projection = project_practice_job(
        {
            "operation": "generate_from_plan",
            "status": "completed",
            "current_stage": "completed",
            "completed_count": 0,
            "generated_count": 0,
            "total_count": 3,
            "result": {"exercises": [{}, {}, {}]},
        }
    )

    assert projection["snapshot"]["completed_count"] == 0
    assert projection["work_completion"]["completed_units"] == 3
    assert projection["work_completion"]["output_complete"] is True
    assert projection["work_completion"]["evidence"] == "durable_result_exercises"
    assert "successful_result_has_stale_unit_counters" in _finding_codes(projection)
    assert "successful_task_has_incomplete_output_units" not in _finding_codes(projection)


def test_failed_exam_after_all_units_complete_remains_a_failure() -> None:
    projection = project_exam_task(
        {
            "status": "failed",
            "current_stage": "docx",
            "completed_count": 12,
            "total_count": 12,
        }
    )

    assert projection["lifecycle"]["outcome"] == "failed"
    assert projection["work_completion"]["output_complete"] is True
    assert projection["presentation_progress"]["reported_percent"] < 100
    assert "terminal_failure_after_output_units_complete" in _finding_codes(projection)
    assert not any(item["contradiction"] for item in projection["findings"])


def test_completed_with_issues_is_a_success_terminal_state() -> None:
    projection = project_exam_task(
        {
            "status": "completed_with_issues",
            "current_stage": "completed",
            "completed_count": 5,
            "total_count": 5,
        }
    )

    assert projection["lifecycle"] == {
        "status": "completed_with_issues",
        "outcome": "succeeded_with_issues",
        "terminal": True,
        "successful": True,
    }
    assert projection["presentation_progress"]["reported_percent"] == 100
    assert projection["findings"] == []


def test_shadow_projection_reports_event_divergence_without_rewriting_snapshot() -> None:
    projection = project_exam_task(
        {"status": "failed", "current_stage": "docx"},
        task_events=[
            {
                "event": "task_updated",
                "payload": {"status": "running", "current_stage": "docx"},
            }
        ],
    )

    assert projection["snapshot"]["status"] == "failed"
    assert projection["task_events"]["latest_task_update_matches_snapshot"] is False
    assert "task_event_snapshot_divergence" in _finding_codes(projection)
    assert projection["business_state_changed"] is False


def test_terminal_task_with_unresolved_model_intent_is_an_evidence_gap() -> None:
    projection = project_practice_job(
        {
            "operation": "plan",
            "status": "failed",
            "current_stage": "failed",
        },
        model_execution={
            "intent_count": 1,
            "result_count": 0,
            "unresolved_intent_count": 1,
            "covered_by_execution_ledger": True,
        },
    )

    assert "terminal_task_has_unknown_model_result" in _finding_codes(projection)
    assert not any(item["contradiction"] for item in projection["findings"])


def test_cancelled_practice_job_is_not_promoted_by_complete_counters() -> None:
    projection = project_practice_job(
        {
            "operation": "generate_from_contract",
            "status": "cancelled",
            "current_stage": "cancelled",
            "completed_count": 4,
            "total_count": 4,
            "result": {"exercises": [{}, {}, {}, {}]},
        }
    )

    assert projection["lifecycle"]["outcome"] == "cancelled"
    assert projection["lifecycle"]["successful"] is False
    assert projection["work_completion"]["output_complete"] is True
    assert "terminal_failure_after_output_units_complete" in _finding_codes(projection)
    assert projection["business_state_changed"] is False


def test_recovery_event_keeps_queued_task_nonterminal() -> None:
    projection = project_exam_task(
        {
            "status": "queued",
            "current_stage": "answer_generation",
            "completed_count": 2,
            "total_count": 5,
        },
        task_events=[
            {"event": "task_recovery_queued", "payload": {"interrupted_stage": "answer_generation"}},
            {
                "event": "task_updated",
                "payload": {"status": "queued", "current_stage": "answer_generation"},
            },
        ],
    )

    assert projection["lifecycle"]["outcome"] == "queued"
    assert projection["lifecycle"]["terminal"] is False
    assert projection["work_completion"]["completion_percent"] == 40
    assert projection["task_events"]["latest_task_update_matches_snapshot"] is True
    assert projection["findings"] == []


def test_report_aggregates_findings_without_exposing_task_ids(tmp_path) -> None:
    tasks_dir = tmp_path / "tasks"
    exam_dir = tasks_dir / "private-exam-id"
    (exam_dir / "stage_outputs").mkdir(parents=True)
    (exam_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "private-exam-id",
                "status": "failed",
                "current_stage": "docx",
                "completed_count": 2,
                "total_count": 2,
            }
        ),
        encoding="utf-8",
    )
    (exam_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "event": "task_updated",
                "payload": {"status": "failed", "current_stage": "docx"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    jobs_dir = tmp_path / "practice_jobs"
    jobs_dir.mkdir()
    (jobs_dir / "generation_private-job-id.json").write_text(
        json.dumps(
            {
                "job_id": "generation_private-job-id",
                "operation": "generate_from_plan",
                "status": "failed",
                "current_stage": "failed",
                "completed_count": 0,
                "total_count": 4,
            }
        ),
        encoding="utf-8",
    )
    ledger = tmp_path / "model_execution_events.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "event_type": "invocation.intent",
                "task_id": "generation_private-job-id",
                "invocation_id": "invocation-private",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_execution_projection_report(
        tasks_dir=tasks_dir,
        practice_jobs_dir=jobs_dir,
        model_execution_ledger=ledger,
    )

    assert report["sample_count"] == 2
    assert report["exam_task_count"] == 1
    assert report["practice_job_count"] == 1
    assert report["finding_counts"]["terminal_failure_after_output_units_complete"] == 1
    assert report["finding_counts"]["terminal_progress_not_output_completion"] == 1
    assert report["finding_counts"]["terminal_task_has_unknown_model_result"] == 1
    assert report["real_state_contradiction_task_count"] == 0
    assert report["readiness"]["authoritative_projection_ready"] is False
    assert report["added_model_calls"] == 0
    serialized = json.dumps(report, ensure_ascii=False)
    assert "private-exam-id" not in serialized
    assert "private-job-id" not in serialized
    assert "invocation-private" not in serialized


def test_shared_exam_progress_contract_preserves_existing_percentages() -> None:
    assert _stage_progress_percent("environment") == 4
    assert _stage_progress_percent("docx") == 71
    assert _stage_progress_percent("completed") == 100


def test_execution_projection_api_is_read_only(monkeypatch) -> None:
    from app import server as platform_server

    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        platform_server,
        "build_execution_projection_report",
        lambda: {
            "schema_version": "answer_book.execution_projection.v1",
            "mode": "shadow",
            "authority": "observation_only",
            "enforced": False,
            "business_state_changed": False,
            "sample_count": 3,
            "added_model_calls": 0,
            "added_tokens": 0,
            "added_network_requests": 0,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_port}/api/quality/execution-projection"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)

    assert payload["mode"] == "shadow"
    assert payload["authority"] == "observation_only"
    assert payload["enforced"] is False
    assert payload["business_state_changed"] is False
    assert payload["added_model_calls"] == 0
