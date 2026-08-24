from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.task_contracts import present_error
from app.task_read_model import build_exam_run, build_practice_runs, practice_network_statistics


def test_exam_public_metadata_recovers_names_without_storage_paths_or_internal_keys() -> None:
    run = build_exam_run({
        "task_id": "exam-public-metadata",
        "status": "completed",
        "current_stage": "answer_generation",
        "exam_path": "/Users/test/private/2025年材料分析真题.docx",
        "textbooks_dir": "/data/tasks/exam-public-metadata/selected_textbooks",
        "selected_textbooks": [
            "/data/tasks/exam-public-metadata/selected_textbooks/材料科学基础.pdf",
            "/data/tasks/exam-public-metadata/selected_textbooks/材料工程基础.pdf",
        ],
        "textbook_display_names": {
            "/source/材料科学基础.pdf": "材料科学基础（第3版）",
            "/source/材料工程基础.pdf": "材料工程基础",
        },
    })

    assert run["display_title"] == "真题解析 · 2025年材料分析真题"
    assert run["description"] == "2025年材料分析真题.docx"
    assert run["textbook_material_names"] == ["材料科学基础（第3版）", "材料工程基础"]
    public_copy = str({key: run[key] for key in ("display_title", "description", "textbook_material_names")})
    assert "/Users/" not in public_copy
    assert "/data/" not in public_copy
    assert "selected_textbooks" not in public_copy


@pytest.mark.parametrize("task_kind", ["practice", "knowledge"])
def test_practice_public_metadata_recovers_source_basename_for_old_internal_title(task_kind: str) -> None:
    run = build_practice_runs([{
        "job_id": f"job-{task_kind}",
        "practice_batch_id": f"batch-{task_kind}",
        "task_kind": task_kind,
        "operation": "generate_from_plan",
        "status": "paused",
        "current_stage": "generating",
        "title": "selected_textbooks",
        "payload": {
            "source_mode": "knowledge" if task_kind == "knowledge" else "exam",
            "source_files": [{"name": "/private/input/相图原题-07.png"}],
        },
        "created_at": "2026-08-23T10:00:00+08:00",
        "updated_at": "2026-08-23T10:01:00+08:00",
    }], [])[0]

    assert run["description"] == "相图原题-07.png"
    assert run["material_display_names"] == ["相图原题-07.png"]
    assert "/private/" not in run["display_title"]
    assert "selected_textbooks" not in run["display_title"]


def test_old_automatic_filename_title_is_presented_as_a_friendly_task_name() -> None:
    run = build_practice_runs([{
        "job_id": "job-friendly-old-title",
        "practice_batch_id": "batch-friendly-old-title",
        "task_kind": "practice",
        "operation": "analyze",
        "status": "running",
        "title": "跨年组合_高分子物理_按题生题",
        "model": "gpt-5.6-sol",
        "payload": {
            "source_mode": "exam",
            "task_title": "跨年组合_高分子物理_按题生题",
            "source_files": [{"name": "跨年组合_高分子物理_按题生题.docx"}],
        },
        "created_at": "2026-08-24T19:00:00+08:00",
        "updated_at": "2026-08-24T19:01:00+08:00",
    }], [])[0]

    assert run["display_title"] == "按题出题 · Sol · 跨年组合 · 高分子物理"


def test_user_renamed_title_remains_unchanged_in_public_task_name() -> None:
    run = build_practice_runs([{
        "job_id": "job-user-title",
        "practice_batch_id": "batch-user-title",
        "task_kind": "practice",
        "operation": "analyze",
        "status": "running",
        "title": "我的_自定义任务名",
        "model": "gemini-3.6-flash",
        "payload": {
            "source_mode": "exam",
            "task_title": "我的_自定义任务名",
            "source_files": [{"name": "跨年组合_高分子物理_按题生题.docx"}],
        },
        "created_at": "2026-08-24T19:00:00+08:00",
        "updated_at": "2026-08-24T19:01:00+08:00",
    }], [])[0]

    assert run["display_title"] == "按题出题 · Gemini · 我的_自定义任务名"


@pytest.mark.parametrize("status", ["failed", "cancelled", "paused", "completed"])
def test_newest_practice_terminal_record_is_batch_authority(status: str) -> None:
    runs = build_practice_runs([
        {
            "job_id": "old-running",
            "practice_batch_id": "batch-authority",
            "task_kind": "practice",
            "operation": "analyze",
            "status": "running",
            "current_stage": "analyzing",
            "created_at": "2026-08-23T10:00:00+08:00",
            "updated_at": "2026-08-23T10:10:00+08:00",
        },
        {
            "job_id": "new-authority",
            "practice_batch_id": "batch-authority",
            "task_kind": "practice",
            "operation": "plan",
            "status": status,
            "current_stage": "planning",
            "created_at": "2026-08-23T10:11:00+08:00",
            "updated_at": "2026-08-23T10:12:00+08:00",
        },
    ], [])

    assert runs[0]["task_id"] == "new-authority"
    expected = "needs_input" if status == "completed" else status
    assert runs[0]["status"] == expected
    assert runs[0]["engine_status"] == status


def test_low_level_wall_clock_and_unknown_errors_are_never_exposed() -> None:
    deadline = present_error("Responses stream exceeded total wall-clock deadline; /private/socket")
    unknown = present_error("RuntimeError at /private/app/worker.py: opaque provider response")

    assert deadline and deadline.kind == "provider_timeout"
    assert "wall-clock" not in deadline.message
    assert unknown and unknown.kind == "provider_error"
    assert "/private/" not in unknown.message
    assert "RuntimeError" not in unknown.message
    assert "检查 API 配置和模型服务状态" in unknown.retry_hint


def test_network_statistics_restore_from_durable_retry_state_and_ledger_snapshot() -> None:
    deadline = (datetime.now().astimezone() + timedelta(minutes=5)).isoformat(timespec="seconds")
    durable = {
        "status": "paused",
        "generation_deadline_at": deadline,
        "network_attempted_count": 99,
        "generation_retry_state": {
            "schema_version": 2,
            "batches": {
                "root-a": {"calls_used": 2, "limit": 4},
                "root-b": {"calls_used": 1, "limit": 3},
            },
        },
        "model_usage": {"call_count": 8},
    }

    first = practice_network_statistics(durable)
    restored = practice_network_statistics(dict(durable))
    assert first["network_attempted_count"] == restored["network_attempted_count"] == 3
    assert first["network_call_budget"] == restored["network_call_budget"] == 7
    assert first["network_statistics_status"] == restored["network_statistics_status"] == "ready"
    assert isinstance(first["deadline_remaining_seconds"], int)


def test_unsynchronized_zero_is_unknown_but_terminal_ledger_zero_is_truthful() -> None:
    active = practice_network_statistics({
        "status": "running",
        "network_attempted_count": 0,
        "network_stats_synced": False,
    })
    terminal = practice_network_statistics({
        "status": "failed",
        "network_attempted_count": 0,
        "network_stats_synced": False,
        "model_usage": {"call_count": 0},
    })

    assert active["network_attempted_count"] is None
    assert active["network_statistics_status"] == "syncing"
    assert terminal["network_attempted_count"] == 0
    assert terminal["network_statistics_status"] == "ready"
