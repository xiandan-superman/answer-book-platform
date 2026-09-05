from __future__ import annotations

import math
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
    max_model_calls_per_run: int = 120
    estimated_model_calls_per_run: int = 0
    model_call_headroom_percent: int = 180
    model_call_budget_dynamic: bool = False
    # Zero disables the task-wide token cap; positive explicit budgets remain supported.
    max_model_tokens_per_run: int = 0
    max_model_wall_seconds_per_run: int = 1800
    provider_failure_circuit_breaker: int = 3

    @classmethod
    def from_environment(
        cls,
        *,
        question_count: int = 0,
        task_kind: str = "",
        textbook_evidence_enabled: bool = False,
    ) -> QualityExecutionBudget:
        estimated_calls = cls.estimate_model_calls(
            question_count=question_count,
            task_kind=task_kind,
            textbook_evidence_enabled=textbook_evidence_enabled,
        )
        headroom_percent = _bounded_env_int(
            "QUALITY_MODEL_CALL_HEADROOM_PERCENT", 180, minimum=100, maximum=200
        )
        explicit_call_limit = "QUALITY_MAX_MODEL_CALLS_PER_RUN" in os.environ
        if explicit_call_limit:
            max_model_calls = _bounded_env_int(
                "QUALITY_MAX_MODEL_CALLS_PER_RUN", 120, minimum=10, maximum=500
            )
        elif estimated_calls:
            max_model_calls = max(
                120,
                min(500, math.ceil(estimated_calls * headroom_percent / 100)),
            )
        else:
            max_model_calls = 120
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
            max_model_calls_per_run=max_model_calls,
            estimated_model_calls_per_run=estimated_calls,
            model_call_headroom_percent=headroom_percent,
            model_call_budget_dynamic=bool(estimated_calls and not explicit_call_limit),
            max_model_tokens_per_run=_bounded_env_int(
                "QUALITY_MAX_MODEL_TOKENS_PER_RUN", 0, minimum=0, maximum=20_000_000
            ),
            max_model_wall_seconds_per_run=_bounded_env_int(
                "QUALITY_MAX_MODEL_WALL_SECONDS_PER_RUN", 1800, minimum=300, maximum=7200
            ),
            provider_failure_circuit_breaker=_bounded_env_int(
                "QUALITY_PROVIDER_FAILURE_CIRCUIT_BREAKER", 3, minimum=2, maximum=10
            ),
        )

    @staticmethod
    def estimate_model_calls(
        *,
        question_count: int,
        task_kind: str,
        textbook_evidence_enabled: bool,
    ) -> int:
        """Estimate normal model attempts before transient-failure headroom.

        Exam analysis has one whole-paper understanding call, one answer call
        per question, and—when textbook evidence is enabled—one knowledge-plan
        call, one evidence-selection call, and an expansion pass for roughly
        half of the questions.  A small fixed allowance covers the bounded
        correctness and document-repair stages.  Practice generation keeps a
        more conservative two-attempt-per-item estimate because batching and
        item repair vary by task contract.
        """

        count = max(0, int(question_count or 0))
        if count <= 0:
            return 0
        normalized_kind = str(task_kind or "").strip().lower()
        if normalized_kind == "exam":
            estimate = 6 + count
            if textbook_evidence_enabled:
                estimate += (2 * count) + math.ceil(count / 2)
            return estimate
        if normalized_kind in {"practice", "knowledge"}:
            return 6 + (2 * count)
        return 6 + count

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)
