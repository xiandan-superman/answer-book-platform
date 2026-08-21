from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

from app import model_diagnostics, support_reporting
from scripts import support_receiver


def _write_bundle(path: Path, report_id: str, fingerprint: str, *, payload: str = "ok") -> dict:
    manifest = {
        "schema_version": 1,
        "report_id": report_id,
        "fingerprint": fingerprint,
        "created_at": "2026-08-21T12:00:00+00:00",
        "scope": "question",
        "application": {"version": "8.23"},
        "context": {"page": "result", "question_id": "q1"},
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("related_content.json", json.dumps({"answer": payload}, ensure_ascii=False))
    return manifest


def test_model_diagnostic_keeps_relevant_content_and_redacts_credentials() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp) / "model_diagnostics"
        image = base64.b64encode(b"small-image").decode("ascii")
        call = {"task_id": "task-1", "call_id": "1", "stage": "answer_generation", "active_item": "q1"}
        request = {
            "api_key": "sk-secret-value",
            "messages": [{"role": "user", "content": "请回答这道题"}],
            "image_url": f"data:image/png;base64,{image}",
        }
        response = {"choices": [{"message": {"content": "模型原始答案"}}]}
        with patch.object(model_diagnostics, "MODEL_DIAGNOSTICS_DIR", root):
            target = model_diagnostics.record_model_diagnostic(call, request, response_payload=response)
            assert target is not None
            traces = model_diagnostics.relevant_model_diagnostics("task-1", "q1")
            attachments = model_diagnostics.diagnostic_attachments("task-1", traces)
        text = json.dumps(traces, ensure_ascii=False)
        assert "请回答这道题" in text
        assert "模型原始答案" in text
        assert "sk-secret-value" not in text
        assert '"api_key": "***"' in text
        assert len(attachments) == 1
        assert attachments[0].read_bytes() == b"small-image"


def test_long_task_lifecycle_is_not_time_windowed_and_bundle_is_bounded() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        task_root = root / "tasks" / "task-long"
        task_root.mkdir(parents=True)
        event_path = task_root / "events.jsonl"
        with event_path.open("w", encoding="utf-8") as handle:
            for index in range(5000):
                handle.write(json.dumps({
                    "time": f"2026-08-{1 + index // 300:02d} 10:00:00",
                    "event": "stage_changed",
                    "payload": {"current_stage": f"stage_{index}", "status": "running"},
                }) + "\n")
        pending = root / "support_reports" / "pending"
        with (
            patch.object(support_reporting, "SUPPORT_ROOT", pending.parent),
            patch.object(support_reporting, "PENDING_DIR", pending),
            patch.object(support_reporting, "RECEIPTS_PATH", pending.parent / "receipts.jsonl"),
            patch.object(support_reporting, "task_dir", lambda _task_id: task_root),
            patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
            patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
            patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
            patch("app.support_reporting.relevant_model_diagnostics", return_value=[]),
            patch("app.support_reporting.diagnostic_attachments", return_value=[]),
        ):
            path, _manifest = support_reporting._build_report({"scope": "task", "task_id": "task-long", "events": []})
        assert path.stat().st_size < support_reporting.MAX_COMPRESSED_BYTES
        with zipfile.ZipFile(path) as zf:
            lifecycle = json.loads(zf.read("task_lifecycle.json"))
        assert lifecycle["truncated"] is True
        assert lifecycle["first_items"][0]["payload"]["current_stage"] == "stage_0"
        assert lifecycle["last_items"][-1]["payload"]["current_stage"] == "stage_4999"


def test_offline_queue_replaces_same_fingerprint_instead_of_adding_files() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        pending = root / "pending"
        patches = (
            patch.object(support_reporting, "SUPPORT_ROOT", root),
            patch.object(support_reporting, "PENDING_DIR", pending),
            patch.object(support_reporting, "RECEIPTS_PATH", root / "receipts.jsonl"),
            patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
            patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
            patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
            patch("app.support_reporting.relevant_model_diagnostics", return_value=[]),
            patch("app.support_reporting.diagnostic_attachments", return_value=[]),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            support_reporting._build_report({"scope": "page", "page": "home", "events": []})
            support_reporting._build_report({"scope": "page", "page": "home", "events": []})
            files = list(pending.glob("*.zip"))
        assert len(files) == 1


def test_receiver_groups_duplicate_issue_and_keeps_one_latest_bundle() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root, quota_bytes=64 * 1024 * 1024)
        first = root / "tmp" / "first.part"
        second = root / "tmp" / "second.part"
        first_manifest = _write_bundle(first, "AB-1", "fp-1", payload="first")
        second_manifest = _write_bundle(second, "AB-2", "fp-1", payload="second")
        first_sha = support_receiver.hashlib.sha256(first.read_bytes()).hexdigest()
        second_sha = support_receiver.hashlib.sha256(second.read_bytes()).hexdigest()
        first_result = inbox.store(first, first_manifest, first_sha, "device-a")
        second_result = inbox.store(second, second_manifest, second_sha, "device-a")
        row = inbox.issue("fp-1")
        assert row is not None
        assert first_result["duplicate"] is False
        assert second_result["duplicate"] is True
        assert row["occurrence_count"] == 2
        assert len(list(inbox.inbox.glob("*.zip"))) == 1
        with zipfile.ZipFile(Path(row["bundle_path"])) as zf:
            assert "second" in zf.read("related_content.json").decode("utf-8")


def test_receiver_cleanup_removes_resolved_raw_bundle_but_keeps_summary() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root, quota_bytes=64 * 1024 * 1024)
        source = root / "tmp" / "source.part"
        manifest = _write_bundle(source, "AB-1", "fp-clean")
        digest = support_receiver.hashlib.sha256(source.read_bytes()).hexdigest()
        inbox.store(source, manifest, digest, "device-a")
        old = "2026-01-01T00:00:00+00:00"
        with inbox.connect() as connection:
            connection.execute("UPDATE issues SET status='resolved', resolved_at=? WHERE fingerprint='fp-clean'", (old,))
        inbox.cleanup()
        row = inbox.issue("fp-clean")
        assert row is not None
        assert row["bundle_path"] == ""
        assert row["occurrence_count"] == 1


def test_receiver_rejects_zip_path_traversal() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        path = Path(raw_tmp) / "bad.zip"
        manifest = {"schema_version": 1, "report_id": "AB-1", "fingerprint": "fp-1"}
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("../escape.txt", "bad")
        try:
            support_receiver.validate_bundle(path, "AB-1", "fp-1")
        except ValueError as exc:
            assert str(exc) == "unsafe_zip_path"
        else:
            raise AssertionError("unsafe ZIP path was accepted")


def test_client_stream_upload_reaches_receiver_and_returns_canonical_id() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root / "receiver", quota_bytes=64 * 1024 * 1024)
        token = "test-upload-token"
        server = support_receiver.ThreadingHTTPServer(("127.0.0.1", 0), support_receiver.UploadHandler)
        server.inbox = inbox
        server.token = token
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bundle = root / "report.zip"
        manifest = _write_bundle(bundle, "AB-STREAM-1", "fp-stream")
        try:
            with patch("app.support_reporting._config", return_value={
                "receiver_url": f"http://127.0.0.1:{server.server_port}",
                "receiver_token": token,
            }):
                result = support_reporting._upload(bundle, manifest)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        assert result["submitted"] is True
        assert result["report_id"] == "AB-STREAM-1"
        assert inbox.issue("fp-stream") is not None


def test_receiver_caps_threads_before_request_handlers_are_created() -> None:
    class CountingHandler(BaseHTTPRequestHandler):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            with self.lock:
                type(self).active += 1
                type(self).maximum = max(type(self).maximum, type(self).active)
            try:
                time.sleep(0.04)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            finally:
                with self.lock:
                    type(self).active -= 1

    server = support_receiver.BoundedThreadingHTTPServer(("127.0.0.1", 0), CountingHandler, max_workers=2)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()

    def request() -> None:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request("GET", "/")
            assert connection.getresponse().status == 200
        finally:
            connection.close()

    workers = [threading.Thread(target=request) for _ in range(10)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            assert not worker.is_alive()
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
    assert CountingHandler.maximum == 2


def test_frontend_support_is_contextual_and_api_requests_have_correlation_ids() -> None:
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    html_text = (root / "web" / "index.html").read_text(encoding="utf-8")
    api_js = (root / "web" / "platform-api.js").read_text(encoding="utf-8")
    assert "反馈此题" in app_js
    assert 'id="supportFeedbackBtn"' in html_text
    assert '"X-Request-ID": correlationId' in api_js
    assert "window.SupportTelemetry?.snapshot()" in app_js


def test_upload_implementation_streams_instead_of_reading_whole_bundle() -> None:
    source = Path(support_reporting.__file__).read_text(encoding="utf-8")
    start = source.index("def _upload(")
    end = source.index("\ndef _append_receipt", start)
    upload_source = source[start:end]
    assert "read_bytes()" not in upload_source
    assert "64 * 1024" in upload_source
