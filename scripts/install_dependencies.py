#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    requirements = ROOT / "requirements.txt"
    if not requirements.exists():
        print(json.dumps({"ok": False, "issues": [f"missing {requirements}"]}, ensure_ascii=False, indent=2))
        return 1
    procs = [subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements)], cwd=ROOT)]
    windows_requirements = ROOT / "requirements-windows.txt"
    if platform.system() == "Windows" and windows_requirements.exists():
        procs.append(subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(windows_requirements)], cwd=ROOT))
    checks = {
        "python": sys.executable,
        "pdftoppm": shutil.which("pdftoppm"),
        "soffice": shutil.which("soffice") or shutil.which("libreoffice"),
    }
    ok = all(proc.returncode == 0 for proc in procs) and bool(checks["pdftoppm"])
    issues = []
    if any(proc.returncode != 0 for proc in procs):
        issues.append("pip install failed")
    if not checks["pdftoppm"]:
        issues.append("pdftoppm not found; install Poppler and add it to PATH")
    print(json.dumps({"ok": ok, "checks": checks, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
