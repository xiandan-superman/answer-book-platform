from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Iterable


def python_dependency_profile(version_info: Iterable[int] | None = None) -> str:
    parts = tuple(version_info or sys.version_info[:2])
    major = int(parts[0]) if parts else 3
    minor = int(parts[1]) if len(parts) > 1 else 0
    if (major, minor) == (3, 9):
        return "py39"
    if (major, minor) >= (3, 11):
        return "py311"
    return "py310"


def dependency_profile_key(
    version_info: Iterable[int] | None = None,
    *,
    platform_name: str | None = None,
) -> str:
    profile = python_dependency_profile(version_info)
    platform_value = str(platform_name or sys.platform).lower()
    return f"{profile}-windows" if platform_value.startswith("win") else profile


def constraint_filename(version_info: Iterable[int] | None = None) -> str:
    profile = python_dependency_profile(version_info)
    return {
        "py39": "constraints-py39.txt",
        "py311": "constraints-py311.txt",
    }.get(profile, "")


def runtime_dependency_files(
    project_root: Path,
    version_info: Iterable[int] | None = None,
    *,
    platform_name: str | None = None,
) -> list[Path]:
    files = [project_root / "requirements.txt"]
    platform_value = str(platform_name or sys.platform).lower()
    if platform_value.startswith("win"):
        files.append(project_root / "requirements-windows.txt")
    constraint = constraint_filename(version_info)
    if constraint:
        files.append(project_root / constraint)
    return [path for path in files if path.is_file()]


def runtime_dependency_fingerprint(
    project_root: Path,
    version_info: Iterable[int] | None = None,
    *,
    platform_name: str | None = None,
) -> str:
    digest = hashlib.sha256()
    for path in runtime_dependency_files(
        project_root,
        version_info,
        platform_name=platform_name,
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def release_dependency_fingerprints(project_root: Path) -> dict[str, str]:
    profiles: dict[str, str] = {}
    for version_info in ((3, 9), (3, 10), (3, 11)):
        for platform_name in ("linux", "win32"):
            key = dependency_profile_key(version_info, platform_name=platform_name)
            profiles[key] = runtime_dependency_fingerprint(
                project_root,
                version_info,
                platform_name=platform_name,
            )
    return profiles
