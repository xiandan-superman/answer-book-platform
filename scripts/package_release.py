#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT.parent / "answer_book_platform_v1_release.zip"

EXCLUDED_DIRS = {
    ".artifacts",
    ".git",
    ".mypy_cache",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-app",
    "__pycache__",
    "backups",
    "cache",
    "dist",
    "exams",
    "logs",
    "model_diagnostics",
    "output",
    "outputs",
    "practice_history",
    "practice_jobs",
    "tasks",
    "support_reports",
    "tests",
    "textbooks",
    "tmp",
    "tools",
    "truth-exam-redesign",
    "validation",
    "validation_artifacts",
    "validation_runs",
}
EXCLUDED_FILES = {
    ".coverage",
    ".DS_Store",
    ".env",
    "AGENTS.md",
    "RELEASE_MANIFEST.json",
    "config/api_keys.json",
    "config/providers.local.json",
    "config/support_reporting.json",
    "quality_gates_report.json",
}
EXCLUDED_PREFIXES = {
    "assets/fonts/dolbydu-font/Sans/",
    "assets/fonts/dolbydu-font/Serif/",
    "assets/fonts/dolbydu-font/art/",
    "assets/fonts/dolbydu-font/elegant/",
    "assets/fonts/dolbydu-font/mono/",
    "assets/fonts/dolbydu-font/unicode/",
    "build/generated/",
    "build/pyinstaller-work/",
}
EXCLUDED_SUFFIXES = {".pyc"}
RELEASE_MANIFEST_NAME = "RELEASE_MANIFEST.json"


def should_include(path: Path, root: Path | None = None) -> bool:
    source_root = root or ROOT
    rel = path.relative_to(source_root)
    parts = set(rel.parts)
    if parts & EXCLUDED_DIRS:
        return False
    rel_posix = rel.as_posix()
    if rel_posix in EXCLUDED_FILES or path.name in EXCLUDED_FILES:
        return False
    if any(rel_posix.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def release_source_files(root: Path = ROOT) -> list[Path]:
    """Return only files already present in Git's index, failing closed."""

    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    files: list[Path] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8")
        path = root / relative
        if should_include(path, root):
            files.append(path)
    return sorted(files)


def build_release(output: Path) -> dict:
    files = release_source_files(ROOT)
    included_files = [p.relative_to(ROOT).as_posix() for p in files]
    forbidden = []
    for rel in included_files:
        if rel in EXCLUDED_FILES or any(rel.startswith(f"{name}/") for name in EXCLUDED_DIRS) or "__pycache__" in rel:
            forbidden.append(rel)
    if forbidden:
        raise RuntimeError(f"Forbidden files selected for release: {forbidden[:20]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(ROOT).as_posix())
        release_manifest = {
            "package_name": "answer_book_platform",
            "product_name": "真题解析与生题平台",
            "version": (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip(),
            "platform_version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "file_count": len(files) + 1,
            "included_files": included_files + [RELEASE_MANIFEST_NAME],
            "excluded_dirs": sorted(EXCLUDED_DIRS),
            "excluded_files": sorted(EXCLUDED_FILES),
            "excluded_suffixes": sorted(EXCLUDED_SUFFIXES),
            "excluded_prefixes": sorted(EXCLUDED_PREFIXES),
            "notes": "Runtime data, uploaded materials, task history, outputs, API keys, validation artifacts and development-only files are intentionally excluded.",
        }
        zf.writestr(RELEASE_MANIFEST_NAME, json.dumps(release_manifest, ensure_ascii=False, indent=2))
    manifest = {
        "output": str(output),
        "version": (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip(),
        "platform_version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "file_count": len(files) + 1,
        "size_bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "release_manifest": RELEASE_MANIFEST_NAME,
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "excluded_files": sorted(EXCLUDED_FILES),
        "excluded_prefixes": sorted(EXCLUDED_PREFIXES),
    }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    manifest = build_release(Path(args.output).expanduser())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
