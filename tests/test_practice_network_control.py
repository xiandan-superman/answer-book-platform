from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import app.capabilities  # noqa: F401  # Initialize the capability registry before LLM imports.
from app import exercise_generation, llm_client, practice_jobs, practice_worker, task_read_model
from app.concurrency import ModelRequestAborted
from app.practice_runtime import (
    PracticeGenerationStopped,
    ensure_practice_generation_active,
    iter_bounded_futures,
    partition_compatible_batches,
)
from app.settings import ProviderConfig


def test_compatible_batch_partition_reuses_context_key_and_splits_index_gaps() -> None:
    rows = [
        (0, {"source": "a", "value": 1}),
        (1, {"source": "a", "value": 2}),
        (2, {"source": "b", "value": 3}),
        (4, {"source": "b", "value": 4}),
        (5, {"source": "b", "value": 5}),
    ]
    batches = partition_compatible_batches(
        rows,
        compatibility_key=lambda item: item["source"],
        max_batch_size=3,
    )
    assert [(start, [item["value"] for item in items]) for start, items in batches] == [
        (0, [1, 2]),
        (2, [3]),
        (4, [4, 5]),
    ]


def _job(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    return practice_jobs.create_practice_job(
        "generate_from_plan",
        {"source_mode": "exam", "practice_batch_id": "network-stage-12"},
    )


def test_generation_deadline_leaves_room_for_multiple_eight_minute_requests(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    deadline = datetime.fromisoformat(job["generation_deadline_at"])
    remaining = (deadline - datetime.now().astimezone()).total_seconds()

    assert 7195 <= remaining <= 7200


def test_pause_invalidates_lease_and_resume_keeps_original_deadline(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    practice_jobs.update_practice_job(job["job_id"], status="running")
    original = practice_jobs.load_practice_job(job["job_id"])
    old_epoch = original["control_epoch"]

    paused = practice_jobs.pause_practice_job(job["job_id"])
    assert paused["status"] == "paused"
    with pytest.raises(PracticeGenerationStopped, match="已停止"):
        ensure_practice_generation_active({"_job_id": job["job_id"], "_job_epoch": old_epoch})

    resumed = practice_jobs.resume_practice_job(job["job_id"])
    assert resumed["status"] == "queued"
    assert resumed["generation_deadline_at"] == original["generation_deadline_at"]
    assert resumed["control_epoch"] == old_epoch + 2


def test_old_worker_cannot_overwrite_resumed_lease(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def old_worker(_operation, _payload):
        started.set()
        assert release.wait(2)
        raise RuntimeError("迟到故障")

    thread = threading.Thread(target=practice_jobs.run_practice_job, args=(job["job_id"], old_worker))
    thread.start()
    assert started.wait(2)
    practice_jobs.pause_practice_job(job["job_id"])
    practice_jobs.resume_practice_job(job["job_id"])
    practice_jobs.update_practice_job(job["job_id"], expected_status="queued", status="running")
    release.set()
    thread.join(2)

    latest = practice_jobs.load_practice_job(job["job_id"])
    assert latest["status"] == "running"
    assert latest["error"] == ""


def test_concurrent_cancel_is_idempotent_and_invalidates_once(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    practice_jobs.update_practice_job(job["job_id"], status="running")
    results = []

    threads = [
        threading.Thread(
            target=lambda: results.append(practice_jobs.cancel_practice_job(job["job_id"]))
        )
        for _ in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)

    latest = practice_jobs.load_practice_job(job["job_id"])
    assert latest["status"] == "cancelled"
    assert latest["control_epoch"] == 1
    assert sum(result["ok"] is True for result in results) == 1


def test_cancelled_late_generation_never_saves_history(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    practice_jobs.update_practice_job(job["job_id"], status="running")
    epoch = practice_jobs.load_practice_job(job["job_id"])["control_epoch"]
    saved = []

    monkeypatch.setattr(practice_worker, "find_completed_by_plan", lambda _payload: None)
    monkeypatch.setattr(practice_worker, "generate_practice_from_plan", lambda _payload: {"exercises": []})
    monkeypatch.setattr(practice_worker, "save_practice_record", lambda *_args, **_kwargs: saved.append(True))
    practice_jobs.cancel_practice_job(job["job_id"])

    with pytest.raises(PracticeGenerationStopped):
        practice_worker.execute_practice_operation(
            "generate_from_plan",
            {"_job_id": job["job_id"], "_job_epoch": epoch, "fresh_generation": True},
        )
    assert saved == []


def test_parent_deadline_and_call_budget_persist_across_recovery(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    past = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat(timespec="seconds")
    practice_jobs.update_practice_job(job["job_id"], status="running", generation_deadline_at=past)
    payload = {"_job_id": job["job_id"]}
    coordinator = exercise_generation._GenerationRetryCoordinator(payload)

    with pytest.raises(exercise_generation._GenerationRetryBudgetExceeded, match="截止"):
        coordinator.reserve("batch", limit=3, phase="initial")
    recovered = exercise_generation._GenerationRetryCoordinator(payload)
    assert recovered.batch_stop("batch", limit=3)["code"] == "generation_parent_deadline_exceeded"
    assert practice_jobs.load_practice_job(job["job_id"])["generation_deadline_at"] == past


def test_paused_job_cannot_resume_after_parent_deadline(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    past = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat(timespec="seconds")
    practice_jobs.update_practice_job(
        job["job_id"], status="paused", generation_deadline_at=past
    )

    result = practice_jobs.resume_practice_job(job["job_id"])

    assert result == {
        "ok": False,
        "task_id": job["job_id"],
        "status": "paused",
        "code": "generation_deadline_expired",
        "message": "本批次生成截止时间已到，请从检查点重试未完成项。",
    }
    latest = practice_jobs.load_practice_job(job["job_id"])
    assert latest["status"] == "paused"
    assert latest["generation_deadline_at"] == past


def test_generation_deadline_enters_draining_without_losing_worker_lease(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    running = practice_jobs.update_practice_job(job["job_id"], status="running")

    draining = practice_jobs._mark_generation_deadline_draining(
        job["job_id"],
        lease_epoch=int(running["control_epoch"]),
        elapsed=421,
    )

    assert draining["status"] == "running"
    assert draining["control_epoch"] == running["control_epoch"]
    assert draining["deadline_stop_requested"] is True
    assert draining["progress_message"] == "网络等待截止时间已到，已停止派发新请求；正在保存已完成题目。"
    # The still-valid lease lets the worker finalize and persist a partial
    # result/history instead of being rejected as a stale failed task.
    ensure_practice_generation_active({
        "_job_id": job["job_id"],
        "_job_epoch": running["control_epoch"],
    })


def test_invalidated_inflight_attempt_is_charged_and_recorded(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    practice_jobs.update_practice_job(job["job_id"], status="running")
    coordinator = exercise_generation._GenerationRetryCoordinator({"_job_id": job["job_id"]})
    monkeypatch.setattr(
        exercise_generation,
        "_call_practice_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PracticeGenerationStopped("暂停")),
    )

    with pytest.raises(PracticeGenerationStopped):
        exercise_generation._call_practice_json_with_transport_retry(
            object(), [], model="fake", temperature=0, thinking=None, timeout_seconds=1,
            before_attempt=lambda _attempt: coordinator.reserve("batch", limit=1, phase="call"),
            after_attempt=lambda _attempt, detail: coordinator.record("batch", phase="call", detail=detail),
        )

    attempt = coordinator.summary()["batches"]["batch"]["attempts"][0]
    assert coordinator.summary()["total_model_calls"] == 1
    assert attempt["status"] == "failed"
    assert attempt["error_code"] == "generation_request_invalidated"


def test_resumed_epoch_owns_retry_state_and_old_epoch_cannot_write_back(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    practice_jobs.update_practice_job(job["job_id"], status="running")
    original_epoch = practice_jobs.load_practice_job(job["job_id"])["control_epoch"]
    old = exercise_generation._GenerationRetryCoordinator({
        "_job_id": job["job_id"],
        "_job_epoch": original_epoch,
    })
    old.reserve("batch", limit=3, phase="initial")

    practice_jobs.pause_practice_job(job["job_id"])
    resumed = practice_jobs.resume_practice_job(job["job_id"])
    resumed_epoch = resumed["control_epoch"]
    practice_jobs.update_practice_job(job["job_id"], expected_status="queued", status="running")

    current = exercise_generation._GenerationRetryCoordinator({
        "_job_id": job["job_id"],
        "_job_epoch": resumed_epoch,
    })
    stale_attempt = current.summary()["batches"]["batch"]["attempts"][0]
    assert stale_attempt["error_code"] == "generation_request_invalidated"
    current.reserve("batch", limit=3, phase="resumed")
    current.record("batch", phase="resumed", detail=None)

    old.record("batch", phase="initial", detail={
        "code": "provider_call_deadline_exceeded",
        "signature": "provider_call_deadline_exceeded",
    })
    durable = practice_jobs.load_practice_job(job["job_id"])["generation_retry_state"]
    assert durable["batches"]["batch"]["attempts"] == [
        {
            "call": 1,
            "phase": "initial",
            "status": "failed",
            "lease_epoch": original_epoch,
            "signature": "generation_request_invalidated",
            "error_code": "generation_request_invalidated",
        },
        {
            "call": 2,
            "phase": "resumed",
            "status": "succeeded",
            "lease_epoch": resumed_epoch,
        },
    ]
    assert practice_jobs.load_practice_job(job["job_id"])["network_attempted_count"] == 2


def test_network_admission_abort_becomes_generation_stop_and_is_not_retried(monkeypatch) -> None:
    calls = 0
    details = []

    def abort(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ModelRequestAborted("已取消")

    monkeypatch.setattr(exercise_generation, "_call_practice_json", abort)
    with pytest.raises(PracticeGenerationStopped, match="已取消"):
        exercise_generation._call_practice_json_with_transport_retry(
            object(), [], model="fake", temperature=0, thinking=None,
            timeout_seconds=1, attempts=3,
            after_attempt=lambda _attempt, detail: details.append(detail),
        )
    assert calls == 1
    assert details[0]["code"] == "generation_request_invalidated"


def test_network_body_read_has_idle_and_hard_boundaries(monkeypatch) -> None:
    class Response:
        def __init__(self):
            self.parts = [b'{"ok":', b"true}", b""]
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

        def read(self, _size):
            return self.parts.pop(0)

    response = Response()
    assert json.loads(llm_client._read_response_body(response, hard_timeout=5)) == {"ok": True}
    assert response.timeouts
    assert max(response.timeouts) <= 5


def _ark_fixture_provider(base_url: str) -> ProviderConfig:
    return ProviderConfig(
        name="ark",
        type="openai_compatible",
        base_url=base_url,
        api_key="stage-12-deterministic-fixture-key",
        default_model="ark-stage-12-fixture-model",
        model_options=("ark-stage-12-fixture-model",),
        allow_custom_model=False,
        model_hint="",
        temperature=0,
        max_tokens=64,
    )


def _run_ark_transport_fixture(mode: str, monkeypatch, *, hard_timeout: int) -> llm_client.LLMResult:
    response_body = json.dumps({
        "choices": [{"message": {"content": "{\"fixture\": true}"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if mode == "first_byte":
                time.sleep(1.2)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            try:
                if mode == "read_idle":
                    self.wfile.write(response_body[:8])
                    self.wfile.flush()
                    time.sleep(1.2)
                    self.wfile.write(response_body[8:])
                elif mode == "hard_timeout":
                    for byte in response_body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.2)
                else:
                    self.wfile.write(response_body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PRACTICE_MODEL_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("PRACTICE_MODEL_FIRST_BYTE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("PRACTICE_MODEL_READ_IDLE_TIMEOUT_SECONDS", "1")
    try:
        client = llm_client.OpenAICompatibleClient(
            _ark_fixture_provider(f"http://127.0.0.1:{server.server_port}/api/v3")
        )
        return client.chat_text(
            [{"role": "user", "content": "deterministic Ark fixture"}],
            timeout=hard_timeout,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_ark_fixture_separates_first_byte_timeout(monkeypatch) -> None:
    with pytest.raises(llm_client.LLMError) as captured:
        _run_ark_transport_fixture("first_byte", monkeypatch, hard_timeout=3)
    assert captured.value.transport_phase == "first_byte"


def test_ark_fixture_separates_read_idle_timeout(monkeypatch) -> None:
    with pytest.raises(llm_client.LLMError) as captured:
        _run_ark_transport_fixture("read_idle", monkeypatch, hard_timeout=3)
    assert captured.value.transport_phase == "read_idle"


def test_ark_fixture_enforces_one_call_wall_clock_deadline(monkeypatch) -> None:
    started = time.monotonic()
    with pytest.raises(llm_client.LLMError) as captured:
        _run_ark_transport_fixture("hard_timeout", monkeypatch, hard_timeout=2)
    assert captured.value.transport_phase == "hard_timeout"
    assert time.monotonic() - started < 4


def test_connect_timeout_has_its_own_transport_phase(monkeypatch) -> None:
    def timeout_connect(_connection) -> None:
        raise socket.timeout("deterministic connect timeout")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", timeout_connect)
    connection = llm_client._LayeredHTTPConnection(
        "127.0.0.1:9",
        connect_timeout=1,
        first_byte_timeout=2,
        hard_deadline_monotonic=time.monotonic() + 3,
    )
    with pytest.raises(llm_client.LLMError) as captured:
        connection.connect()
    assert captured.value.transport_phase == "connect"


def test_non_interruptible_batch_threads_are_bounded() -> None:
    release = threading.Event()
    started: list[int] = []
    lock = threading.Lock()

    def worker(item: int) -> int:
        with lock:
            started.append(item)
        if item:
            release.wait(2)
        return item

    futures = iter_bounded_futures(range(30), worker, max_workers=3, thread_name_prefix="stage12-bound")
    item, future = next(futures)
    assert future.result() == item == 0
    futures.close()
    try:
        time.sleep(0.05)
        assert len(started) <= 3
    finally:
        release.set()


def test_network_error_layers_are_sanitized() -> None:
    connect = exercise_generation._generation_error_detail(
        llm_client.LLMError("socket secret detail", transport_phase="connect")
    )
    first_byte = exercise_generation._generation_error_detail(
        llm_client.LLMError("socket secret detail", transport_phase="first_byte")
    )
    idle = exercise_generation._generation_error_detail(
        llm_client.LLMError("socket secret detail", transport_phase="read_idle")
    )
    hard = exercise_generation._generation_error_detail(
        llm_client.LLMError("socket secret detail", transport_phase="hard_timeout")
    )
    active_hard = exercise_generation._generation_error_detail(
        llm_client.LLMError(
            "socket secret detail",
            transport_phase="hard_timeout",
            partial_output_received=True,
        )
    )
    auth = exercise_generation._generation_error_detail(
        llm_client.LLMError("raw provider json", status_code=401)
    )
    assert connect["code"] == "provider_connect_timeout" and connect["retryable"] is True
    assert first_byte["code"] == "provider_first_byte_timeout" and first_byte["retryable"] is True
    assert idle["code"] == "provider_read_idle_timeout" and idle["retryable"] is True
    assert hard["code"] == "provider_call_deadline_exceeded" and hard["retryable"] is True
    assert active_hard["retryable"] is False and active_hard["partial_output_received"] is True
    assert auth["requires_configuration"] is True and auth["retryable"] is False
    assert "raw provider json" not in auth["message"]


def test_task_contract_exposes_pause_resume_attempts_and_remaining_time(tmp_path, monkeypatch) -> None:
    job = _job(tmp_path, monkeypatch)
    practice_jobs.update_practice_job(job["job_id"], status="paused", network_attempted_count=2)
    record = practice_jobs.load_practice_job(job["job_id"])
    run = task_read_model._practice_job_run(record, [])

    assert run["capabilities"]["resume"] is True
    assert run["capabilities"]["pause"] is False
    assert run["network_attempted_count"] == 2
    assert isinstance(run["deadline_remaining_seconds"], int)
    assert run["duration_text"] == "已暂停"
    assert run["progress_percent"] < 100


def test_stage_12_uses_fake_provider_only() -> None:
    provider = _ark_fixture_provider("http://127.0.0.1:9/api/v3")
    assert provider.name == "ark"
    assert provider.default_model == "ark-stage-12-fixture-model"
    assert provider.base_url.startswith("http://127.0.0.1:")
