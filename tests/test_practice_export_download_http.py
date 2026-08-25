from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from app import server as platform_server


@contextmanager
def _running_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_word_preflight_returns_json_then_downloads_one_named_nonempty_file(monkeypatch, tmp_path):
    content = b"complete-word-document"
    source = tmp_path / "server-output.docx"
    source.write_bytes(content)
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        platform_server,
        "practice_export_download",
        lambda _job_id: (source, "服务端默认文件.docx"),
    )
    requested_name = "用户选择的完整题目.docx"
    quoted_name = urllib.parse.quote(requested_name)

    with _running_server() as base_url:
        endpoint = f"{base_url}/api/practice/export-jobs/practice_word_test1234/download"
        with urllib.request.urlopen(f"{endpoint}?check=1&filename={quoted_name}") as response:
            metadata = json.loads(response.read().decode("utf-8"))
            assert response.headers.get_content_type() == "application/json"
            assert response.headers.get("Content-Disposition") is None

        with urllib.request.urlopen(f"{endpoint}?filename={quoted_name}") as response:
            downloaded = response.read()
            disposition = response.headers["Content-Disposition"]

    assert metadata == {"ok": True, "filename": requested_name, "size_bytes": len(content)}
    assert downloaded == content
    assert urllib.parse.quote(requested_name) in disposition


def test_word_preflight_rejects_empty_files_without_triggering_attachment(monkeypatch, tmp_path):
    source = tmp_path / "empty.docx"
    source.write_bytes(b"")
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform_server, "append_exception_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform_server, "practice_export_download", lambda _job_id: (source, "empty.docx"))

    with _running_server() as base_url:
        try:
            urllib.request.urlopen(
                f"{base_url}/api/practice/export-jobs/practice_word_test1234/download?check=1"
            )
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert exc.headers.get_content_type() == "application/json"
            assert "为空" in payload["error"]
        else:
            raise AssertionError("empty Word download unexpectedly passed preflight")
