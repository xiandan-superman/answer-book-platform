#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.paths import LOGS_DIR, OUTPUTS_DIR, TASKS_DIR


def clean_dir(path: Path, dry_run: bool) -> list[str]:
    removed: list[str] = []
    if not path.exists():
        return removed
    for child in path.iterdir():
        if child.name == ".gitkeep":
            continue
        removed.append(str(child))
        if not dry_run:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Actually remove runtime task/output/log files")
    args = parser.parse_args()
    dry_run = not args.yes
    removed = []
    for path in (TASKS_DIR, OUTPUTS_DIR, LOGS_DIR):
        removed.extend(clean_dir(path, dry_run))
    print(("DRY RUN: " if dry_run else "REMOVED: ") + str(len(removed)) + " runtime entries")
    for item in removed:
        print(item)
    if dry_run:
        print("Run with --yes to actually clean runtime state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

