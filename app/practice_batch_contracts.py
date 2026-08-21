from __future__ import annotations

from typing import Any, Callable


def partition_practice_batch_rows(
    raw: Any,
    *,
    expected_count: int,
    clean_value: Callable[[Any, int], str] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Accept only one in-range row per temporary batch index.

    Invalid, duplicate, out-of-range and nullable provider output becomes a
    missing slot.  Callers may then recover that slot independently without
    replacing healthy siblings or binding a response to the wrong blueprint.
    """

    payload = raw if isinstance(raw, dict) else {}
    raw_rows = payload.get("exercises")
    rows: list[Any] = raw_rows if isinstance(raw_rows, list) else []
    expected_indexes = list(range(1, max(0, int(expected_count)) + 1))
    actual_indexes: list[Any] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        raw_index: Any = item.get("batch_index")
        try:
            actual_indexes.append(int(raw_index))
        except (TypeError, ValueError):
            actual_indexes.append(clean_value(raw_index, 40) if clean_value else str(raw_index or "")[:40])

    by_index: dict[int, dict[str, Any]] = {}
    duplicate_indexes: set[int] = set()
    invalid_index_count = 0
    for item in rows:
        if not isinstance(item, dict):
            invalid_index_count += 1
            continue
        raw_index = item.get("batch_index")
        try:
            local_index = int(raw_index)
        except (TypeError, ValueError):
            invalid_index_count += 1
            continue
        if local_index not in expected_indexes:
            invalid_index_count += 1
            continue
        if local_index in by_index or local_index in duplicate_indexes:
            duplicate_indexes.add(local_index)
            by_index.pop(local_index, None)
            continue
        by_index[local_index] = dict(item)
    missing_indexes = [index for index in expected_indexes if index not in by_index]
    return by_index, {
        "actual_indexes": actual_indexes,
        "accepted_indexes": sorted(by_index),
        "missing_indexes": missing_indexes,
        "duplicate_indexes": sorted(duplicate_indexes),
        "invalid_item_count": invalid_index_count,
    }


def complete_practice_slots(
    exercises: list[dict[str, Any]],
    exercise_plan: list[dict[str, Any]],
    *,
    failed_placeholder: Callable[[dict[str, Any], int, dict[str, Any]], dict[str, Any]],
    failures: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fill missing blueprint slots and restore exact plan-item order."""

    failure_by_id = failures or {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in exercises:
        if not isinstance(item, dict):
            continue
        plan_item_id = str(item.get("plan_item_id") or "").strip()
        if plan_item_id and plan_item_id not in by_id:
            by_id[plan_item_id] = item
    completed: list[dict[str, Any]] = []
    for index, planned_item in enumerate(exercise_plan, start=1):
        if not isinstance(planned_item, dict):
            continue
        plan_item_id = str(planned_item.get("plan_item_id") or f"plan_item_{index:02d}")
        if plan_item_id in by_id:
            completed.append(by_id[plan_item_id])
            continue
        error = failure_by_id.get(plan_item_id) or {
            "code": "provider_generation_missing",
            "message": "上游模型未返回本题。",
            "retryable": True,
            "detail": "",
        }
        completed.append(failed_placeholder(planned_item, index, error))
    return completed
