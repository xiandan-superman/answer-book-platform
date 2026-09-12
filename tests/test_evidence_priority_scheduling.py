from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from app.concurrency import ModelRequestAborted, _FairProviderGate, background_model_requests
from app.pipeline import _generate_parallel_answer_draft, _parallel_answer_questions


def test_spare_slot_preserves_foreground_capacity_and_join_releases_it() -> None:
    gate = _FairProviderGate(2)
    done, stopped, entered = Event(), Event(), Event()
    with background_model_requests(done, stopped):
        assert gate.acquire("task") is True

    def second():
        with background_model_requests(done, stopped):
            low = gate.acquire("task")
            entered.set()
            gate.release(low)

    with ThreadPoolExecutor() as pool:
        pending = pool.submit(second)
        assert not entered.wait(0.1)
        foreground = gate.acquire("task")
        assert foreground is False
        gate.release(foreground)
        assert not entered.wait(0.1)
        done.set()
        pending.result(timeout=2)
    gate.release(True)
    assert gate.snapshot()["active"] == 0


def test_single_slot_waits_and_cancel_removes_queue() -> None:
    gate = _FairProviderGate(1)
    done, stopped = Event(), Event()

    def request():
        with background_model_requests(done, stopped):
            return gate.acquire("draft")

    with ThreadPoolExecutor() as pool:
        pending = pool.submit(request)
        stopped.set()
        with pytest.raises(ModelRequestAborted):
            pending.result(timeout=2)
    assert gate.snapshot()["waiting"] == 0
    assert gate.acquire("evidence") is False
    gate.release()


def test_mixed_paper_only_defers_unreadable_image_question(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.pipeline.provider_model_supports_vision", lambda *_: True)
    snapshot = tmp_path / "snapshot.png"
    snapshot.write_bytes(b"test")
    items = [
        {"question_id": "text"},
        {"question_id": "draw", "question_type": "作图题"},
        {"question_id": "image", "image_refs": ["original"], "question_snapshot_refs": [str(snapshot)]},
        {"question_id": "missing", "image_refs": ["missing"]},
    ]
    assert [q["question_id"] for q in _parallel_answer_questions({"items": items}, None, "m")] == ["text", "draw", "image"]


def test_image_call_waits_without_extra_generation_and_independent_pool_is_normal(monkeypatch, tmp_path) -> None:
    from app.concurrency import _BACKGROUND_REQUEST

    done, stopped, prepared, image_called = Event(), Event(), Event(), Event()

    def generate(*args, **kwargs):
        assert _BACKGROUND_REQUEST.get()[0].is_set()  # independent supplier
        prepared.set()
        kwargs["before_image_generation"]()
        image_called.set()
        return "complete"

    monkeypatch.setattr("app.pipeline.generate_answer_fragments", generate)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            _generate_parallel_answer_draft, {"items": []}, SimpleNamespace(), "m", tmp_path / "draft.json",
            done, stopped, shared_pool=False, progress_json=tmp_path / "progress.json",
        )
        assert prepared.wait(2)
        assert not image_called.wait(0.1)
        done.set()
        assert future.result(timeout=2) == "complete"
        assert image_called.is_set()


def test_dependency_wait_does_not_spend_model_budget(monkeypatch) -> None:
    from app.answer_generation import _QuestionModelExecutionBudget

    clock = [10.0]
    monkeypatch.setattr("app.answer_generation.time.monotonic", lambda: clock[0])
    budget = _QuestionModelExecutionBudget(60)
    budget.mark_provider_admitted()
    clock[0] += 5
    budget.wait_for_dependency(lambda: clock.__setitem__(0, clock[0] + 600))
    assert budget.deadline_monotonic() - clock[0] == 55
    assert not budget.exhausted()


def test_independent_supplier_keeps_its_single_slot() -> None:
    gate = _FairProviderGate(1)
    independent, stopped = Event(), Event()
    independent.set()
    with background_model_requests(independent, stopped):
        assert gate.acquire("draft") is False
    gate.release()
    assert gate.snapshot()["active"] == 0


def test_stopped_drawing_does_not_call_image(monkeypatch, tmp_path) -> None:
    stopped = Event()
    stopped.set()

    def generate(*args, **kwargs):
        kwargs["before_image_generation"]()
        pytest.fail("image execution must not continue")

    monkeypatch.setattr("app.pipeline.generate_answer_fragments", generate)
    with pytest.raises(ModelRequestAborted):
        _generate_parallel_answer_draft(
            {"items": []}, None, "m", tmp_path / "draft.json", Event(), stopped,
            shared_pool=True, progress_json=tmp_path / "progress.json",
        )


def test_parallel_progress_is_exposed_and_cleared_on_resume(monkeypatch, tmp_path) -> None:
    from app.artifact_store import atomic_write_json
    from app.pipeline import reset_transient_progress
    from app.server import _task_current_progress

    monkeypatch.setattr("app.server.stage_dir", lambda _: tmp_path)
    atomic_write_json(tmp_path / "evidence_selection_progress.json", {"completed": 2, "total": 4})
    atomic_write_json(tmp_path / "answer_generation_parallel_progress.json", {"completed": 1, "total": 4})
    result = _task_current_progress("task", "evidence_selection")
    assert result["completed"] == 2
    assert result["parallel_answer"]["completed"] == 1
    reset_transient_progress(tmp_path)
    assert not _task_current_progress("task", "evidence_selection")


def test_cached_drawing_contract_is_attached_before_speculation(monkeypatch, tmp_path) -> None:
    def attach(exam, plan):
        assert plan == {"cached": True}
        return {**exam, "contract_attached": True}

    def generate(exam, *args, **kwargs):
        assert exam["contract_attached"]
        return "complete"

    monkeypatch.setattr("app.pipeline.attach_figure_schema_plans", attach)
    monkeypatch.setattr("app.pipeline.generate_answer_fragments", generate)
    assert _generate_parallel_answer_draft(
        {"items": [{"question_id": "draw"}]}, None, "m", tmp_path / "draft.json", Event(), Event(),
        shared_pool=True, progress_json=tmp_path / "progress.json", schema_plan={"cached": True},
    ) == "complete"
