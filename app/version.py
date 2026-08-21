from __future__ import annotations

import json
import subprocess
from typing import Any

from .paths import PROJECT_ROOT


def get_version() -> str:
    version = get_base_version()
    revision = get_source_revision()
    if revision:
        return f"{version} {revision}"
    return version


def get_base_version() -> str:
    path = PROJECT_ROOT / "VERSION"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "0.0.0"


def get_app_version() -> str:
    """Return the independently versioned desktop distribution version."""
    path = PROJECT_ROOT / "APP_VERSION"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    return get_base_version()


def get_source_revision() -> str:
    git_dir = PROJECT_ROOT / ".git"
    if git_dir.exists():
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            revision = result.stdout.strip()
            if revision:
                return revision
        except (OSError, subprocess.SubprocessError):
            pass
    manifest = PROJECT_ROOT / "RELEASE_MANIFEST.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return str(data.get("commit") or data.get("source_revision") or "").strip()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ""
    return ""


def release_manifest_status() -> dict[str, Any]:
    """Return whether checked-in release metadata matches the VERSION source."""
    manifest_path = PROJECT_ROOT / "RELEASE_MANIFEST.json"
    version = get_base_version()
    result: dict[str, Any] = {
        "exists": manifest_path.exists(),
        "version": version,
        "manifest_version": "",
        "version_matches": False,
        "issues": [],
    }
    if not manifest_path.exists():
        result["issues"].append("RELEASE_MANIFEST.json 不存在")
        return result
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["issues"].append(f"RELEASE_MANIFEST.json 无法读取：{exc.__class__.__name__}")
        return result
    manifest_version = str(data.get("version") or "").strip()
    result["manifest_version"] = manifest_version
    result["version_matches"] = manifest_version == version
    if not result["version_matches"]:
        result["issues"].append(f"版本不一致：VERSION={version}，manifest={manifest_version or '未填写'}")
    return result
