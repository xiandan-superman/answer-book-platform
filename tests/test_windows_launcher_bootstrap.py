from __future__ import annotations

from pathlib import Path

from scripts import windows_launcher_bootstrap


class FakeProcess:
    def __init__(self, return_codes: list[int | None]) -> None:
        self.return_codes = iter(return_codes)

    def poll(self) -> int | None:
        return next(self.return_codes)


def test_bootstrap_waits_for_visible_launcher_health(tmp_path: Path) -> None:
    commands = []
    readiness = iter([False, True])

    result = windows_launcher_bootstrap.supervise_launcher(
        ["--mode", "local"],
        launcher_port=18876,
        log_path=tmp_path / "launcher.log",
        health_check=lambda _port: next(readiness),
        popen_factory=lambda command, **kwargs: commands.append((command, kwargs)) or FakeProcess([None]),
        sleep=lambda _seconds: None,
        error_reporter=lambda _message: None,
    )

    assert result == 0
    assert commands[0][0][1].endswith("source_launcher_gui.py")
    assert commands[0][0][-2:] == ["--mode", "local"]
    assert commands[0][1]["env"]["PYTHONUTF8"] == "1"
    assert commands[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "launcher_health=ready" in (tmp_path / "launcher.log").read_text(encoding="utf-8")


def test_bootstrap_reports_hidden_launcher_failure(tmp_path: Path) -> None:
    errors = []
    result = windows_launcher_bootstrap.supervise_launcher(
        [],
        launcher_port=18876,
        log_path=tmp_path / "launcher.log",
        health_check=lambda _port: False,
        popen_factory=lambda *_args, **_kwargs: FakeProcess([7]),
        sleep=lambda _seconds: None,
        error_reporter=errors.append,
    )

    assert result == 7
    assert errors and "诊断日志" in errors[0]
    assert "launcher_exit=7" in (tmp_path / "launcher.log").read_text(encoding="utf-8")


def test_bootstrap_log_uses_windows_user_data(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert windows_launcher_bootstrap.bootstrap_log_path() == (
        tmp_path / "Answer Book Platform" / "runtime" / "launcher-bootstrap.log"
    )


def test_bootstrap_rejects_python_314_before_starting_gui(monkeypatch) -> None:
    errors = []
    monkeypatch.setattr(windows_launcher_bootstrap.sys, "version_info", (3, 14, 6))
    monkeypatch.setattr(windows_launcher_bootstrap, "show_bootstrap_error", errors.append)
    monkeypatch.setattr(
        windows_launcher_bootstrap,
        "supervise_launcher",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GUI must not start")),
    )

    assert windows_launcher_bootstrap.main([]) == 2
    assert errors and "Python 3.11" in errors[0]
    assert "Python 3.14.6" in errors[0]
