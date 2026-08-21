from __future__ import annotations

import http.client
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import practice_store
from app import server as server_module


def _post(host: str, port: int, path: str, payload: dict) -> tuple[int, dict]:
    connection = http.client.HTTPConnection(host, port, timeout=3)
    connection.request(
        "POST",
        path,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    response = connection.getresponse()
    data = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, data


def test_stale_question_patch_returns_actionable_http_conflict() -> None:
    with tempfile.TemporaryDirectory() as raw, patch.object(
        practice_store,
        "PRACTICE_HISTORY_DIR",
        Path(raw),
    ):
        saved = practice_store.save_practice_record({"exercises": [{"number": 1, "stem": "原题"}]})
        history_id = str(saved["history_id"])
        version = practice_store.load_practice_record(history_id)["data"]["exercises"][0]["_edit_version"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), server_module.PlatformHandler)
        server.daemon_threads = True
        host, port = server.server_address
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        path = f"/api/practice/history/{history_id}/exercise"
        try:
            first_status, _first = _post(
                host,
                port,
                path,
                {
                    "exercise_index": 0,
                    "exercise": {"number": 1, "stem": "新页面内容"},
                    "expected_edit_version": version,
                },
            )
            stale_status, stale = _post(
                host,
                port,
                path,
                {
                    "exercise_index": 0,
                    "exercise": {"number": 1, "stem": "旧页面内容"},
                    "expected_edit_version": version,
                },
            )
        finally:
            server.shutdown()
            server.server_close()
            serving.join(2)

        assert first_status == 200
        assert stale_status == 409
        assert stale["error_code"] == "practice_edit_conflict"
        assert "未覆盖" in stale["error"]
        assert "重新打开" in stale["suggested_action"]
        assert practice_store.load_practice_record(history_id)["data"]["exercises"][0]["stem"] == "新页面内容"


def test_stale_full_set_save_cannot_overwrite_a_newer_record() -> None:
    with tempfile.TemporaryDirectory() as raw, patch.object(
        practice_store,
        "PRACTICE_HISTORY_DIR",
        Path(raw),
    ):
        saved = practice_store.save_practice_record({"exercises": [{"number": 1, "stem": "原题"}]})
        history_id = str(saved["history_id"])
        stale_data = saved["data"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), server_module.PlatformHandler)
        server.daemon_threads = True
        host, port = server.server_address
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            first_status, _first = _post(
                host,
                port,
                "/api/practice/history",
                {
                    "data": {
                        **stale_data,
                        "exercises": [{"number": 1, "stem": "先保存的新整套内容"}],
                    }
                },
            )
            stale_status, stale = _post(
                host,
                port,
                "/api/practice/history",
                {
                    "data": {
                        **stale_data,
                        "exercises": [{"number": 1, "stem": "旧页面整套覆盖"}],
                    }
                },
            )
        finally:
            server.shutdown()
            server.server_close()
            serving.join(2)

        assert first_status == 200
        assert stale_status == 409
        assert stale["error_code"] == "practice_edit_conflict"
        assert practice_store.load_practice_record(history_id)["data"]["exercises"][0]["stem"] == "先保存的新整套内容"
