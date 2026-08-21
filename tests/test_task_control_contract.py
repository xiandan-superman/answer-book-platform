from __future__ import annotations

import json

import pytest

from app import task_control, task_store
from app.task_store import TaskRecord


def _save_task(tmp_path, monkeypatch, *, status: str) -> TaskRecord:
    tasks_dir = tmp_path / "tasks"
    outputs_dir = tmp_path / "outputs"
    tasks_dir.mkdir()
    outputs_dir.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(task_control, "OUTPUTS_DIR", outputs_dir)
    record = TaskRecord(
        task_id="exam_contract_test",
        exam_path="exam.docx",
        textbooks_dir="textbooks",
        provider="test",
        model="test",
        status=status,
        created_at="2026-08-05 10:00:00",
        updated_at="2026-08-05 10:00:00",
    )
    task_store.task_dir(record.task_id).mkdir()
    task_store.save_task(record)
    return record


def test_created_task_cannot_be_paused_but_can_be_cancelled(tmp_path, monkeypatch) -> None:
    record = _save_task(tmp_path, monkeypatch, status="created")

    paused = task_control.control_task(record.task_id, "pause")
    cancelled = task_control.control_task(record.task_id, "cancel")

    assert paused["ok"] is False
    assert cancelled["ok"] is True
    assert task_store.load_task(record.task_id).status == "cancelled"


def test_completed_task_cannot_be_cancelled(tmp_path, monkeypatch) -> None:
    record = _save_task(tmp_path, monkeypatch, status="completed")

    result = task_control.control_task(record.task_id, "cancel")

    assert result["ok"] is False
    assert not task_control.control_path(record.task_id).exists()
    assert task_store.load_task(record.task_id).status == "completed"


def test_running_task_pause_and_resume_preserve_the_same_task_record(tmp_path, monkeypatch) -> None:
    record = _save_task(tmp_path, monkeypatch, status="running")

    paused = task_control.control_task(record.task_id, "pause")
    paused_record = task_store.load_task(record.task_id)
    resumed = task_control.control_task(record.task_id, "resume")
    resumed_record = task_store.load_task(record.task_id)

    assert paused["ok"] is True
    assert paused_record.status == "paused"
    assert task_control.read_task_control(record.task_id) == {}
    assert resumed["ok"] is True
    assert resumed_record.task_id == record.task_id
    assert resumed_record.status == "running"
    assert resumed_record.error == ""


def test_detached_resume_is_queued_with_checkpoint_reconciliation(tmp_path, monkeypatch) -> None:
    record = _save_task(tmp_path, monkeypatch, status="paused")
    stage = task_store.task_dir(record.task_id) / "stage_outputs"
    stage.mkdir()
    (stage / "structured_exam.json").write_text('{"items": [{"question_id": "q1"}]}', encoding="utf-8")
    (stage / "answer_fragments.json").write_text('{"fragments": []}', encoding="utf-8")

    resumed = task_control.control_task(record.task_id, "resume", detached_resume=True)

    assert resumed["ok"] is True
    assert resumed["restart_required"] is True
    assert resumed["checkpoint_reconciliation"]["redrive_question_ids"] == ["q1"]
    assert "复用 0 题，重做 1 题" in resumed["message"]
    assert task_store.load_task(record.task_id).status == "queued"
    assert (stage / "answer_checkpoint_reconciliation.json").exists()


def test_control_write_keeps_previous_instruction_if_atomic_replace_fails(tmp_path, monkeypatch) -> None:
    record = _save_task(tmp_path, monkeypatch, status="running")
    path = task_control.control_path(record.task_id)
    previous = {"action": "pause", "reason": "旧指令", "updated_at": "2026-08-14 12:00:00"}
    path.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")

    def fail_replace(_source, _target) -> None:
        raise OSError("模拟原子替换失败")

    monkeypatch.setattr(task_control.os, "replace", fail_replace)

    with pytest.raises(OSError, match="模拟原子替换失败"):
        task_control.write_task_control(record.task_id, "cancel", "新指令")

    assert task_control.read_task_control(record.task_id) == previous
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
