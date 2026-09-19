"""Application-owned outbound routing for known service endpoints."""
from __future__ import annotations

import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DIRECT_FIRST_HOSTS = frozenset({
    "answer-book-smart-router.answer-book-smart-router.workers.dev",
    "wawapii.com",
    "api-top.com",
    "ark.cn-beijing.volces.com",
    "api.deepseek.com",
    "dashscope.aliyuncs.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "github.com",
    "release-assets.githubusercontent.com",
})
_DEFAULT_URLOPEN = urllib.request.urlopen


def _host(value: Any) -> str:
    url = value.full_url if isinstance(value, urllib.request.Request) else str(value or "")
    return str(urllib.parse.urlparse(url).hostname or "").strip().lower()


def _configured_smart_router_host() -> str:
    return _host(os.environ.get("CLOUDFLARE_SMART_ROUTER_URL", ""))


def prefers_direct(value: Any) -> bool:
    host = _host(value)
    return bool(host and (host in DIRECT_FIRST_HOSTS or host == _configured_smart_router_host()))


def is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError, socket.timeout, ssl.SSLError, OSError))


def open_url(
    value: Any,
    *,
    timeout: float,
    system_urlopen: Any | None = None,
) -> Any:
    """Open direct first for selected hosts, then use the system proxy once."""
    system_urlopen = system_urlopen or urllib.request.urlopen
    # Preserve injectable transports used by tests and offline tooling.
    if system_urlopen is not _DEFAULT_URLOPEN:
        return system_urlopen(value, timeout=timeout)
    if not prefers_direct(value):
        return system_urlopen(value, timeout=timeout)
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    direct_timeout = max(0.1, min(8.0, float(timeout) * 0.5))
    try:
        return direct.open(value, timeout=direct_timeout)
    except Exception as exc:
        if not is_connection_error(exc):
            raise
        remaining = float(timeout) - (time.monotonic() - started)
        if remaining <= 0:
            raise
        return system_urlopen(value, timeout=max(0.1, remaining))
