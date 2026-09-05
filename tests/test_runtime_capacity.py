from __future__ import annotations

from unittest.mock import patch

from app.runtime_capacity import (
    model_request_max_concurrency,
    practice_inner_concurrency,
    practice_job_max_concurrency,
    provider_request_max_concurrency,
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
        "bigmodel_request_ceiling": 2,
        "lingsuan_request_ceiling": 6,
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


def test_bigmodel_has_a_shared_default_ceiling_and_respects_emergency_cap() -> None:
    provider = type("Provider", (), {"name": "bigmodel"})()
    other = type("Provider", (), {"name": "other"})()
    with patch.dict("os.environ", {}, clear=True):
        assert provider_request_max_concurrency(provider) == 2
        assert provider_request_max_concurrency(other) == 0
    with patch.dict(
        "os.environ",
        {"MODEL_REQUEST_MAX_CONCURRENCY": "1", "BIGMODEL_REQUEST_MAX_CONCURRENCY": "4"},
        clear=True,
    ):
        assert provider_request_max_concurrency(provider) == 1


def test_lingsuan_variants_share_six_slots_and_respect_emergency_cap() -> None:
    from app.concurrency import _provider_key

    google = type("Provider", (), {"name": "lingsuan_google"})()
    openai = type("Provider", (), {"name": "lingsuan_openai"})()
    with patch.dict("os.environ", {}, clear=True):
        assert provider_request_max_concurrency(google) == 6
        assert provider_request_max_concurrency(openai) == 6
    with patch.dict(
        "os.environ",
        {"MODEL_REQUEST_MAX_CONCURRENCY": "1", "LINGSUAN_REQUEST_MAX_CONCURRENCY": "4"},
        clear=True,
    ):
        assert provider_request_max_concurrency(google) == 1
    google_with_url = type(
        "Provider", (), {"name": "lingsuan_google", "base_url": "https://lingsuan.org/v1"}
    )()
    openai_with_url = type(
        "Provider", (), {"name": "lingsuan_openai", "base_url": "https://lingsuan.org/v1"}
    )()
    assert _provider_key(google_with_url) == _provider_key(openai_with_url)
