from copy import deepcopy

import pytest

from app.task_read_model import build_exam_run, build_practice_runs


@pytest.mark.parametrize("profile", ["evidence_backed", "question_only"])
@pytest.mark.parametrize("status", ["queued", "running", "paused", "needs_input", "failed", "cancelled"])
def test_saved_exam_candidate_preserves_current_lifecycle(profile, status):
    row = {"task_id": "exam-demo", "status": status, "current_stage": "answer_generation", "analysis_profile": profile}
    report = {"final_acceptance": {"status": "completed_with_issues", "delivery_tier": "review_candidate", "delivery_ready": True, "formal_acceptance_passed": False}}
    before = deepcopy(report)
    baseline = build_exam_run(row)
    result = build_exam_run(row, report)
    assert result["status"] == status
    for action in ["pause", "resume", "cancel", "retry", "start", "delete"]:
        assert result["capabilities"][action] == baseline["capabilities"][action]
    assert result["capabilities"]["view_files"] is True
    assert result["capabilities"]["view_result"] is True
    assert result["quality_summary"] == before == report


def test_completed_exam_candidate_still_downloadable():
    result = build_exam_run({"status": "completed"}, {"final_acceptance": {"status": "completed_with_issues", "delivery_tier": "review_candidate", "delivery_ready": True}})
    assert result["status"] == "completed_with_issues"
    assert result["capabilities"]["download"] is True


def history(kind, updated="2026-09-06T10:00:00+08:00"):
    return {"history_id": "practice-old", "task_kind": kind, "request": {"practice_batch_id": "batch"}, "updated_at": updated, "status": "completed", "data": {"exercises": [{"stem": "saved"}]}}


def job(kind, status, updated="2026-09-06T11:00:00+08:00"):
    return {"job_id": "generation-new", "task_kind": kind, "practice_batch_id": "batch", "operation": "generate_from_plan", "status": status, "created_at": updated, "updated_at": updated}


@pytest.mark.parametrize("kind", ["practice", "knowledge"])
@pytest.mark.parametrize("status", ["queued", "running", "paused", "failed", "cancelled"])
def test_continuation_controls_coexist_with_saved_history(kind, status):
    saved, current = history(kind), job(kind, status)
    before = deepcopy((saved, current))
    result = {row["task_id"]: row for row in build_practice_runs([current], [saved])}
    assert set(result) == {"practice-old", "generation-new"}
    assert result["generation-new"]["status"] == status
    assert result["generation-new"]["capabilities"] == build_practice_runs([current], [])[0]["capabilities"]
    assert result["practice-old"]["capabilities"]["view_result"] is True
    assert (saved, current) == before


@pytest.mark.parametrize("kind", ["practice", "knowledge"])
def test_completed_continuation_has_no_duplicate_job(kind):
    assert [row["task_id"] for row in build_practice_runs([job(kind, "completed")], [history(kind)])] == ["practice-old"]


@pytest.mark.parametrize("kind", ["practice", "knowledge"])
def test_history_supersedes_old_failed_attempt_but_never_active_attempt(kind):
    saved = history(kind, "2026-09-06T12:00:00+08:00")
    assert len(build_practice_runs([job(kind, "failed")], [saved])) == 1
    assert len(build_practice_runs([job(kind, "running")], [saved])) == 2
    assert len(build_practice_runs([job(kind, "failed", "")], [saved])) == 2


def test_new_success_supersedes_previous_cancelled_retry():
    old = job("practice", "cancelled", "2026-09-06T09:00:00+08:00")
    new = {**job("practice", "completed"), "job_id": "generation-success"}
    assert len(build_practice_runs([old, new], [history("practice")])) == 1
