#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dist" / "update-manifest.json"
SCHEMA_VERSION = "answer_book.update_manifest.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_asset(value: str) -> tuple[str, Path]:
    key, separator, raw_path = value.partition("=")
    if not separator or not key.strip() or not raw_path.strip():
        raise ValueError("--asset 必须使用 platform=/absolute/path 格式")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return key.strip(), path


def build_manifest(*, version: str, assets: list[tuple[str, Path]], notes: str = "") -> dict:
    if not version.strip():
        raise ValueError("版本号不能为空")
    platforms: dict[str, dict] = {}
    names: set[str] = set()
    for key, path in assets:
        if key in platforms:
            raise ValueError(f"重复的平台键：{key}")
        if path.name in names:
            raise ValueError(f"不同平台不能复用同一附件名：{path.name}")
        names.add(path.name)
        platforms[key] = {
            "asset_name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "version": version.strip(),
        "notes": notes.strip(),
        "platforms": platforms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the signed-by-checksum GitHub update manifest.")
    parser.add_argument("--version", default=(ROOT / "APP_VERSION").read_text(encoding="utf-8").strip())
    parser.add_argument("--asset", action="append", default=[], help="macos-arm64=/path/app.zip")
    parser.add_argument("--notes", default="")
    parser.add_argument("--notes-file", default="")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    notes = args.notes
    if args.notes_file:
        notes = Path(args.notes_file).expanduser().read_text(encoding="utf-8")
    manifest = build_manifest(
        version=args.version,
        assets=[parse_asset(value) for value in args.asset],
        notes=notes,
    )
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
