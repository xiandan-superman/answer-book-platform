from __future__ import annotations

import io
import json
import sys
import threading
from types import SimpleNamespace

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


def test_desktop_app_uses_a_port_range_separate_from_legacy_source() -> None:
    assert desktop_launcher.DESKTOP_DEFAULT_PORT == 18766
    assert desktop_launcher.DESKTOP_DEFAULT_PORT != 8766


def test_desktop_server_helper_starts_parent_watchdog(monkeypatch) -> None:
    calls = []
    watchdogs = []

    monkeypatch.setattr(server, "run", lambda host, port: calls.append((host, port)))
    monkeypatch.setattr(desktop_launcher, "_start_parent_watchdog", watchdogs.append)
    monkeypatch.setattr(
        sys,
        "argv",
        ["desktop_launcher.py", "--desktop-server", "0.0.0.0", "18766", "43210"],
    )

    assert desktop_launcher._run_frozen_python_helper() is True
    assert watchdogs == [43210]
    assert calls == [("0.0.0.0", 18766)]


def test_readiness_rejects_a_different_local_backend(monkeypatch) -> None:
    responses = iter([
        {"desktop_launch_id": "legacy-or-other-instance"},
        {"desktop_launch_id": "expected-instance"},
    ])

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(*_args, **_kwargs):
        return Response(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr(desktop_launcher, "urlopen", fake_urlopen)
    monkeypatch.setattr(desktop_launcher.time, "sleep", lambda _seconds: None)
    process = SimpleNamespace(poll=lambda: None, returncode=None)

    desktop_launcher._wait_until_ready(
        "http://127.0.0.1:18766",
        process,
        expected_launch_id="expected-instance",
        timeout=1,
    )


def test_desktop_window_enables_downloads_and_exposes_controlled_word_bridge() -> None:
    captured = {}
    fake_window = object()

    class FakeWebview:
        settings = {"ALLOW_DOWNLOADS": False}
        FileDialog = SimpleNamespace(SAVE=30)

        @staticmethod
        def create_window(title, url, **kwargs):
            captured.update({"title": title, "url": url, **kwargs})
            return fake_window

    window = desktop_launcher._create_desktop_window(FakeWebview, "http://127.0.0.1:18766")

    assert window is fake_window
    assert FakeWebview.settings["ALLOW_DOWNLOADS"] is True
    assert captured["url"] == "http://127.0.0.1:18766/?desktop_app=1"
    assert captured["js_api"].__class__.__name__ == "DesktopWordSaveBridge"
    assert captured["js_api"]._window is fake_window


def test_desktop_shell_starts_with_the_bundled_product_icon() -> None:
    source = (desktop_launcher.ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    spec = (desktop_launcher.ROOT / "build" / "answer_book_platform.spec").read_text(encoding="utf-8")

    assert desktop_launcher.APP_ICON.is_file()
    assert 'webview.start(private_mode=False, icon=str(APP_WINDOW_ICON))' in source
    assert '"app-icon.ico"' in spec
    assert '"app-icon-transparent.png"' in spec
