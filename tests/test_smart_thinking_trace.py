import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from app import llm_client, provider_control, runtime_monitor, smart_gemini_router
from app.settings import ProviderConfig


def _config(name="lingsuan_google", model="gemini-3.8-flash-medium", minimum="medium"):
    return ProviderConfig(
        name=name, type="openai_compatible", base_url="https://example.invalid/v1",
        api_key="test-only", default_model=model, model_options=(model,),
        allow_custom_model=False, model_hint="", temperature=0.2, max_tokens=128,
        model_profiles={model: {"thinking_minimum": minimum}},
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "calls.jsonl")
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", tmp_path / "events.jsonl")
    monkeypatch.setattr(runtime_monitor, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(provider_control, "runtime_route_guard", lambda **kw: nullcontext())
    monkeypatch.setattr(provider_control, "record_provider_observation", lambda **kw: None)
    monkeypatch.setattr(llm_client, "record_model_diagnostic", lambda *args, **kw: None)
    monkeypatch.setattr(llm_client, "model_request_slot", lambda *args, **kw: nullcontext())
    monkeypatch.setattr(llm_client, "_shadow_lingsuan_result", lambda config, messages, result, *args, **kw: result)
    return tmp_path


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"ok\\":true}"}}]}'


@pytest.mark.parametrize("task_id", ["exam", "generation_question", "generation_knowledge"])
@pytest.mark.parametrize("method", ["chat_text", "chat_json", "chat_json_object", "create_tool_response"])
@pytest.mark.parametrize("selected,actual,reason", [
    ("low", "medium", "model_minimum"),
    ("disabled", "medium", "model_minimum"),
    ("high", "high", "unchanged"),
    ("auto", "provider_default", "provider_default"),
    (None, "provider_default", "selection_unrecorded"),
])
def test_smart_trace_matches_wire_and_persists(isolated, monkeypatch, task_id, method, selected, actual, reason):
    candidate = _config()
    monkeypatch.setattr(smart_gemini_router, "execute_smart_route", lambda capability, invoke, **_kwargs: invoke(candidate))
    client = llm_client.SmartGeminiClient(replace(candidate, name="gemini_smart_router"))
    payloads = []

    def send(request, timeout):
        payloads.append(json.loads(request.data))
        active = runtime_monitor.model_call_route_summary(task_id)["model_route_timeline"][-1]
        assert active["outcome"] == "running"
        assert active["thinking_trace"]["actual_requested"] == actual
        return Response()

    client._urlopen = send
    kwargs = {"thinking": selected}
    if method == "create_tool_response":
        kwargs["tools"] = []
        kwargs["max_tokens"] = 128
    with runtime_monitor.model_call_context(task_id=task_id, stage="answer_generation", active_item="q1"):
        getattr(client, method)([{"role": "user", "content": "test"}], **kwargs)
    assert len(payloads) == 1
    assert payloads[0].get("reasoning_effort", "provider_default") == actual
    assert "thinking_trace" not in payloads[0]
    trace = runtime_monitor.model_call_route_summary(task_id)["model_route_timeline"][-1]["thinking_trace"]
    assert trace == {"selected": selected or "unknown", "actual_requested": actual, "minimum": "medium", "reason": reason}
    events = [json.loads(line) for line in (isolated / "events.jsonl").read_text().splitlines()]
    assert all(row["thinking_trace"] == trace for row in events)
    assert runtime_monitor._smart_thinking_trace(payloads[0]) == {}


def test_cloudflare_final_route_replaces_virtual_route_in_ledger():
    record = {"provider": "gemini_smart_router", "model": "gemini-smart-router"}

    runtime_monitor.record_model_call_usage(record, {
        "model": "gemini-d",
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "_smart_route": {
            "provider": "wawapi_google",
            "model": "gemini-d",
            "attempts": [
                {"provider": "lingsuan_google", "model": "gemini-a", "status": 429},
                {"provider": "wawapi_google", "model": "gemini-d", "status": "succeeded"},
            ],
        },
    })

    assert record["provider"] == "wawapi_google"
    assert record["model"] == "gemini-d"
    assert len(record["smart_route"]["attempts"]) == 2


def test_prebuilt_payload_is_not_misrepresented_as_adjusted(isolated, monkeypatch):
    candidate = _config()
    monkeypatch.setattr(smart_gemini_router, "execute_smart_route", lambda capability, invoke, **_kwargs: invoke(candidate))
    client = llm_client.SmartGeminiClient(replace(candidate, name="gemini_smart_router"))
    client._urlopen = lambda request, timeout: Response()
    with runtime_monitor.model_call_context(task_id="raw", stage="answer_generation", active_item="q1"):
        client._post_json("unused", {"messages": [], "reasoning_effort": "low"}, timeout=10)
    trace = runtime_monitor.model_call_route_summary("raw")["model_route_timeline"][-1]["thinking_trace"]
    assert trace["selected"] == "unknown"
    assert trace["actual_requested"] == "low"
    assert trace["reason"] == "selection_unrecorded"


def test_legacy_and_frontend_depth_wording():
    summary = runtime_monitor._model_call_route_summary_from_rows([
        {"provider": "lingsuan_google", "model": "gemini-3.8-flash-medium", "outcome": "succeeded"},
    ])
    assert summary["model_route_timeline"][0]["thinking_trace"] == {}
    source = (Path(__file__).parents[1] / "web/app.js").read_text()
    labels = source[source.index("const THINKING_MODE_LABELS ="):source.index("const THINKING_MODE_ORDER =")]
    functions = source[source.index("function smartGeminiThinkingText("):source.index("function completedGenerationTaskMessage(")]
    script = labels + functions + '''
const assert = require('node:assert/strict');
const taskUsesSmartGemini = () => true;
const displayProviderName = value => value;
const escapeHtml = value => String(value).replaceAll('<', '&lt;');
const formatDuration = String;
const formatTaskTimestamp = String;
assert.match(smartGeminiThinkingText({}), /未记录.*历史记录/);
assert.match(smartGeminiThinkingText({thinking_trace:{selected:'low',actual_requested:'medium',minimum:'medium',reason:'model_minimum'}}), /用户选择：低.*实际请求深度：中.*最低档位/);
assert.match(smartGeminiThinkingText({thinking_trace:{selected:'auto',actual_requested:'provider_default',reason:'provider_default'}}), /未发送深度参数/);
const html = smartGeminiRouteSummaryHtml({model_route_timeline:[
  {provider:'wawapi_google',model:'gemini-3.8-flash',outcome:'timeout',thinking_trace:{selected:'low',actual_requested:'low',reason:'unchanged'}},
  {provider:'lingsuan_google',model:'gemini-3.8-flash-medium',outcome:'running',thinking_trace:{selected:'low',actual_requested:'medium',minimum:'medium',reason:'model_minimum'}}
]});
assert.match(html, /正在使用/);
assert.match(html, /最低档位/);
assert.match(html, /保持所选档位/);
assert.match(html, /不代表内部思考程度已验证/);
'''
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


def test_nested_context_resets_on_failure():
    with runtime_monitor.smart_thinking_context(selected="low", minimum="medium"):
        with pytest.raises(ValueError), runtime_monitor.smart_thinking_context(selected="high", minimum="minimal"):
            raise ValueError("test")
        assert runtime_monitor._smart_thinking_trace({"reasoning_effort": "medium"})["selected"] == "low"
    assert runtime_monitor._smart_thinking_trace({}) == {}


def test_parallel_tasks_do_not_share_selection():
    barrier = threading.Barrier(2)

    def observe(selected):
        with runtime_monitor.smart_thinking_context(selected=selected, minimum="medium"):
            barrier.wait(timeout=5)
            return runtime_monitor._smart_thinking_trace({"reasoning_effort": "high"})["selected"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(observe, ["low", "high"])) == ["low", "high"]


@pytest.mark.parametrize("kind", ["exam", "practice", "knowledge"])
def test_task_read_models_preserve_thinking_trace(kind):
    from app.task_read_model import build_exam_run, build_practice_runs

    timeline = [{"provider": "lingsuan_google", "model": "gemini-3.8-flash-medium",
                 "thinking_trace": {"selected": "low", "actual_requested": "medium", "reason": "model_minimum"}}]
    record = {"task_id": "trace-projection", "status": "running", "current_stage": "answer_generation",
              "provider": "gemini_smart_router", "model_route_timeline": timeline}
    if kind == "exam":
        assert build_exam_run(record)["model_route_timeline"] == timeline
    else:
        runs = build_practice_runs([], [{**record, "kind": kind, "task_kind": kind}])
        assert runs[0]["model_route_timeline"] == timeline
        active = build_practice_runs([{**record, "job_id": "trace-job", "operation": "generate_from_plan", "task_kind": kind}], [])
        assert active[0]["model_route_timeline"] == timeline
