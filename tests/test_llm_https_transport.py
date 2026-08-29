from __future__ import annotations

import gzip
import json
import select
import socket
import socketserver
import ssl
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from app import llm_client, model_diagnostics, runtime_monitor
from app.settings import ProviderConfig

TLS_FIXTURES = Path(__file__).parent / "fixtures" / "tls"
TEST_CA = TLS_FIXTURES / "answer_book_test_ca.pem"
SERVER_CERT = TLS_FIXTURES / "localhost.pem"
SERVER_KEY = TLS_FIXTURES / "localhost.key"
FIXTURE_KEY = "ark-test-https-secret-12345678"
FIXTURE_MODEL = "ark-test-https-model"


def _provider(base_url: str) -> ProviderConfig:
    return ProviderConfig(
        name="ark",
        type="openai_compatible",
        base_url=base_url,
        api_key=FIXTURE_KEY,
        default_model=FIXTURE_MODEL,
        model_options=(FIXTURE_MODEL,),
        allow_custom_model=False,
        model_hint="",
        temperature=0,
        max_tokens=64,
    )


def _clear_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


def _trust_test_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", str(TEST_CA))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")


@contextmanager
def _https_fixture(mode: str = "ok") -> Iterator[ThreadingHTTPServer]:
    response_body = json.dumps(
        {
            "choices": [{"message": {"content": '{"fixture": true}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.server.authorization_headers.append(self.headers.get("Authorization", ""))  # type: ignore[attr-defined]
            if mode == "first_byte":
                time.sleep(1.2)
            if mode == "auth_error":
                body = json.dumps(
                    {
                        "error": "fixture authentication rejected",
                        "api_key": FIXTURE_KEY,
                        "authorization": f"Bearer {FIXTURE_KEY}",
                    }
                ).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            try:
                if mode == "read_idle":
                    self.wfile.write(response_body[:8])
                    self.wfile.flush()
                    time.sleep(1.2)
                    self.wfile.write(response_body[8:])
                elif mode == "hard_timeout":
                    for byte in response_body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.2)
                else:
                    self.wfile.write(response_body)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.authorization_headers = []  # type: ignore[attr-defined]
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=SERVER_CERT, keyfile=SERVER_KEY)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def _chat(base_url: str, *, timeout: int = 3) -> llm_client.LLMResult:
    client = llm_client.OpenAICompatibleClient(_provider(base_url))
    return client.chat_text(
        [{"role": "user", "content": "deterministic HTTPS fixture"}],
        timeout=timeout,
    )


def test_execution_intent_failure_prevents_transport_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked_path = tmp_path / "blocked-execution-ledger"
    blocked_path.mkdir()
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", blocked_path)
    monkeypatch.setenv("MODEL_EXECUTION_LEDGER_WRITE_ATTEMPTS", "1")
    client = llm_client.OpenAICompatibleClient(_provider("https://example.invalid/v1"))
    transport_called = False

    def forbidden_transport(*args, **kwargs):
        nonlocal transport_called
        transport_called = True
        raise AssertionError("transport must not run before durable intent")

    client._urlopen = forbidden_transport

    with pytest.raises(runtime_monitor.ModelExecutionLedgerError, match="无法持久化"):
        client.chat_text([{"role": "user", "content": "local-only test"}], timeout=3)

    assert transport_called is False


def test_https_handler_uses_verified_standard_context_only(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = llm_client._LayeredHTTPSHandler(
        connect_timeout=1,
        first_byte_timeout=2,
        hard_deadline_monotonic=time.monotonic() + 3,
    )
    captured: dict[str, object] = {}

    def fake_do_open(factory: object, request: urllib.request.Request, **kwargs: object) -> object:
        captured.update({"factory": factory, "request": request, **kwargs})
        return object()

    monkeypatch.setattr(handler, "do_open", fake_do_open)
    request = urllib.request.Request("https://localhost/example")

    handler.https_open(request)

    context = captured["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context is handler._context
    assert "check_hostname" not in captured
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_trusted_ca_and_matching_hostname_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxies(monkeypatch)
    _trust_test_ca(monkeypatch)
    with _https_fixture() as server:
        result = _chat(f"https://localhost:{server.server_port}/api/v3")

        assert json.loads(result.content) == {"fixture": True}
        assert server.authorization_headers == [f"Bearer {FIXTURE_KEY}"]  # type: ignore[attr-defined]


def test_trusted_ca_with_mismatched_hostname_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxies(monkeypatch)
    _trust_test_ca(monkeypatch)
    with _https_fixture() as server:
        with pytest.raises(llm_client.LLMError) as captured:
            _chat(f"https://127.0.0.1:{server.server_port}/api/v3")

    message = str(captured.value)
    assert "CERTIFICATE_VERIFY_FAILED" in message
    assert "IP address mismatch" in message or "not valid for" in message
    assert FIXTURE_KEY not in message


def test_unknown_ca_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxies(monkeypatch)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    with _https_fixture() as server:
        with pytest.raises(llm_client.LLMError) as captured:
            _chat(f"https://localhost:{server.server_port}/api/v3")

    message = str(captured.value)
    assert "CERTIFICATE_VERIFY_FAILED" in message
    assert "self-signed certificate" in message or "unable to get local issuer certificate" in message
    assert FIXTURE_KEY not in message


@pytest.mark.parametrize(
    ("mode", "hard_timeout", "expected_phase"),
    (
        ("first_byte", 3, "first_byte"),
        ("read_idle", 3, "read_idle"),
        ("hard_timeout", 2, "hard_timeout"),
    ),
)
def test_https_preserves_layered_timeouts(
    mode: str,
    hard_timeout: int,
    expected_phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxies(monkeypatch)
    _trust_test_ca(monkeypatch)
    monkeypatch.setenv("PRACTICE_MODEL_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("PRACTICE_MODEL_FIRST_BYTE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("PRACTICE_MODEL_READ_IDLE_TIMEOUT_SECONDS", "1")
    with _https_fixture(mode) as server:
        started = time.monotonic()
        with pytest.raises(llm_client.LLMError) as captured:
            _chat(f"https://localhost:{server.server_port}/api/v3", timeout=hard_timeout)

    assert captured.value.transport_phase == expected_phase
    assert time.monotonic() - started < hard_timeout + 2


class _ConnectProxy(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        request_line = self.rfile.readline().decode("ascii", errors="replace").strip()
        while self.rfile.readline().strip():
            pass
        method, target, _version = request_line.split(" ", 2)
        if method != "CONNECT":
            self.wfile.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return
        host, raw_port = target.rsplit(":", 1)
        self.server.connect_targets.append(target)  # type: ignore[attr-defined]
        with socket.create_connection((host, int(raw_port)), timeout=3) as upstream:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            peers = (self.connection, upstream)
            while True:
                readable, _, _ = select.select(peers, (), (), 3)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(64 * 1024)
                    if not data:
                        return
                    target_socket = upstream if source is self.connection else self.connection
                    target_socket.sendall(data)


@contextmanager
def _connect_proxy() -> Iterator[socketserver.ThreadingTCPServer]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ConnectProxy)
    server.daemon_threads = True
    server.connect_targets = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_https_transport_preserves_connect_proxy_support(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxies(monkeypatch)
    _trust_test_ca(monkeypatch)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: False)
    with _https_fixture() as target, _connect_proxy() as proxy:
        proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
        monkeypatch.setenv("HTTPS_PROXY", proxy_url)
        monkeypatch.setenv("https_proxy", proxy_url)

        result = _chat(f"https://localhost:{target.server_port}/api/v3")

    assert json.loads(result.content) == {"fixture": True}
    assert proxy.connect_targets == [f"localhost:{target.server_port}"]  # type: ignore[attr-defined]


def test_https_error_and_diagnostics_redact_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxies(monkeypatch)
    _trust_test_ca(monkeypatch)
    monkeypatch.setattr(model_diagnostics, "MODEL_DIAGNOSTICS_DIR", tmp_path / "diagnostics")
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "model_calls.jsonl")
    with _https_fixture("auth_error") as server:
        with pytest.raises(llm_client.LLMError) as captured:
            _chat(f"https://localhost:{server.server_port}/api/v3")

    assert captured.value.status_code == 401
    assert FIXTURE_KEY not in str(captured.value)
    trace_text = "\n".join(
        gzip.open(path, "rt", encoding="utf-8").read()
        for path in (tmp_path / "diagnostics").rglob("*.json.gz")
    )
    ledger_text = (tmp_path / "model_calls.jsonl").read_text(encoding="utf-8")
    assert FIXTURE_KEY not in trace_text
    assert FIXTURE_KEY not in ledger_text
    assert "Provider HTTP 401" in str(captured.value)
