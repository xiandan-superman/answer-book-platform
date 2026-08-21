from __future__ import annotations

import json
import threading

from app import task_store
from app.task_store import TaskRecord


def _record() -> TaskRecord:
    return TaskRecord(
        task_id="atomic-task",
        exam_path="exam.docx",
        textbooks_dir="textbooks",
        provider="test",
        model="test",
        status="created",
        created_at="2026-08-09 10:00:00",
        updated_at="2026-08-09 10:00:00",
    )


def test_task_updates_remain_valid_under_concurrent_health_writes(tmp_path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks_dir)
    task_store.task_dir("atomic-task").mkdir()
    task_store.save_task(_record())

    threads = [
        threading.Thread(
            target=task_store.update_task_health,
            kwargs={
                "task_id": "atomic-task",
                "completed_count": index,
                "total_count": 20,
                "active_item": f"question-{index}",
                "progress": True,
            },
        )
        for index in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    raw = json.loads(task_store.task_record_path("atomic-task").read_text(encoding="utf-8"))
    loaded = task_store.load_task("atomic-task")
    assert raw["total_count"] == 20
    assert 0 <= loaded.completed_count <= 19
    assert not list(task_store.task_dir("atomic-task").glob("*.tmp"))


def test_interrupted_task_is_queued_for_checkpoint_recovery(tmp_path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks_dir)
    record = _record()
    record.status = "running"
    record.current_stage = "answer_generation"
    task_store.task_dir(record.task_id).mkdir()
    task_store.save_task(record)

    recovered = task_store.recover_interrupted_tasks()

    assert recovered[0]["previous_stage"] == "answer_generation"
    assert recovered[0]["reuse_fragments"] is True
    loaded = task_store.load_task(record.task_id)
    assert loaded.status == "queued"
    assert loaded.current_stage == "recovering"
    assert loaded.interrupted_stage == "answer_generation"


def test_success_terminal_states_finalize_progress(tmp_path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks_dir)

    for status in ("completed", "completed_with_issues"):
        record = _record()
        record.task_id = status
        record.status = "running"
        record.completed_count = 2
        record.total_count = 7
        record.active_item = "question-3"
        task_store.task_dir(record.task_id).mkdir()
        task_store.save_task(record)

        updated = task_store.update_task(record.task_id, status=status)

        assert updated.completed_count == 7
        assert updated.total_count == 7
        assert updated.active_item == ""
        assert updated.health_status == "normal"


def test_unsuccessful_terminal_states_preserve_partial_progress(tmp_path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks_dir)

    for status in ("failed", "cancelled"):
        record = _record()
        record.task_id = status
        record.status = "running"
        record.completed_count = 2
        record.total_count = 7
        task_store.task_dir(record.task_id).mkdir()
        task_store.save_task(record)

        updated = task_store.update_task(record.task_id, status=status)

        assert updated.completed_count == 2
        assert updated.total_count == 7


def test_health_updates_cannot_persist_completed_above_total(tmp_path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks_dir)
    task_store.task_dir("atomic-task").mkdir()
    task_store.save_task(_record())

    updated = task_store.update_task_health(
        "atomic-task",
        completed_count=9,
        total_count=4,
        progress=True,
    )

    assert updated.completed_count == 4
    assert updated.total_count == 4


def test_late_health_updates_cannot_mutate_terminal_task_state(tmp_path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks_dir)
    record = _record()
    record.status = "running"
    record.completed_count = 2
    record.total_count = 5
    task_store.task_dir(record.task_id).mkdir()
    task_store.save_task(record)
    task_store.update_task(record.task_id, status="cancelled", current_stage="cancelled", error="用户取消任务")

    updated = task_store.update_task_health(
        record.task_id,
        current_operation="迟到的阶段回调",
        completed_count=5,
        health_status="normal",
        progress=True,
    )

    assert updated.status == "cancelled"
    assert updated.current_stage == "cancelled"
    assert updated.completed_count == 2
    assert updated.health_status == "error"
    assert updated.warning_reason == "用户取消任务"
