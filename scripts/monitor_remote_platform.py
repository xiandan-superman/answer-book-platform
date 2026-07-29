#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "logs" / "remote_monitor"


def read_json(url: str, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def task_brief(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "current_stage": task.get("current_stage"),
        "provider": task.get("provider"),
        "model": task.get("model"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "error": task.get("error"),
    }


def capture(base_url: str, output_dir: Path, timeout: int) -> dict[str, Any]:
    base = base_url.rstrip("/")
    status = read_json(f"{base}/api/system/status", timeout)
    tasks = status.get("tasks", {}).get("recent", [])
    diagnostics: dict[str, Any] = {}
    for task in tasks:
        if task.get("status") in {"failed", "running", "paused"}:
            task_id = str(task.get("task_id") or "")
            if not task_id:
                continue
            url = f"{base}/api/tasks/{urllib.parse.quote(task_id)}/diagnostics"
            try:
                diagnostics[task_id] = read_json(url, timeout)
            except Exception as exc:
                diagnostics[task_id] = {"error": str(exc)}

    snapshot = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": base,
        "system": status,
        "diagnostics": diagnostics,
    }
    stamp = time.strftime("%Y%m%d_%H%M%S")
    write_json(output_dir / f"remote_monitor_{stamp}.json", snapshot)
    write_json(output_dir / "latest.json", snapshot)
    return snapshot


def print_summary(snapshot: dict[str, Any]) -> None:
    system = snapshot.get("system", {})
    host = system.get("host", {})
    counts = system.get("tasks", {}).get("counts", {})
    tasks = system.get("tasks", {}).get("recent", [])
    diagnostics = snapshot.get("diagnostics", {})
    print(json.dumps(
        {
            "captured_at": snapshot.get("captured_at"),
            "base_url": snapshot.get("base_url"),
            "host": {
                "name": host.get("name"),
                "system": host.get("system"),
                "platform": host.get("platform"),
                "python": host.get("python"),
                "pid": host.get("pid"),
                "project_root": host.get("project_root"),
            },
            "task_counts": counts,
            "recent_tasks": [task_brief(task) for task in tasks[:8]],
            "diagnostic_task_ids": list(diagnostics.keys()),
        },
        ensure_ascii=False,
        indent=2,
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://192.168.31.115:8766")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--timeout", default=10, type=int)
    args = parser.parse_args()
    snapshot = capture(args.base_url, Path(args.output_dir).expanduser(), args.timeout)
    print_summary(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
