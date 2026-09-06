import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import pytest

from app import server as platform_server
from app.unit_delivery import publish_manifest, store_unit


def test_task_download_requires_pinned_checked_revision(tmp_path, monkeypatch):
    root = tmp_path / "unit_delivery"
    unit = store_unit(root, b"checked", revision="v1", object_ids=["a"], warnings=[])
    manifest = publish_manifest(root, expected=[{"object_id": "a"}], units=[unit])
    downloads = []
    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform_server, "append_exception_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform_server, "load_task", lambda task_id: {"task_id": task_id})
    monkeypatch.setattr(platform_server, "stage_dir", lambda _: tmp_path)
    monkeypatch.setattr(platform_server, "mark_task_downloaded", lambda task_id: downloads.append(task_id))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{httpd.server_port}/api/tasks/task"
    try:
        with urllib.request.urlopen(base + "/unit-delivery") as response:
            assert json.load(response)["revision"] == manifest["revision"]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/unit-package")
        assert error.value.code == 400
        with urllib.request.urlopen(base + "/unit-package?revision=" + manifest["revision"]) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                assert archive.read("分题成果_001.docx") == b"checked"
        assert downloads == ["task"]
        (root / "artifacts" / f"{unit['sha256']}.docx").write_bytes(b"changed")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/unit-package?revision=" + manifest["revision"])
        assert error.value.code == 400
        assert downloads == ["task"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)
