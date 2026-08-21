#!/usr/bin/env python3
"""Create a read-only inventory of local runtime data; never deletes files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {
    "task_records": "tasks",
    "generated_outputs": "outputs",
    "practice_jobs": "practice_jobs",
    "practice_history": "practice_history",
    "textbook_sources": "textbooks",
    "derived_cache": "cache",
    "runtime_logs": "logs",
    "runtime_state": "runtime",
    "temporary_files": "tmp",
    "local_archives": "archive/local",
}
PROTECTED = {"task_records", "generated_outputs", "practice_history", "textbook_sources"}


def _scan(path: Path) -> dict[str, Any]:
    files = 0
    directories = 0
    bytes_total = 0
    latest_mtime = 0.0
    if not path.exists():
        return {"exists": False, "files": 0, "directories": 0, "bytes": 0, "latest_modified": None}
    for entry in path.rglob("*"):
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                directories += 1
                continue
            stat = entry.stat()
        except OSError:
            continue
        files += 1
        bytes_total += stat.st_size
        latest_mtime = max(latest_mtime, stat.st_mtime)
    return {
        "exists": True,
        "files": files,
        "directories": directories,
        "bytes": bytes_total,
        "latest_modified": datetime.fromtimestamp(latest_mtime).astimezone().isoformat(timespec="seconds") if latest_mtime else None,
    }


def build_inventory() -> dict[str, Any]:
    rows = []
    for category, relative in CATEGORIES.items():
        rows.append(
            {
                "category": category,
                "path": relative,
                "protected": category in PROTECTED,
                "decision": "保留；删除前必须做逐文件引用核对" if category in PROTECTED else "仅盘点；本脚本不执行清理",
                **_scan(ROOT / relative),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "dry-run",
        "project_root": str(ROOT),
        "deletes_files": False,
        "categories": rows,
    }


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()
    report = build_inventory()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("本地数据盘点（只读 dry-run，不删除文件）")
        for row in report["categories"]:
            mark = "保护" if row["protected"] else "可盘点"
            print(f"- {row['path']}: {_human_size(row['bytes'])}, {row['files']} files, {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
