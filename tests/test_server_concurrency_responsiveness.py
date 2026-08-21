from __future__ import annotations

import http.client
import json
import os
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import runtime_monitor
from app import server as server_module
from app.settings import ProviderConfig


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        payload = {"choices": [{"message": {"content": '{"ping":"pong"}'}, "finish_reason": "stop"}]}
        return json.dumps(payload).encode("utf-8")


class ServerConcurrencyResponsivenessTests(unittest.TestCase):
    def test_status_api_stays_responsive_while_one_model_call_runs_and_another_waits(self) -> None:
        provider = ProviderConfig(
            name="server-concurrency-provider",
            type="openai_compatible",
            base_url="https://server-concurrency.invalid/v1",
            api_key="test-key",
            default_model="test-model",
            model_options=("test-model",),
            allow_custom_model=False,
            model_hint="",
            temperature=0.1,
            max_tokens=100,
        )
        first_entered = threading.Event()
        release_first = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def fake_urlopen(_request, timeout):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            if current == 1:
                first_entered.set()
                if not release_first.wait(3.0):
                    raise TimeoutError("test did not release the first model call")
            return _Response()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.PlatformHandler)
        httpd.daemon_threads = True
        host, port = httpd.server_address
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        serving.start()

        def provider_test_request() -> tuple[int, dict]:
            connection = http.client.HTTPConnection(host, port, timeout=4)
            body = json.dumps({"provider": provider.name, "model": provider.default_model})
            connection.request("POST", "/api/provider-test", body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            return response.status, payload

        first_result: list[tuple[int, dict]] = []
        second_result: list[tuple[int, dict]] = []
        try:
            with patch.dict(os.environ, {"MODEL_REQUEST_MAX_CONCURRENCY": "1"}), patch.object(
                server_module, "get_provider", return_value=provider
            ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                first = threading.Thread(target=lambda: first_result.append(provider_test_request()))
                first.start()
                self.assertTrue(first_entered.wait(1.5))

                second = threading.Thread(target=lambda: second_result.append(provider_test_request()))
                second.start()
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline and runtime_monitor.model_call_summary()["waiting_count"] < 1:
                    time.sleep(0.01)
                self.assertEqual(1, runtime_monitor.model_call_summary()["waiting_count"])

                connection = http.client.HTTPConnection(host, port, timeout=2)
                started = time.monotonic()
                connection.request("GET", "/api/system/status")
                response = connection.getresponse()
                status_payload = json.loads(response.read().decode("utf-8"))
                elapsed = time.monotonic() - started
                connection.close()

                self.assertEqual(200, response.status)
                self.assertLess(elapsed, 1.0)
                self.assertEqual(1, status_payload["models"]["active_count"])
                self.assertEqual(1, status_payload["models"]["waiting_count"])

                release_first.set()
                first.join(2.0)
                second.join(2.0)
                self.assertEqual(200, first_result[0][0])
                self.assertEqual(200, second_result[0][0])
        finally:
            release_first.set()
            httpd.shutdown()
            httpd.server_close()
            serving.join(2.0)


if __name__ == "__main__":
    unittest.main()
