from __future__ import annotations

from app.dependency_profiles import (
    dependency_profile_key,
    python_dependency_profile,
    runtime_dependency_files,
    runtime_dependency_fingerprint,
)


def test_python_versions_select_explicit_dependency_profiles() -> None:
    assert python_dependency_profile((3, 9)) == "py39"
    assert python_dependency_profile((3, 10)) == "py310"
    assert python_dependency_profile((3, 11)) == "py311"
    assert python_dependency_profile((3, 13)) == "py311"
    assert dependency_profile_key((3, 11), platform_name="win32") == "py311-windows"


def test_runtime_dependency_files_use_version_and_platform_specific_inputs(tmp_path) -> None:
    for name in ("requirements.txt", "requirements-windows.txt", "constraints-py39.txt", "constraints-py311.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    py39 = [path.name for path in runtime_dependency_files(tmp_path, (3, 9), platform_name="darwin")]
    windows = [path.name for path in runtime_dependency_files(tmp_path, (3, 11), platform_name="win32")]

    assert py39 == ["requirements.txt", "constraints-py39.txt"]
    assert windows == ["requirements.txt", "requirements-windows.txt", "constraints-py311.txt"]


def test_fingerprint_changes_when_selected_constraint_changes(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("example>=1\n", encoding="utf-8")
    constraint = tmp_path / "constraints-py39.txt"
    constraint.write_text("example==1.0\n", encoding="utf-8")
    first = runtime_dependency_fingerprint(tmp_path, (3, 9), platform_name="linux")
    constraint.write_text("example==1.1\n", encoding="utf-8")
    assert runtime_dependency_fingerprint(tmp_path, (3, 9), platform_name="linux") != first
