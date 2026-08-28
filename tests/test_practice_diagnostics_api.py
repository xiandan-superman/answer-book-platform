from __future__ import annotations

from app import practice_diagnostics, practice_jobs


def test_failed_practice_job_diagnostics_exposes_saved_traceback_and_failure_context(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    secret = "sk-test-secret-value-1234567890"
    created = practice_jobs.create_practice_job(
        "generate_from_plan",
        {"source_mode": "exam", "plan": {"blueprint": {"exercise_plan": []}}},
    )
    practice_jobs.update_practice_job(
        created["job_id"],
        status="failed",
        current_stage="failed",
        checkpoint_stage="model_generation_complete",
        partial_exercises=[{"plan_item_id": "p1", "stem": "已完成"}],
        error=f"保存失败 api_key={secret}",
        failure_context={"failure_type": "history_save", "api_key": secret, "path": "history.json"},
        diagnostic_context={
            "exception_type": "RuntimeError",
            "traceback": f"Traceback\nRuntimeError: save failed {secret}",
        },
    )

    report = practice_diagnostics.build_practice_diagnostics(created["job_id"])
    serialized = str(report)

    assert report["record_type"] == "practice_job"
    assert report["diagnostic_context"]["exception_type"] == "RuntimeError"
    assert "Traceback" in report["diagnostic_context"]["traceback"]
    assert report["failure_context"]["failure_type"] == "history_save"
    assert report["recovery"]["postprocess_recoverable"] is True
    assert report["recovery"]["completed_question_checkpoint_count"] == 1
    assert secret not in serialized
    assert "postprocess_checkpoint" not in serialized
