#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.fragment_repair import repair_answer_fragments_for_docx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("answer_fragments_json")
    args = parser.parse_args()
    path = Path(args.answer_fragments_json)
    report = repair_answer_fragments_for_docx(path, path.with_suffix(path.suffix + ".bak"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
