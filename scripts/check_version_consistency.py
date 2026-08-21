#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    issues: list[str] = []
    if not version:
        issues.append("VERSION 为空")

    manifest_path = ROOT / "RELEASE_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"RELEASE_MANIFEST.json 无法读取：{exc.__class__.__name__}")
        manifest = {}

    manifest_version = str(manifest.get("version") or "").strip()
    if manifest_version != version:
        issues.append(f"版本不一致：VERSION={version!r}，RELEASE_MANIFEST={manifest_version!r}")

    result = {
        "ok": not issues,
        "source": "VERSION",
        "version": version,
        "checked": ["RELEASE_MANIFEST.json"],
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
