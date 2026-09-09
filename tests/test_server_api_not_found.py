from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app import server as platform_server


def test_unknown_get_api_returns_json_404(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/does-not-exist"):
            raise AssertionError("unknown API unexpectedly returned 200")
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        assert exc.code == 404
        assert exc.headers.get_content_type() == "application/json"
        assert payload["error_code"] == "api_not_found"
        assert payload["path"] == "/api/does-not-exist"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_static_woff_uses_explicit_font_mime_type(tmp_path, monkeypatch) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "font.woff").write_bytes(b"font")
    monkeypatch.setattr(platform_server, "WEB_DIR", web_root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/font.woff") as response:
            assert response.headers.get_content_type() == "font/woff"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_practice_job_collection_get_has_a_stable_contract(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "list_practice_jobs", lambda **_kwargs: [{
        "job_id": "generation_test",
        "task_id": "batch_test",
        "run_id": "generation_test",
        "status": "queued",
    }])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/practice/jobs") as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["count"] == 1
        assert payload["jobs"][0]["task_id"] == "batch_test"
        assert payload["jobs"][0]["run_id"] == "generation_test"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_practice_runtime_gate_blocks_unsupported_python_before_persistence(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "check_environment", lambda: {
        "python": "3.12.1",
        "python_supported": False,
        "python_requirement": "3.11.x",
    })
    with pytest.raises(platform_server.RuntimeEnvironmentUnsupported) as exc_info:
        platform_server._ensure_practice_runtime_ready()
    assert exc_info.value.payload["error_code"] == "runtime_environment_unsupported"
    assert "未创建任务" in exc_info.value.payload["suggested_action"]


def test_exam_path_is_validated_before_provider_configuration() -> None:
    with pytest.raises(ValueError, match="请选择要解析"):
        platform_server._validate_exam_path_for_creation("")
