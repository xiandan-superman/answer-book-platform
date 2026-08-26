from __future__ import annotations

import threading
from concurrent.futures import Future

from app import hybrid_client, pipeline, task_runner, task_store
from app.task_store import TaskRecord


def _record(tmp_path, monkeypatch, task_id: str = "exam_admission") -> TaskRecord:
    tasks = tmp_path / "tasks"
    tasks.mkdir(exist_ok=True)
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks)
    record = TaskRecord(
        task_id=task_id,
        exam_path="exam.docx",
        textbooks_dir="textbooks",
        provider="test",
        model="test",
        status="created",
        created_at="2026-08-12 10:00:00",
        updated_at="2026-08-12 10:00:00",
    )
    task_store.task_dir(task_id).mkdir(exist_ok=True)
    task_store.save_task(record)
    return record


def test_repeated_exam_start_reuses_one_active_future(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch)
    blocker = threading.Event()
    calls: list[str] = []

    def fake_run(task_id: str, **_kwargs) -> None:
        calls.append(task_id)
        blocker.wait(2)

    monkeypatch.setattr(task_runner, "run_exam_task", fake_run)
    with task_runner._ACTIVE_LOCK:
        task_runner._ACTIVE_RUNS.clear()
    first = task_runner.start_exam_task(record.task_id, use_model=False, render=False, reuse_fragments=False)
    second = task_runner.start_exam_task(record.task_id, use_model=False, render=False, reuse_fragments=False)
    blocker.set()
    first.result(timeout=2)

    assert isinstance(first, Future)
    assert second is first
    assert calls == [record.task_id]


def test_cancelled_exam_task_can_be_explicitly_retried(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_cancelled_queue")
    task_store.update_task(record.task_id, status="cancelled", current_stage="cancelled")
    calls: list[str] = []
    monkeypatch.setattr(task_runner, "run_exam_task", lambda task_id, **_kwargs: calls.append(task_id))
    with task_runner._ACTIVE_LOCK:
        task_runner._ACTIVE_RUNS.clear()

    future = task_runner.start_exam_task(record.task_id, use_model=False, render=False, reuse_fragments=False)
    future.result(timeout=2)

    assert calls == [record.task_id]


def test_cancelled_queued_exam_worker_does_not_enter_pipeline(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_cancelled_worker")
    task_store.update_task(record.task_id, status="cancelled", current_stage="cancelled")
    monkeypatch.setattr(task_runner, "run_pipeline", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline entered")))

    task_runner.run_exam_task(record.task_id, use_model=False, render=False, reuse_fragments=False)


def test_model_exam_runs_locally_when_hybrid_switch_is_off(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_local_mode")
    local_calls = []
    monkeypatch.setattr(hybrid_client, "hybrid_enabled", lambda: False)
    monkeypatch.setattr(
        hybrid_client,
        "run_hybrid_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("hybrid pipeline entered")),
    )
    monkeypatch.setattr(task_runner, "run_pipeline", lambda task_id, options: local_calls.append((task_id, options)))

    task_runner.run_exam_task(record.task_id, use_model=True, render=False, reuse_fragments=False)

    assert len(local_calls) == 1
    assert local_calls[0][0] == record.task_id
    assert local_calls[0][1].use_model is True


def test_model_exam_uses_server_only_after_hybrid_switch_is_on(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_hybrid_mode")
    hybrid_calls = []
    monkeypatch.setattr(hybrid_client, "hybrid_enabled", lambda: True)
    monkeypatch.setattr(
        hybrid_client,
        "run_hybrid_task",
        lambda task_id, *, render_with_word: hybrid_calls.append((task_id, render_with_word)),
    )
    monkeypatch.setattr(
        task_runner,
        "run_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local pipeline entered")),
    )

    task_runner.run_exam_task(record.task_id, use_model=True, render=True, reuse_fragments=False)

    assert hybrid_calls == [(record.task_id, True)]


def test_pipeline_worker_entry_never_clears_a_new_cancellation() -> None:
    import inspect

    source = inspect.getsource(pipeline._run_pipeline_impl)
    entry = source[source.index("try:"):source.index("telemetry.start_heartbeat()")]

    assert "checkpoint(task_id)" in entry
    assert "clear_task_control(task_id)" not in entry


def test_detached_pause_resume_is_atomically_requeued_with_saved_options(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_detached_resume")
    record.status = "paused"
    record.last_run_use_model = False
    record.last_run_render = False
    task_store.save_task(record)
    stage = task_store.task_dir(record.task_id) / "stage_outputs"
    stage.mkdir()
    (stage / "structured_exam.json").write_text('{"items": [{"question_id": "q1"}]}', encoding="utf-8")
    calls: list[dict[str, object]] = []
    blocker = threading.Event()

    def fake_run(task_id: str, **kwargs: object) -> None:
        calls.append({"task_id": task_id, **kwargs})
        blocker.wait(2)

    monkeypatch.setattr(
        task_runner,
        "run_exam_task",
        fake_run,
    )
    with task_runner._ACTIVE_LOCK:
        task_runner._ACTIVE_RUNS.clear()

    result = task_runner.resume_exam_task(record.task_id)
    with task_runner._ACTIVE_LOCK:
        future = task_runner._ACTIVE_RUNS[record.task_id]
    blocker.set()
    future.result(timeout=2)

    assert result["ok"] is True
    assert result["requeued"] is True
    assert result["restart_required"] is False
    assert calls == [
        {
            "task_id": record.task_id,
            "use_model": False,
            "render": False,
            "document_diagnostics": False,
            "reuse_fragments": True,
        }
    ]


def test_live_pause_resume_releases_existing_worker_without_requeue(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_live_resume")
    record.status = "paused"
    task_store.save_task(record)
    stage = task_store.task_dir(record.task_id) / "stage_outputs"
    stage.mkdir()
    (stage / "structured_exam.json").write_text('{"items": [{"question_id": "q1"}]}', encoding="utf-8")
    active: Future[None] = Future()
    with task_runner._ACTIVE_LOCK:
        task_runner._ACTIVE_RUNS.clear()
        task_runner._ACTIVE_RUNS[record.task_id] = active

    result = task_runner.resume_exam_task(record.task_id)

    assert result["ok"] is True
    assert result["restart_required"] is False
    assert "requeued" not in result
    assert task_store.load_task(record.task_id).status == "running"
    with task_runner._ACTIVE_LOCK:
        assert task_runner._ACTIVE_RUNS[record.task_id] is active
        task_runner._ACTIVE_RUNS.clear()


def test_detached_resume_does_not_resurrect_interleaved_cancellation(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_resume_cancel_race")
    record.status = "paused"
    task_store.save_task(record)
    stage = task_store.task_dir(record.task_id) / "stage_outputs"
    stage.mkdir()
    (stage / "structured_exam.json").write_text('{"items": [{"question_id": "q1"}]}', encoding="utf-8")
    original_control = task_runner.control_task

    def cancel_after_resume(task_id: str, action: str, **kwargs):
        result = original_control(task_id, action, **kwargs)
        task_store.update_task(task_id, status="cancelled", current_stage="cancelled")
        return result

    monkeypatch.setattr(task_runner, "control_task", cancel_after_resume)
    with task_runner._ACTIVE_LOCK:
        task_runner._ACTIVE_RUNS.clear()

    result = task_runner.resume_exam_task(record.task_id)

    assert result["ok"] is False
    assert result["restart_required"] is False
    assert task_store.load_task(record.task_id).status == "cancelled"
    with task_runner._ACTIVE_LOCK:
        assert record.task_id not in task_runner._ACTIVE_RUNS


def test_resume_admission_requires_the_reconciled_queued_state(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path, monkeypatch, "exam_resume_expected_state")
    record.status = "cancelled"
    task_store.save_task(record)

    try:
        task_runner.start_exam_task(
            record.task_id,
            use_model=False,
            render=False,
            reuse_fragments=True,
            expected_status="queued",
        )
    except RuntimeError as exc:
        assert "预期任务状态为 queued" in str(exc)
    else:
        raise AssertionError("应拒绝非 queued 状态的恢复入队")

    assert task_store.load_task(record.task_id).status == "cancelled"
