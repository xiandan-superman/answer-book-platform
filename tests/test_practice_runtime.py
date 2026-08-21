from __future__ import annotations

import pytest

from app import practice_jobs
from app.practice_runtime import (
    PracticeGenerationStopped,
    ensure_practice_generation_active,
    load_practice_generation_checkpoint,
)


def _generation_job(tmp_path, monkeypatch, payload: dict):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    return practice_jobs.create_practice_job("generate_from_plan", payload)


def test_generation_active_guard_follows_durable_job_state(tmp_path, monkeypatch) -> None:
    job = _generation_job(tmp_path, monkeypatch, {"source_mode": "exam"})
    practice_jobs.update_practice_job(job["job_id"], status="running")

    ensure_practice_generation_active({"_job_id": job["job_id"]})
    practice_jobs.cancel_practice_job(job["job_id"])

    with pytest.raises(PracticeGenerationStopped):
        ensure_practice_generation_active({"_job_id": job["job_id"]})


def test_resume_accepts_only_completed_items_from_the_same_plan(tmp_path, monkeypatch) -> None:
    payload = {
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "plan": {"blueprint": {"exercise_plan": [
            {"plan_item_id": "plan_item_01"},
            {"plan_item_id": "plan_item_02"},
        ]}},
    }
    job = _generation_job(tmp_path, monkeypatch, payload)
    practice_jobs.update_practice_job(
        job["job_id"],
        status="failed",
        partial_exercises=[
            {"plan_item_id": "plan_item_01", "stem": "已完成"},
            {"plan_item_id": "plan_item_02", "generation_status": "failed", "stem": "失败占位"},
        ],
    )

    checkpoint = load_practice_generation_checkpoint(
        {**payload, "resume_from_job_id": job["job_id"]},
        expected_plan_item_ids=["plan_item_01", "plan_item_02"],
    )

    assert checkpoint.generated_plan_item_ids == ("plan_item_01",)
    assert [item["stem"] for item in checkpoint.exercises] == ["已完成"]


def test_resume_rejects_a_different_confirmed_plan(tmp_path, monkeypatch) -> None:
    original = {
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01", "difficulty": "基础"}]}},
    }
    job = _generation_job(tmp_path, monkeypatch, original)
    practice_jobs.update_practice_job(job["job_id"], status="failed")
    changed = {
        **original,
        "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01", "difficulty": "挑战"}]}},
        "resume_from_job_id": job["job_id"],
    }

    with pytest.raises(ValueError, match="蓝图"):
        load_practice_generation_checkpoint(changed, expected_plan_item_ids=["plan_item_01"])


def test_resume_rejects_duplicate_or_foreign_plan_items(tmp_path, monkeypatch) -> None:
    payload = {
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}},
    }
    job = _generation_job(tmp_path, monkeypatch, payload)
    practice_jobs.update_practice_job(
        job["job_id"],
        status="failed",
        partial_exercises=[{"plan_item_id": "foreign_item", "stem": "旧题"}],
    )

    with pytest.raises(ValueError, match="不属于"):
        load_practice_generation_checkpoint(
            {**payload, "resume_from_job_id": job["job_id"]},
            expected_plan_item_ids=["plan_item_01"],
        )
