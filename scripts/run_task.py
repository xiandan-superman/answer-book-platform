#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pipeline import PipelineOptions, run_pipeline
from app.process_lock import ProcessLockUnavailable, platform_process_lock
from app.task_control import clear_task_control
from app.task_store import remember_task_run_options


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--no-model", action="store_true", help="Use demo fragments instead of API calls")
    parser.add_argument("--render", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reuse-fragments", action="store_true", help="Reuse existing stage_outputs/answer_fragments.json")
    parser.add_argument("--allow-formula-fallback", action="store_true", help="Allow built-in minimal formula converter if preferred chain is missing")
    args = parser.parse_args()
    try:
        with platform_process_lock(purpose=f"run-task {args.task_id}"):
            # Invoking this CLI is an explicit new run, so discard a control
            # left by a previous run before entering the cancellation-aware pipeline.
            # Keep the durable task options aligned with this CLI invocation.
            # Otherwise an interrupted `--no-model` / no-render run can be
            # recovered later with the task record's stale GUI defaults.
            remember_task_run_options(
                args.task_id,
                use_model=not args.no_model,
                render=False,
                reuse_fragments=args.reuse_fragments,
            )
            clear_task_control(args.task_id)
            result = run_pipeline(
                args.task_id,
                PipelineOptions(
                    use_model=not args.no_model,
                    allow_demo_without_key=args.no_model,
                    render_with_word=False,
                    reuse_fragments=args.reuse_fragments,
                    require_preferred_formula_chain=not args.allow_formula_fallback,
                ),
            )
    except ProcessLockUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
