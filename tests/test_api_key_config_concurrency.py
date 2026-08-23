from __future__ import annotations

import hashlib
import http.client
import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from app import api_key_config
from app import server as server_module
from app.http_errors import public_error_payload


def _isolated_config(tmp_path: Path):
    config_dir = tmp_path / "config"
    key_file = config_dir / "api_keys.json"
    empty_environment = {name: "" for name in api_key_config.ALLOWED_API_KEY_NAMES}
    return (
        key_file,
        patch.object(api_key_config, "API_KEY_FILE", key_file),
        patch.object(api_key_config, "LOCAL_CONFIG_DIR", config_dir),
        patch.object(api_key_config, "DATA_ROOT", tmp_path),
        patch.object(api_key_config, "ensure_project_dirs"),
        patch.dict(os.environ, empty_environment),
    )


def _request(httpd: ThreadingHTTPServer, path: str) -> tuple[int, dict]:
    host = str(httpd.server_address[0])
    port = int(httpd.server_address[1])
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request("GET", path)
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, payload


def test_first_start_is_safe_across_128_threaded_rounds(tmp_path: Path) -> None:
    key_file, *contexts = _isolated_config(tmp_path)
    errors: list[BaseException] = []
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        for round_number in range(128):
            key_file.unlink(missing_ok=True)
            barrier = threading.Barrier(2)

            def read_info(round_barrier: threading.Barrier = barrier) -> None:
                try:
                    round_barrier.wait(timeout=2)
                    api_key_config.api_key_file_info()
                except BaseException as exc:  # captured without printing sensitive values
                    errors.append(exc)

            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(lambda _index: read_info(), range(2)))
            assert key_file.is_file(), round_number
            assert isinstance(json.loads(key_file.read_text(encoding="utf-8")), dict)

    assert len(errors) == 0


def test_concurrent_updates_merge_different_provider_fields(tmp_path: Path) -> None:
    key_file, *contexts = _isolated_config(tmp_path)
    barrier = threading.Barrier(2)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        api_key_config.ensure_api_key_file()

        def update(name: str) -> None:
            barrier.wait(timeout=2)
            api_key_config.update_api_key_values({name: f"test-value-{name.lower()}"})

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(update, ["ARK_API_KEY", "DEEPSEEK_API_KEY"]))
        configured = set(api_key_config.api_key_file_info()["configured_keys"])

    assert configured == {"ARK_API_KEY", "DEEPSEEK_API_KEY"}
    assert key_file.is_file()


def test_same_field_competition_is_serialized_with_last_transaction_winning(tmp_path: Path) -> None:
    _key_file, *contexts = _isolated_config(tmp_path)
    first_inside_read = threading.Event()
    release_first = threading.Event()
    original_read = api_key_config._read_json

    def ordered_read(path: Path) -> dict:
        if threading.current_thread().name == "first-update" and not first_inside_read.is_set():
            first_inside_read.set()
            assert release_first.wait(2)
        return original_read(path)

    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch.object(
        api_key_config, "_read_json", side_effect=ordered_read
    ):
        api_key_config.ensure_api_key_file()
        first = threading.Thread(
            name="first-update",
            target=lambda: api_key_config.update_api_key_values({"ARK_API_KEY": "first-test-value"}),
        )
        second = threading.Thread(
            name="second-update",
            target=lambda: api_key_config.update_api_key_values({"ARK_API_KEY": "second-test-value"}),
        )
        first.start()
        assert first_inside_read.wait(2)
        second.start()
        release_first.set()
        first.join(3)
        second.join(3)
        final_value = api_key_config.read_api_keys().get("ARK_API_KEY")

    assert hashlib.sha256(str(final_value).encode()).digest() == hashlib.sha256(b"second-test-value").digest()


def test_atomic_write_failure_preserves_old_file_and_cleans_own_temporary(tmp_path: Path) -> None:
    key_file, *contexts = _isolated_config(tmp_path)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        api_key_config.update_api_key_values({"ARK_API_KEY": "existing-test-value"})
        old_digest = hashlib.sha256(key_file.read_bytes()).digest()
        with patch.object(api_key_config.os, "replace", side_effect=OSError("simulated replace failure")):
            with pytest.raises(api_key_config.ApiKeyConfigUnavailable):
                api_key_config.update_api_key_values({"DEEPSEEK_API_KEY": "new-test-value"})

        assert hashlib.sha256(key_file.read_bytes()).digest() == old_digest
        assert not list(key_file.parent.glob(f".{key_file.name}.*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not available on Windows")
def test_api_key_file_and_atomic_temporary_use_owner_only_permissions(tmp_path: Path) -> None:
    key_file, *contexts = _isolated_config(tmp_path)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        api_key_config.ensure_api_key_file()
        api_key_config.update_api_key_values({"ARK_API_KEY": "permission-test-value"})

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert not list(key_file.parent.glob(f".{key_file.name}.*.tmp"))


def test_first_provider_endpoints_concurrently_return_200(tmp_path: Path) -> None:
    _key_file, *contexts = _isolated_config(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.PlatformHandler)
    httpd.daemon_threads = True
    serving = threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    try:
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
            barrier = threading.Barrier(2)

            def fetch(path: str) -> tuple[int, dict]:
                barrier.wait(timeout=2)
                return _request(httpd, path)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(fetch, ["/api/providers", "/api/providers/key-file"]))

        assert [status for status, _payload in results] == [200, 200]
        assert all(isinstance(payload, dict) for _status, payload in results)
    finally:
        httpd.shutdown()
        httpd.server_close()
        serving.join(2)


def test_api_key_initialization_failure_is_sanitized_recoverable_503() -> None:
    failure = api_key_config.ApiKeyConfigUnavailable(api_key_config.ApiKeyConfigUnavailable.public_message)
    payload = public_error_payload(failure, status=503, path="/api/providers/key-file")

    assert payload["error_code"] == "api_configuration_unavailable"
    assert payload["error"] == "API 配置暂时无法加载，请重试。"
    assert "重试" in payload["suggested_action"]
    assert payload["support_id"]


def test_key_file_endpoint_reports_initialization_failure_as_503_not_404() -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.PlatformHandler)
    httpd.daemon_threads = True
    serving = threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    try:
        failure = api_key_config.ApiKeyConfigUnavailable(api_key_config.ApiKeyConfigUnavailable.public_message)
        with patch.object(server_module, "api_key_file_info", side_effect=failure):
            status, payload = _request(httpd, "/api/providers/key-file")

        assert status == 503
        assert payload["error_code"] == "api_configuration_unavailable"
        assert payload["error"] == "API 配置暂时无法加载，请重试。"
        assert payload["support_id"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        serving.join(2)
