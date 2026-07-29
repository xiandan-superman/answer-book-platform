#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.environment import check_environment
from app.server import run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    args = parser.parse_args()
    env = check_environment()
    formula_ready = bool(env.get("formula_conversion", {}).get("preferred_chain_ready"))
    if not formula_ready:
        print(json.dumps({"ok": False, "error": "preferred formula conversion chain is not ready", "environment": env}, ensure_ascii=False, indent=2))
        return 1
    print(f"Answer Book Platform starting at http://{args.host}:{args.port}")
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
