#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pipeline import stage_dir
from app.review_export import build_question_review, write_question_review_csv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--json", action="store_true", help="Print JSON review instead of CSV path")
    args = parser.parse_args()
    sdir = stage_dir(args.task_id)
    review = build_question_review(sdir)
    if args.json:
        print(json.dumps(review, ensure_ascii=False, indent=2))
        return 0 if review.get("ok") else 1
    output = write_question_review_csv(review, sdir / "question_review.csv")
    print(json.dumps({"ok": review.get("ok"), "path": str(output), "row_count": len(review.get("review_rows", []))}, ensure_ascii=False, indent=2))
    return 0 if review.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
