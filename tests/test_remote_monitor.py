from __future__ import annotations

import base64
import json
from pathlib import Path

from scripts import monitor_remote_platform as monitor


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_read_json_sends_basic_auth_without_putting_password_in_url(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({"ok": True})

    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)

    assert monitor.read_json("http://100.64.0.8:18766/api/system/status", 5, ("monitor", "secret")) == {"ok": True}
    request = captured["request"]
    assert request.full_url == "http://100.64.0.8:18766/api/system/status"
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(b"monitor:secret").decode("ascii")


def test_discovery_checks_desktop_range_and_legacy_port(monkeypatch) -> None:
    visited = []

    def fake_read_json(url, _timeout, _credentials=None):
        visited.append(url)
        if url == "http://100.64.0.8:18768/api/version":
            return {"platform": "Answer Book Platform", "version": "0.9.8"}
        raise OSError("closed")

    monkeypatch.setattr(monitor, "read_json", fake_read_json)

    found = monitor.discover_base_url("100.64.0.8", [18766, 18767, 18768, 8766], 1)

    assert found == "http://100.64.0.8:18768"
    assert visited[-1].endswith(":18768/api/version")


def test_capture_fetches_logs_and_requested_task_then_prunes_old_snapshots(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_read_json(url, _timeout, _credentials=None):
        calls.append(url)
        if url.endswith("/api/system/status"):
            return {"host": {}, "tasks": {"recent": []}}
        if url.endswith("/api/system/logs"):
            return {"logs": [{"message": "failure"}]}
        if url.endswith("/api/tasks/task-123/diagnostics"):
            return {"task_id": "task-123", "status": "failed"}
        raise AssertionError(url)

    for index in range(5):
        (tmp_path / f"remote_monitor_20260101_00000{index}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(monitor, "read_json", fake_read_json)

    snapshot = monitor.capture(
        "http://100.64.0.8:18766",
        tmp_path,
        2,
        ("monitor", "secret"),
        requested_task_ids=["task-123"],
        retain=2,
    )

    assert snapshot["logs"]["logs"][0]["message"] == "failure"
    assert snapshot["diagnostics"]["task-123"]["status"] == "failed"
    assert any(url.endswith("/api/system/logs") for url in calls)
    assert len(list(tmp_path.glob("remote_monitor_*.json"))) == 2


def test_parse_ports_accepts_ranges_and_deduplicates() -> None:
    assert monitor.parse_ports("18766-18768,8766,18767") == [18766, 18767, 18768, 8766]
