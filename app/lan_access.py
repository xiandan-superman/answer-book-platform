from __future__ import annotations

import json
import os
import secrets
import socket
from pathlib import Path
from typing import Any

from .paths import DATA_ROOT, ensure_project_dirs


LAN_CONFIG_PATH = DATA_ROOT / "runtime" / "lan_access.json"
DEFAULT_USERNAME = "monitor"


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


def lan_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            address = str(item[4][0])
            if address and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        address = str(probe.getsockname()[0])
        probe.close()
        if address and not address.startswith("127."):
            addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def lan_access_info(port: int, include_secret: bool = False) -> dict[str, Any]:
    username, password = lan_credentials()
    addresses = lan_ipv4_addresses()
    result: dict[str, Any] = {
        "enabled": lan_access_enabled(),
        "username": username,
        "addresses": addresses,
        "urls": [f"http://{address}:{port}" for address in addresses],
        "port": int(port),
        "config_path": str(LAN_CONFIG_PATH),
    }
    if include_secret:
        result["password"] = password
    return result
