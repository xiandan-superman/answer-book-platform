#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, cmd: list[str]) -> dict:
    started = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return {
        "name": name,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.time() - started, 3),
        "stdout": proc.stdout[-3000:],
        "stderr": proc.stderr[-3000:],
        "cmd": cmd,
    }


def main() -> int:
    py_files = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "app").glob("*.py"))]
    py_files.extend(str(p.relative_to(ROOT)) for p in sorted((ROOT / "scripts").glob("*.py")))
    steps = [
        ("py_compile", [sys.executable, "-m", "py_compile", *py_files]),
        ("selftest", [sys.executable, "scripts/selftest.py"]),
        ("formula_omml", [sys.executable, "scripts/test_formula_omml.py"]),
        ("project_completeness", [sys.executable, "scripts/audit_project_completeness.py"]),
        ("package_release", [sys.executable, "scripts/package_release.py"]),
        ("verify_release", [sys.executable, "scripts/verify_release_package.py"]),
    ]
    results = []
    for name, cmd in steps:
        result = run_step(name, cmd)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k not in {"stdout", "stderr"}}, ensure_ascii=False, indent=2))
        if not result["ok"]:
            print(json.dumps({"failed_step": name, "stdout": result["stdout"], "stderr": result["stderr"]}, ensure_ascii=False, indent=2))
            report = {"ok": False, "failed_step": name, "results": results}
            (ROOT / "quality_gates_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 1
    report = {"ok": True, "results": results}
    (ROOT / "quality_gates_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(ROOT / "quality_gates_report.json")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
