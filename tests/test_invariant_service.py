from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from app.invariant_service import build_invariant_report


def _write_rows(path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_invariant_service_separates_contradictions_from_coverage_gaps(tmp_path) -> None:
    ledger = tmp_path / "events.jsonl"
    _write_rows(
        ledger,
        [
            {"event_type": "invocation.intent", "invocation_id": "a", "task_id": "private-task"},
            {"event_type": "invocation.result", "invocation_id": "a", "task_id": "private-task"},
            {"event_type": "invocation.result", "invocation_id": "b", "task_id": "other-task"},
            {"event_type": "retry.started", "invocation_id": "a", "task_id": "private-task"},
        ],
    )
    report = build_invariant_report(
        model_execution_ledger=ledger,
        projection_report={"finding_counts": {}, "real_state_contradiction_task_count": 0},
        artifact_report={"integrity_violation_count": 0},
    )

    assert report["enforced"] is False
    assert report["actual_blocked_count"] == 0
    assert report["finding_counts"]["result_without_intent"] == 1
    assert report["finding_counts"]["legacy_intent_without_prompt_observation"] == 1
    assert "private-task" not in json.dumps(report)


def test_invariant_service_counts_unresolved_intent_as_evidence_gap(tmp_path) -> None:
    ledger = tmp_path / "events.jsonl"
    _write_rows(
        ledger,
        [
            {
                "event_type": "invocation.intent",
                "invocation_id": "a",
                "task_id": "task",
                "prompt_observation": {"registered": True},
            }
        ],
    )
    report = build_invariant_report(
        model_execution_ledger=ledger,
        projection_report={"finding_counts": {}, "real_state_contradiction_task_count": 0},
        artifact_report={"integrity_violation_count": 0},
    )

    finding = next(item for item in report["findings"] if item["code"] == "unresolved_invocation_intent")
    assert finding["class"] == "evidence_gap"
    assert report["readiness"]["fail_closed_ready"] is False


def test_new_quality_endpoints_are_read_only(monkeypatch) -> None:
    from app import server as platform_server

    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        platform_server,
        "build_invariant_report",
        lambda: {"mode": "shadow", "authority": "observation_only", "enforced": False},
    )
    monkeypatch.setattr(
        platform_server,
        "build_artifact_integrity_report",
        lambda: {"mode": "read_only", "integrity_violation_count": 0},
    )
    monkeypatch.setattr(
        platform_server,
        "build_token_meter_report",
        lambda: {"mode": "active_measurement_shadow_policy", "added_model_calls": 0},
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        payloads = {}
        for name in ("invariants", "artifacts", "token-meter"):
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/quality/{name}") as response:
                payloads[name] = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)

    assert payloads["invariants"]["enforced"] is False
    assert payloads["artifacts"]["integrity_violation_count"] == 0
    assert payloads["token-meter"]["added_model_calls"] == 0
