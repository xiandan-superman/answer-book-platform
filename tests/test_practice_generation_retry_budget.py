from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import exercise_generation, practice_jobs, practice_store, task_read_model
from app.llm_client import LLMError


def _plan(count: int = 3) -> dict:
    return {
        "source_mode": "knowledge",
        "knowledge_title": "重试预算测试",
        "source_analysis": {"subject": "材料科学"},
        "source_scope": {"mode": "single", "questions": []},
        "blueprint": {
            "training_goal": "验证批量恢复边界",
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
        },
    }


def _exercise(batch_index: int, knowledge_index: int | None = None) -> dict:
    knowledge_index = knowledge_index or batch_index
    return {
        "batch_index": batch_index,
        "question_type": "简答题",
        "difficulty": "进阶",
        "target_skill": f"能力{knowledge_index}",
        "variation_type": f"边界分析{knowledge_index}",
        "stem": f"在互不相同的研究情境{knowledge_index}中，说明给定边界条件如何影响材料状态并写出判断依据。",
        "options": [],
        "knowledge_points": [f"知识点{knowledge_index}"],
        "verification_note": "题干条件完整。",
        "formulas": [],
        "tables": [],
        "figures": [],
    }


def _payload(count: int = 3) -> dict:
    return {
        "source_mode": "knowledge",
        "question_text": "确定性假材料",
        "generation_batch_size": count,
        "generation_concurrency": 1,
        "generation_transport_attempts": 2,
        "generation_retry_backoff_seconds": 0,
        "semantic_review_enabled": False,
        "plan": _plan(count),
    }


def _patch_runtime(monkeypatch, fake_call) -> None:
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


@pytest.mark.parametrize("status_code", [401, 403, 404])
def test_configuration_error_short_circuits_batch_without_split(monkeypatch, status_code: int) -> None:
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise LLMError(f"Provider HTTP {status_code}: request-id=secret", status_code=status_code)

    _patch_runtime(monkeypatch, fake_call)
    result = exercise_generation.generate_practice_from_plan(_payload(3))

    assert calls == 1
    assert result["quality"]["generated_count"] == 0
    assert result["generation"]["status"] == "configuration_blocked"
    assert result["generation"]["retry_budget"]["total_model_calls"] == 1
    assert all(item["generation_error"]["retryable"] is False for item in result["exercises"])
    assert all(item["generation_error"]["requires_configuration"] is True for item in result["exercises"])
    assert all("request-id" not in item["generation_error"]["message"].lower() for item in result["exercises"])


def test_same_transport_signature_exhausts_one_item_without_retrying_every_sibling(monkeypatch) -> None:
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise LLMError("Provider HTTP 524: upstream timeout", status_code=524)

    _patch_runtime(monkeypatch, fake_call)
    result = exercise_generation.generate_practice_from_plan(_payload(5))

    assert calls == 4  # two batch attempts plus the first item's two remaining opportunities
    assert result["quality"]["generated_count"] == 0
    assert result["generation"]["retry_budget"]["total_model_calls"] == 4
    assert result["exercises"][0]["generation_error"]["code"] == "provider_http_524"
    assert all(item["generation_error"]["code"] == "generation_retry_circuit_open" for item in result["exercises"][1:])


def test_probe_success_continues_remaining_items_within_shared_limit(monkeypatch) -> None:
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise LLMError("Provider HTTP 524: upstream timeout", status_code=524)
        item_index = calls - 2
        return {"exercises": [_exercise(1, item_index)]}

    _patch_runtime(monkeypatch, fake_call)
    result = exercise_generation.generate_practice_from_plan(_payload(5))

    assert calls == 7
    assert result["quality"]["generated_count"] == 5
    assert result["generation"]["retry_budget"]["total_model_calls"] == 7
    assert all(item["generation_status"] == "completed" for item in result["exercises"])


def test_different_probe_signature_can_recover_within_the_items_independent_budget(monkeypatch) -> None:
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise LLMError("Provider HTTP 524: timeout", status_code=524)
        if calls == 3:
            raise LLMError("Provider HTTP 503: unavailable", status_code=503)
        return {"exercises": [_exercise(1, 1 if calls == 4 else calls - 3)]}

    _patch_runtime(monkeypatch, fake_call)
    result = exercise_generation.generate_practice_from_plan(_payload(3))

    assert calls == 6
    assert [item["generation_status"] for item in result["exercises"]] == ["completed", "completed", "completed"]


def test_persistent_empty_response_gives_every_question_four_independent_chances(monkeypatch) -> None:
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"exercises": []}

    _patch_runtime(monkeypatch, fake_call)
    result = exercise_generation.generate_practice_from_plan(_payload(4))

    assert calls == 13  # one shared initial batch plus three independent attempts per question
    assert result["quality"]["generated_count"] == 0
    assert result["exercises"][0]["generation_error"]["signature"] == "generation_response_invalid:empty"
    assert all(item["generation_error"]["code"] == "generation_response_invalid" for item in result["exercises"])
    per_item = {
        key: row
        for key, row in result["generation"]["retry_budget"]["batches"].items()
        if "|" not in key
    }
    assert len(per_item) == 4
    assert all(row["calls_used"] == 4 for row in per_item.values())


def test_fill_in_repair_must_pass_structure_before_it_counts_as_repaired(monkeypatch) -> None:
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        item = _exercise(1)
        item["question_type"] = "填空题"
        item["stem"] = (
            "根据给定边界判断材料的状态变化并说明其原因。"
            if calls < 3
            else "根据给定边界判断材料的状态变化，其变化量为____。"
        )
        return {"exercises": [item]}

    _patch_runtime(monkeypatch, fake_call)
    payload = _payload(1)
    payload["plan"]["blueprint"]["exercise_plan"][0]["question_type"] = "填空题"

    result = exercise_generation.generate_practice_from_plan(payload)

    assert calls == 3
    assert result["exercises"][0]["generation_status"] == "completed"
    assert "____" in result["exercises"][0]["stem"]
    assert result["generation"]["batch_diagnostics"][0]["content_gate_retries"][0]["status"] == "repaired"


@pytest.mark.parametrize(
    "blank",
    [
        r"$C=\underline{\hspace{1.2cm}}$",
        r"$C=\underbrace{\hspace{1.2cm}}$",
    ],
)
def test_latex_fill_in_slot_is_accepted_without_regeneration(monkeypatch, blank: str) -> None:
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        item = _exercise(1)
        item["question_type"] = "填空题"
        item["stem"] = f"根据给定条件计算独立组分数并填空：{blank}。"
        return {"exercises": [item]}

    _patch_runtime(monkeypatch, fake_call)
    payload = _payload(1)
    payload["plan"]["blueprint"]["exercise_plan"][0]["question_type"] = "填空题"

    result = exercise_generation.generate_practice_from_plan(payload)

    assert calls == 1
    assert result["exercises"][0]["generation_status"] == "completed"
    assert result["generation"]["batch_diagnostics"][0].get("content_gate_retries") is None


def test_partial_success_survives_configuration_block_and_continuation_only_fills_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_store, "PRACTICE_HISTORY_DIR", tmp_path / "history")
    calls = 0

    def partial_then_401(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"exercises": [_exercise(1)]}
        raise LLMError("Provider HTTP 403: forbidden", status_code=403)

    _patch_runtime(monkeypatch, partial_then_401)
    initial_payload = _payload(3)
    partial = exercise_generation.generate_practice_from_plan(initial_payload)
    saved = practice_store.save_practice_record(partial, request=initial_payload)
    history_id = saved["history_id"]
    listed = practice_store.list_practice_records()[0]

    assert calls == 2
    assert listed["status"] == "completed_with_issues"
    assert (listed["generated_count"], listed["total_count"], listed["unfinished_count"]) == (1, 3, 2)
    assert listed["configuration_blocked"] is True

    continuation = practice_store.build_practice_continuation_payload(history_id)
    resumed_calls = 0

    def finish_missing(*_args, **_kwargs):
        nonlocal resumed_calls
        resumed_calls += 1
        return {"exercises": [_exercise(1, 2), _exercise(2, 3)]}

    _patch_runtime(monkeypatch, finish_missing)
    completed = exercise_generation.generate_practice_from_plan(continuation)

    assert resumed_calls == 1
    assert completed["history_id"] == history_id
    assert completed["quality"]["generated_count"] == 3
    assert completed["exercises"][0]["stem"] == partial["exercises"][0]["stem"]
    assert [item["plan_item_id"] for item in completed["exercises"]] == ["plan_item_01", "plan_item_02", "plan_item_03"]


def test_retry_after_is_honoured_with_cap_without_real_wait(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMError("Provider HTTP 429: slow down", status_code=429, retry_after_seconds=20)
        return {"exercises": []}

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    monkeypatch.setattr(exercise_generation.time, "sleep", sleeps.append)
    result = exercise_generation._call_practice_json_with_transport_retry(
        object(), [], model="fake", temperature=0, thinking=None, timeout_seconds=1,
        attempts=2, backoff_seconds=0, max_retry_after_seconds=5,
    )

    assert result == {"exercises": []}
    assert calls == 2
    assert len(sleeps) == 1
    assert 5 <= sleeps[0] <= 5.25


def test_preparatory_stages_clamp_thinking_and_output_budget(monkeypatch) -> None:
    assert exercise_generation._practice_stage_thinking({"thinking": "high"}, "analyze") == "low"
    assert exercise_generation._practice_stage_thinking({"thinking": "high"}, "plan") == "medium"
    assert exercise_generation._practice_stage_thinking({"thinking": "disabled"}, "plan") == "disabled"
    monkeypatch.setenv("PRACTICE_ANALYZE_MAX_OUTPUT_TOKENS", "999999")
    assert exercise_generation._practice_stage_output_tokens("analyze", 16000) == exercise_generation.DEFAULT_MODEL_MAX_TOKENS


def test_transport_retry_forwards_stage_output_budget(monkeypatch) -> None:
    captured = {}

    def fake_call(*_args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    result = exercise_generation._call_practice_json_with_transport_retry(
        object(), [], model="fake", temperature=0, thinking="low",
        timeout_seconds=30, max_tokens=12000, attempts=1,
    )
    assert result == {"ok": True}
    assert captured["max_tokens"] == 12000


def test_transport_retry_switches_to_fallback_protocol_after_stream_failure(monkeypatch) -> None:
    primary = object()
    fallback = object()
    clients = []

    def fake_call(client, *_args, **_kwargs):
        clients.append(client)
        if len(clients) == 1:
            raise LLMError("Responses stream exceeded total wall-clock deadline", status_code=524)
        return {"ok": True}

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    monkeypatch.setattr(exercise_generation, "_chat_fallback_client", lambda _client: fallback)
    monkeypatch.setattr(exercise_generation.time, "sleep", lambda _seconds: None)
    attempts = []
    result = exercise_generation._call_practice_json_with_transport_retry(
        primary, [], model="fake", temperature=0, thinking="low", timeout_seconds=30,
        attempts=2, backoff_seconds=0, attempt_log=attempts,
    )
    assert result == {"ok": True}
    assert clients == [primary, fallback]
    assert attempts[-1]["protocol_fallback"] is True


def test_transport_retry_reconnects_responses_before_any_protocol_fallback(monkeypatch) -> None:
    primary = object()
    fallback = object()
    clients = []

    def fake_call(client, *_args, **_kwargs):
        clients.append(client)
        if len(clients) == 1:
            raise LLMError("SSL: EOF occurred in violation of protocol")
        return {"ok": True}

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    monkeypatch.setattr(exercise_generation, "_chat_fallback_client", lambda _client: fallback)
    monkeypatch.setattr(exercise_generation.time, "sleep", lambda _seconds: None)
    attempts = []

    result = exercise_generation._call_practice_json_with_transport_retry(
        primary, [], model="fake", temperature=0, thinking="low", timeout_seconds=30,
        attempts=2, backoff_seconds=0, attempt_log=attempts,
        same_protocol_retries=1, allow_chat_fallback=False,
    )

    assert result == {"ok": True}
    assert clients == [primary, primary]
    assert attempts[0]["error_code"] == "provider_tls_connection_failed"
    assert attempts[-1]["same_protocol_retry"] is True
    assert attempts[-1]["protocol_fallback"] is False


def test_invalid_json_uses_four_progressively_stricter_gemini_attempts(monkeypatch) -> None:
    primary = SimpleNamespace(config=SimpleNamespace(name="lingsuan_google"))
    fallback = SimpleNamespace(config=SimpleNamespace(name="lingsuan_google"))
    observed = []

    def fake_call(client, messages, **kwargs):
        observed.append({"client": client, "messages": messages, "thinking": kwargs["thinking"]})
        if len(observed) < 4:
            raise LLMError("专项练习 JSON 解析失败：模型返回了思考摘要。")
        return {"exercises": [_exercise(1)]}

    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)
    monkeypatch.setattr(exercise_generation, "_chat_fallback_client", lambda _client: fallback)
    monkeypatch.setattr(exercise_generation.time, "sleep", lambda _seconds: None)
    attempts = []

    result = exercise_generation._call_practice_json_with_transport_retry(
        primary,
        [{"role": "user", "content": "请生成 JSON。"}],
        model="gemini-3.6-flash",
        temperature=0,
        thinking=None,
        timeout_seconds=480,
        attempts=4,
        backoff_seconds=0,
        attempt_log=attempts,
    )

    assert result["exercises"]
    assert [item["thinking"] for item in observed] == [None, "minimal", "minimal", "minimal"]
    assert [item["client"] for item in observed] == [primary, primary, primary, fallback]
    assert [item["strict_json_contract"] for item in attempts] == [False, False, True, True]


def test_split_question_budgets_inherit_batch_calls_without_sharing_remaining_attempts() -> None:
    coordinator = exercise_generation._GenerationRetryCoordinator({})
    parent = "plan_item_01|plan_item_02"
    for _ in range(2):
        coordinator.reserve(parent, limit=4, phase="initial_batch")
        coordinator.record(parent, phase="initial_batch", detail={"code": "provider_http_524", "signature": "provider_http_524"})

    first = coordinator.prepare_item_budget(parent, "plan_item_01")
    second = coordinator.prepare_item_budget(parent, "plan_item_02")
    coordinator.reserve(first, limit=4, phase="single_item")
    coordinator.record(first, phase="single_item", detail=None)
    coordinator.reserve(first, limit=4, phase="single_item")
    coordinator.record(first, phase="single_item", detail=None)

    assert coordinator.remaining(first) == 0
    assert coordinator.remaining(second) == 2
    assert coordinator.summary()["total_model_calls"] == 4


def test_retry_budget_persists_across_same_job_recovery(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    job = practice_jobs.create_practice_job("generate_from_plan", _payload(3))
    practice_jobs.update_practice_job(job["job_id"], status="running")
    payload = {**_payload(3), "_job_id": job["job_id"]}
    first = exercise_generation._GenerationRetryCoordinator(payload)
    first.reserve("plan_item_01|plan_item_02|plan_item_03", limit=1, phase="initial_batch")
    first.record("plan_item_01|plan_item_02|plan_item_03", phase="initial_batch", detail={
        "code": "provider_http_524", "signature": "provider_http_524", "message": "超时",
        "requires_configuration": False,
    })

    recovered = exercise_generation._GenerationRetryCoordinator(payload)

    assert recovered.summary()["total_model_calls"] == 1
    assert recovered.batch_stop("plan_item_01|plan_item_02|plan_item_03", limit=1)["code"] == "generation_retry_budget_exhausted"


def test_task_contract_distinguishes_zero_success_config_failure_from_partial_review() -> None:
    generation = {
        "status": "configuration_blocked",
        "configuration_blocked": True,
        "generated_count": 0,
        "failed_count": 5,
        "total_count": 5,
        "batch_errors": [{
            "code": "provider_configuration_blocked",
            "message": "模型服务认证失败，API Key 可能无效或已过期。",
            "requires_configuration": True,
        }],
    }
    run = task_read_model._practice_history_run({
        "history_id": "practice_config_blocked",
        "task_kind": "practice",
        "title": "配置阻断测试",
        "status": "failed",
        "question_count": 0,
        "generated_count": 0,
        "total_count": 5,
        "unfinished_count": 5,
        "configuration_blocked": True,
        "generation": generation,
        "quality": {"generated_count": 0, "failed_count": 5, "total_count": 5, "blocking_issues": ["生成未完成"]},
    })

    assert run["status"] == "failed"
    assert run["configuration_blocked"] is True
    assert run["capabilities"]["view_result"] is True
    assert run["capabilities"]["retry"] is True
    assert run["capabilities"]["reuse"] is False
    assert run["quality_presentation"]["label"] == "生成未完成"
    assert "request" not in run["error"].lower()


def test_frontend_exposes_config_then_continue_and_real_success_counts() -> None:
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")

    assert 'data-practice-config' in source
    assert 'data-practice-continue' in source
    assert 'history-continue' in source
    assert '继续未完成项' in source
    assert '共 ${totalCount} 题：已生成 ${generatedCount} 题' in source
