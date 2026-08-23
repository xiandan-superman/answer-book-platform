from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, TypeVar

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")


class PracticeGenerationStopped(RuntimeError):
    """Raised when a durable practice job may no longer spend model work."""


def ensure_practice_generation_active(payload: dict[str, Any]) -> None:
    """Stop new work after cancellation, timeout, or another terminal state."""
    job_id = str(payload.get("_job_id") or "").strip()[:100]
    if not job_id:
        return
    from .practice_jobs import load_practice_job

    status = str(load_practice_job(job_id).get("status") or "")
    if status != "running":
        raise PracticeGenerationStopped(
            f"出题任务已停止（{status or '未知状态'}），不再发起后续模型请求。"
        )


@dataclass(frozen=True)
class PracticeGenerationCheckpoint:
    source_job_id: str
    exercises: tuple[dict[str, Any], ...]
    generated_plan_item_ids: tuple[str, ...]


def iter_bounded_futures(
    items: Iterable[InputT],
    worker: Callable[[InputT], ResultT],
    *,
    max_workers: int,
    thread_name_prefix: str,
    ensure_active: Callable[[], None] | None = None,
) -> Iterator[tuple[InputT, Future[ResultT]]]:
    """Yield incrementally scheduled work without pre-submitting the full task.

    At most ``max_workers`` calls are submitted. A new call is scheduled only
    after the caller has consumed a completed future. Closing the iterator (or
    raising from its consumer) cancels work that has not started yet.
    """
    iterator = iter(items)
    executor = ThreadPoolExecutor(
        max_workers=max(1, max_workers),
        thread_name_prefix=thread_name_prefix,
    )
    futures: dict[Future[ResultT], InputT] = {}

    def submit_next() -> bool:
        try:
            item = next(iterator)
        except StopIteration:
            return False
        if ensure_active is not None:
            ensure_active()
        futures[executor.submit(copy_context().run, worker, item)] = item
        return True

    try:
        for _ in range(max(1, max_workers)):
            if not submit_next():
                break
        while futures:
            completed, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
            for future in completed:
                item = futures.pop(future)
                yield item, future
                if ensure_active is not None:
                    ensure_active()
                submit_next()
    finally:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


def load_practice_generation_checkpoint(
    payload: dict[str, Any],
    *,
    expected_plan_item_ids: list[str],
) -> PracticeGenerationCheckpoint:
    """Load only a checkpoint proven to belong to this confirmed plan.

    A background job may checkpoint completed questions after each batch. A
    retry may reuse those questions, but must never mix content from another
    plan, accept duplicate IDs, or silently fall back to an empty checkpoint
    when the requested source record is unavailable.
    """
    current_job_id = str(payload.get("_job_id") or "").strip()[:100]
    resume_job_id = str(payload.get("resume_from_job_id") or "").strip()[:100]
    resume_history_id = str(payload.get("resume_from_history_id") or "").strip()[:100]
    if resume_history_id:
        from .practice_store import _blueprint_fingerprint, load_practice_record

        record = load_practice_record(resume_history_id)
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        blueprint = data.get("blueprint") if isinstance(data.get("blueprint"), dict) else {}
        continuation_snapshot = payload.get("continuation_snapshot") if isinstance(payload.get("continuation_snapshot"), dict) else {}
        expected_blueprint_fingerprint = str(continuation_snapshot.get("blueprint_fingerprint") or "")
        if expected_blueprint_fingerprint and expected_blueprint_fingerprint != _blueprint_fingerprint(blueprint):
            raise ValueError("历史记录的蓝图已在继续任务创建后发生变化，已停止旧任务以保护较新成果。")
        stored_plan_ids = [
            str(item.get("plan_item_id") or "").strip()
            for item in blueprint.get("exercise_plan") or []
            if isinstance(item, dict)
        ]
        if stored_plan_ids != expected_plan_item_ids:
            raise ValueError("历史记录的已确认蓝图与当前继续请求不一致，已阻止混用旧题目。")
        accepted: list[dict[str, Any]] = []
        accepted_ids: list[str] = []
        seen: set[str] = set()
        for item in data.get("exercises") or []:
            if not isinstance(item, dict) or item.get("generation_status") == "failed":
                continue
            plan_item_id = str(item.get("plan_item_id") or "").strip()
            if not plan_item_id or plan_item_id not in set(expected_plan_item_ids) or plan_item_id in seen:
                raise ValueError("历史记录的已生成题目与当前蓝图不一致，已停止恢复。")
            seen.add(plan_item_id)
            accepted_ids.append(plan_item_id)
            accepted.append(dict(item))
        return PracticeGenerationCheckpoint(resume_history_id, tuple(accepted), tuple(accepted_ids))
    source_job_id = resume_job_id or current_job_id
    if not source_job_id:
        return PracticeGenerationCheckpoint("", (), ())

    from .practice_jobs import load_practice_job
    from .practice_store import plan_fingerprint

    try:
        record = load_practice_job(source_job_id)
    except (FileNotFoundError, ValueError) as exc:
        if resume_job_id:
            raise ValueError("无法读取原出题任务的断点；为防止混用内容，已停止本次恢复。") from exc
        return PracticeGenerationCheckpoint(source_job_id, (), ())

    source_payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    if resume_job_id:
        source_operation = str(record.get("operation") or "")
        if source_operation not in {"generate_from_plan", "generate_from_contract"}:
            raise ValueError("恢复来源不是题目生成任务，不能复用其中间结果。")
        if plan_fingerprint(source_payload) != plan_fingerprint(payload):
            raise ValueError("恢复任务的已确认蓝图与当前请求不一致，已阻止混用旧题目。")

    expected = set(expected_plan_item_ids)
    accepted: list[dict[str, Any]] = []
    accepted_ids: list[str] = []
    seen: set[str] = set()
    for item in record.get("partial_exercises") or []:
        if not isinstance(item, dict):
            raise ValueError("断点中的部分题目结构无效，已停止恢复。")
        plan_item_id = str(item.get("plan_item_id") or "").strip()
        if not plan_item_id or plan_item_id not in expected:
            raise ValueError(f"断点包含不属于当前蓝图的计划项：{plan_item_id or '缺少 ID'}。")
        if plan_item_id in seen:
            raise ValueError(f"断点包含重复计划项：{plan_item_id}。")
        if str(item.get("generation_status") or "completed") == "failed":
            continue
        seen.add(plan_item_id)
        accepted_ids.append(plan_item_id)
        accepted.append(dict(item))
    return PracticeGenerationCheckpoint(
        source_job_id,
        tuple(accepted),
        tuple(accepted_ids),
    )
