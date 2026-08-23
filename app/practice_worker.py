from __future__ import annotations

from typing import Any

from .exercise_generation import (
    analyze_practice_source,
    generate_practice_from_contract,
    generate_practice_from_plan,
    plan_practice_set,
)
from .practice_runtime import ensure_practice_generation_active
from .practice_store import find_completed_by_plan, save_practice_continuation_record, save_practice_record


def execute_practice_operation(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one durable practice operation outside the transport layer."""
    if operation == "analyze":
        return {"result": analyze_practice_source(payload), "history_id": ""}
    if operation == "plan":
        return {"result": plan_practice_set(payload), "history_id": ""}
    if operation in {"generate_from_plan", "generate_from_contract"}:
        existing = None if payload.get("fresh_generation") else find_completed_by_plan(payload)
        if existing:
            reused = {**existing.get("data", {})}
            reused["generation"] = {
                **(reused.get("generation") or {}),
                "reused_history_id": existing.get("history_id"),
                "reused": True,
            }
            return {"result": reused, "history_id": str(existing.get("history_id") or "")}
        result = (
            generate_practice_from_contract(payload)
            if operation == "generate_from_contract"
            else generate_practice_from_plan(payload)
        )
        ensure_practice_generation_active(payload)
        record = (
            save_practice_continuation_record(result, request=payload)
            if payload.get("resume_from_history_id")
            else save_practice_record(result, request=payload)
        )
        return {"result": record["data"], "history_id": record["history_id"]}
    raise ValueError("不支持的出题任务类型。")
