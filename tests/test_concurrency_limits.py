from __future__ import annotations

import os
import unittest
from contextvars import ContextVar
from unittest.mock import patch

from app.answer_generation import answer_generation_worker_count
from app.audit_model_repair import audit_model_repair_timeout_seconds, audit_model_repair_worker_count
from app.concurrency import _model_request_limit, run_limited_concurrent
from app.docx_model_repair import docx_model_repair_worker_count
from app.evidence_selection import evidence_selection_worker_count
from app.exercise_generation import blueprint_refinement_concurrency, practice_generation_concurrency
from app.figure_schema_planning import figure_schema_planning_worker_count
from app.figures import figure_model_worker_count
from app.knowledge_planning import knowledge_planning_worker_count
from app.question_understanding import question_understanding_worker_count


class ConcurrencyLimitTests(unittest.TestCase):
    def test_limited_workers_receive_parent_context_for_task_telemetry(self) -> None:
        marker: ContextVar[str] = ContextVar("test_model_context", default="missing")
        token = marker.set("task-123")
        try:
            self.assertEqual(
                ["task-123", "task-123"],
                run_limited_concurrent([1, 2], lambda _: marker.get(), max_workers=2),
            )
        finally:
            marker.reset(token)

    def test_priority_model_tasks_keep_short_calls_fast_and_bound_long_generation_streams(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(0, _model_request_limit())
            self.assertEqual(10, question_understanding_worker_count())
            self.assertEqual(10, knowledge_planning_worker_count())
            self.assertEqual(10, evidence_selection_worker_count())
            self.assertEqual(4, blueprint_refinement_concurrency({}))
            self.assertEqual(6, practice_generation_concurrency({}))

        with patch.dict(os.environ, {
            "MODEL_REQUEST_MAX_CONCURRENCY": "99",
            "QUESTION_UNDERSTANDING_MAX_WORKERS": "99",
            "KNOWLEDGE_PLANNING_MAX_WORKERS": "99",
            "EVIDENCE_SELECTION_MAX_WORKERS": "99",
        }, clear=True):
            self.assertEqual(64, _model_request_limit())
            self.assertEqual(10, question_understanding_worker_count())
            self.assertEqual(10, knowledge_planning_worker_count())
            self.assertEqual(10, evidence_selection_worker_count())
        self.assertEqual(12, blueprint_refinement_concurrency({"blueprint_concurrency": 99}))
        self.assertEqual(12, practice_generation_concurrency({"generation_concurrency": 99}))
        self.assertEqual(5, practice_generation_concurrency({"generation_concurrency": 5}))

    def test_other_model_stages_use_doubled_defaults_and_ceilings(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(10, answer_generation_worker_count())
            self.assertEqual(6, figure_schema_planning_worker_count())
            self.assertEqual(6, figure_model_worker_count())
            self.assertEqual(6, audit_model_repair_worker_count())
            self.assertEqual(4, docx_model_repair_worker_count())

        with patch.dict(os.environ, {
            "ANSWER_GENERATION_MAX_WORKERS": "99",
            "FIGURE_SCHEMA_PLANNING_MAX_WORKERS": "99",
            "FIGURE_MODEL_MAX_WORKERS": "99",
            "AUDIT_MODEL_REPAIR_MAX_WORKERS": "99",
            "DOCX_MODEL_REPAIR_MAX_WORKERS": "99",
        }, clear=True):
            self.assertEqual(12, answer_generation_worker_count())
            self.assertEqual(6, figure_schema_planning_worker_count())
            self.assertEqual(6, figure_model_worker_count())
            self.assertEqual(6, audit_model_repair_worker_count())
            self.assertEqual(6, docx_model_repair_worker_count())

    def test_audit_repair_timeout_matches_answer_complexity(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(180, audit_model_repair_timeout_seconds({"question_type": "简答题"}))
            self.assertEqual(300, audit_model_repair_timeout_seconds({"question_type": "计算题"}))
            self.assertEqual(300, audit_model_repair_timeout_seconds({"question_type": "作图题"}))

        with patch.dict(
            os.environ,
            {
                "AUDIT_MODEL_REPAIR_TIMEOUT_SECONDS": "10",
                "AUDIT_MODEL_REPAIR_COMPLEX_TIMEOUT_SECONDS": "1200",
            },
            clear=True,
        ):
            self.assertEqual(30, audit_model_repair_timeout_seconds({"question_type": "简答题"}))
            self.assertEqual(900, audit_model_repair_timeout_seconds({"question_type": "计算题"}))


if __name__ == "__main__":
    unittest.main()
