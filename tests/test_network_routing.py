from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from app import network_routing


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _Opener:
    def __init__(self, result):
        self.result = result

    def open(self, _request, timeout):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_known_platform_routes_prefer_direct(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://custom-router.example.test")

    assert network_routing.prefers_direct("https://wawapii.com/v1/models") is True
    assert network_routing.prefers_direct("https://raw.githubusercontent.com/org/repo/main/feed.json") is True
    assert network_routing.prefers_direct("https://custom-router.example.test/v1/reservations") is True
    assert network_routing.prefers_direct("https://unrelated.example.test") is False


def test_direct_connection_failure_falls_back_to_system_proxy(monkeypatch) -> None:
    calls: list[str] = []

    def system_open(_request, timeout):
        calls.append(f"system:{timeout}")
        return _Response(b"proxy")

    monkeypatch.setattr(network_routing, "_DEFAULT_URLOPEN", system_open)
    monkeypatch.setattr(network_routing.urllib.request, "urlopen", system_open)
    monkeypatch.setattr(
        network_routing.urllib.request,
        "build_opener",
        lambda *_handlers: _Opener(urllib.error.URLError(ConnectionResetError("reset"))),
    )

    with network_routing.open_url("https://api.deepseek.com/models", timeout=12) as response:
        assert response.read() == b"proxy"
    assert len(calls) == 1
    assert calls[0].startswith("system:")
    assert 0 < float(calls[0].split(":", 1)[1]) <= 12


def test_http_response_never_switches_to_proxy(monkeypatch) -> None:
    calls: list[str] = []

    def system_open(_request, timeout):
        calls.append("system")
        return _Response(b"proxy")

    error = urllib.error.HTTPError(
        "https://api.deepseek.com/models", 401, "unauthorized", {}, io.BytesIO(b"{}")
    )
    monkeypatch.setattr(network_routing, "_DEFAULT_URLOPEN", system_open)
    monkeypatch.setattr(network_routing.urllib.request, "urlopen", system_open)
    monkeypatch.setattr(network_routing.urllib.request, "build_opener", lambda *_handlers: _Opener(error))

    with pytest.raises(urllib.error.HTTPError) as caught:
        network_routing.open_url("https://api.deepseek.com/models", timeout=12)
    assert caught.value.code == 401
    assert calls == []
