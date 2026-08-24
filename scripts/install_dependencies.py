#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dependency_profiles import runtime_dependency_files  # noqa: E402


def main() -> int:
    dependency_files = runtime_dependency_files(ROOT, sys.version_info[:2], platform_name=sys.platform)
    requirements = ROOT / "requirements.txt"
    if not requirements.exists():
        print(json.dumps({"ok": False, "issues": [f"missing {requirements}"]}, ensure_ascii=False, indent=2))
        return 1
    command = [sys.executable, "-m", "pip", "install"]
    for dependency_file in dependency_files:
        command.extend(["-c" if dependency_file.name.startswith("constraints-") else "-r", str(dependency_file)])
    procs = [subprocess.run(command, cwd=ROOT)]
    checks = {
        "python": sys.executable,
        "huey": bool(find_spec("huey")),
        "pypdfium2": bool(find_spec("pypdfium2")),
        "pdftoppm": shutil.which("pdftoppm"),
        "soffice": shutil.which("soffice") or shutil.which("libreoffice"),
    }
    ok = all(proc.returncode == 0 for proc in procs) and bool(checks["huey"]) and bool(checks["pypdfium2"] or checks["pdftoppm"])
    issues = []
    if any(proc.returncode != 0 for proc in procs):
        issues.append("pip install failed")
    if not checks["huey"]:
        issues.append("huey not found; durable practice queue is unavailable")
    if not checks["pypdfium2"] and not checks["pdftoppm"]:
        issues.append("no PDF page renderer available; install pypdfium2 or Poppler")
    print(json.dumps({"ok": ok, "checks": checks, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
