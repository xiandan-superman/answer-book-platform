from __future__ import annotations

import os
from typing import Any


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def model_request_max_concurrency() -> int:
    """Optional emergency ceiling; zero leaves provider concurrency uncapped."""
    return bounded_env_int("MODEL_REQUEST_MAX_CONCURRENCY", 0, 0, 64)


def provider_request_max_concurrency(provider: object | None) -> int:
    """Return the shared ceiling for one provider across all user tasks."""
    global_limit = model_request_max_concurrency()
    provider_name = str(
        provider if isinstance(provider, str) else getattr(provider, "name", "") or ""
    ).strip().lower()
    if provider_name == "lingsuan" or provider_name.startswith("lingsuan_"):
        lingsuan_limit = bounded_env_int("LINGSUAN_REQUEST_MAX_CONCURRENCY", 6, 1, 8)
        return min(global_limit, lingsuan_limit) if global_limit > 0 else lingsuan_limit
    if provider_name != "bigmodel":
        return global_limit
    bigmodel_limit = bounded_env_int("BIGMODEL_REQUEST_MAX_CONCURRENCY", 2, 1, 8)
    return min(global_limit, bigmodel_limit) if global_limit > 0 else bigmodel_limit


def bigmodel_rate_limit_backoff() -> tuple[float, float]:
    """Base and cap for provider-wide GLM 429 cooldowns."""
    try:
        base = max(0.25, min(30.0, float(os.environ.get("BIGMODEL_RATE_LIMIT_BASE_SECONDS", "2"))))
    except (TypeError, ValueError):
        base = 2.0
    try:
        cap = max(base, min(120.0, float(os.environ.get("BIGMODEL_RATE_LIMIT_CAP_SECONDS", "30"))))
    except (TypeError, ValueError):
        cap = 30.0
    return base, cap


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
        default = 4 if stage == "blueprint" else 6
        return max(1, min(12, int(requested or default)))
    except (TypeError, ValueError):
        return 4 if stage == "blueprint" else 6


def runtime_capacity_summary() -> dict[str, int]:
    jobs = practice_job_max_concurrency()
    inner = practice_inner_concurrency({}, stage="generation")
    provider = model_request_max_concurrency()
    return {
        "practice_jobs": jobs,
        "practice_calls_per_job": inner,
        "theoretical_practice_call_demand": jobs * inner,
        "provider_request_ceiling": provider,
        "bigmodel_request_ceiling": provider_request_max_concurrency("bigmodel"),
        "lingsuan_request_ceiling": provider_request_max_concurrency("lingsuan"),
    }
