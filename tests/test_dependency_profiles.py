from __future__ import annotations

from app.dependency_profiles import (
    dependency_profile_key,
    python_dependency_profile,
    runtime_dependency_files,
    runtime_dependency_fingerprint,
    runtime_python_supported,
)


def test_python_versions_select_explicit_dependency_profiles() -> None:
    assert python_dependency_profile((3, 9)) == "unsupported"
    assert python_dependency_profile((3, 10)) == "unsupported"
    assert python_dependency_profile((3, 11)) == "py311"
    assert python_dependency_profile((3, 12)) == "unsupported"
    assert python_dependency_profile((3, 13)) == "unsupported"
    assert python_dependency_profile((3, 14)) == "unsupported"
    assert runtime_python_supported((3, 11)) is True
    assert runtime_python_supported((3, 14)) is False
    assert dependency_profile_key((3, 11), platform_name="win32") == "py311-windows"
    assert dependency_profile_key((3, 11), platform_name="darwin") == "py311-macos"
    assert dependency_profile_key((3, 11), platform_name="linux") == "py311-linux"


def test_runtime_dependency_files_use_version_and_platform_specific_inputs(tmp_path) -> None:
    for name in (
        "requirements.txt",
        "requirements-windows.txt",
        "constraints-py311.txt",
        "constraints-source-windows-py311.txt",
    ):
        (tmp_path / name).write_text(name, encoding="utf-8")

    unsupported = [path.name for path in runtime_dependency_files(tmp_path, (3, 9), platform_name="darwin")]
    windows = [path.name for path in runtime_dependency_files(tmp_path, (3, 11), platform_name="win32")]

    assert unsupported == ["requirements.txt"]
    assert windows == [
        "requirements.txt",
        "requirements-windows.txt",
        "constraints-py311.txt",
        "constraints-source-windows-py311.txt",
    ]


def test_fingerprint_changes_when_selected_constraint_changes(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("example>=1\n", encoding="utf-8")
    constraint = tmp_path / "constraints-py311.txt"
    constraint.write_text("example==1.0\n", encoding="utf-8")
    first = runtime_dependency_fingerprint(tmp_path, (3, 11), platform_name="linux")
    constraint.write_text("example==1.1\n", encoding="utf-8")
    assert runtime_dependency_fingerprint(tmp_path, (3, 11), platform_name="linux") != first


def test_platform_constraint_changes_only_its_selected_fingerprint(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("example>=1\n", encoding="utf-8")
    (tmp_path / "constraints-py311.txt").write_text("example==1.0\n", encoding="utf-8")
    mac_constraint = tmp_path / "constraints-source-macos-py311.txt"
    mac_constraint.write_text("shell==1.0\n", encoding="utf-8")
    mac_before = runtime_dependency_fingerprint(tmp_path, (3, 11), platform_name="darwin")
    linux_before = runtime_dependency_fingerprint(tmp_path, (3, 11), platform_name="linux")

    mac_constraint.write_text("shell==1.1\n", encoding="utf-8")

    assert runtime_dependency_fingerprint(tmp_path, (3, 11), platform_name="darwin") != mac_before
    assert runtime_dependency_fingerprint(tmp_path, (3, 11), platform_name="linux") == linux_before
