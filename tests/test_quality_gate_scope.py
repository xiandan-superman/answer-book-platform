from __future__ import annotations

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
