#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ROOT / "THIRD_PARTY_NOTICES.md"
DEPENDENCY_FILES = (
    "requirements.txt",
    "requirements-windows.txt",
    "constraints-py39.txt",
    "constraints-py311.txt",
    "constraints-source-macos-py39.txt",
    "constraints-source-macos-py311.txt",
    "constraints-source-windows-py39.txt",
    "constraints-source-windows-py311.txt",
)
VENDORED_COMPONENTS = ("MathJax", "Lucide", "GSAP")
_PACKAGE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^]]+\])?\s*(?:[<>=!~]|;|$)")


def declared_packages() -> set[str]:
    packages: set[str] = set()
    for name in DEPENDENCY_FILES:
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = _PACKAGE_RE.match(line)
            if match:
                packages.add(match.group(1).lower().replace("_", "-"))
    return packages


def audit_third_party_notices() -> dict[str, object]:
    text = NOTICE.read_text(encoding="utf-8") if NOTICE.is_file() else ""
    lowered = text.lower().replace("_", "-")
    missing_packages = sorted(package for package in declared_packages() if f"`{package}`" not in lowered)
    missing_components = [component for component in VENDORED_COMPONENTS if component.lower() not in lowered]
    required_license_files = ["web/vendor/mathjax/LICENSE"]
    missing_license_files = [name for name in required_license_files if not (ROOT / name).is_file()]
    return {
        "ok": not (missing_packages or missing_components or missing_license_files),
        "declared_package_count": len(declared_packages()),
        "missing_packages": missing_packages,
        "missing_components": missing_components,
        "missing_license_files": missing_license_files,
    }


def main() -> int:
    report = audit_third_party_notices()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
