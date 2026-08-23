from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import exercise_generation, practice_jobs, practice_store


def _blueprint(count: int = 3) -> dict:
    return {
        "training_goal": "继续任务幂等测试",
        "generation_strategy": "knowledge_overall",
        "exercise_plan": [
            {
                "plan_item_id": f"plan_item_{index:02d}",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": [f"知识点{index}"],
            }
            for index in range(1, count + 1)
        ],
    }


def _success(index: int, stem: str | None = None) -> dict:
    return {
        "number": index,
        "plan_item_id": f"plan_item_{index:02d}",
        "generation_status": "completed",
        "question_type": "简答题",
        "difficulty": "进阶",
        "stem": stem or f"已成功题目{index}，请说明边界条件改变后材料状态的变化并给出判断依据。",
        "knowledge_points": [f"知识点{index}"],
        "options": [],
        "formulas": [],
        "tables": [],
        "figures": [],
    }


def _failure(index: int) -> dict:
    return {
        "number": index,
        "plan_item_id": f"plan_item_{index:02d}",
        "generation_status": "failed",
        "generation_error": {
            "code": "provider_http_401",
            "message": "模型服务认证失败。",
            "retryable": False,
            "requires_configuration": True,
        },
    }


def _partial_data(*, successful_ids: set[int] | None = None, blueprint: dict | None = None) -> dict:
    successful_ids = successful_ids or {1}
    blueprint = blueprint or _blueprint()
    exercises = [
        _success(index) if index in successful_ids else _failure(index)
        for index in range(1, len(blueprint["exercise_plan"]) + 1)
    ]
    return {
        "source_mode": "knowledge",
        "knowledge_title": "继续幂等测试",
        "blueprint": blueprint,
        "exercises": exercises,
        "generation": {"status": "configuration_blocked", "configuration_blocked": True},
    }


def _request(blueprint: dict | None = None) -> dict:
    blueprint = blueprint or _blueprint()
    return {
        "source_mode": "knowledge",
        "knowledge_title": "继续幂等测试",
        "question_text": "确定性假材料。",
        "plan": {"source_mode": "knowledge", "blueprint": blueprint},
        "practice_batch_id": "original_batch",
        "blueprint_review_enabled": False,
        "semantic_review_enabled": False,
        "generation_batch_size": 3,
        "generation_concurrency": 1,
        "generation_transport_attempts": 1,
        "api_key": "must-not-enter-continuation-key",
    }


def _saved_partial(tmp_path: Path, monkeypatch, *, successful_ids: set[int] | None = None) -> dict:
    monkeypatch.setattr(practice_store, "PRACTICE_HISTORY_DIR", tmp_path / "history")
    return practice_store.save_practice_record(
        _partial_data(successful_ids=successful_ids),
        request=_request(),
    )


def _patch_fake_generation(monkeypatch, fake_call) -> None:
    provider = SimpleNamespace(name="fake", supports_vision=False)
    monkeypatch.setattr(exercise_generation, "_primary_model_runtime", lambda _payload: (provider, "fake-model"))
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    monkeypatch.setattr(exercise_generation, "_batch_variation_issues", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(exercise_generation, "_batch_sibling_variant_issues", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        exercise_generation,
        "_selectively_repair_practice_diversity",
        lambda _result, _payload: ({"attempts": [], "status": "not_required"}, {}),
    )


def test_continuation_key_is_stable_semantic_and_secret_free(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    first = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="attempt-a")
    second = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="attempt-b")

    assert first["continuation_key"] == second["continuation_key"]
    assert first["practice_batch_id"] == second["practice_batch_id"]
    assert first["continuation_attempt_id"] != second["continuation_attempt_id"]
    assert first["continuation_snapshot"]["history_id"] == saved["history_id"]
    assert first["continuation_snapshot"]["history_updated_at"] == saved["updated_at"]
    assert first["continuation_snapshot"]["blueprint_fingerprint"]
    assert first["continuation_snapshot"]["unfinished_plan_item_ids"] == ["plan_item_02", "plan_item_03"]
    assert "must-not-enter-continuation-key" not in json.dumps(first["continuation_snapshot"], ensure_ascii=False)
    assert "attempt-a" not in first["continuation_key"]


def test_same_snapshot_concurrent_requests_atomically_reuse_one_job(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")

    def start(index: int) -> dict:
        payload = practice_store.build_practice_continuation_payload(
            saved["history_id"],
            attempt_id=f"tab-{index}",
        )
        return practice_jobs.create_or_reuse_practice_job("generate_from_plan", payload)

    with ThreadPoolExecutor(max_workers=12) as executor:
        rows = list(executor.map(start, range(24)))

    assert len({row["job_id"] for row in rows}) == 1
    assert sum(row["deduplicated"] is False for row in rows) == 1
    assert len(list((tmp_path / "jobs").glob("generation_*.json"))) == 1


def test_duplicate_queue_delivery_executes_one_effective_continuation(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    first = practice_jobs.create_or_reuse_practice_job(
        "generate_from_plan",
        practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="tab-a"),
    )
    second = practice_jobs.create_or_reuse_practice_job(
        "generate_from_plan",
        practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="tab-b"),
    )
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def worker(_operation, _payload):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return {"result": {"ok": True}, "history_id": saved["history_id"]}

    threads = [
        threading.Thread(target=practice_jobs.run_practice_job, args=(row["job_id"], worker))
        for row in (first, second)
    ]
    threads[0].start()
    assert started.wait(2)
    threads[1].start()
    threads[1].join(timeout=2)
    release.set()
    threads[0].join(timeout=2)

    assert first["job_id"] == second["job_id"]
    assert calls == 1


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_terminal_attempt_replay_is_idempotent_but_explicit_new_attempt_is_allowed(
    tmp_path, monkeypatch, terminal_status: str
) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    original_payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="attempt-old")
    original = practice_jobs.create_or_reuse_practice_job("generate_from_plan", original_payload)
    lost_response_replay = practice_jobs.create_or_reuse_practice_job("generate_from_plan", original_payload)
    assert lost_response_replay["job_id"] == original["job_id"]
    assert lost_response_replay["deduplicated"] is True
    practice_jobs.update_practice_job(original["job_id"], status=terminal_status, current_stage=terminal_status)

    replay = practice_jobs.create_or_reuse_practice_job("generate_from_plan", original_payload)
    new_payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="attempt-new")
    retried = practice_jobs.create_or_reuse_practice_job("generate_from_plan", new_payload)

    assert replay["job_id"] == original["job_id"]
    assert replay["deduplicated"] is True
    assert replay["replayed_terminal"] is True
    assert retried["job_id"] != original["job_id"]
    assert retried["deduplicated"] is False


def test_restart_style_disk_scan_reuses_active_continuation(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    first_payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="before-restart")
    first = practice_jobs.create_or_reuse_practice_job("generate_from_plan", first_payload)
    practice_jobs.update_practice_job(first["job_id"], status="running", queue_task_id="durable-queue-message")
    recovered_records = practice_jobs.recover_practice_jobs(fail_interrupted=False)

    after_restart_payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="after-restart")
    recovered = practice_jobs.create_or_reuse_practice_job("generate_from_plan", after_restart_payload)

    assert [record["job_id"] for record in recovered_records] == [first["job_id"]]
    assert recovered["job_id"] == first["job_id"]
    assert recovered["status"] == "running"
    assert recovered["deduplicated"] is True


def test_generation_preflight_reloads_history_and_calls_only_still_missing_items(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="preflight")
    latest = practice_store.load_practice_record(saved["history_id"])["data"]
    latest["exercises"][1] = _success(2, "其他任务已完成的第二题，必须直接复用。")
    practice_store.save_practice_record(latest, request=_request(), change_reason="other_task_completed")
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        generated = _success(3, "假服务只生成仍缺失的第三题。")
        generated["batch_index"] = 1
        generated.pop("number", None)
        generated.pop("plan_item_id", None)
        return {"exercises": [generated]}

    _patch_fake_generation(monkeypatch, fake_call)
    result = exercise_generation.generate_practice_from_plan(payload)

    assert calls == 1
    assert result["quality"]["generated_count"] == 3
    assert result["exercises"][1]["stem"] == "其他任务已完成的第二题，必须直接复用。"
    assert result["exercises"][2]["stem"] == "假服务只生成仍缺失的第三题。"


def test_competing_commits_preserve_newer_success_and_create_no_noise_revision(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="commit-a")
    base = practice_store.load_practice_record(saved["history_id"])["data"]
    first_result = {
        **base,
        "history_id": saved["history_id"],
        "exercises": [_success(1), _success(2, "先提交的第二题"), _success(3, "先提交的第三题")],
    }
    stale_result = {
        **base,
        "history_id": saved["history_id"],
        "exercises": [_success(1, "旧任务中的第一题"), _success(2, "后到的重复第二题"), _success(3, "后到的重复第三题")],
    }

    first_saved = practice_store.save_practice_continuation_record(first_result, request=payload)
    stale_saved = practice_store.save_practice_continuation_record(stale_result, request=payload)

    assert [item["stem"] for item in stale_saved["data"]["exercises"]] == [
        _success(1)["stem"],
        "先提交的第二题",
        "先提交的第三题",
    ]
    assert len(stale_saved["revisions"]) == len(first_saved["revisions"]) == 1


def test_completed_history_rejects_later_continue_without_creating_empty_job(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="complete")
    complete = {
        **practice_store.load_practice_record(saved["history_id"])["data"],
        "history_id": saved["history_id"],
        "exercises": [_success(1), _success(2), _success(3)],
    }
    practice_store.save_practice_continuation_record(complete, request=payload)

    with pytest.raises(ValueError, match="没有未完成题目"):
        practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="too-late")


def test_blueprint_change_blocks_stale_continuation_commit(tmp_path, monkeypatch) -> None:
    saved = _saved_partial(tmp_path, monkeypatch)
    payload = practice_store.build_practice_continuation_payload(saved["history_id"], attempt_id="stale-blueprint")
    latest = practice_store.load_practice_record(saved["history_id"])["data"]
    latest["blueprint"]["training_goal"] = "用户后来修改的蓝图"
    practice_store.save_practice_record(latest, request=_request(latest["blueprint"]), change_reason="blueprint_edit")

    with pytest.raises(practice_store.PracticeEditConflict, match="蓝图已被修改"):
        practice_store.save_practice_continuation_record(
            {**latest, "history_id": saved["history_id"]},
            request=payload,
        )


def test_frontend_sends_attempt_id_separately_from_server_continuation_key() -> None:
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")

    assert "const continuationAttemptId = newPracticeBatchId();" in source
    assert "JSON.stringify({ continuation_attempt_id: continuationAttemptId })" in source
