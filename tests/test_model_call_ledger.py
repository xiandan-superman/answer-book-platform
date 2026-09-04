import json

import pytest

from app import runtime_monitor
from app.concurrency import ModelRequestAborted, run_limited_concurrent


@pytest.fixture(autouse=True)
def _isolate_execution_event_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime_monitor,
        "MODEL_EXECUTION_EVENT_LEDGER",
        tmp_path / "model_execution_events.jsonl",
    )


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


def test_execution_event_ledger_records_intent_before_result_without_content(
    tmp_path, monkeypatch
) -> None:
    event_ledger = tmp_path / "execution.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", event_ledger)
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "calls.jsonl")
    request_payload = {
        "model": "model-a",
        "messages": [{"role": "user", "content": "不得写入事件账本的正文"}],
    }

    with runtime_monitor.model_call_context(
        task_id="task-shadow",
        stage="answer_generation",
        operation="draft",
    ):
        with runtime_monitor.track_model_call(
            provider="provider-a",
            model="model-a",
            purpose="chat_json",
            timeout=60,
            request_payload=request_payload,
            protocol="chat_completions",
            endpoint="https://example.invalid/v1/chat/completions?api_key=secret",
        ) as record:
            runtime_monitor.record_model_call_usage(
                record,
                {"id": "response-a", "usage": {"prompt_tokens": 20, "completion_tokens": 5}},
            )

    rows = [json.loads(line) for line in event_ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["event_type"] for row in rows] == ["invocation.intent", "invocation.result"]
    assert rows[0]["invocation_id"] == rows[1]["invocation_id"] == record["invocation_id"]
    assert rows[0]["run_id"]
    route = rows[0]["route_decision"]
    assert route["selection"]["provider"] == "provider-a"
    assert route["selection"]["model"] == "model-a"
    assert route["selection"]["required_capabilities"] == ["text_generation"]
    assert route["transport"]["protocol"] == "chat_completions"
    assert route["transport"]["endpoint_path"] == "/v1/chat/completions"
    assert route["authority"] == "shadow"
    assert route["policy_expectation"]["silent_model_switch_allowed"] is False
    assert route["policy_enforced_by_snapshot"] is False
    assert rows[0]["request_summary"]["payload_fingerprint_sha256"]
    assert rows[1]["usage"]["response_id"] == "response-a"
    assert "不得写入事件账本的正文" not in event_ledger.read_text(encoding="utf-8")
    assert "api_key" not in event_ledger.read_text(encoding="utf-8")


def test_implicit_execution_run_id_does_not_enable_existing_business_budget(
    tmp_path, monkeypatch
) -> None:
    event_ledger = tmp_path / "implicit-run.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", event_ledger)
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "calls.jsonl")
    runtime_monitor._RUN_MODEL_BUDGETS.clear()

    with runtime_monitor.model_call_context(task_id="legacy-entry-without-run-id"):
        with runtime_monitor.track_model_call(
            provider="provider-a",
            model="model-a",
            purpose="chat_json",
            timeout=60,
            request_payload={"model": "model-a"},
        ):
            pass

    intent = json.loads(event_ledger.read_text(encoding="utf-8").splitlines()[0])
    assert intent["run_id"]
    assert not any(key[0] == "legacy-entry-without-run-id" for key in runtime_monitor._RUN_MODEL_BUDGETS)


def test_execution_intent_write_failure_stops_call_before_body(tmp_path, monkeypatch) -> None:
    blocked_path = tmp_path / "blocked-ledger"
    blocked_path.mkdir()
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", blocked_path)
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "calls.jsonl")
    monkeypatch.setenv("MODEL_EXECUTION_LEDGER_WRITE_ATTEMPTS", "1")
    entered = False

    with pytest.raises(runtime_monitor.ModelExecutionLedgerError, match="无法持久化"):
        with runtime_monitor.model_call_context(task_id="task-blocked", run_id="run-blocked"):
            with runtime_monitor.track_model_call(
                provider="provider-a",
                model="model-a",
                purpose="chat_json",
                timeout=60,
                request_payload={"model": "model-a"},
                protocol="chat_completions",
                endpoint="https://example.invalid/chat/completions",
            ):
                entered = True

    assert entered is False
    assert runtime_monitor._RUN_MODEL_BUDGETS[("task-blocked", "run-blocked")]["call_count"] == 0
    assert not (tmp_path / "calls.jsonl").exists()


def test_execution_result_write_failure_discards_returned_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "calls.jsonl")
    calls = 0

    def fail_second_event(event_type, record, **payload):
        nonlocal calls
        calls += 1
        if event_type == "invocation.result":
            raise runtime_monitor.ModelExecutionLedgerError("result ledger unavailable")

    monkeypatch.setattr(runtime_monitor, "_append_model_execution_event", fail_second_event)

    with pytest.raises(runtime_monitor.ModelExecutionLedgerError, match="result ledger unavailable"):
        with runtime_monitor.track_model_call(
            provider="provider-a",
            model="model-a",
            purpose="chat_json",
            timeout=60,
            request_payload={"model": "model-a"},
            protocol="chat_completions",
            endpoint="https://example.invalid/chat/completions",
        ):
            pass

    assert calls == 2
    final = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert final["provider_outcome"] == "succeeded"
    assert final["outcome"] == "execution_ledger_failed"
    assert final["billable_disposition"] == "discarded_after_provider_return"
    assert final["result_adoption"] == "discarded"


def test_cancelled_paid_result_is_recorded_as_discarded(tmp_path, monkeypatch) -> None:
    event_ledger = tmp_path / "cancelled-execution.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", event_ledger)
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "cancelled-calls.jsonl")

    with pytest.raises(ModelRequestAborted, match="cancelled after response"):
        with runtime_monitor.track_model_call(
            provider="provider-a",
            model="model-a",
            purpose="chat_json",
            timeout=60,
            request_payload={"model": "model-a"},
            protocol="chat_completions",
            endpoint="https://example.invalid/chat/completions",
        ):
            raise ModelRequestAborted("cancelled after response")

    result = json.loads(event_ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert result["event_type"] == "invocation.result"
    assert result["outcome"] == "cancelled"
    assert result["billable_disposition"] == "cancelled_discarded"


def test_retry_observation_is_durable_sanitized_and_does_not_change_budget_policy(
    tmp_path, monkeypatch
) -> None:
    event_ledger = tmp_path / "retry-events.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", event_ledger)
    error = RuntimeError('Provider HTTP 429: {"request_id":"secret-retry-id"}')
    vars(error).update(
        {
            "status_code": 429,
            "retry_after_seconds": 12,
            "model_invocation_id": "invocation-source",
            "model_call_id": "call-source",
        }
    )

    with runtime_monitor.model_call_context(
        task_id="retry-task",
        run_id="retry-run",
        stage="generating",
        operation="question-batch",
    ):
        observation = runtime_monitor.record_model_retry_scheduled(
            error,
            category="transport_retry",
            retry_number=1,
            max_attempts=2,
            delay_seconds=5.125,
            provider="provider-a",
            model="model-a",
            from_protocol="responses",
            to_protocol="responses",
            budget_scope="practice_generation_retry_budget",
            budget_charged=False,
        )
        runtime_monitor.record_model_retry_started(observation)

    content = event_ledger.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in content.splitlines()]
    assert [row["event_type"] for row in rows] == ["retry.scheduled", "retry.started"]
    assert rows[0]["retry_id"] == rows[1]["retry_id"]
    assert rows[0]["invocation_id"] == "invocation-source"
    assert rows[0]["failure"] == {
        "kind": "provider_rate_limit",
        "status_code": 429,
        "transport_phase": "",
        "retryable_by_provider_classifier": True,
        "requires_configuration": False,
    }
    assert rows[0]["delay"] == {
        "seconds": 5.125,
        "provider_retry_after_seconds": 12.0,
    }
    assert rows[0]["budget_observation"]["charged"] is False
    assert rows[0]["budget_observation"]["policy_changed"] is False
    assert rows[0]["authority"] == "observation_only"
    assert rows[0]["behavior_changed"] is False
    assert rows[1]["provider"] == "provider-a" and rows[1]["model"] == "model-a"
    assert "secret-retry-id" not in content


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


def test_run_level_model_call_budget_uses_confirmed_task_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "dynamic-budget.jsonl")
    monkeypatch.delenv("QUALITY_MAX_MODEL_CALLS_PER_RUN", raising=False)
    monkeypatch.delenv("QUALITY_MODEL_CALL_HEADROOM_PERCENT", raising=False)
    runtime_monitor._RUN_MODEL_BUDGETS.clear()

    with runtime_monitor.model_call_context(task_id="dynamic-task", run_id="dynamic-run"):
        runtime_monitor.configure_model_call_task_shape(
            question_count=33,
            task_kind="exam",
            textbook_evidence_enabled=True,
        )
        with runtime_monitor.track_model_call(provider="p", model="m", purpose="chat", timeout=1):
            pass

    state = runtime_monitor._RUN_MODEL_BUDGETS[("dynamic-task", "dynamic-run")]
    assert state["budget"].estimated_model_calls_per_run == 122
    assert state["budget"].max_model_calls_per_run == 220


def test_provider_circuit_allows_one_recovery_probe_and_resets_after_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "circuit.jsonl")
    monkeypatch.setenv("PRACTICE_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "0")
    runtime_monitor._RUN_MODEL_BUDGETS.clear()

    with runtime_monitor.model_call_context(task_id="circuit-task", run_id="circuit-run"):
        for _ in range(3):
            with pytest.raises(RuntimeError, match="503"):
                with runtime_monitor.track_model_call(provider="lingsuan_google", model="gemini", purpose="chat", timeout=10):
                    raise RuntimeError("Provider HTTP 503: temporary failure")
        with runtime_monitor.track_model_call(provider="lingsuan_google", model="gemini", purpose="probe", timeout=10) as probe:
            assert probe["circuit_probe"] is True
        with runtime_monitor.track_model_call(provider="lingsuan_google", model="gemini", purpose="next", timeout=10) as next_call:
            assert next_call["circuit_probe"] is False

    state = runtime_monitor._RUN_MODEL_BUDGETS[("circuit-task", "circuit-run")]
    route_key = "lingsuan_google|gemini|default"
    assert state["provider_failures"][route_key] == 0
    assert route_key not in state["provider_circuits"]


def test_provider_circuit_isolated_by_provider_model_and_protocol(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "route-circuit.jsonl")
    monkeypatch.setenv("PRACTICE_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "30")
    runtime_monitor._RUN_MODEL_BUDGETS.clear()

    with runtime_monitor.model_call_context(task_id="route-task", run_id="route-run"):
        for _ in range(3):
            with pytest.raises(RuntimeError, match="503"):
                with runtime_monitor.track_model_call(
                    provider="lingsuan_google",
                    model="gemini-a",
                    protocol="responses",
                    purpose="chat",
                    timeout=10,
                ):
                    raise RuntimeError("Provider HTTP 503: temporary failure")
        with runtime_monitor.track_model_call(
            provider="lingsuan_google",
            model="gemini-b",
            protocol="responses",
            purpose="other-model",
            timeout=10,
        ):
            pass
        with runtime_monitor.track_model_call(
            provider="lingsuan_google",
            model="gemini-a",
            protocol="chat",
            purpose="other-protocol",
            timeout=10,
        ):
            pass


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
