#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _run_frozen_python_helper() -> bool:
    """Let frozen subprocess calls reuse the bundled Python runtime."""
    if not getattr(sys, "frozen", False) or len(sys.argv) < 2:
        return False
    if sys.argv[1] == "-c" and len(sys.argv) >= 3:
        namespace = {"__name__": "__main__", "__file__": "<string>"}
        exec(sys.argv[2], namespace, namespace)
        return True
    candidate = Path(sys.argv[1])
    if candidate.suffix == ".py" and candidate.exists():
        sys.argv = sys.argv[1:]
        runpy.run_path(str(candidate), run_name="__main__")
        return True
    return False


def _available_port(preferred: int) -> int:
    for port in range(preferred, preferred + 30):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            probe.close()
        return port
    raise RuntimeError("未找到可用的本地服务端口")


def _wait_until_ready(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{url}/api/version", timeout=1):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError("桌面服务启动超时")


def main() -> int:
    if _run_frozen_python_helper():
        return 0

    os.environ.setdefault("ANSWER_BOOK_DESKTOP_APP", "1")
    os.environ.setdefault("ANSWER_BOOK_DISABLE_AUTO_PACKAGE_INSTALL", "1")

    from app.lan_access import ensure_lan_access_config
    from app.server import run

    preferred_port = int(os.environ.get("ANSWER_BOOK_PORT") or "8766")
    port = _available_port(preferred_port)
    local_url = f"http://127.0.0.1:{port}"
    ensure_lan_access_config()

    server_thread = threading.Thread(
        target=run,
        kwargs={"host": "0.0.0.0", "port": port},
        name="answer-book-platform-server",
        daemon=True,
    )
    server_thread.start()
    _wait_until_ready(local_url)

    try:
        import webview
    except ImportError:
        webbrowser.open(local_url)
        server_thread.join()
        return 0

    window = webview.create_window(
        "真题解析平台",
        local_url,
        width=1440,
        height=920,
        min_size=(1100, 720),
        text_select=True,
    )
    webview.start(private_mode=False)
    if window:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
