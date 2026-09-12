"""Resolve a user-provided task number to local diagnostic record IDs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.task_identity import resolve_task_number  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", help="RW-XXXX-XXXX-XXXX-XXXX")
    args = parser.parse_args()
    try:
        result = resolve_task_number(args.number)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["found"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
