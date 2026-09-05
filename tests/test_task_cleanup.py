from __future__ import annotations

import json

from app import task_cleanup


def _row(index: int, *, status: str = "completed", progress: int = 100) -> dict:
    return {
        "task_id": f"task-{index:02d}",
        "display_title": f"任务 {index}",
        "status": status,
        "progress_percent": progress,
        "created_at": f"2026-09-{index + 1:02d} 10:00:00",
    }


def test_cleanup_only_recommends_eligible_tasks_older_than_newest_40(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "downloads.json"
    ledger.write_text(json.dumps({"task-03": {"download_count": 1}}), encoding="utf-8")
    monkeypatch.setattr(task_cleanup, "DOWNLOAD_LEDGER_PATH", ledger)
    rows = [_row(index) for index in range(45)]
    rows[1]["status"] = "failed"
    rows[2]["status"] = "cancelled"
    rows[4]["progress_percent"] = 30

    result = task_cleanup.build_cleanup_recommendation(rows)

    assert result["task_count"] == 45
    assert result["overflow_count"] == 5
    assert {item["task_id"] for item in result["recommended"]} == {
        "task-01", "task-02", "task-03", "task-04"
    }


def test_cleanup_never_offers_live_or_waiting_overflow_tasks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(task_cleanup, "DOWNLOAD_LEDGER_PATH", tmp_path / "missing.json")
    rows = [_row(index) for index in range(42)]
    rows[0].update(status="running", progress_percent=20)
    rows[1].update(status="needs_input", progress_percent=20)

    result = task_cleanup.build_cleanup_recommendation(rows)

    assert result["safe_overflow_count"] == 0
    assert result["show_prompt"] is False


def test_download_ledger_is_durable_and_forgettable(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "downloads.json"
    monkeypatch.setattr(task_cleanup, "DOWNLOAD_LEDGER_PATH", ledger)

    task_cleanup.mark_task_downloaded("task-a")
    task_cleanup.mark_task_downloaded("task-a")
    assert json.loads(ledger.read_text(encoding="utf-8"))["task-a"]["download_count"] == 2

    task_cleanup.forget_deleted_tasks(["task-a"])
    assert json.loads(ledger.read_text(encoding="utf-8")) == {}
