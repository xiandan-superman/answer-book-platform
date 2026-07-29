#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pipeline import PipelineOptions, run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--no-model", action="store_true", help="Use demo fragments instead of API calls")
    parser.add_argument("--render", action="store_true", help="Export through Word/PDF/PNG")
    parser.add_argument("--reuse-fragments", action="store_true", help="Reuse existing stage_outputs/answer_fragments.json")
    parser.add_argument("--allow-formula-fallback", action="store_true", help="Allow built-in minimal formula converter if preferred chain is missing")
    args = parser.parse_args()
    result = run_pipeline(
        args.task_id,
        PipelineOptions(
            use_model=not args.no_model,
            allow_demo_without_key=args.no_model,
            render_with_word=args.render,
            reuse_fragments=args.reuse_fragments,
            require_preferred_formula_chain=not args.allow_formula_fallback,
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
