from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import runtime_monitor


class RuntimeMonitorTests(unittest.TestCase):
    def test_model_context_default_is_immutable_and_nested_context_restores_parent(self) -> None:
        self.assertIsNone(runtime_monitor._MODEL_CALL_CONTEXT.get())
        with runtime_monitor.model_call_context(task_id="outer", stage="planning"):
            outer = runtime_monitor._MODEL_CALL_CONTEXT.get()
            with runtime_monitor.model_call_context(active_item="q1"):
                inner = runtime_monitor._MODEL_CALL_CONTEXT.get()
                self.assertEqual("outer", inner["task_id"])
                self.assertEqual("q1", inner["active_item"])
            self.assertEqual(outer, runtime_monitor._MODEL_CALL_CONTEXT.get())
        self.assertIsNone(runtime_monitor._MODEL_CALL_CONTEXT.get())

    def test_running_task_with_heartbeat_and_stale_progress_is_warning(self) -> None:
        now = datetime.now().astimezone()
        row = {
            "task_id": "task_live",
            "status": "running",
            "current_stage": "answer_generation",
            "last_heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
            "last_progress_at": (now - timedelta(seconds=runtime_monitor.PROGRESS_WARNING_SECONDS + 1)).isoformat(),
        }
        health = runtime_monitor.task_health_summary(row)
        self.assertEqual("warning", health["health_status"])
        self.assertIn("没有新的业务进展", health["warning_reason"])

    def test_stale_heartbeat_marks_running_task_as_error(self) -> None:
        now = datetime.now().astimezone()
        row = {
            "task_id": "task_stale",
            "status": "running",
            "current_stage": "question_understanding",
            "last_heartbeat_at": (now - timedelta(seconds=runtime_monitor.HEARTBEAT_ERROR_SECONDS + 1)).isoformat(),
            "last_progress_at": now.isoformat(),
        }
        health = runtime_monitor.task_health_summary(row)
        self.assertEqual("error", health["health_status"])

    def test_user_confirmation_and_queue_are_waiting_not_stalled(self) -> None:
        confirmation = runtime_monitor.task_health_summary({"task_id": "review", "status": "paused", "current_stage": "exam_structure_review"})
        queued = runtime_monitor.task_health_summary({"job_id": "generation_queue", "status": "queued", "current_stage": "planning"}, kind="practice")
        self.assertEqual(("waiting", "等待用户确认"), (confirmation["health_status"], confirmation["current_operation"]))
        self.assertEqual(("waiting", "正在排队"), (queued["health_status"], queued["current_operation"]))

    def test_model_slot_queue_is_reported_as_waiting_instead_of_stalled(self) -> None:
        now = datetime.now().astimezone()
        row = {
            "task_id": "practice-waiting-for-model",
            "status": "running",
            "current_stage": "generating",
            "last_heartbeat_at": now.isoformat(),
            "last_progress_at": (now - timedelta(seconds=runtime_monitor.PROGRESS_WARNING_SECONDS + 1)).isoformat(),
        }
        with patch.object(
            runtime_monitor,
            "model_request_snapshot",
            return_value={"waiting_task_ids": ["practice-waiting-for-model"]},
        ):
            health = runtime_monitor.task_health_summary(row, kind="practice")
        self.assertEqual("waiting", health["health_status"])
        self.assertEqual("正在等待模型处理位置", health["current_operation"])
        self.assertIn("公平队列", health["suggested_action"])

    def test_historical_failure_does_not_make_system_unhealthy(self) -> None:
        old_failure = {
            "task_id": "task_old_failure",
            "status": "failed",
            "current_stage": "answer_generation",
            "updated_at": "2020-01-01 00:00:00",
            "last_heartbeat_at": "2020-01-01 00:00:00",
            "error": "old failure",
        }
        service = {"started_at": runtime_monitor._now(), "uptime_seconds": 1, "pid": 1, "directories": [], "disk": {}, "errors": []}
        model = {"health_status": "unknown", "label": "暂无调用记录", "active_count": 0, "waiting_count": 0, "concurrency_limit": 3, "recent_success_count": 0, "recent_failure_count": 0, "recent_timeout_count": 0, "recent_rate_limited_count": 0, "recent_retry_count": 0, "average_duration_ms": 0, "active": [], "recent": []}
        with patch.object(runtime_monitor, "list_tasks", return_value=[old_failure]), patch("app.practice_jobs.list_practice_jobs", return_value=[]), patch.object(runtime_monitor, "_service_health", return_value=service), patch.object(runtime_monitor, "model_call_summary", return_value=model):
            status = runtime_monitor.build_system_status()
        self.assertTrue(status["ok"])
        self.assertEqual("normal", status["health"]["status"])
        self.assertEqual(0, status["tasks"]["counts"]["error"])

    def test_model_records_exclude_request_content(self) -> None:
        with runtime_monitor.model_call_context(task_id="task_monitor", stage="answer_generation", active_item="第 1 题"):
            with runtime_monitor.track_model_call(provider="deepseek", model="test-model", purpose="chat_json", timeout=120):
                pass
        record = runtime_monitor.model_call_summary()["recent"][-1]
        self.assertEqual("task_monitor", record["task_id"])
        self.assertNotIn("prompt", record)
        self.assertNotIn("content", record)
        self.assertNotIn("response", record)

    def test_completed_count_is_never_greater_than_total(self) -> None:
        health = runtime_monitor.task_health_summary(
            {
                "task_id": "bounded",
                "status": "running",
                "current_stage": "figures",
                "completed_count": 5,
                "total_count": 2,
                "last_heartbeat_at": datetime.now().astimezone().isoformat(),
            }
        )
        self.assertEqual(2, health["completed_count"])
        self.assertEqual(2, health["total_count"])

    def test_current_stage_outputs_progress_repairs_stale_task_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_id = "durable-progress"
            tasks_dir = Path(temporary)
            stage_dir = tasks_dir / task_id / "stage_outputs"
            stage_dir.mkdir(parents=True)
            (stage_dir / "answer_generation_progress.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "completed": 6,
                        "total": 6,
                        "active": {"question_id": "stale-question"},
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(runtime_monitor, "TASKS_DIR", tasks_dir):
                health = runtime_monitor._task_health(
                    {
                        "task_id": task_id,
                        "status": "completed",
                        "current_stage": "completed",
                        "completed_count": 0,
                        "total_count": 6,
                        "created_at": "2026-08-09 10:00:00",
                        "updated_at": "2026-08-09 10:00:00",
                        "last_progress_at": "2026-08-09 10:00:00",
                    },
                    datetime.now().astimezone(),
                    kind="exam",
                )

        self.assertEqual(6, health["completed_count"])
        self.assertEqual(6, health["total_count"])
        self.assertEqual("", health["active_item"])
        self.assertEqual("2026-08-09 10:00:00", health["last_progress_at"])

    def test_unrelated_stage_progress_cannot_replace_task_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_id = "stage-local-progress"
            tasks_dir = Path(temporary)
            stage_dir = tasks_dir / task_id / "stage_outputs"
            stage_dir.mkdir(parents=True)
            (stage_dir / "figure_progress.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "question_count": 12,
                        "generated_count": 0,
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(runtime_monitor, "TASKS_DIR", tasks_dir):
                health = runtime_monitor._task_health(
                    {
                        "task_id": task_id,
                        "status": "running",
                        "current_stage": "question_review",
                        "completed_count": 12,
                        "total_count": 12,
                        "last_heartbeat_at": datetime.now().astimezone().isoformat(),
                    },
                    datetime.now().astimezone(),
                    kind="exam",
                )

        self.assertEqual(12, health["completed_count"])
        self.assertEqual(12, health["total_count"])

    def test_partial_usage_is_available_before_provider_completion(self) -> None:
        record = {}
        runtime_monitor.record_model_call_estimate(record, {"messages": [{"content": "长材料" * 100}]})
        runtime_monitor.record_model_stream_progress(record, '{"source_scope":')

        self.assertGreater(record["prompt_tokens"], 0)
        self.assertGreater(record["completion_tokens"], 0)
        self.assertEqual(1, record["stream_chunk_count"])
        self.assertEqual("platform_estimated_partial", record["usage_source"])

        runtime_monitor.record_model_call_usage(
            record,
            {"usage": {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168}},
        )
        self.assertEqual(123, record["prompt_tokens"])
        self.assertEqual(45, record["completion_tokens"])
        self.assertEqual("provider_reported", record["usage_source"])

    def test_multimodal_estimate_separates_image_bytes_from_text_tokens(self) -> None:
        record = {}
        encoded = __import__("base64").b64encode(b"image-bytes" * 1000).decode("ascii")

        runtime_monitor.record_model_call_estimate(record, {
            "messages": [{"content": ["短文本", f"data:image/png;base64,{encoded}"]}],
        })

        self.assertEqual(1, record["image_input_count"])
        self.assertEqual(11000, record["image_input_bytes"])
        self.assertGreater(record["request_bytes"], record["image_input_bytes"])
        self.assertLess(record["estimated_prompt_tokens"], 100)
        self.assertEqual("platform_text_estimate_without_vision", record["usage_source"])
