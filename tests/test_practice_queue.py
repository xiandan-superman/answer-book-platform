from __future__ import annotations

import time

from huey import SqliteHuey

from app import practice_jobs, practice_queue


def _test_queue(tmp_path, monkeypatch) -> SqliteHuey:
    queue = SqliteHuey(
        "test-practice",
        filename=str(tmp_path / "queue.sqlite3"),
        results=True,
        strict_fifo=True,
    )

    @queue.task(name="tests.execute_practice_job")
    def execute(job_id: str) -> None:
        practice_jobs.run_practice_job(
            job_id,
            lambda operation, payload: {
                "result": {"operation": operation, "title": payload.get("knowledge_title", "")},
                "history_id": "",
            },
        )

    monkeypatch.setattr(practice_queue, "practice_huey", queue)
    monkeypatch.setattr(practice_queue, "execute_queued_practice_job", execute)
    return queue


def test_huey_sqlite_queue_persists_job_id_and_executes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    queue = _test_queue(tmp_path, monkeypatch)
    created = practice_jobs.create_practice_job(
        "analyze",
        {"source_mode": "knowledge", "knowledge_title": "扩散"},
    )

    queued = practice_queue.enqueue_practice_job(created["job_id"])

    assert queued["queue_backend"] == "huey_sqlite"
    pending = queue.pending()[0]
    assert queued["queue_task_id"] == pending.id
    assert pending.args == (created["job_id"],)
    queue_bytes = (tmp_path / "queue.sqlite3").read_bytes()
    assert "扩散".encode("utf-8") not in queue_bytes
    task = queue.dequeue()
    queue.execute(task)
    completed = practice_jobs.load_practice_job(created["job_id"])
    assert completed["status"] == "completed"
    assert completed["result"] == {"operation": "analyze", "title": "扩散"}


def test_recovery_does_not_duplicate_a_still_pending_queue_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    queue = _test_queue(tmp_path, monkeypatch)
    created = practice_jobs.create_practice_job("plan", {"source_mode": "exam"})
    queued = practice_queue.enqueue_practice_job(created["job_id"])

    result = practice_queue.recover_practice_queue([queued])

    assert result == {"resumed": 0, "already_queued": 1}
    assert queue.pending_count() == 1


def test_recovery_reuses_pending_message_even_when_record_was_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    queue = _test_queue(tmp_path, monkeypatch)
    created = practice_jobs.create_practice_job("plan", {"source_mode": "exam"})
    queued = practice_queue.enqueue_practice_job(created["job_id"])
    running = practice_jobs.update_practice_job(created["job_id"], status="running")

    result = practice_queue.recover_practice_queue([{**running, "queue_task_id": queued["queue_task_id"]}])

    assert result == {"resumed": 0, "already_queued": 1}
    assert queue.pending_count() == 1
    assert practice_jobs.load_practice_job(created["job_id"])["status"] == "queued"


def test_embedded_consumer_processes_jobs_without_server_owned_worker_threads(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    _test_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(practice_queue, "_CONSUMER", None)
    monkeypatch.setattr(practice_queue, "_worker_count", lambda: 1)
    created = practice_jobs.create_practice_job("analyze", {"source_mode": "exam"})
    practice_queue.enqueue_practice_job(created["job_id"])

    practice_queue.start_practice_queue_consumer()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if practice_jobs.load_practice_job(created["job_id"])["status"] == "completed":
                break
            time.sleep(0.02)
        assert practice_jobs.load_practice_job(created["job_id"])["status"] == "completed"
    finally:
        practice_queue.stop_practice_queue_consumer()
