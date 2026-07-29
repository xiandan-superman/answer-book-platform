#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT.parent / "answer_book_platform_v1_release.zip"

EXCLUDED_DIRS = {"tasks", "outputs", "logs", "cache", "tmp", "output", "backups", "dist", "generated", "pyinstaller-work", ".git", ".playwright-cli", "__pycache__", ".pytest_cache"}
EXCLUDED_FILES = {".env", "config/providers.local.json", "textbooks/textbook_page_map.manual.csv", "quality_gates_report.json", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc"}
RELEASE_MANIFEST_NAME = "RELEASE_MANIFEST.json"


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    parts = set(rel.parts)
    if parts & EXCLUDED_DIRS:
        return False
    rel_posix = rel.as_posix()
    if rel_posix in EXCLUDED_FILES or path.name in EXCLUDED_FILES:
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


def build_release(output: Path) -> dict:
    files = [p for p in sorted(ROOT.rglob("*")) if should_include(p)]
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
            "package_name": "answer_book_platform_v1",
            "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "0.0.0",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "file_count": len(files) + 1,
            "included_files": included_files + [RELEASE_MANIFEST_NAME],
            "excluded_dirs": sorted(EXCLUDED_DIRS),
            "excluded_files": sorted(EXCLUDED_FILES),
            "excluded_suffixes": sorted(EXCLUDED_SUFFIXES),
            "notes": "Runtime tasks, outputs, logs, local API keys, manual page maps, caches, temporary files, and local browser artifacts are intentionally excluded.",
        }
        zf.writestr(RELEASE_MANIFEST_NAME, json.dumps(release_manifest, ensure_ascii=False, indent=2))
    manifest = {
        "output": str(output),
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "0.0.0",
        "file_count": len(files) + 1,
        "size_bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "release_manifest": RELEASE_MANIFEST_NAME,
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "excluded_files": sorted(EXCLUDED_FILES),
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
