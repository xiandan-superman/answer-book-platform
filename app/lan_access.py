from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from ipaddress import IPv4Address, ip_address, ip_network
from typing import Any

from .paths import DATA_ROOT, ensure_project_dirs

LAN_CONFIG_PATH = DATA_ROOT / "runtime" / "lan_access.json"
DEFAULT_USERNAME = "monitor"
TAILSCALE_IPV4_NETWORK = ip_network("100.64.0.0/10")
_TAILSCALE_CACHE_SECONDS = 60.0
_tailscale_cache: tuple[float, list[str]] = (0.0, [])


def _read_config() -> dict[str, Any]:
    if not LAN_CONFIG_PATH.exists():
        return {}
    try:
        value = json.loads(LAN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def ensure_lan_access_config() -> dict[str, Any]:
    ensure_project_dirs()
    LAN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = _read_config()
    configured_password = str(os.environ.get("ANSWER_BOOK_LAN_PASSWORD") or "").strip()
    username = str(current.get("username") or DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    password = configured_password or str(current.get("password") or "").strip()
    if not password:
        password = secrets.token_urlsafe(12)
    value = {
        "enabled": bool(current.get("enabled", True)),
        "username": username,
        "password": password,
    }
    if value != current:
        LAN_CONFIG_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            LAN_CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
    return value


def lan_credentials() -> tuple[str, str]:
    config = ensure_lan_access_config()
    return str(config["username"]), str(config["password"])


def lan_access_enabled() -> bool:
    return bool(ensure_lan_access_config().get("enabled", True))


def _valid_ipv4(value: str) -> str:
    try:
        address = ip_address(str(value).strip())
    except ValueError:
        return ""
    if not isinstance(address, IPv4Address) or address.is_loopback or address.is_unspecified:
        return ""
    return str(address)


def tailscale_ipv4_addresses() -> list[str]:
    """Return the local Tailscale IPv4 without requiring an optional Python package."""

    global _tailscale_cache
    cached_at, cached_addresses = _tailscale_cache
    now = time.monotonic()
    if now - cached_at < _TAILSCALE_CACHE_SECONDS:
        return list(cached_addresses)
    executable = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if not executable:
        _tailscale_cache = (now, [])
        return []
    run_options: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": 2,
        "check": False,
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_options["creationflags"] = subprocess.CREATE_NO_WINDOW
    addresses: list[str] = []
    try:
        completed = subprocess.run([executable, "ip", "-4"], **run_options)
    except (OSError, subprocess.SubprocessError):
        pass
    else:
        if completed.returncode == 0:
            for line in completed.stdout.splitlines():
                address = _valid_ipv4(line)
                if address and ip_address(address) in TAILSCALE_IPV4_NETWORK:
                    addresses.append(address)
    result = sorted(set(addresses), key=lambda value: int(ip_address(value)))
    _tailscale_cache = (now, result)
    return list(result)


def lan_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            address = _valid_ipv4(str(item[4][0]))
            if address:
                addresses.add(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        address = _valid_ipv4(str(probe.getsockname()[0]))
        probe.close()
        if address:
            addresses.add(address)
    except OSError:
        pass
    tailscale_addresses = tailscale_ipv4_addresses()
    addresses.update(tailscale_addresses)
    # Tailscale is the preferred route for cross-network beta diagnostics.
    return tailscale_addresses + sorted(addresses.difference(tailscale_addresses))


def lan_access_info(port: int, include_secret: bool = False, *, bind_host: str = "") -> dict[str, Any]:
    normalized_host = str(bind_host or "").strip().lower()
    listening_on_lan = normalized_host not in {"", "127.0.0.1", "::1", "localhost"}
    if not listening_on_lan:
        return {
            "enabled": False,
            "listening_on_lan": False,
            "addresses": [],
            "urls": [],
            "port": int(port),
            "reason": "当前服务仅允许本机访问；如需局域网监控，请使用局域网启动入口。",
            "transport_security": "local_only",
        }
    username, password = lan_credentials()
    addresses = lan_ipv4_addresses()
    tailscale_addresses = [
        address for address in addresses if ip_address(address) in TAILSCALE_IPV4_NETWORK
    ]
    result: dict[str, Any] = {
        "enabled": lan_access_enabled(),
        "listening_on_lan": True,
        "username": username,
        "addresses": addresses,
        "urls": [f"http://{address}:{port}" for address in addresses],
        "tailscale_addresses": tailscale_addresses,
        "tailscale_urls": [f"http://{address}:{port}" for address in tailscale_addresses],
        "port": int(port),
        "transport_security": "http_basic",
        "warning": "请仅在可信局域网或 Tailscale 私网中使用；远程接口受监控账号和密码保护。",
    }
    if include_secret and result["enabled"]:
        result["password"] = password
    return result
