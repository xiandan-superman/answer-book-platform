import json

import pytest

from app import runtime_monitor
from app.concurrency import run_limited_concurrent


def test_public_pipeline_entry_always_sets_task_call_context(monkeypatch) -> None:
    from app import pipeline

    observed: dict[str, str] = {}

    def fake_impl(task_id, options=None, *, run_id=""):
        observed.update(runtime_monitor._MODEL_CALL_CONTEXT.get() or {})
        return {"task_id": task_id, "options": options, "run_id": run_id}

    monkeypatch.setattr(pipeline, "_run_pipeline_impl", fake_impl)

    result = pipeline.run_pipeline("cli-task")

    assert result["task_id"] == "cli-task"
    assert observed["task_id"] == "cli-task"
    assert observed["run_id"] == result["run_id"]
    assert observed["operation"] == "解析任务"


def test_model_call_ledger_records_usage_and_failure_disposition(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "model_calls.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", ledger)
    with runtime_monitor.model_call_context(task_id="task-1", run_id="run-1", stage="answer_generation", operation="draft", active_item="q1"):
        with runtime_monitor.track_model_call(provider="provider-a", model="model-a", purpose="chat_json", timeout=300) as record:
            runtime_monitor.record_model_call_usage(record, {"id": "response-1", "usage": {"prompt_tokens": 120, "completion_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 10}}})
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert row["task_id"] == "task-1" and row["prompt_tokens"] == 120 and row["completion_tokens"] == 30
    assert row["run_id"] == "run-1" and row["active_item"] == "q1"
    assert row["reasoning_tokens"] == 10 and row["outcome"] == "succeeded"
    assert row["billable_disposition"] == "unclassified_success"
    assert "prompt" not in row and "response" not in row


def test_task_context_survives_parallel_model_workers(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "parallel.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", ledger)
    def worker(item: int) -> int:
        with runtime_monitor.track_model_call(provider="p", model="m", purpose=f"item-{item}", timeout=60):
            return item
    with runtime_monitor.model_call_context(task_id="parallel-task", stage="answer_generation"):
        assert run_limited_concurrent([1, 2], worker, max_workers=2) == [1, 2]
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and {row["task_id"] for row in rows} == {"parallel-task"}


def test_run_level_model_call_budget_stops_unbounded_retries(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "budget.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", ledger)
    monkeypatch.setenv("QUALITY_MAX_MODEL_CALLS_PER_RUN", "10")
    runtime_monitor._RUN_MODEL_BUDGETS.clear()

    with runtime_monitor.model_call_context(task_id="budget-task", run_id="budget-run"):
        for _ in range(10):
            with runtime_monitor.track_model_call(provider="p", model="m", purpose="chat", timeout=1):
                pass
        try:
            with runtime_monitor.track_model_call(provider="p", model="m", purpose="chat", timeout=1):
                pass
        except RuntimeError as exc:
            assert "model call budget exhausted" in str(exc)
        else:
            raise AssertionError("run-level model budget did not stop the 11th call")


def test_provider_circuit_allows_one_recovery_probe_and_resets_after_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "circuit.jsonl")
    monkeypatch.setenv("PRACTICE_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "0")
    runtime_monitor._RUN_MODEL_BUDGETS.clear()

    with runtime_monitor.model_call_context(task_id="circuit-task", run_id="circuit-run"):
        for _ in range(3):
            with pytest.raises(RuntimeError, match="temporary failure"):
                with runtime_monitor.track_model_call(provider="lingsuan_google", model="gemini", purpose="chat", timeout=10):
                    raise RuntimeError("temporary failure")
        with runtime_monitor.track_model_call(provider="lingsuan_google", model="gemini", purpose="probe", timeout=10) as probe:
            assert probe["circuit_probe"] is True
        with runtime_monitor.track_model_call(provider="lingsuan_google", model="gemini", purpose="next", timeout=10) as next_call:
            assert next_call["circuit_probe"] is False

    state = runtime_monitor._RUN_MODEL_BUDGETS[("circuit-task", "circuit-run")]
    assert state["provider_failures"]["lingsuan_google"] == 0
    assert "lingsuan_google" not in state["provider_circuits"]


def test_practice_generation_gets_its_own_long_task_wall_clock_budget(monkeypatch) -> None:
    monkeypatch.delenv("QUALITY_MAX_MODEL_WALL_SECONDS_PER_RUN", raising=False)

    generation = runtime_monitor._model_execution_budget({
        "task_id": "generation_20260825_test",
        "stage": "generating",
    })
    regular = runtime_monitor._model_execution_budget({
        "task_id": "regular-task",
        "stage": "answer_generation",
    })

    assert generation.max_model_wall_seconds_per_run == 7200
    assert regular.max_model_wall_seconds_per_run == 1800


def test_explicit_task_wall_clock_override_remains_authoritative(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_MAX_MODEL_WALL_SECONDS_PER_RUN", "2100")

    budget = runtime_monitor._model_execution_budget({
        "task_id": "generation_20260825_test",
        "stage": "generating",
    })

    assert budget.max_model_wall_seconds_per_run == 2100
