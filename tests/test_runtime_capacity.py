from __future__ import annotations

from unittest.mock import patch

from app.runtime_capacity import (
    model_request_max_concurrency,
    practice_inner_concurrency,
    practice_job_max_concurrency,
    runtime_capacity_summary,
)


def test_default_capacity_uses_workflow_concurrency_without_a_global_provider_gate() -> None:
    with patch.dict("os.environ", {}, clear=True):
        summary = runtime_capacity_summary()

    assert summary == {
        "practice_jobs": 2,
        "practice_calls_per_job": 6,
        "theoretical_practice_call_demand": 12,
        "provider_request_ceiling": 0,
    }


def test_capacity_overrides_remain_bounded() -> None:
    with patch.dict(
        "os.environ",
        {"PRACTICE_JOB_MAX_CONCURRENCY": "99", "MODEL_REQUEST_MAX_CONCURRENCY": "99"},
        clear=True,
    ):
        assert practice_job_max_concurrency() == 8
        assert model_request_max_concurrency() == 64
    assert practice_inner_concurrency({"generation_concurrency": 99}, stage="generation") == 12
