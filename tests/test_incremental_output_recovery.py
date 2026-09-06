import threading
from dataclasses import replace
from typing import Any

import pytest

from app.concurrency import run_limited_concurrent
from app.output_checkpoints import load_output_checkpoint, save_output_checkpoint


def test_delivery_reserve_preserves_total_cap_and_task_isolation(tmp_path, monkeypatch):
    from app import runtime_monitor as monitor
    from app.capabilities.quality_budget import QualityExecutionBudget

    monkeypatch.setattr(monitor, "MODEL_CALL_LEDGER", tmp_path / "calls.jsonl")
    monkeypatch.setattr(monitor, "MODEL_EXECUTION_EVENT_LEDGER", tmp_path / "events.jsonl")
    monkeypatch.setattr(monitor, "_RUN_MODEL_BUDGETS", {})
    budget = replace(QualityExecutionBudget(), max_model_calls_per_run=4, delivery_reserve_calls=2)
    monkeypatch.setattr(monitor, "_model_execution_budget", lambda _context: budget)

    def invoke():
        with monitor.track_model_call(provider="test", model="test", purpose="chat", timeout=1):
            pass

    with monitor.model_call_context(task_id="first", run_id="run"):
        invoke()
        invoke()
        with pytest.raises(RuntimeError, match="reserved for delivery repair"):
            invoke()
        with monitor.model_call_context(budget_phase="delivery_repair"):
            invoke()
            invoke()
            with pytest.raises(RuntimeError, match="model call budget exhausted"):
                invoke()
    with monitor.model_call_context(task_id="second", run_id="run"):
        invoke()
    assert monitor._RUN_MODEL_BUDGETS[("first", "run")]["call_count"] == 4
    assert monitor._RUN_MODEL_BUDGETS[("second", "run")]["call_count"] == 1
    with monitor.model_call_context(task_id="interleaved", run_id="run"):
        with monitor.model_call_context(budget_phase="delivery_repair"):
            invoke()
        invoke()
        invoke()
        with pytest.raises(RuntimeError, match="1 calls reserved"):
            invoke()
        with monitor.model_call_context(budget_phase="delivery_repair"):
            invoke()


def test_preflight_never_claims_delivery_and_preserves_failure_responsibility(tmp_path, monkeypatch):
    from app import practice_export
    from app.document_tool import DocumentToolFailure
    from app.unit_preflight import preflight_object

    def fail(_payload):
        raise DocumentToolFailure("renderer_missing", "not installed", "install renderer", responsibility="environment")

    monkeypatch.setattr(practice_export, "validate_practice_export", fail)
    report = preflight_object(tmp_path, object_id="p1", source={}, candidate={}, kind="practice")
    assert report["responsibility"] == "environment"
    assert report["status"] == "blocked"
    assert report["delivery_ready"] is False
    assert report["set_review"] == "pending"
    monkeypatch.setattr(practice_export, "validate_practice_export", lambda _: {"blocking_issues": []})
    recovered = preflight_object(tmp_path, object_id="p1", source={}, candidate={}, kind="practice")
    assert recovered["status"] == "passed"
    assert recovered["delivery_ready"] is False


def test_callback_backpressure_stops_unsubmitted_work():
    started = []
    barrier = threading.Barrier(2)

    def work(value):
        started.append(value)
        barrier.wait(timeout=3)
        return value

    def commit(*_):
        raise RuntimeError("downstream stopped")

    with pytest.raises(RuntimeError, match="downstream stopped"):
        run_limited_concurrent(range(30), work, max_workers=2, on_complete=commit)
    assert sorted(started) == [0, 1]


def test_bounded_pool_keeps_context_and_input_order():
    from contextvars import ContextVar

    owner = ContextVar("test_owner", default="")
    token = owner.set("task")
    try:
        assert run_limited_concurrent(range(20), lambda value: (owner.get(), value), max_workers=3) == [
            ("task", value) for value in range(20)
        ]
    finally:
        owner.reset(token)


def test_checkpoint_requires_current_dependencies_and_asset_bytes(tmp_path):
    asset = tmp_path / "figure.png"
    asset.write_bytes(b"original")
    arguments: dict[str, Any] = dict(stage="generation", object_id="q1", dependencies={"plan": "v1"})
    save_output_checkpoint(tmp_path, **arguments, source={"answer": "draft"},
                           candidate={"image": {"path": str(asset)}}, diagnostics={})
    assert load_output_checkpoint(tmp_path, **arguments)
    assert load_output_checkpoint(tmp_path, **{**arguments, "dependencies": {"plan": "v2"}}) is None
    asset.write_bytes(b"changed")
    assert load_output_checkpoint(tmp_path, **arguments) is None


def test_generation_reuses_committed_objects_and_invalidates_only_changed_input(tmp_path, monkeypatch):
    import app.answer_generation as module
    from app.settings import ProviderConfig

    provider = ProviderConfig(name="test", type="openai_compatible", base_url="https://example.test/v1",
                              api_key="test", default_model="test", model_options=(), allow_custom_model=True,
                              model_hint="", temperature=0.1, max_tokens=24576)
    monkeypatch.setenv("ANSWER_GENERATION_BATCH_ENABLED", "0")
    calls = []

    def generate(_client, _provider, question, _evidence, _model, **_kwargs):
        calls.append(question["question_id"])
        return {
            "schema_version": "answer_book.answer_fragment.v4", "question_id": question["question_id"],
            "answer": "晶格常数描述晶胞几何尺寸。", "formulas": [], "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "晶格常数描述晶胞几何尺寸。"}]}],
        }, []

    monkeypatch.setattr(module, "generate_one_fragment", generate)
    exam = {"items": [{"question_id": key, "question_type": "名词解释", "stem": "晶格常数"} for key in ["a", "b"]]}
    output = tmp_path / "answer_fragments.json"
    module.generate_answer_fragments(exam, [], provider, "test", output, include_textbook_evidence=False)
    output.unlink()  # Simulate loss of collection assembly, not unit checkpoints.
    module.generate_answer_fragments(exam, [], provider, "test", output, include_textbook_evidence=False)
    assert sorted(calls) == ["a", "b"]
    exam["items"][1]["stem"] = "晶胞"
    module.generate_answer_fragments(exam, [], provider, "test", output, include_textbook_evidence=False)
    assert calls.count("a") == 1
    assert calls.count("b") == 2
    module.generate_answer_fragments(exam, [], replace(provider, temperature=0.3), "test", output,
                                     include_textbook_evidence=False)
    assert calls.count("a") == 2
    assert calls.count("b") == 3
