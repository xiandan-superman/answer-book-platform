#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.answer_coverage_audit import audit_answer_coverage
from app.pipeline import stage_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    args = parser.parse_args()
    sdir = stage_dir(args.task_id)
    exam_path = sdir / "structured_exam.json"
    fragments_path = sdir / "answer_fragments.json"
    if not exam_path.exists() or not fragments_path.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "issues": [
                        f"missing structured_exam.json: {exam_path.exists()}",
                        f"missing answer_fragments.json: {fragments_path.exists()}",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    report = audit_answer_coverage(
        json.loads(exam_path.read_text(encoding="utf-8")),
        json.loads(fragments_path.read_text(encoding="utf-8")),
        sdir / "answer_coverage_audit.json",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
