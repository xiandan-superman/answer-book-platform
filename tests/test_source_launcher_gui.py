from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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

    assert "windows_launcher_bootstrap.py --mode lan --autostart" in windows
    assert "source_launcher_gui.py --mode lan --autostart" in macos
    assert "scripts\\start_platform.py" not in windows
    assert "scripts/start_platform.py" not in macos


def test_windows_entries_use_diagnostic_bootstrap_and_visible_alias() -> None:
    root = source_launcher_gui.ROOT
    standard = (root / "start_platform_windows.bat").read_text(encoding="utf-8")
    visible = (root / "启动平台.bat").read_text(encoding="utf-8")

    assert "windows_launcher_bootstrap.py" in standard
    assert "source_launcher_gui.py" not in standard
    assert "start_platform_windows.bat" in visible
    assert "%*" in standard
    assert "%*" in visible


def test_launcher_page_keeps_mode_choice_simple_and_user_facing() -> None:
    page = (source_launcher_gui.ROOT / "web" / "launcher.html").read_text(encoding="utf-8")

    assert "仅本机使用" in page
    assert "局域网监控" in page
    assert "启动平台" in page
    assert "自动检查依赖" in page
    assert "pip install" not in page
    assert "source_launcher.py" not in page
    assert '<img class="logo" src="/app-icon.png"' in page
    assert '<div class="logo">真</div>' not in page
    assert "当前安装：" in page
    assert "pending_components" in page
    assert 'role="progressbar"' in page
    assert "复制日志位置" in page
    assert "重新尝试" in page


def test_launcher_snapshot_exposes_only_matching_startup_progress(monkeypatch, tmp_path: Path) -> None:
    controller = source_launcher_gui.LauncherController(port=8766)
    controller.status = "starting"
    controller.run_id = "current-run"
    progress_path = tmp_path / "startup-progress.json"
    monkeypatch.setattr(source_launcher_gui, "service_ready", lambda _url: False)
    monkeypatch.setattr(
        source_launcher_gui.LauncherController,
        "progress_path",
        property(lambda _self: progress_path),
    )
    progress_path.write_text(json.dumps({
        "run_id": "current-run",
        "status": "downloading_dependencies",
        "percent": 52,
        "message": "正在准备 Pillow（4/16）",
        "current_component": "Pillow",
        "current_index": 4,
        "completed_count": 3,
        "total_count": 16,
        "progress_mode": "indeterminate",
        "private_output": "must not leak",
    }), encoding="utf-8")

    snapshot = controller.snapshot()

    assert snapshot["status"] == "starting"
    assert snapshot["stage"] == "downloading_dependencies"
    assert snapshot["current_component"] == "Pillow"
    assert snapshot["current_index"] == 4
    assert "private_output" not in snapshot

    progress_path.write_text(json.dumps({"run_id": "stale-run", "message": "stale"}), encoding="utf-8")
    assert controller.snapshot()["message"] != "stale"


def test_launcher_uses_the_product_icon_in_every_visible_shell() -> None:
    source = (source_launcher_gui.ROOT / "scripts" / "source_launcher_gui.py").read_text(encoding="utf-8")

    assert source_launcher_gui.APP_ICON.is_file()
    assert 'Image.open(APP_ICON)' in source
    assert 'icon=str(APP_WINDOW_ICON)' in source
    assert 'window.iconphoto(True, self.icon)' in source


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


def test_launcher_failure_is_written_to_bootstrap_log(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "launcher-bootstrap.log"
    monkeypatch.setenv("ANSWER_BOOK_LAUNCHER_BOOTSTRAP_LOG", str(log_path))

    try:
        raise RuntimeError("simulated early launcher failure")
    except RuntimeError:
        source_launcher_gui.record_launcher_failure("test")

    content = log_path.read_text(encoding="utf-8")
    assert "launcher_failure_stage=test" in content
    assert "simulated early launcher failure" in content


def test_runtime_handoff_preserves_windows_paths_with_spaces(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "Answer Book Platform" / "python-env" / "Scripts" / "pythonw.exe"
    calls = []
    monkeypatch.setattr(source_launcher_gui.sys, "platform", "win32")
    monkeypatch.setattr(source_launcher_gui.sys, "argv", ["source_launcher_gui.py", "--mode", "local"])
    monkeypatch.setattr(source_launcher_gui.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(
        source_launcher_gui.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    try:
        source_launcher_gui.run_with_shell_runtime(runtime)
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("runtime handoff must exit the bootstrap interpreter")

    assert calls[0][0][0] == str(runtime)
    assert calls[0][0][1].endswith("source_launcher_gui.py")
    assert calls[0][0][-2:] == ["--mode", "local"]
    assert calls[0][1]["creationflags"] == source_launcher_gui.subprocess.CREATE_NO_WINDOW
