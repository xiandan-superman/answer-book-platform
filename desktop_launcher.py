#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _run_frozen_python_helper() -> bool:
    """Let frozen subprocess calls reuse the bundled Python runtime."""
    if len(sys.argv) >= 4 and sys.argv[1] == "--desktop-server":
        from app.server import run

        run(sys.argv[2], int(sys.argv[3]))
        return True
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
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("未找到可用的本地服务端口")


def _wait_until_ready(url: str, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{url}/api/version", timeout=1):
                return
        except OSError:
            if process.poll() is not None:
                raise RuntimeError(f"桌面服务启动失败，退出码 {process.returncode}")
            time.sleep(0.15)
    raise RuntimeError("桌面服务启动超时")


def main() -> int:
    if _run_frozen_python_helper():
        return 0
    os.environ.setdefault("ANSWER_BOOK_DESKTOP_APP", "1")
    os.environ.setdefault("ANSWER_BOOK_DISABLE_AUTO_PACKAGE_INSTALL", "1")

    from app.lan_access import ensure_lan_access_config

    preferred_port = int(os.environ.get("ANSWER_BOOK_PORT") or "8766")
    port = _available_port(preferred_port)
    local_url = f"http://127.0.0.1:{port}"
    ensure_lan_access_config()
    server_process = subprocess.Popen(
        [sys.executable, "--desktop-server", "0.0.0.0", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_until_ready(local_url, server_process)
        try:
            import webview
        except ImportError:
            webbrowser.open(local_url)
            server_process.wait()
            return 0
        webview.create_window(
            "真题解析与生题平台",
            local_url,
            width=1440,
            height=920,
            min_size=(1100, 720),
            text_select=True,
        )
        webview.start(private_mode=False)
        return 0
    finally:
        if server_process.poll() is None:
            server_process.terminate()
            try:
                server_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server_process.kill()
                server_process.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
