import json

from app import runtime_monitor


def test_model_route_summary_uses_latest_successful_real_call(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "model_calls.jsonl"
    rows = [
        {"task_id": "task-1", "provider": "deepseek", "model": "deepseek-chat", "outcome": "failed", "stage": "question_understanding"},
        {"task_id": "task-1", "provider": "openai", "model": "gpt-5.6-terra", "outcome": "succeeded", "stage": "answer_generation"},
        {"task_id": "task-1", "provider": "google", "model": "gemini-3.6-flash", "outcome": "succeeded", "stage": "answer_generation"},
        {"task_id": "task-1", "provider": "google:litellm_shadow", "model": "shadow", "outcome": "succeeded", "purpose": "litellm_shadow"},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", ledger)

    summary = runtime_monitor.model_call_route_summary("task-1")

    assert summary["actual_provider"] == "google"
    assert summary["actual_model"] == "gemini-3.6-flash"
    assert [route["model"] for route in summary["actual_model_routes"]] == [
        "deepseek-chat",
        "gpt-5.6-terra",
        "gemini-3.6-flash",
    ]


def test_model_route_summary_exposes_switch_timeline_and_route_totals(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "model_calls.jsonl"
    rows = [
        {"task_id": "smart-1", "provider": "wawapi_google", "model": "gemini-3.8-flash", "outcome": "timeout", "stage": "answer_generation", "started_at": "2026-09-17T10:00:00+08:00", "finished_at": "2026-09-17T10:00:05+08:00", "elapsed_ms": 5000, "error": "连接超时"},
        {"task_id": "smart-1", "provider": "lingsuan_google", "model": "gemini-3.7-flash-medium", "outcome": "succeeded", "stage": "answer_generation", "started_at": "2026-09-17T10:00:06+08:00", "finished_at": "2026-09-17T10:00:08+08:00", "elapsed_ms": 2000, "prompt_tokens": 10, "completion_tokens": 20},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", ledger)

    summary = runtime_monitor.model_call_route_summary("smart-1")

    assert summary["actual_provider"] == "lingsuan_google"
    assert summary["actual_model_routes"][0]["failed_count"] == 1
    assert summary["actual_model_routes"][1]["prompt_tokens"] == 10
    assert summary["model_route_timeline"][1]["switched"] is True
    assert summary["model_route_timeline"][1]["switch_reason"] == "连接超时"


def test_model_route_summary_maps_final_success_to_each_question(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "model_calls.jsonl"
    rows = [
        {"task_id": "question-routes", "provider": "lingsuan_google", "model": "gemini-a", "outcome": "failed", "stage": "answer_generation", "active_item": "q1,q2"},
        {"task_id": "question-routes", "provider": "wawapi_google", "model": "gemini-d", "outcome": "succeeded", "stage": "answer_generation", "active_item": "q1,q2"},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", ledger)

    summary = runtime_monitor.model_call_route_summary("question-routes")

    assert summary["question_model_routes"] == {
        "q1": {"provider": "wawapi_google", "model": "gemini-d", "stage": "answer_generation"},
        "q2": {"provider": "wawapi_google", "model": "gemini-d", "stage": "answer_generation"},
    }
