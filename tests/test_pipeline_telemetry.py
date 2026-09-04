from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.pipeline_telemetry import PIPELINE_TELEMETRY_VERSION, PipelineRunTelemetry


def test_pipeline_telemetry_preserves_status_event_and_health_contract(tmp_path: Path) -> None:
    status_path = tmp_path / "pipeline_status.json"
    governance = {
        "mode": "unattended",
        "human_review_required": False,
        "budget": {"max_content_repair_questions": 4},
    }
    telemetry = PipelineRunTelemetry(
        task_id="task-1",
        status_path=status_path,
        quality_governance=governance,
    )

    with (
        patch("app.pipeline_telemetry.append_event") as append_event,
        patch("app.pipeline_telemetry.update_task_health") as update_health,
    ):
        telemetry.mark("answer_generation", "started", {"total": 3, "completed": 0})
        telemetry.mark(
            "answer_generation",
            "passed",
            {"total": 3, "completed": 3},
        )

    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["telemetry_version"] == PIPELINE_TELEMETRY_VERSION
    assert saved["quality_governance"] == governance
    assert [item["event_index"] for item in saved["stages"]] == [1, 2]
    assert [item["status"] for item in saved["stages"]] == ["started", "passed"]
    assert saved["stages"][1]["stage_elapsed_seconds"] >= 0
    assert saved["elapsed_seconds"] >= 0
    assert append_event.call_count == 2
    assert update_health.call_count == 2
    assert update_health.call_args_list[0].kwargs == {
        "current_operation": "answer_generation",
        "total_count": 3,
        "completed_count": 0,
        "progress": True,
    }
    assert update_health.call_args_list[1].kwargs == {
        "current_operation": "answer_generation",
        "total_count": 3,
        "completed_count": 3,
        "progress": True,
    }


def test_pipeline_telemetry_ignores_non_progress_health_updates(tmp_path: Path) -> None:
    telemetry = PipelineRunTelemetry(
        task_id="task-2",
        status_path=tmp_path / "pipeline_status.json",
        quality_governance={},
    )

    with (
        patch("app.pipeline_telemetry.append_event"),
        patch("app.pipeline_telemetry.update_task_health") as update_health,
    ):
        telemetry.mark("environment", "started", {"message": "checking"})

    update_health.assert_not_called()


def test_pipeline_telemetry_updates_task_shaped_quality_budget(tmp_path: Path) -> None:
    status_path = tmp_path / "pipeline_status.json"
    telemetry = PipelineRunTelemetry(
        task_id="task-budget",
        status_path=status_path,
        quality_governance={"budget": {"max_model_calls_per_run": 120}},
    )

    telemetry.update_quality_budget(
        {
            "max_model_calls_per_run": 220,
            "estimated_model_calls_per_run": 122,
            "model_call_headroom_percent": 180,
            "model_call_budget_dynamic": True,
        }
    )

    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["quality_governance"]["budget"]["max_model_calls_per_run"] == 220
    assert saved["quality_governance"]["budget"]["model_call_budget_dynamic"] is True


def test_pipeline_telemetry_does_not_mix_domain_specific_counters(tmp_path: Path) -> None:
    telemetry = PipelineRunTelemetry(
        task_id="task-3",
        status_path=tmp_path / "pipeline_status.json",
        quality_governance={},
    )

    with (
        patch("app.pipeline_telemetry.append_event"),
        patch("app.pipeline_telemetry.update_task_health") as update_health,
    ):
        telemetry.mark(
            "figures",
            "passed",
            {"question_count": 12, "generated_count": 0},
        )

    update_health.assert_called_once_with(
        "task-3",
        current_operation="figures",
        total_count=None,
        completed_count=None,
        progress=True,
    )
