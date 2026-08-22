#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one isolated hybrid cloud pipeline job.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_root = Path(args.data_root).resolve()
    os.environ["ANSWER_BOOK_DATA_DIR"] = str(data_root)

    # Import only after the isolated data root is fixed. Every tenant gets a
    # separate task store, model trace directory, logs, and cache namespace.
    from app.hybrid_contract import create_result_bundle, rebind_task_root, safe_extract_bundle
    from app.pipeline import PipelineOptions, run_pipeline
    from app.task_store import TaskRecord, save_task, task_dir

    input_path = Path(args.input).resolve()
    result_path = Path(args.result).resolve()
    staging_root = data_root / "incoming" / args.job_id
    staging_root.mkdir(parents=True, exist_ok=True)
    manifest_probe = staging_root / "probe"
    if manifest_probe.exists():
        shutil.rmtree(manifest_probe)
    manifest = safe_extract_bundle(input_path, manifest_probe)
    if manifest.get("bundle_kind") != "input":
        raise RuntimeError("Expected a hybrid input bundle")
    task_id = str(manifest["task_id"])
    cloud_task_root = task_dir(task_id)
    cloud_task_root.parent.mkdir(parents=True, exist_ok=True)
    identity_path = cloud_task_root / "hybrid_input_identity.json"
    previous_identity = {}
    try:
        previous_identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    resuming = bool(
        cloud_task_root.is_dir()
        and previous_identity.get("input_fingerprint")
        and previous_identity.get("input_fingerprint") == manifest.get("input_fingerprint")
    )
    if resuming:
        shutil.copy2(manifest_probe / "manifest.json", cloud_task_root / "manifest.json")
        shutil.rmtree(manifest_probe)
    else:
        if cloud_task_root.exists():
            shutil.rmtree(cloud_task_root)
        manifest_probe.replace(cloud_task_root)
    rebind_task_root(cloud_task_root, cloud_task_root, manifest_probe)
    identity_path.write_text(
        json.dumps(
            {
                "schema_version": "answer_book.hybrid_input_identity.v1",
                "input_fingerprint": manifest.get("input_fingerprint", ""),
                "job_id": args.job_id,
                "resumed_from_checkpoint": resuming,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    task_payload = manifest.get("task") if isinstance(manifest.get("task"), dict) else {}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    record = TaskRecord(
        task_id=task_id,
        exam_path=str(cloud_task_root / "hybrid_preprocessed.exam"),
        textbooks_dir=str(cloud_task_root / "hybrid_preprocessed_textbooks"),
        provider=str(task_payload.get("provider") or ""),
        model=str(task_payload.get("model") or ""),
        status="queued",
        created_at=now,
        updated_at=now,
        selected_textbooks=["hybrid_preprocessed"],
        model_thinking=str(task_payload.get("model_thinking") or "auto"),
        reasoning_provider=str(task_payload.get("reasoning_provider") or ""),
        reasoning_model=str(task_payload.get("reasoning_model") or ""),
        answer_provider=str(task_payload.get("answer_provider") or ""),
        answer_model=str(task_payload.get("answer_model") or ""),
        correctness_provider=str(task_payload.get("correctness_provider") or ""),
        correctness_model=str(task_payload.get("correctness_model") or ""),
        vision_provider=str(task_payload.get("vision_provider") or ""),
        vision_model=str(task_payload.get("vision_model") or ""),
        image_provider=str(task_payload.get("image_provider") or ""),
        image_model=str(task_payload.get("image_model") or ""),
        execution_mode="hybrid_cloud",
        hybrid_phase="cloud_pipeline",
        cloud_job_id=args.job_id,
        cloud_status="running",
    )
    save_task(record)
    status_path = cloud_task_root / "stage_outputs" / "hybrid_cloud_worker.json"
    try:
        report = run_pipeline(
            task_id,
            PipelineOptions(
                use_model=True,
                allow_demo_without_key=False,
                render_with_word=False,
                reuse_fragments=resuming,
                require_preferred_formula_chain=False,
                preprocessed_input=True,
                defer_local_delivery=True,
            ),
        )
        status = {
            "schema_version": "answer_book.hybrid_worker.v1",
            "job_id": args.job_id,
            "task_id": task_id,
            "status": "completed",
            "report": report,
        }
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        create_result_bundle(
            task_id,
            cloud_task_root,
            result_path,
            cloud_job_id=args.job_id,
            tenant_id=args.tenant_id,
        )
        return 0
    except Exception as exc:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status = {
            "schema_version": "answer_book.hybrid_worker.v1",
            "job_id": args.job_id,
            "task_id": task_id,
            "status": "failed",
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "traceback": traceback.format_exc(),
        }
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        create_result_bundle(
            task_id,
            cloud_task_root,
            result_path,
            cloud_job_id=args.job_id,
            tenant_id=args.tenant_id,
            require_handoff=False,
        )
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
