from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app import hybrid_local, pipeline, task_store
from app.task_store import TaskRecord


def test_local_delivery_refuses_incomplete_cloud_diagnostics(tmp_path, monkeypatch) -> None:
    tasks = tmp_path / "tasks"
    outputs = tmp_path / "outputs"
    tasks.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks)
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", outputs)
    record = TaskRecord(
        task_id="hybrid-incomplete",
        exam_path="exam.docx",
        textbooks_dir="textbooks",
        provider="provider",
        model="model",
        status="downloading",
        created_at="2026-08-22 12:00:00",
        updated_at="2026-08-22 12:00:00",
    )
    task_store.task_dir(record.task_id).mkdir()
    task_store.save_task(record)

    try:
        hybrid_local.complete_hybrid_local_delivery(record.task_id, render_with_word=True)
    except RuntimeError as exc:
        assert "诊断不完整" in str(exc)
    else:
        raise AssertionError("incomplete handoff must not generate Word")


def test_local_delivery_uses_cloud_outputs_and_keeps_word_local(tmp_path, monkeypatch) -> None:
    tasks = tmp_path / "tasks"
    outputs = tmp_path / "outputs"
    tasks.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks)
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", outputs)
    record = TaskRecord(
        task_id="hybrid-complete",
        exam_path="exam.docx",
        textbooks_dir="textbooks",
        provider="provider",
        model="model",
        status="downloading",
        created_at="2026-08-22 12:00:00",
        updated_at="2026-08-22 12:00:00",
        execution_mode="hybrid",
        cloud_job_id="job-1",
    )
    root = task_store.task_dir(record.task_id)
    stage = root / "stage_outputs"
    stage.mkdir(parents=True)
    task_store.save_task(record)
    payloads = {
        "structured_exam.json": {"items": []},
        "answer_fragments.json": {"fragments": []},
        "evidence_selection.json": {},
        "content_quality_audit.json": {"ok": True},
        "hybrid_handoff.json": {"status": "awaiting_local_delivery"},
        "cloud_pipeline_status.json": {"stages": []},
        "hybrid_import_receipt.json": {"status": "imported"},
    }
    for name, value in payloads.items():
        (stage / name).write_text(json.dumps(value), encoding="utf-8")
    (stage / "confirmed_evidence_candidates.csv").write_text("evidence_id\n", encoding="utf-8")
    captured = {}

    def fake_delivery(**kwargs):
        captured.update(kwargs)
        task_store.update_task(record.task_id, status="completed", current_stage="completed")
        return {"status": "passed"}

    monkeypatch.setattr(hybrid_local, "complete_pipeline_delivery", fake_delivery)
    monkeypatch.setattr(hybrid_local, "load_confirmed_candidates", lambda _path: [])
    monkeypatch.setattr(hybrid_local, "get_provider", lambda _name: SimpleNamespace(default_model="model", thinking_mode="auto"))
    monkeypatch.setattr(hybrid_local, "replace", lambda value, **_changes: value)

    report = hybrid_local.complete_hybrid_local_delivery(record.task_id, render_with_word=True)

    assert report["status"] == "passed"
    assert captured["render_with_word"] is True
    assert captured["stage_dir"] == stage
    assert (stage / "cloud_pipeline_status.json").exists()
