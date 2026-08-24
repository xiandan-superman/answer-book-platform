from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

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
