from __future__ import annotations

import sys
import threading

import desktop_launcher
from app import server


def test_desktop_server_helper_runs_server_in_main_process_thread(monkeypatch) -> None:
    calls = []

    def fake_run(host: str, port: int) -> None:
        calls.append((host, port, threading.current_thread() is threading.main_thread()))

    monkeypatch.setattr(server, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["desktop_launcher.py", "--desktop-server", "127.0.0.1", "8799"])

    assert desktop_launcher._run_frozen_python_helper() is True
    assert calls == [("127.0.0.1", 8799, True)]
