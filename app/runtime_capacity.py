from __future__ import annotations

import os
from typing import Any


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def model_request_max_concurrency() -> int:
    """Global per-provider request ceiling shared by every workflow."""
    return bounded_env_int("MODEL_REQUEST_MAX_CONCURRENCY", 4, 1, 10)


def practice_job_max_concurrency() -> int:
    """Number of whole practice workflows allowed to execute concurrently."""
    return bounded_env_int("PRACTICE_JOB_MAX_CONCURRENCY", 2, 1, 8)


def practice_inner_concurrency(payload: dict[str, Any], *, stage: str) -> int:
    """Bound calls inside one practice job so task and provider limits compose."""
    if stage == "blueprint":
        requested = payload.get("blueprint_concurrency")
    else:
        requested = payload.get("generation_concurrency")
    try:
        return max(1, min(4, int(requested or 2)))
    except (TypeError, ValueError):
        return 2


def runtime_capacity_summary() -> dict[str, int]:
    jobs = practice_job_max_concurrency()
    inner = practice_inner_concurrency({}, stage="generation")
    provider = model_request_max_concurrency()
    return {
        "practice_jobs": jobs,
        "practice_calls_per_job": inner,
        "theoretical_practice_call_demand": jobs * inner,
        "provider_request_ceiling": provider,
    }
