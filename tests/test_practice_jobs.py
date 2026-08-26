from __future__ import annotations

import threading

from app import practice_jobs


def test_practice_job_is_visible_while_running_and_persists_result(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job(
        "generate_from_plan",
        {"source_mode": "knowledge", "knowledge_title": "相平衡"},
    )

    listed = practice_jobs.list_practice_jobs()
    assert listed[0]["job_id"] == created["job_id"]
    assert listed[0]["status"] == "queued"
    assert "payload" not in listed[0]

    practice_jobs.run_practice_job(
        created["job_id"],
        lambda operation, payload: {
            "result": {"operation": operation, "title": payload["knowledge_title"]},
            "history_id": "practice_20260731120000_abcdefgh",
        },
    )
    completed = practice_jobs.load_practice_job(created["job_id"])
    assert completed["status"] == "completed"
    assert completed["result"]["title"] == "相平衡"
    # Once saved to normal history, the transient job no longer duplicates the task row.
    assert practice_jobs.list_practice_jobs() == []


def test_practice_job_records_worker_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(practice_jobs, "pin_model_diagnostics_for_failure", lambda _job_id: 2)
    created = practice_jobs.create_practice_job("plan", {"source_mode": "exam"})

    def fail(_operation, _payload):
        error = RuntimeError("模型连接失败")
        error.failure_context = {"failure_type": "blueprint_audit", "blueprint": {"exercise_plan": [1]}}
        raise error

    practice_jobs.run_practice_job(created["job_id"], fail)
    failed = practice_jobs.load_practice_job(created["job_id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "模型连接失败"
    assert failed["failure_context"]["failure_type"] == "blueprint_audit"
    assert failed["failure_context"]["blueprint"]["exercise_plan"] == [1]
    assert failed["diagnostic_context"]["pinned_model_traces"] == 2
    assert failed["diagnostic_context"]["exception_type"] == "RuntimeError"
    assert "raise error" in failed["diagnostic_context"]["traceback"]
    assert failed["support_id"].startswith("PJ-")


def test_only_explicit_selected_provider_missing_key_marks_configuration_required(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(practice_jobs, "pin_model_diagnostics_for_failure", lambda _job_id: 0)

    def run_failure(provider: str, message: str) -> dict:
        created = practice_jobs.create_practice_job(
            "analyze",
            {"source_mode": "knowledge", "provider": provider},
        )

        def fail(_operation, _payload):
            raise RuntimeError(message)

        practice_jobs.run_practice_job(created["job_id"], fail)
        return practice_jobs.load_practice_job(created["job_id"])

    missing = run_failure("ark", "API key is not configured for provider: ark")
    missing_deepseek = run_failure("deepseek", "API key is not configured for provider: deepseek")
    network = run_failure("ark", "Provider request failed: connection reset")
    permission = run_failure("ark", "Provider HTTP 403: access denied")
    other_provider = run_failure("other", "API key is not configured for provider: ark")

    assert missing["requires_configuration"] is True
    assert missing["configuration_provider"] == "ark"
    assert missing["configuration_reason"] == "missing_api_key"
    assert missing_deepseek["requires_configuration"] is True
    assert missing_deepseek["configuration_provider"] == "deepseek"
    assert missing_deepseek["configuration_reason"] == "missing_api_key"
    for record in (network, permission, other_provider):
        assert record["requires_configuration"] is False
        assert record["configuration_provider"] == ""
        assert record["configuration_reason"] == ""


def test_practice_failure_diagnostics_redact_credentials_but_keep_provider_response(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(practice_jobs, "pin_model_diagnostics_for_failure", lambda _job_id: 1)
    created = practice_jobs.create_practice_job("analyze", {"source_mode": "knowledge"})
    secret = "ark-12345678-1234-1234-1234-123456789abc-secret"

    def fail(_operation, _payload):
        error = RuntimeError(
            f'Provider HTTP 404: {{"code":"InvalidEndpointOrModel.NotFound","api_key":"{secret}","request_id":"req-kept"}}'
        )
        error.failure_context = {"provider_response": {"request_id": "req-kept", "api_key": secret}}
        raise error

    practice_jobs.run_practice_job(created["job_id"], fail)
    failed = practice_jobs.load_practice_job(created["job_id"])
    serialized = str(failed)

    assert secret not in serialized
    assert "InvalidEndpointOrModel.NotFound" in serialized
    assert "req-kept" in serialized
    assert failed["failure_context"]["provider_response"]["api_key"] == "***"
    assert failed["support_id"].startswith("PJ-")


def test_interrupted_jobs_can_be_requeued_after_server_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job("analyze", {"source_mode": "exam"})
    practice_jobs.update_practice_job(created["job_id"], status="running")

    recovered = practice_jobs.recover_practice_jobs(fail_interrupted=False)

    assert [item["job_id"] for item in recovered] == [created["job_id"]]
    assert practice_jobs.load_practice_job(created["job_id"])["status"] == "running"


def test_running_practice_job_can_be_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job("generate_from_plan", {"source_mode": "exam"})
    practice_jobs.update_practice_job(created["job_id"], status="running")

    result = practice_jobs.cancel_practice_job(created["job_id"])

    assert result["ok"] is True
    assert practice_jobs.load_practice_job(created["job_id"])["status"] == "cancelled"


def test_late_worker_updates_cannot_overwrite_cancelled_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job("generate_from_plan", {"source_mode": "exam"})
    practice_jobs.update_practice_job(created["job_id"], status="running", generated_count=1, total_count=3)
    practice_jobs.cancel_practice_job(created["job_id"])

    stale_transition = practice_jobs.update_practice_job(
        created["job_id"],
        expected_status="running",
        status="completed",
        generated_count=3,
        health_status="normal",
    )
    stale_progress = practice_jobs.update_practice_job(
        created["job_id"],
        generated_count=2,
        current_operation="迟到的模型结果",
        health_status="normal",
    )
    stale_revival = practice_jobs.update_practice_job(
        created["job_id"],
        status="queued",
        current_stage="generating",
        current_operation="服务恢复后重新排队",
    )

    assert stale_transition["status"] == "cancelled"
    assert stale_progress["status"] == "cancelled"
    assert stale_revival["status"] == "cancelled"
    assert stale_progress["generated_count"] == 1
    assert stale_progress["health_status"] != "normal"


def test_restart_recovery_scans_all_active_jobs_not_only_task_manager_page(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    active_ids = []
    for index in range(105):
        created = practice_jobs.create_practice_job(
            "analyze",
            {"source_mode": "exam", "practice_batch_id": f"batch-{index}"},
        )
        active_ids.append(created["job_id"])

    recovered = practice_jobs.recover_practice_jobs(fail_interrupted=False)

    assert {item["job_id"] for item in recovered} == set(active_ids)
    assert len(recovered) == 105


def test_duplicate_queue_messages_claim_one_job_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job("analyze", {"source_mode": "exam"})
    started = threading.Event()
    release = threading.Event()
    calls = []

    def worker(operation, _payload):
        calls.append(operation)
        started.set()
        assert release.wait(2)
        return {"result": {"ok": True}, "history_id": ""}

    first = threading.Thread(target=practice_jobs.run_practice_job, args=(created["job_id"], worker))
    duplicate = threading.Thread(target=practice_jobs.run_practice_job, args=(created["job_id"], worker))
    first.start()
    assert started.wait(2)
    duplicate.start()
    duplicate.join(timeout=2)
    release.set()
    first.join(timeout=2)

    assert calls == ["analyze"]
    assert practice_jobs.load_practice_job(created["job_id"])["status"] == "completed"


def test_terminal_job_syncs_network_attempt_count_from_model_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(practice_jobs, "model_call_cost_summary", lambda _job_id: {
        "call_count": 3, "success_count": 2, "failed_count": 1,
    })
    created = practice_jobs.create_practice_job("analyze", {"source_mode": "exam"})

    practice_jobs.run_practice_job(
        created["job_id"],
        lambda _operation, _payload: {"result": {"ok": True}, "history_id": ""},
    )

    completed = practice_jobs.load_practice_job(created["job_id"])
    assert completed["status"] == "completed"
    assert completed["network_attempted_count"] == 3
    assert completed["network_stats_synced"] is True


def test_cancellation_while_worker_is_blocked_preserves_cancelled_result(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job("analyze", {"source_mode": "exam"})
    started = threading.Event()
    release = threading.Event()

    def worker(_operation, _payload):
        started.set()
        assert release.wait(2)
        return {"result": {"late": True}, "history_id": "late-history"}

    thread = threading.Thread(target=practice_jobs.run_practice_job, args=(created["job_id"], worker))
    thread.start()
    assert started.wait(2)
    practice_jobs.cancel_practice_job(created["job_id"])
    release.set()
    thread.join(timeout=2)

    cancelled = practice_jobs.load_practice_job(created["job_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["result"] is None
    assert cancelled["history_id"] == ""


def test_active_jobs_are_deduplicated_only_within_the_same_practice_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    payload = {"source_mode": "knowledge", "knowledge_title": "晶胞", "practice_batch_id": "browser-a"}
    first = practice_jobs.create_or_reuse_practice_job("analyze", payload)
    # Simulate a job created before the batch-aware fingerprint policy.
    practice_jobs.update_practice_job(first["job_id"], request_fingerprint="legacy-fingerprint")
    repeated_click = practice_jobs.create_or_reuse_practice_job("analyze", {**payload, "client_request_id": "retry-click"})
    independent_task = practice_jobs.create_or_reuse_practice_job("analyze", {**payload, "practice_batch_id": "browser-b"})

    assert repeated_click["deduplicated"] is True
    assert repeated_click["job_id"] == first["job_id"]
    assert independent_task["deduplicated"] is False
    assert independent_task["job_id"] != first["job_id"]

    practice_jobs.update_practice_job(first["job_id"], status="failed", current_stage="failed")
    third = practice_jobs.create_or_reuse_practice_job("analyze", payload)
    assert third["deduplicated"] is False
    assert third["job_id"] != first["job_id"]


def test_practice_task_title_uses_material_name_and_persists_across_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    first = practice_jobs.create_practice_job(
        "analyze",
        {
            "source_mode": "exam",
            "practice_batch_id": "batch-material-name",
            "source_files": [{"name": "热力学复习讲义.docx"}],
        },
    )
    second = practice_jobs.create_practice_job(
        "plan",
        {"source_mode": "exam", "practice_batch_id": "batch-material-name", "task_title": "会被批次名称覆盖"},
    )

    assert first["title"] == "热力学复习讲义"
    assert second["title"] == "热力学复习讲义"

    renamed = practice_jobs.rename_practice_job(first["job_id"], "期末热力学")

    assert renamed["updated_jobs"] == 2
    assert practice_jobs.load_practice_job(first["job_id"])["title"] == "期末热力学"
    assert practice_jobs.load_practice_job(second["job_id"])["payload"]["task_title"] == "期末热力学"


def test_automatic_material_title_removes_mode_suffix_and_filename_separators(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")

    created = practice_jobs.create_practice_job(
        "analyze",
        {
            "source_mode": "exam",
            "practice_batch_id": "friendly-title",
            "source_files": [{"name": "跨年组合_高分子物理_按题生题.docx"}],
        },
    )

    assert created["title"] == "跨年组合 · 高分子物理"


def test_explicit_user_task_title_is_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")

    created = practice_jobs.create_practice_job(
        "analyze",
        {
            "source_mode": "exam",
            "task_title": "我的_自定义任务名",
            "source_files": [{"name": "跨年组合_高分子物理_按题生题.docx"}],
        },
    )

    assert created["title"] == "我的_自定义任务名"


def test_practice_task_title_uses_text_summary_when_no_file_name_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")

    created = practice_jobs.create_practice_job(
        "analyze",
        {"source_mode": "exam", "question_text": "在恒温条件下，分析该反应达到平衡时的组成变化，并说明原因。"},
    )

    assert created["title"].startswith("在恒温条件下")
    assert created["title"] != "未命名材料"


def test_practice_task_title_ignores_generated_pasted_screenshot_name(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")

    created = practice_jobs.create_practice_job(
        "analyze",
        {
            "source_mode": "exam",
            "source_files": [{"name": "粘贴截图-2026-08-21T14-21-51.png"}],
            "count": 6,
            "difficulty": "基础到进阶",
            "provider": "lingsuan_openai",
            "model": "gpt-5.6-terra",
        },
    )

    assert created["title"] == "图像原题 · 6题 · 基础到进阶"
    listed = practice_jobs.list_practice_jobs()
    assert listed[0]["model"] == "gpt-5.6-terra"
    assert "粘贴截图" not in listed[0]["title"]
