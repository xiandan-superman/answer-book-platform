#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import runpy
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
APP_ICON = ROOT / "assets" / "app-icon" / "app-icon-transparent.png"
APP_WINDOW_ICON = (
    ROOT / "assets" / "app-icon" / "app-icon.ico"
    if sys.platform == "win32"
    else ROOT / "assets" / "app-icon" / "app-icon.icns"
    if sys.platform == "darwin"
    else APP_ICON
)

# The legacy standalone source project has always used 8766.  The installed
# desktop application owns a separate range so both products can run at once
# without one browser/window silently talking to the other's backend.
DESKTOP_DEFAULT_PORT = 18766


def _parent_process_alive(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return True
    if sys.platform != "win32":
        return os.getppid() == parent_pid
    try:
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return True


def _start_parent_watchdog(parent_pid: int) -> None:
    """Ensure a crashed/force-closed desktop window cannot leave a backend."""
    if parent_pid <= 0:
        return

    def watch() -> None:
        while _parent_process_alive(parent_pid):
            time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=watch, name="desktop-parent-watchdog", daemon=True).start()


def _run_frozen_python_helper() -> bool:
    """Let frozen subprocess calls reuse the bundled Python runtime."""
    if len(sys.argv) >= 4 and sys.argv[1] == "--desktop-server":
        from app.server import run

        parent_pid = int(sys.argv[4]) if len(sys.argv) >= 5 else 0
        _start_parent_watchdog(parent_pid)
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


def _wait_until_ready(
    url: str,
    process: subprocess.Popen,
    *,
    expected_launch_id: str,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{url}/api/version", timeout=1) as response:
                payload = json.load(response)
            if str(payload.get("desktop_launch_id") or "") == expected_launch_id:
                return
        except (AttributeError, OSError, ValueError, TypeError):
            pass
        finally:
            if process.poll() is not None:
                raise RuntimeError(f"桌面服务启动失败，退出码 {process.returncode}")
        time.sleep(0.15)
    raise RuntimeError("桌面服务启动超时")


def _create_desktop_window(webview: object, local_url: str) -> object:
    from app.desktop_word_save import DesktopWordSaveBridge

    # Keep ordinary pywebview downloads available for compatibility, but Word
    # exports use the native bridge below and never infer save success from a
    # browser download event.
    webview.settings["ALLOW_DOWNLOADS"] = True
    file_dialog = getattr(webview, "FileDialog", None)
    save_dialog_type = getattr(file_dialog, "SAVE", None)
    if save_dialog_type is None:
        save_dialog_type = webview.SAVE_DIALOG
    bridge = DesktopWordSaveBridge(save_dialog_type=int(save_dialog_type))
    desktop_url = f"{local_url.rstrip('/')}/?desktop_app=1"
    window = webview.create_window(
        "真题解析与生题平台",
        desktop_url,
        width=1440,
        height=920,
        min_size=(1100, 720),
        text_select=True,
        js_api=bridge,
    )
    bridge.bind_window(window)
    return window


def main() -> int:
    if _run_frozen_python_helper():
        return 0
    os.environ.setdefault("ANSWER_BOOK_DESKTOP_APP", "1")
    os.environ.setdefault("ANSWER_BOOK_DISABLE_AUTO_PACKAGE_INSTALL", "1")

    from app.lan_access import ensure_lan_access_config

    preferred_port = int(os.environ.get("ANSWER_BOOK_PORT") or str(DESKTOP_DEFAULT_PORT))
    port = _available_port(preferred_port)
    local_url = f"http://127.0.0.1:{port}"
    launch_id = secrets.token_urlsafe(24)
    child_environment = os.environ.copy()
    child_environment["ANSWER_BOOK_DESKTOP_LAUNCH_ID"] = launch_id
    ensure_lan_access_config()
    server_process = subprocess.Popen(
        [sys.executable, "--desktop-server", "0.0.0.0", str(port), str(os.getpid())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_environment,
    )
    try:
        _wait_until_ready(local_url, server_process, expected_launch_id=launch_id)
        try:
            import webview
        except ImportError:
            webbrowser.open(local_url)
            server_process.wait()
            return 0
        _create_desktop_window(webview, local_url)
        webview.start(private_mode=False, icon=str(APP_WINDOW_ICON))
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
