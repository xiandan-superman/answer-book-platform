from __future__ import annotations

import json
from types import SimpleNamespace

from app import task_diagnostics
from app.task_diagnostics import (
    STAGE_FILES,
    _collect_file_issues,
    _collect_image_tool_issues,
    _compact_issue,
)


def test_figure_delivery_issue_exposes_its_question_id() -> None:
    issue = _compact_issue(
        "figure_delivery: qa_s01_01_01 required image missing",
        default_stage="final_acceptance",
        severity="issue",
    )

    assert issue["question_id"] == "qa_s01_01_01"
    assert issue["code"] == "figure_delivery"


def test_image_tool_diagnostics_expose_safe_supplier_cause_and_question(tmp_path) -> None:
    events = tmp_path / "agent_images" / "qa_s01_01_01" / "tool_events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps(
            {
                "event": "tool/result",
                "result": {
                    "ok": False,
                    "is_error": True,
                    "error": {
                        "code": "grok_media_no_eligible_account",
                        "message": "Provider HTTP 503: No eligible Grok media accounts",
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    issues = _collect_image_tool_issues(tmp_path)

    assert issues[0]["question_id"] == "qa_s01_01_01"
    assert issues[0]["code"] == "provider_route_pool_unavailable"
    assert "图片账号" in issues[0]["message"]
    assert "Provider HTTP" not in issues[0]["message"]


def test_answer_generation_diagnostics_expose_checkpoint_reconciliation(tmp_path) -> None:
    report = {
        "schema_version": "answer_book.answer_checkpoint_reconciliation.v1",
        "inconsistencies": ["progress completed 1 differs from durable fragment count 2"],
        "missing_question_ids": ["q_missing"],
        "invalid_question_ids": ["q_invalid"],
        "duplicate_question_ids": [],
        "foreign_question_ids": [],
    }
    (tmp_path / "answer_checkpoint_reconciliation.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    issues = _collect_file_issues(tmp_path, "answer_generation")

    assert "answer_checkpoint_reconciliation.json" in STAGE_FILES["answer_generation"]
    assert {item["question_id"] for item in issues} == {"", "q_missing", "q_invalid"}
    assert all(item["severity"] == "warning" for item in issues)


def test_task_diagnostics_surfaces_checkpoint_plan_and_related_file(tmp_path, monkeypatch) -> None:
    task_root = tmp_path / "task"
    stage = task_root / "stage_outputs"
    output = tmp_path / "output"
    stage.mkdir(parents=True)
    output.mkdir()
    (stage / "pipeline_status.json").write_text(
        json.dumps(
            {
                "stages": [
                    {"stage": "answer_generation", "status": "reused", "detail": {}}
                ]
            }
        ),
        encoding="utf-8",
    )
    (stage / "answer_checkpoint_reconciliation.json").write_text(
        json.dumps(
            {
                "inconsistencies": ["progress completed 1 differs from durable fragment count 2"],
                "missing_question_ids": [],
                "invalid_question_ids": ["q_invalid"],
                "duplicate_question_ids": [],
                "foreign_question_ids": [],
            }
        ),
        encoding="utf-8",
    )
    (task_root / "events.jsonl").write_text(
        json.dumps({"time": "2026-08-14 12:00:00", "event": "checkpoint_reconciled", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    record = SimpleNamespace(status="paused", current_stage="answer_generation", error="")
    monkeypatch.setattr(task_diagnostics, "load_task", lambda _task_id: record)
    monkeypatch.setattr(task_diagnostics, "task_dir", lambda _task_id: task_root)
    monkeypatch.setattr(task_diagnostics, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(task_diagnostics, "output_dir", lambda _task_id: output)

    report = task_diagnostics.build_task_diagnostics("task")

    assert report["needs_attention"] is True
    assert report["summary"]["warning_count"] == 2
    assert report["question_summary"][0]["question_id"] == "q_invalid"
    assert "answer_checkpoint_reconciliation.json" in {
        item["name"] for item in report["related_files"]
    }


def test_failed_task_without_audit_file_still_reports_one_actionable_issue(tmp_path, monkeypatch) -> None:
    task_root = tmp_path / "task"
    stage = task_root / "stage_outputs"
    output = tmp_path / "output"
    stage.mkdir(parents=True)
    output.mkdir()
    (stage / "pipeline_status.json").write_text(
        json.dumps({"stages": [{"stage": "uploading", "status": "failed", "detail": {}}]}),
        encoding="utf-8",
    )
    record = SimpleNamespace(
        status="failed",
        current_stage="uploading",
        error="'latin-1' codec can't encode characters in position 0-3",
    )
    monkeypatch.setattr(task_diagnostics, "load_task", lambda _task_id: record)
    monkeypatch.setattr(task_diagnostics, "task_dir", lambda _task_id: task_root)
    monkeypatch.setattr(task_diagnostics, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(task_diagnostics, "output_dir", lambda _task_id: output)

    report = task_diagnostics.build_task_diagnostics("中文任务")

    assert report["primary_stage_label"] == "上传任务到混合云"
    assert report["summary"]["issue_count"] == 1
    assert report["issues"][0]["code"] == "task_runtime_failure"
    assert "latin-1" not in report["error"]
    assert "latin-1" not in report["issues"][0]["message"]
