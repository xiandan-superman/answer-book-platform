from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
        except Exception:
            pass
    manifest = PROJECT_ROOT / "RELEASE_MANIFEST.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return str(data.get("commit") or data.get("source_revision") or "").strip()
        except Exception:
            return ""
    return ""
