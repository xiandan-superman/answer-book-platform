from __future__ import annotations

import http.client
import io
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import server as platform_server


def test_live_json_snapshot_read_ignores_a_torn_write(tmp_path: Path) -> None:
    from app.server import _read_json_if_exists

    snapshot = tmp_path / "question_understanding_progress.json"
    snapshot.write_text('{"status":', encoding="utf-8")

    assert _read_json_if_exists(snapshot) is None


def test_send_file_streams_content_without_read_bytes(tmp_path, monkeypatch) -> None:
    target = tmp_path / "large.bin"
    target.write_bytes(b"streamed-content")
    monkeypatch.setattr(Path, "read_bytes", lambda _self: (_ for _ in ()).throw(AssertionError("read_bytes used")))
    handler = object.__new__(platform_server.PlatformHandler)
    handler.wfile = io.BytesIO()
    headers: dict[str, str] = {}
    handler.send_response = lambda status: headers.__setitem__("status", str(status))
    handler.send_header = lambda key, value: headers.__setitem__(key, value)
    handler.send_security_headers = lambda: None
    handler.end_headers = lambda: None

    handler.send_file(target, content_type="application/octet-stream")

    assert headers["status"] == "200"
    assert headers["Content-Length"] == str(len(b"streamed-content"))
    assert handler.wfile.getvalue() == b"streamed-content"


def test_read_json_rejects_chunked_transfer_encoding() -> None:
    handler = object.__new__(platform_server.PlatformHandler)
    headers = Message()
    headers["Transfer-Encoding"] = "chunked"
    handler.headers = headers
    handler.rfile = io.BytesIO(b"4\r\n{}\r\n0\r\n\r\n")

    with pytest.raises(ValueError, match="分块传输"):
        handler.read_json()


def test_untrusted_host_is_rejected_before_local_auth_bypass(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        connection.request("GET", "/api/lan/access", headers={"Host": f"attacker.example:{httpd.server_port}"})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        assert response.status == 421
        assert payload["error_code"] == "host_rejected"
        assert "password" not in payload
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_local_privileged_endpoint_requires_process_token(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/providers/local-keys",
            data=b'{"keys": {}}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 403
        assert payload["error_code"] == "local_privilege_required"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_local_update_progress_survives_service_token_rotation(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        platform_server,
        "update_progress",
        lambda: {"ok": True, "status": "completed", "percent": 100, "latest_version": "1.0.0"},
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/update/progress",
            headers={"X-Answer-Book-Local-Token": "stale-token-from-previous-process"},
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["status"] == "completed"
        assert payload["percent"] == 100
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


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


def test_removed_shared_textbook_library_api_returns_404(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/shared-textbook-library/catalog")
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 404
        assert payload["error_code"] == "api_not_found"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("body", [[], "hello", 1, True])
def test_post_rejects_valid_json_non_object_as_400(body, monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform_server, "append_exception_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/provider-control/probe",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Answer-Book-Local-Token": platform_server._LOCAL_PRIVILEGE_TOKEN,
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 400
        assert payload["error_code"] == "invalid_request"
        assert payload["error"] == "请求体必须是 JSON 对象。"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"])
def test_unsupported_http_methods_use_json_contract_without_python_version(method, monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/tasks", method=method)
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 405
        assert exc_info.value.headers.get_content_type() == "application/json"
        assert exc_info.value.headers["Allow"] == "GET, POST"
        assert "Python" not in exc_info.value.headers.get("Server", "")
        assert payload["error_code"] == "method_not_allowed"
        assert payload["support_id"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_unsupported_head_hides_python_version_and_returns_no_body(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/tasks", method="HEAD")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        assert exc_info.value.code == 405
        assert exc_info.value.read() == b""
        assert exc_info.value.headers.get_content_type() == "application/json"
        assert "Python" not in exc_info.value.headers.get("Server", "")
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_task_files_use_relative_resource_ids_and_download_without_exposing_host_path(tmp_path, monkeypatch) -> None:
    stage = tmp_path / "private-user" / "task-stage"
    output = tmp_path / "private-user" / "task-output"
    stage.mkdir(parents=True)
    output.mkdir(parents=True)
    result = output / "answer book.docx"
    result.write_bytes(b"docx")
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform_server, "load_task", lambda _task_id: SimpleNamespace(analysis_profile="evidence_backed"))
    monkeypatch.setattr(platform_server, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(platform_server, "output_dir", lambda _task_id: output)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_port}"
        with urllib.request.urlopen(f"{base}/api/tasks/task-1/files") as response:
            payload = json.loads(response.read().decode("utf-8"))
        file_row = payload["files"][0]
        assert file_row["resource_id"] == "output/answer book.docx"
        assert "path" not in file_row
        assert str(tmp_path) not in json.dumps(payload)
        assert "?file=" in file_row["download_url"]
        with urllib.request.urlopen(base + file_row["download_url"]) as response:
            assert response.read() == b"docx"
        legacy = urllib.parse.quote(str(result), safe="")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base}/api/tasks/task-1/download?path={legacy}")
        assert exc_info.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_paid_answer_fragment_demo_endpoint_is_not_exposed(monkeypatch) -> None:
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/generate-answer-fragment-demo",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 404
        assert payload["error_code"] == "api_not_found"
        assert payload["path"] == "/api/generate-answer-fragment-demo"
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
            policy = response.headers["Content-Security-Policy"]
            assert "script-src 'self';" in policy
            assert "script-src 'self' 'unsafe-inline'" not in policy
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_hosted_word_format_page_uses_csp_nonce() -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/word-format") as response:
            html = response.read().decode("utf-8")
            policy = response.headers["Content-Security-Policy"]
            nonce = re.search(r"'nonce-([^']+)'", policy)
            assert nonce is not None
            assert f'<script nonce="{nonce.group(1)}">' in html
            assert "script-src 'self' 'unsafe-inline'" not in policy
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_delivery_package_response_returns_a_real_download_url(tmp_path, monkeypatch) -> None:
    task_id = "delivery-download-test"
    output_root = tmp_path / "output"
    package = output_root / "delivery" / "delivery-download-test_delivery.zip"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"PK\x03\x04verified-package")
    monkeypatch.setattr(platform_server, "output_dir", lambda _task_id: output_root)
    monkeypatch.setattr(platform_server, "stage_dir", lambda _task_id: tmp_path / "stage")
    monkeypatch.setattr(
        platform_server,
        "build_task_delivery_package",
        lambda *_args: {"ok": True, "status": "completed", "zip": str(package)},
    )
    monkeypatch.setattr(platform_server, "mark_task_downloaded", lambda _task_id: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_port}"
        request = urllib.request.Request(
            f"{base}/api/tasks/{task_id}/delivery-package",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["filename"] == package.name
        assert payload["download_url"].startswith(f"/api/tasks/{task_id}/download?file=")
        with urllib.request.urlopen(base + payload["download_url"]) as response:
            assert response.read() == package.read_bytes()
            assert response.headers.get_filename() == package.name
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
