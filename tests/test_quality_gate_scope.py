from __future__ import annotations

import sys

from scripts import run_quality_gates
from scripts.run_quality_gates import (
    FROZEN_APP_PYTHON_PATHS,
    FULL_COVERAGE_MIN_PERCENT,
    ROOT,
    platform_python_files,
)


def test_platform_quality_gate_excludes_frozen_desktop_app_paths() -> None:
    files = set(platform_python_files(ROOT))

    assert FROZEN_APP_PYTHON_PATHS.isdisjoint(files)
    assert "app/server.py" in files
    assert "scripts/run_quality_gates.py" in files
    assert FULL_COVERAGE_MIN_PERCENT == 60


def test_complete_gate_runs_the_suite_only_once_under_coverage(monkeypatch, tmp_path) -> None:
    commands = []

    def fake_run_step(name, cmd):
        commands.append((name, cmd))
        return {"name": name, "ok": True, "returncode": 0, "stdout": "", "stderr": "", "cmd": cmd}

    monkeypatch.setattr(run_quality_gates, "ROOT", tmp_path)
    monkeypatch.setattr(run_quality_gates, "platform_python_files", lambda: [])
    monkeypatch.setattr(run_quality_gates, "_module_available", lambda _name: True)
    monkeypatch.setattr(run_quality_gates, "run_step", fake_run_step)
    monkeypatch.setattr(sys, "argv", ["run_quality_gates.py", "--full"])

    assert run_quality_gates.main() == 0
    names = [name for name, _cmd in commands]
    assert "pytest" not in names
    assert names.count("coverage") == 1
    assert commands[names.index("coverage")][1][-3:] == ["-m", "pytest", "-q"]
