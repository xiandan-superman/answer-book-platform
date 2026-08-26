from __future__ import annotations

import json

from scripts import source_launcher, source_launcher_gui


def test_lan_mode_uses_supervisor_and_all_interfaces() -> None:
    command = source_launcher_gui.launcher_command("lan")

    assert command[1].endswith("source_launcher.py")
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert command[command.index("--port") + 1] == "8766"


def test_local_mode_uses_supervisor_and_loopback() -> None:
    command = source_launcher_gui.launcher_command("local")

    assert command[command.index("--host") + 1] == "127.0.0.1"


def test_all_interface_listener_still_uses_loopback_browser_url() -> None:
    assert source_launcher.local_service_url("0.0.0.0", 8766) == "http://127.0.0.1:8766"
    assert source_launcher.local_service_url("127.0.0.1", 8766) == "http://127.0.0.1:8766"


def test_current_lan_mode_reads_server_binding(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"listening_on_lan": True}).encode("utf-8")

    monkeypatch.setattr(source_launcher_gui.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    assert source_launcher_gui.current_lan_mode("http://127.0.0.1:8766") is True


def test_legacy_lan_entries_use_gui_supervisor() -> None:
    windows = (source_launcher_gui.ROOT / "start_platform_lan_windows.bat").read_text(encoding="utf-8")
    macos = (source_launcher_gui.ROOT / "start_platform_lan.command").read_text(encoding="utf-8")

    assert "source_launcher_gui.py --mode lan --autostart" in windows
    assert "source_launcher_gui.py --mode lan --autostart" in macos
    assert "scripts\\start_platform.py" not in windows
    assert "scripts/start_platform.py" not in macos


def test_launcher_page_keeps_mode_choice_simple_and_user_facing() -> None:
    page = (source_launcher_gui.ROOT / "web" / "launcher.html").read_text(encoding="utf-8")

    assert "仅本机使用" in page
    assert "局域网监控" in page
    assert "启动平台" in page
    assert "自动检查依赖" in page
    assert "pip install" not in page
    assert "source_launcher.py" not in page


def test_runtime_declares_native_webview_shell() -> None:
    requirements = (source_launcher_gui.ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "pywebview" in requirements
    assert "pystray" in requirements
    assert "sys_platform == \"win32\" or sys_platform == \"darwin\"" in requirements


def test_window_close_hides_shell_without_stopping_platform() -> None:
    class Window:
        def __init__(self) -> None:
            self.hidden = 0
            self.minimized = 0

        def hide(self) -> None:
            self.hidden += 1

        def minimize(self) -> None:
            self.minimized += 1

    controller = source_launcher_gui.LauncherController(port=8766)
    controller.window = Window()
    controller.tray = object()

    assert controller.request_window_close() is False
    assert controller.window.hidden == 1
    assert controller.window.minimized == 0


def test_shell_without_tray_minimizes_instead_of_exiting() -> None:
    class Window:
        minimized = 0

        def minimize(self) -> None:
            self.minimized += 1

    controller = source_launcher_gui.LauncherController(port=8766)
    controller.window = Window()

    assert controller.request_window_close() is False
    assert controller.window.minimized == 1
