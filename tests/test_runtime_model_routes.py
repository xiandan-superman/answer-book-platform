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
