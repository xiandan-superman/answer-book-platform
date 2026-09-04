from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Iterable

from .dependency_profiles import dependency_profile_key, runtime_dependency_files

_PIN_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;#]+)")


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def recommended_dependency_versions(
    project_root: Path,
    version_info: Iterable[int] | None = None,
    *,
    platform_name: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    selected = runtime_dependency_files(
        project_root,
        version_info,
        platform_name=platform_name,
    )
    versions: dict[str, str] = {}
    constraint_files: list[str] = []
    for path in selected:
        if not path.name.startswith("constraints-"):
            continue
        constraint_files.append(path.name)
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _PIN_RE.match(line)
            if match:
                versions[_canonical_name(match.group(1))] = match.group(2)
    return versions, constraint_files


def dependency_version_report(
    project_root: Path,
    version_info: Iterable[int] | None = None,
    *,
    platform_name: str | None = None,
) -> dict[str, object]:
    current_version = tuple(version_info or sys.version_info[:2])
    current_platform = str(platform_name or sys.platform)
    recommended, constraint_files = recommended_dependency_versions(
        project_root,
        current_version,
        platform_name=current_platform,
    )
    mismatches: list[dict[str, str]] = []
    installed_versions: dict[str, str] = {}
    for package, expected in sorted(recommended.items()):
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            installed = ""
        if installed:
            installed_versions[package] = installed
        if installed != expected:
            mismatches.append({
                "package": package,
                "installed": installed or "not_installed",
                "recommended": expected,
            })
    profile = dependency_profile_key(current_version, platform_name=current_platform)
    unsupported = profile.startswith("unsupported")
    return {
        "profile": profile,
        "constraint_files": constraint_files,
        "status": "unsupported" if unsupported else "matched" if recommended and not mismatches else "drift" if recommended else "bounded",
        "non_blocking": True,
        "recommended_count": len(recommended),
        "installed_versions": installed_versions,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
