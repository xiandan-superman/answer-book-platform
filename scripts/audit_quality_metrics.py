#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.capabilities.quality_metrics import build_quality_metrics_report  # noqa: E402
from app.paths import TASKS_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate observation-only quality shadow metrics across tasks.")
    parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    report = build_quality_metrics_report(args.tasks_dir, use_cache=not args.no_cache)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
