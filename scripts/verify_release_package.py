#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = ROOT.parent / "answer_book_platform_v1_release.zip"
FORBIDDEN_MARKERS = [
    ".env",
    "config/providers.local.json",
    "config/api_keys.json",
    "tasks/",
    "outputs/",
    "logs/",
    "cache/",
    "__pycache__/",
    ".pyc",
    "textbooks/textbook_page_map.manual.csv",
    "question_review.csv",
    "final_acceptance_report.json",
    "quality_gates_report.json",
    "_delivery.zip",
    "textbooks/",
    "exams/",
    "practice_history/",
    "practice_jobs/",
    "validation/",
    "validation_artifacts/",
    "validation_runs/",
    "tests/",
]


def forbidden_entries(names: list[str]) -> list[str]:
    bad = []
    for name in names:
        normalized = name.replace("\\", "/")
        for marker in FORBIDDEN_MARKERS:
            if marker.endswith("/"):
                if normalized.startswith(marker) or f"/{marker}" in normalized:
                    bad.append(name)
                    break
            elif normalized == marker or normalized.endswith("/" + marker) or normalized.endswith(marker):
                bad.append(name)
                break
    return sorted(set(bad))


def verify(zip_path: Path) -> dict:
    if not zip_path.exists():
        return {"ok": False, "issues": [f"release zip not found: {zip_path}"]}
    issues: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        bad = forbidden_entries(names)
        if bad:
            issues.append("forbidden entries in zip: " + ", ".join(bad[:30]))
        required = [
            "README.md",
            "APP_VERSION",
            "VERSION",
            "SOFTWARE_LICENSE.md",
            "RELEASE_MANIFEST.json",
            "requirements.txt",
            "app/server.py",
            "standalone_word_format_reviewer/format_engine.py",
            "scripts/run_platform.py",
            "web/index.html",
        ]
        missing = [name for name in required if name not in names]
        if missing:
            issues.append("missing required files: " + ", ".join(missing))
        if "RELEASE_MANIFEST.json" in names:
            try:
                release_manifest = json.loads(zf.read("RELEASE_MANIFEST.json").decode("utf-8"))
            except Exception as exc:
                issues.append(f"invalid RELEASE_MANIFEST.json: {exc}")
            else:
                if not str(release_manifest.get("version") or "").strip():
                    issues.append("RELEASE_MANIFEST.json missing version")
                included_files = release_manifest.get("included_files")
                if not isinstance(included_files, list):
                    issues.append("RELEASE_MANIFEST.json included_files must be a list")
                else:
                    manifest_bad = forbidden_entries([str(x) for x in included_files])
                    if manifest_bad:
                        issues.append("forbidden entries in release manifest: " + ", ".join(manifest_bad[:30]))
                    missing_from_zip = sorted(set(str(x) for x in included_files) - set(names))
                    if missing_from_zip:
                        issues.append("manifest lists files absent from zip: " + ", ".join(missing_from_zip[:30]))
        with tempfile.TemporaryDirectory(prefix="answer_book_release_") as tmp:
            zf.extractall(tmp)
            extracted = Path(tmp)
            cmd = [sys.executable, "-m", "py_compile"]
            cmd.extend(str(p) for p in sorted((extracted / "app").glob("*.py")))
            cmd.extend(str(p) for p in sorted((extracted / "scripts").glob("*.py")))
            proc = subprocess.run(cmd, cwd=extracted, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                issues.append("py_compile failed: " + (proc.stderr or proc.stdout)[:2000])
            for folder in ["tasks", "outputs", "logs", "cache"]:
                if (extracted / folder).exists() and any((extracted / folder).iterdir()):
                    issues.append(f"{folder}/ should be empty or absent in release")
            if (extracted / ".env").exists():
                issues.append(".env exists after extraction")
            import_proc = subprocess.run(
                [sys.executable, "-c", "import app.server; import app.exercise_generation"],
                cwd=extracted,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if import_proc.returncode != 0:
                issues.append("runtime import smoke test failed: " + (import_proc.stderr or import_proc.stdout)[:2000])
            if shutil.which("python3") is None and shutil.which("python") is None:
                issues.append("python executable not found on verifier machine")
    return {"ok": not issues, "zip": str(zip_path), "issue_count": len(issues), "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", default=str(DEFAULT_ZIP))
    args = parser.parse_args()
    report = verify(Path(args.zip).expanduser())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
