from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer

from app import pydantic_shadow


def _valid_plan() -> dict:
    return {
        "schema_version": "answer_book.practice_plan.v1",
        "source_analysis": {},
        "blueprint": {
            "exercise_plan": [{
                "number": 1,
                "plan_item_id": "p1",
                "question_type": "计算题",
                "difficulty": "基础",
                "required_knowledge_points": ["守恒"],
                "required_constraints": {
                    "essential_definitions": [],
                    "essential_formulas": [],
                    "applicable_boundaries": [],
                },
            }],
        },
    }


def test_shadow_records_only_metadata_and_never_raises_on_write_failure(monkeypatch):
    monkeypatch.setattr(pydantic_shadow, "_append_event", lambda event: (_ for _ in ()).throw(OSError("full")))
    event = pydantic_shadow.observe_practice_plan({"private_prompt": "secret"})
    assert event["passed"] is False
    assert event["enforced"] is False
    assert event["actual_blocked"] is False
    assert event["would_block_if_enforced"] is True
    assert event["model_calls_added"] == 0
    assert event["tokens_added"] == 0
    serialized = json.dumps(event, ensure_ascii=False)
    assert "secret" not in serialized
    assert "private_prompt" not in serialized


def test_shadow_valid_plan_passes_without_mutating_input(tmp_path, monkeypatch):
    event_log = tmp_path / "events.jsonl"
    monkeypatch.setattr(pydantic_shadow, "SHADOW_EVENT_LOG", event_log)
    plan = _valid_plan()
    original = json.loads(json.dumps(plan, ensure_ascii=False))
    event = pydantic_shadow.observe_practice_plan(plan)
    assert event["passed"] is True
    assert plan == original
    assert len(event_log.read_text(encoding="utf-8").splitlines()) == 1


def test_shadow_report_requires_real_sample_and_task_floor(tmp_path, monkeypatch):
    event_log = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(pydantic_shadow, "SHADOW_REPORT_JSON", report_path)
    started = datetime.now().astimezone() - timedelta(days=13)
    rows = []
    for index in range(100):
        rows.append({
            "schema_version": pydantic_shadow.SHADOW_SCHEMA_VERSION,
            "timestamp": (started + timedelta(hours=index * 4)).isoformat(timespec="seconds"),
            "object_type": "blueprint",
            "task_id": f"task-{index % 20}",
            "passed": index != 0,
            "would_block_if_enforced": index == 0,
            "validation_ms": 0.2,
            "errors": [{"path": "blueprint.exercise_plan", "type": "missing"}] if index == 0 else [],
        })
    event_log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    report = pydantic_shadow.build_pydantic_shadow_report(event_path=event_log)
    assert report["review_readiness"]["ready"] is True
    assert report["would_block_count"] == 1
    assert report["actual_blocked_count"] == 0
    assert report["added_model_calls"] == 0
    assert report["added_tokens"] == 0
    assert report["automatic_promotion_enabled"] is False
    assert report["problem_tracking"]["avoided_retry_count"] == 0
    assert report["blocking_policy"]["semantic_risks"] == "existing_quality_gate"
    assert report_path.exists()


def test_shadow_review_records_false_positive_without_content(tmp_path, monkeypatch):
    review_log = tmp_path / "reviews.jsonl"
    event_log = tmp_path / "events.jsonl"
    monkeypatch.setattr(pydantic_shadow, "SHADOW_REVIEW_LOG", review_log)
    event_log.write_text(json.dumps({
        "schema_version": pydantic_shadow.SHADOW_SCHEMA_VERSION,
        "event_id": "event-1",
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "object_type": "figure_spec",
        "passed": False,
        "would_block_if_enforced": True,
        "errors": [{"path": "kind", "type": "missing"}],
    }) + "\n", encoding="utf-8")
    pydantic_shadow.record_shadow_review("event-1", "false_positive")
    report = pydantic_shadow.build_pydantic_shadow_report(event_path=event_log, review_path=review_log)
    assert report["problem_tracking"]["false_positive_count"] == 1
    assert "prompt" not in review_log.read_text(encoding="utf-8")


def test_shadow_observer_failure_never_escapes_practice_or_figure_flow(tmp_path, monkeypatch):
    from app import exercise_generation, figures

    monkeypatch.setattr(pydantic_shadow, "observe_practice_set", lambda _data: (_ for _ in ()).throw(RuntimeError("observer")))
    exercise_generation._observe_pydantic_shadow("practice_output", {})

    monkeypatch.setattr(pydantic_shadow, "observe_figure_spec", lambda _data: (_ for _ in ()).throw(RuntimeError("observer")))
    specs = tmp_path / "specs.json"
    specs.write_text(json.dumps({
        "figures": [{"figure_id": "f1", "question_id": "q1", "kind": "creep_curve"}],
    }), encoding="utf-8")
    assert figures.generate_figures(specs, tmp_path / "figures") == []


def test_shadow_report_api_and_frontend_are_observe_only(monkeypatch):
    from app import server as platform_server

    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pydantic_shadow, "build_pydantic_shadow_report", lambda: {
        "mode": "shadow",
        "enforced": False,
        "sample_count": 2,
        "added_model_calls": 0,
    })
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/quality/pydantic-shadow") as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["mode"] == "shadow"
        assert payload["enforced"] is False
        assert payload["added_model_calls"] == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)

    index = (platform_server.WEB_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (platform_server.WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "pydanticShadowSummary" in index
    assert "/api/quality/pydantic-shadow" in app_js
    assert "0 额外模型调用" in app_js
