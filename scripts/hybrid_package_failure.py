#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Package partial hybrid cloud diagnostics after infrastructure interruption.")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--error", required=True)
    args = parser.parse_args()
    os.environ["ANSWER_BOOK_DATA_DIR"] = str(Path(args.data_root).resolve())

    from app.hybrid_contract import create_result_bundle
    from app.task_store import task_dir

    root = task_dir(args.task_id)
    stage = root / "stage_outputs"
    stage.mkdir(parents=True, exist_ok=True)
    diagnostic = {
        "schema_version": "answer_book.hybrid_worker.v1",
        "job_id": args.job_id,
        "task_id": args.task_id,
        "status": "failed",
        "phase": args.phase,
        "error": args.error,
        "infrastructure_interruption": True,
    }
    (stage / "hybrid_cloud_worker.json").write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
    create_result_bundle(
        args.task_id,
        root,
        Path(args.result),
        cloud_job_id=args.job_id,
        tenant_id=args.tenant_id,
        require_handoff=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
