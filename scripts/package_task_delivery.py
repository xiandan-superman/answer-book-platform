#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.delivery_package import build_task_delivery_package
from app.pipeline import output_dir, stage_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--disallow-warnings", action="store_true")
    args = parser.parse_args()
    result = build_task_delivery_package(
        args.task_id,
        stage_dir(args.task_id),
        output_dir(args.task_id),
        allow_warnings=not args.disallow_warnings,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
