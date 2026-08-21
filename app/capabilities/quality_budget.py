from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class QualityExecutionBudget:
    """Hard upper bounds for remote quality work in one task."""

    max_content_repair_questions: int = 5
    max_figure_repair_rounds: int = 1
    max_figure_repair_candidates_per_target: int = 2
    max_selective_review_candidates: int = 8
    # The default quality profile performs one batched semantic review request.
    # Transport retries and provider fallbacks are separate, explicit budgets;
    # they must never be hidden behind a nominal "one batch" description.
    max_selective_review_batches: int = 1
    max_selective_review_attempts_per_batch: int = 1
    max_selective_review_provider_fallbacks: int = 0
    max_answer_generation_repair_rounds: int = 1
    post_content_selective_review_enabled: bool = False
    # Model-semantic correctness gets one bounded repair attempt. Remaining
    # disciplinary disagreement is advisory; delivery contracts must not
    # oscillate on repeated model judgments in unattended mode.
    max_prefigure_correctness_repair_rounds: int = 1
    reuse_existing_visual_qa: bool = True

    @classmethod
    def from_environment(cls) -> QualityExecutionBudget:
        return cls(
            max_content_repair_questions=_bounded_env_int(
                "QUALITY_MAX_CONTENT_REPAIR_QUESTIONS", 5, minimum=0, maximum=20
            ),
            max_figure_repair_rounds=_bounded_env_int(
                "QUALITY_MAX_FIGURE_REPAIR_ROUNDS", 1, minimum=0, maximum=2
            ),
            max_figure_repair_candidates_per_target=_bounded_env_int(
                "QUALITY_MAX_FIGURE_REPAIR_CANDIDATES", 2, minimum=1, maximum=2
            ),
            max_selective_review_candidates=_bounded_env_int(
                "QUALITY_MAX_SELECTIVE_REVIEW_CANDIDATES", 8, minimum=0, maximum=20
            ),
            max_selective_review_batches=_bounded_env_int(
                "QUALITY_MAX_SELECTIVE_REVIEW_BATCHES", 1, minimum=0, maximum=20
            ),
            max_selective_review_attempts_per_batch=_bounded_env_int(
                "QUALITY_MAX_SELECTIVE_REVIEW_ATTEMPTS_PER_BATCH", 1, minimum=1, maximum=2
            ),
            max_selective_review_provider_fallbacks=_bounded_env_int(
                "QUALITY_MAX_SELECTIVE_REVIEW_PROVIDER_FALLBACKS", 0, minimum=0, maximum=1
            ),
            max_answer_generation_repair_rounds=_bounded_env_int(
                "QUALITY_MAX_ANSWER_GENERATION_REPAIR_ROUNDS", 1, minimum=0, maximum=2
            ),
            max_prefigure_correctness_repair_rounds=_bounded_env_int(
                "QUALITY_MAX_PREFIGURE_CORRECTNESS_REPAIR_ROUNDS", 1, minimum=0, maximum=2
            ),
            post_content_selective_review_enabled=_env_bool(
                "QUALITY_ENABLE_POST_CONTENT_SELECTIVE_REVIEW", False
            ),
            reuse_existing_visual_qa=_env_bool("QUALITY_REUSE_VISUAL_QA", True),
        )

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)
