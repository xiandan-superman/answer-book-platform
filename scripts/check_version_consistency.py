#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from extract_release_notes import extract_version_section

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    app_version = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    issues: list[str] = []
    if not app_version:
        issues.append("APP_VERSION 为空")
    if not version:
        issues.append("VERSION 为空")
    if app_version != version:
        issues.append(f"版本不一致：APP_VERSION={app_version!r}，VERSION={version!r}")

    manifest_path = ROOT / "RELEASE_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"RELEASE_MANIFEST.json 无法读取：{exc.__class__.__name__}")
        manifest = {}

    manifest_version = str(manifest.get("version") or "").strip()
    if manifest_version != app_version:
        issues.append(f"版本不一致：APP_VERSION={app_version!r}，RELEASE_MANIFEST={manifest_version!r}")

    changelog_path = ROOT / "CHANGELOG.md"
    try:
        extract_version_section(changelog_path.read_text(encoding="utf-8"), app_version)
    except (OSError, ValueError) as exc:
        issues.append(f"CHANGELOG.md 缺少版本 {app_version!r} 的明确更新记录：{exc}")

    result = {
        "ok": not issues,
        "source": "APP_VERSION",
        "version": app_version,
        "checked": ["VERSION", "RELEASE_MANIFEST.json", "CHANGELOG.md"],
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
