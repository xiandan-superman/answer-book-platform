#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.final_acceptance import build_final_acceptance_report
from app.pipeline import output_dir, stage_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--no-render-required", action="store_true")
    args = parser.parse_args()
    report = build_final_acceptance_report(stage_dir(args.task_id), output_dir(args.task_id), require_render=not args.no_render_required)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
