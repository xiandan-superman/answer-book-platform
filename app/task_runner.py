from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .analysis_profiles import analysis_uses_textbook_evidence
from .pipeline import PipelineOptions, run_pipeline
from .runtime_monitor import append_runtime_log, model_call_context
from .task_control import clear_task_control, control_task
from .task_store import load_task, remember_task_run_options, update_task, update_task_health


def _exam_task_worker_count() -> int:
    try:
        return max(1, min(4, int(os.environ.get("EXAM_TASK_MAX_CONCURRENCY", "2"))))
    except (TypeError, ValueError):
        return 2


_EXAM_EXECUTOR = ThreadPoolExecutor(
    max_workers=_exam_task_worker_count(),
    thread_name_prefix="exam-task",
)
_ACTIVE_LOCK = threading.RLock()
_ACTIVE_RUNS: dict[str, Future[None]] = {}


def exam_task_is_active(task_id: str) -> bool:
    with _ACTIVE_LOCK:
        future = _ACTIVE_RUNS.get(task_id)
        return future is not None and not future.done()


def resume_exam_task(task_id: str) -> dict[str, Any]:
    """Resume a live pause or atomically requeue a detached paused task."""

    with _ACTIVE_LOCK:
        active = _ACTIVE_RUNS.get(task_id)
        worker_active = active is not None and not active.done()
        result = control_task(task_id, "resume", detached_resume=not worker_active)
        if not result.get("ok") or worker_active:
            return result
        record = load_task(task_id)
        if record.status != "queued":
            return {
                **result,
                "ok": False,
                "message": f"恢复请求未入队；任务状态已变为 {record.status}。",
                "restart_required": False,
                "task": record.__dict__,
            }
        try:
            start_exam_task(
                task_id,
                use_model=record.last_run_use_model,
                render=record.last_run_render,
                document_diagnostics=record.last_run_document_diagnostics,
                reuse_fragments=True,
                remember_options=False,
                thread_name_prefix="exam-resume",
                expected_status="queued",
            )
        except RuntimeError as exc:
            current = load_task(task_id)
            return {
                **result,
                "ok": False,
                "message": f"恢复请求未入队；{exc}",
                "restart_required": False,
                "task": current.__dict__,
            }
        result["task"] = load_task(task_id).__dict__
        result["restart_required"] = False
        result["requeued"] = True
        return result


def control_exam_task(task_id: str, action: str) -> dict[str, Any]:
    """Serialize controls with task admission and detached resume."""

    if action == "resume":
        return resume_exam_task(task_id)
    with _ACTIVE_LOCK:
        return control_task(task_id, action)


def run_exam_task(
    task_id: str,
    *,
    use_model: bool,
    render: bool,
    reuse_fragments: bool,
    document_diagnostics: bool = False,
) -> None:
    """Run one exam pipeline with a consistent logging boundary."""
    current = load_task(task_id)
    if current.status == "cancelled":
        append_runtime_log(
            "pipeline",
            f"跳过已取消的排队任务 {task_id}",
            "warning",
            {"task_id": task_id},
        )
        return
    try:
        with model_call_context(task_id=task_id, operation="解析任务"):
            from .hybrid_client import hybrid_enabled, run_hybrid_task

            if use_model and hybrid_enabled() and analysis_uses_textbook_evidence(current.analysis_profile):
                if document_diagnostics:
                    run_hybrid_task(
                        task_id,
                        render_with_word=render,
                        preserve_document_diagnostics=True,
                    )
                else:
                    run_hybrid_task(task_id, render_with_word=render)
            else:
                run_pipeline(
                    task_id,
                    PipelineOptions(
                        use_model=use_model,
                        allow_demo_without_key=not use_model,
                        render_with_word=render,
                        preserve_document_diagnostics=document_diagnostics,
                        reuse_fragments=reuse_fragments,
                    ),
                )
    except Exception as exc:  # Worker boundary: preserve the durable task error and log the crash.
        append_runtime_log(
            "pipeline",
            f"任务 {task_id} 执行失败：{exc}",
            "error",
            {"task_id": task_id, "error_type": exc.__class__.__name__},
        )
        failed = load_task(task_id)
        if failed.status == "failed":
            try:
                from .support_reporting import queue_automatic_failure_report

                queue_automatic_failure_report({
                    "task_id": task_id,
                    "task_kind": "exam",
                    "task_status": failed.status,
                    "task_stage": failed.current_stage,
                    "task_run_started_at": failed.run_started_at or failed.created_at,
                    "task_title": os.path.basename(failed.exam_path),
                    "task_model": failed.answer_model or failed.model,
                    "error": failed.error or str(exc),
                })
            except Exception:
                # Reporting must never replace or delay the original task failure.
                pass


def _release_active_run(task_id: str, future: Future[None]) -> None:
    with _ACTIVE_LOCK:
        if _ACTIVE_RUNS.get(task_id) is future:
            _ACTIVE_RUNS.pop(task_id, None)


def start_exam_task(
    task_id: str,
    *,
    use_model: bool,
    render: bool,
    reuse_fragments: bool,
    document_diagnostics: bool = False,
    remember_options: bool = True,
    thread_name_prefix: str = "exam-run",
    expected_status: str | None = None,
) -> Future[None]:
    """Queue one bounded exam run and deduplicate repeated starts in-process."""

    with _ACTIVE_LOCK:
        record = load_task(task_id)
        if expected_status is not None and record.status != expected_status:
            raise RuntimeError(
                f"预期任务状态为 {expected_status}，实际为 {record.status}。"
            )
        active = _ACTIVE_RUNS.get(task_id)
        if active is not None and not active.done():
            if record.status != "cancelled":
                return active
            # A queued cancellation can be withdrawn before its worker starts.
            # If execution already began, keep the old run isolated until its
            # cancellation checkpoint has finished instead of racing two runs.
            if not active.cancel():
                raise RuntimeError("上一次取消正在收尾，请稍后重试。")
            _ACTIVE_RUNS.pop(task_id, None)
        if record.status in {"running", "paused"}:
            raise RuntimeError("任务已在运行，不能重复启动。")
        if remember_options:
            remember_task_run_options(
                task_id,
                use_model=use_model,
                render=render,
                reuse_fragments=reuse_fragments,
                document_diagnostics=document_diagnostics,
            )
        # Retrying a cancelled task is an explicit new run over the same task
        # identity. Remove the old cancellation request before it is queued;
        # a later cancel action will write a fresh control record.
        clear_task_control(task_id)
        update_task(task_id, status="queued", current_stage="queued", error="")
        update_task_health(
            task_id,
            current_operation="正在等待真题解析处理位置",
            health_status="waiting",
            warning_reason="",
            suggested_action="任务会在可用处理位置释放后自动开始。",
        )
        future = _EXAM_EXECUTOR.submit(
            run_exam_task,
            task_id,
            use_model=use_model,
            render=render,
            reuse_fragments=reuse_fragments,
            document_diagnostics=document_diagnostics,
        )
        _ACTIVE_RUNS[task_id] = future
        future.add_done_callback(lambda completed: _release_active_run(task_id, completed))
        return future


def exam_task_queue_status() -> dict[str, Any]:
    with _ACTIVE_LOCK:
        active_ids = sorted(task_id for task_id, future in _ACTIVE_RUNS.items() if not future.done())
    return {
        "max_concurrent_tasks": _exam_task_worker_count(),
        "active_or_queued_count": len(active_ids),
        "task_ids": active_ids,
    }
