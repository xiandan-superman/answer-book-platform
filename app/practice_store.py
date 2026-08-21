from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from .paths import DATA_ROOT

PRACTICE_HISTORY_DIR = DATA_ROOT / "practice_history"
_PRACTICE_ANSWER_FIELDS = {"answer", "answer_summary", "solution_steps", "solution", "solutions", "verification_note"}
_DERIVED_HISTORY_SUFFIXES = ("_repaired", "_semantic_candidate")
_MAX_REVISIONS = 20
_STORE_LOCK = threading.RLock()


class PracticeEditConflict(ValueError):
    """A stale browser attempted to replace a question changed elsewhere."""


def _store_synchronized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _STORE_LOCK:
            return function(*args, **kwargs)

    return wrapped


def _write_json_atomic(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clean_task_title(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()[:80]


def _material_task_title(request: dict[str, Any], fallback: Any = "") -> str:
    """Resolve the human material name for new records and older history rows."""
    for value in (request.get("task_title"), request.get("knowledge_title")):
        title = _clean_task_title(value)
        if title:
            return title
    files = request.get("source_files") if isinstance(request.get("source_files"), list) else []
    names = [item.get("name") for item in files if isinstance(item, dict)] or request.get("source_file_names") or []
    for name in names:
        material_name = _clean_task_title(Path(str(name or "")).stem)
        if material_name:
            return material_name
    return _clean_task_title(fallback)


def _with_current_quality(data: dict[str, Any]) -> dict[str, Any]:
    """Apply the current deterministic gate without rewriting audit history."""
    from .exercise_generation import reconcile_practice_generation

    return _with_edit_versions(strip_practice_answer_content(reconcile_practice_generation(data)))


def _status_for_data(data: dict[str, Any]) -> str:
    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    generation = data.get("generation") if isinstance(data.get("generation"), dict) else {}
    return (
        "completed_with_issues"
        if quality.get("blocking_issues")
        or str(generation.get("status") or "") == "partial_success"
        or bool(generation.get("partial_success"))
        else "completed"
    )


def strip_practice_answer_content(data: dict[str, Any]) -> dict[str, Any]:
    """Return the public question-only representation without mutating audit history."""
    copied = json.loads(json.dumps(data, ensure_ascii=False)) if isinstance(data, dict) else {}
    copied.pop("_record_edit_version", None)
    exercises = copied.get("exercises") if isinstance(copied.get("exercises"), list) else []
    for item in exercises:
        if isinstance(item, dict):
            if str(item.get("verification_note") or "").strip():
                # Preserve only the fact that the condition-sufficiency check
                # was supplied. The note itself remains hidden with all other
                # answer-like content so it cannot leak a conclusion.
                item["answerability_check_status"] = "reported"
            for field in _PRACTICE_ANSWER_FIELDS:
                item.pop(field, None)
            item.pop("_edit_version", None)
    return copied


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"practice_[a-zA-Z0-9_-]{8,80}", text):
        raise ValueError("练习记录 ID 无效。")
    return text


def _path(history_id: str) -> Path:
    return PRACTICE_HISTORY_DIR / f"{_safe_id(history_id)}.json"


def _content_fingerprint(data: dict[str, Any] | None) -> str:
    copied = strip_practice_answer_content(data if isinstance(data, dict) else {})
    for field in ("history_id", "quality", "generation"):
        copied.pop(field, None)
    encoded = json.dumps(copied, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _with_edit_versions(data: dict[str, Any]) -> dict[str, Any]:
    exercises = data.get("exercises") if isinstance(data.get("exercises"), list) else []
    for item in exercises:
        if isinstance(item, dict):
            item["_edit_version"] = _content_fingerprint({"exercises": [item]})
    data["_record_edit_version"] = _content_fingerprint(data)
    return data


def _revision_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_items = before.get("exercises") if isinstance(before.get("exercises"), list) else []
    after_items = after.get("exercises") if isinstance(after.get("exercises"), list) else []
    changed: list[str] = []
    for index in range(max(len(before_items), len(after_items))):
        old = before_items[index] if index < len(before_items) and isinstance(before_items[index], dict) else None
        new = after_items[index] if index < len(after_items) and isinstance(after_items[index], dict) else None
        if _content_fingerprint({"exercises": [old] if old else []}) != _content_fingerprint({"exercises": [new] if new else []}):
            changed.append(str((new or old or {}).get("number") or index + 1))
    return {
        "before_count": len(before_items),
        "after_count": len(after_items),
        "changed_question_numbers": changed[:30],
        "changed_count": len(changed),
    }


def _canonical_history_records() -> list[tuple[Path, dict[str, Any]]]:
    """Return user-visible records, excluding repair/candidate side artifacts."""
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(PRACTICE_HISTORY_DIR.glob("practice_*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        history_id = str(record.get("history_id") or "")
        if not history_id or history_id.endswith(_DERIVED_HISTORY_SUFFIXES):
            continue
        if path != PRACTICE_HISTORY_DIR / f"{history_id}.json":
            continue
        records.append((path, record))
    return records


def plan_fingerprint(payload: dict[str, Any] | None) -> str:
    """Stable identity for a confirmed plan; excludes volatile model/runtime fields."""
    payload = payload if isinstance(payload, dict) else {}
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    selected = [
        {
            "source_question_id": str(row.get("source_question_id") or ""),
            "number": str(row.get("number") or ""),
            "title": str(row.get("title") or ""),
            "stem_excerpt": str(row.get("stem_excerpt") or ""),
            "source_content": str(row.get("source_content") or row.get("source_text") or ""),
            "question_type": str(row.get("question_type") or ""),
            "knowledge_points": row.get("knowledge_points") or [],
            "required_constraints": row.get("required_constraints") or {},
        }
        for row in payload.get("selected_source_questions") or []
        if isinstance(row, dict)
    ]
    raw_scope = payload.get("source_scope") or plan.get("source_scope") or {}
    scope = {
        "mode": raw_scope.get("mode") if isinstance(raw_scope, dict) else "",
        "granularity": raw_scope.get("granularity") if isinstance(raw_scope, dict) else "",
        "title": raw_scope.get("title") if isinstance(raw_scope, dict) else "",
        "questions": raw_scope.get("questions") if isinstance(raw_scope, dict) else [],
    }
    scope["questions"] = [
        {
            "source_question_id": str(row.get("source_question_id") or ""),
            "number": str(row.get("number") or ""),
            "title": str(row.get("title") or ""),
            "stem_excerpt": str(row.get("stem_excerpt") or ""),
            "source_content": str(row.get("source_content") or row.get("source_text") or ""),
            "question_type": str(row.get("question_type") or ""),
            "knowledge_points": row.get("knowledge_points") or [],
            "required_constraints": row.get("required_constraints") or {},
            "parent_id": str(row.get("parent_id") or ""),
            "source_ref": str(row.get("source_ref") or ""),
        }
        for row in (scope["questions"] or []) if isinstance(row, dict)
    ]
    materials = []
    for item in payload.get("source_files") or []:
        if not isinstance(item, dict):
            continue
        data_url = str(item.get("data_url") or "")
        materials.append({
            "name": str(item.get("name") or ""),
            "type": str(item.get("type") or ""),
            "size": int(item.get("size") or 0),
            "content_hash": hashlib.sha256(data_url.encode("utf-8")).hexdigest() if data_url else "",
        })
    identity = {
        "source_mode": payload.get("source_mode"),
        "generation_strategy": payload.get("generation_strategy"),
        "include_source_content_in_generation": payload.get("include_source_content_in_generation") is not False,
        "strategy_count": payload.get("strategy_count"),
        "variants_per_question": payload.get("variants_per_question"),
        "blueprint_multi_question_enabled": payload.get("blueprint_multi_question_enabled") is True,
        "blueprint_variants_per_item": payload.get("blueprint_variants_per_item"),
        "blueprint_variant_mode": payload.get("blueprint_variant_mode"),
        "difficulty_selection_order": payload.get("difficulty_selection_order"),
        "blueprint_variant_selection_order": payload.get("blueprint_variant_selection_order"),
        "count": payload.get("count"),
        "difficulty": payload.get("difficulty"),
        "difficulty_counts": payload.get("difficulty_counts") or {},
        "question_types": payload.get("question_types") or [],
        "selected_source_questions": selected,
        "source_scope": scope,
        "source_files": materials,
        "question_text_hash": hashlib.sha256(str(payload.get("question_text") or "").encode("utf-8")).hexdigest(),
        "exercise_plan": plan.get("blueprint", {}).get("exercise_plan") if isinstance(plan.get("blueprint"), dict) else plan.get("exercise_plan"),
        "generation_contract": payload.get("generation_contract") or {},
        "generation_run_id": str(payload.get("generation_run_id") or ""),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def find_completed_by_plan(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    fingerprint = plan_fingerprint(payload)
    if not fingerprint:
        return None
    PRACTICE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for _path_value, record in _canonical_history_records():
        status = record.get("status")
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        legacy_completed = status is None and isinstance(data.get("exercises"), list) and bool(data.get("exercises"))
        if status != "completed" and not legacy_completed:
            continue
        request = record.get("request") if isinstance(record.get("request"), dict) else {}
        stored = str(record.get("plan_fingerprint") or "") or plan_fingerprint(request)
        if stored == fingerprint and isinstance(record.get("data"), dict):
            data = _with_current_quality(record["data"])
            exercises = data.get("exercises") if isinstance(data.get("exercises"), list) else []
            quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
            reusable = (
                bool(exercises)
                and _status_for_data(data) == "completed"
                and not any(
                    isinstance(item, dict) and item.get("generation_status") == "failed"
                    for item in exercises
                )
                and int(quality.get("failed_count") or 0) == 0
                and int(quality.get("generated_count") or len(exercises)) == len(exercises)
            )
            if reusable:
                return {**record, "status": "completed", "data": data}
    return None


@_store_synchronized
def save_practice_record(
    data: dict[str, Any],
    *,
    request: dict[str, Any] | None = None,
    change_reason: str = "save",
) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("exercises"), list):
        raise ValueError("练习记录内容无效。")
    PRACTICE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_id = str(data.get("history_id") or "")
    existing: dict[str, Any] = {}
    if history_id:
        path = _path(history_id)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
    else:
        history_id = f"practice_{datetime.now():%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        path = _path(history_id)
    created_at = str(existing.get("created_at") or _now())
    public_data = strip_practice_answer_content(data)
    revisions = existing.get("revisions") if isinstance(existing.get("revisions"), list) else []
    previous_data = existing.get("data") if isinstance(existing.get("data"), dict) else {}
    if previous_data and _content_fingerprint(previous_data) != _content_fingerprint(public_data):
        revisions = [
            *revisions,
            {
                "revision_id": f"revision_{datetime.now():%Y%m%d%H%M%S}_{uuid4().hex[:8]}",
                "created_at": _now(),
                "reason": str(change_reason or "save")[:80],
                "summary": _revision_summary(previous_data, public_data),
                "data": strip_practice_answer_content(previous_data),
            },
        ][-_MAX_REVISIONS:]
    fingerprint = (
        plan_fingerprint(request)
        if isinstance(request, dict) and (isinstance(request.get("plan"), dict) or isinstance(request.get("generation_contract"), dict))
        else str(existing.get("plan_fingerprint") or "")
    )
    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    generation = data.get("generation") if isinstance(data.get("generation"), dict) else {}
    completed_with_issues = (
        bool(quality.get("blocking_issues"))
        or str(generation.get("status") or "") == "partial_success"
        or bool(generation.get("partial_success"))
    )
    record = {
        "history_id": history_id,
        "status": "completed_with_issues" if completed_with_issues else "completed",
        "created_at": created_at,
        "updated_at": _now(),
        "title": _material_task_title(request or {}, existing.get("title")) or str((data.get("blueprint") or {}).get("training_goal") or "研究生专项练习")[:120],
        "source_excerpt": str((request or existing.get("request") or {}).get("question_text") or "")[:240],
        "request": _compact_request(request) if request is not None else existing.get("request", {}),
        "generation_phases": existing.get("generation_phases") or (
            [
                {"operation": "analyze", "label": "范围解析", "status": "completed"},
                {"operation": "plan", "label": "蓝图设计", "status": "completed"},
                {"operation": "generate_from_plan", "label": "题目生成", "status": "completed"},
            ]
            if (request or {}).get("blueprint_review_enabled", True)
            else [
                {"operation": "analyze", "label": "范围解析", "status": "completed"},
                {"operation": "generate_from_contract", "label": "题目生成", "status": "completed"},
            ]
        ),
        "plan_fingerprint": fingerprint,
        "data": {**public_data, "history_id": history_id},
        "revisions": revisions,
    }
    _write_json_atomic(path, record)
    # Keep edit tokens out of the audit file, but include them in the very
    # first response. Otherwise a newly generated set is unprotected until
    # the browser happens to reload it from history.
    return {**record, "data": _with_current_quality(record["data"])}


def _compact_request(request: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    source_files: list[dict[str, Any]] = []
    missing_sources: list[str] = []
    for item in request.get("source_files") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "未命名文件")[:200]
        data_url = str(item.get("data_url") or "")
        row = {
            "name": name,
            "type": str(item.get("type") or "application/octet-stream")[:120],
            "size": int(item.get("size") or 0),
            "data_url": data_url if data_url.startswith("data:") else "",
        }
        if not row["data_url"]:
            missing_sources.append(name)
        source_files.append(row)
    source_status = "ready" if source_files and not missing_sources else ("blocked" if source_files or request.get("source_file_names") else "not_required")
    return {
        "practice_batch_id": str(request.get("practice_batch_id") or "")[:100],
        "task_title": _clean_task_title(request.get("task_title")),
        "source_mode": str(request.get("source_mode") or "exam")[:30],
        "knowledge_title": str(request.get("knowledge_title") or "")[:300],
        "question_text": str(request.get("question_text") or "")[:30000],
        "source_file_names": [
            str(item.get("name") or "")[:200]
            for item in request.get("source_files") or []
            if isinstance(item, dict)
        ],
        "source_files": source_files,
        "source_recovery": {"status": source_status, "missing_files": missing_sources},
        "count": request.get("count"),
        "difficulty": request.get("difficulty"),
        "difficulty_counts": request.get("difficulty_counts") or {},
        "question_types": request.get("question_types") or [],
        "focus": str(request.get("focus") or "")[:1000],
        "generation_strategy": request.get("generation_strategy"),
        "include_source_content_in_generation": request.get("include_source_content_in_generation") is not False,
        "strategy_count": request.get("strategy_count"),
        "variants_per_question": request.get("variants_per_question"),
        "blueprint_multi_question_enabled": request.get("blueprint_multi_question_enabled") is True,
        "blueprint_variants_per_item": request.get("blueprint_variants_per_item"),
        "blueprint_variant_mode": str(request.get("blueprint_variant_mode") or "")[:30],
        "difficulty_selection_order": int(request.get("difficulty_selection_order") or 0),
        "blueprint_variant_selection_order": int(request.get("blueprint_variant_selection_order") or 0),
        "blueprint_review_enabled": bool(request.get("blueprint_review_enabled", True)),
        "semantic_review_enabled": bool(request.get("semantic_review_enabled", True)),
        "generation_run_id": str(request.get("generation_run_id") or "")[:100],
        "generation_contract": request.get("generation_contract") if isinstance(request.get("generation_contract"), dict) else {},
        "granularity": str((request.get("source_scope") or {}).get("granularity") or request.get("granularity") or "")[:20],
        # 已确认范围检查点：保存校正后的来源快照（含层级/编辑/合并/拆分结果），供任务恢复时回到范围或蓝图
        "source_scope_checkpoint": request.get("source_scope") if isinstance(request.get("source_scope"), dict) else {},
        "source_analysis": request.get("source_analysis") if isinstance(request.get("source_analysis"), dict) else {},
        "selected_source_questions": [
            {
                "source_question_id": str(item.get("source_question_id") or "")[:80],
                "number": str(item.get("number") or "")[:50],
                "title": str(item.get("title") or "")[:300],
                "stem_excerpt": str(item.get("stem_excerpt") or "")[:1200],
                "source_content": str(item.get("source_content") or item.get("source_text") or "")[:18000],
                "question_type": str(item.get("question_type") or "")[:100],
                "knowledge_points": item.get("knowledge_points") if isinstance(item.get("knowledge_points"), list) else [],
                "required_constraints": item.get("required_constraints") if isinstance(item.get("required_constraints"), dict) else {},
            }
            for item in request.get("selected_source_questions") or []
            if isinstance(item, dict)
        ],
        "provider": request.get("provider"),
        "model": request.get("model"),
        "vision_provider": request.get("vision_provider"),
        "vision_model": request.get("vision_model"),
    }


def list_practice_records(limit: int = 30) -> list[dict[str, Any]]:
    PRACTICE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for _path_value, record in _canonical_history_records():
        data = _with_current_quality(record.get("data") if isinstance(record.get("data"), dict) else {})
        request = record.get("request") if isinstance(record.get("request"), dict) else {}
        source_mode = str(request.get("source_mode") or "exam")
        rows.append(
            {
                "history_id": record.get("history_id"),
                "title": _material_task_title(request, record.get("title")),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "source_excerpt": record.get("source_excerpt"),
                "question_count": len(data.get("exercises") or []),
                "generation": data.get("generation") or {},
                "quality": data.get("quality") or {},
                "generation_strategy": data.get("generation_strategy") or request.get("generation_strategy") or "",
                "status": _status_for_data(data),
                "generation_phases": record.get("generation_phases") or [],
                "source_mode": source_mode,
                "task_kind": "knowledge" if source_mode == "knowledge" else "practice",
                "practice_batch_id": request.get("practice_batch_id") or "",
                "request": {
                    "practice_batch_id": request.get("practice_batch_id") or "",
                    "provider": request.get("provider") or "",
                    "model": request.get("model") or "",
                },
                "source_recovery": request.get("source_recovery") or {"status": "blocked" if request.get("source_file_names") else "not_required"},
                "revision_count": len(record.get("revisions") or []),
            }
        )
        if len(rows) >= max(1, min(limit, 100)):
            break
    return rows


def load_practice_record(history_id: str) -> dict[str, Any]:
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    record = json.loads(path.read_text(encoding="utf-8"))
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    if "source_recovery" not in request:
        request["source_recovery"] = {"status": "blocked" if request.get("source_file_names") else "not_required", "missing_files": request.get("source_file_names", [])}
        record["request"] = request
    record["data"] = _with_current_quality(record.get("data") if isinstance(record.get("data"), dict) else {})
    record["status"] = _status_for_data(record["data"])
    record["revision_count"] = len(record.get("revisions") or [])
    record["revisions"] = [
        {key: value for key, value in revision.items() if key != "data"}
        for revision in record.get("revisions") or []
        if isinstance(revision, dict)
    ]
    return record


@_store_synchronized
def update_practice_exercise(
    history_id: str,
    exercise_index: int,
    exercise: dict[str, Any],
    *,
    change_reason: str = "regenerate_question",
    semantic_review: dict[str, Any] | None = None,
    expected_edit_version: str = "",
) -> dict[str, Any]:
    """Atomically patch one question without accepting a stale full-set copy."""
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    record = json.loads(path.read_text(encoding="utf-8"))
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    exercises = list(data.get("exercises") or [])
    if exercise_index < 0 or exercise_index >= len(exercises):
        raise ValueError("需要保存的题目序号无效。")
    if not isinstance(exercise, dict):
        raise ValueError("需要保存的题目内容无效。")
    current = exercises[exercise_index] if isinstance(exercises[exercise_index], dict) else {}
    current_edit_version = _content_fingerprint({"exercises": [current]})
    expected_edit_version = str(expected_edit_version or "").strip()
    if not expected_edit_version:
        raise PracticeEditConflict(
            f"第 {exercise_index + 1} 题缺少编辑版本，可能来自未刷新的旧页面，本次未覆盖现有内容。"
        )
    if expected_edit_version and expected_edit_version != current_edit_version:
        raise PracticeEditConflict(
            f"第 {exercise_index + 1} 题已在另一个页面或窗口中修改，本次未覆盖较新内容。"
        )
    patched = strip_practice_answer_content({"exercises": [{**exercise}]})["exercises"][0]
    for field in (
        "exercise_id",
        "number",
        "plan_item_id",
        "parent_plan_item_id",
        "variant_id",
        "variant_index",
        "variant_count",
        "variant_mode",
        "variant_role",
        "source_question_id",
    ):
        if current.get(field) not in (None, ""):
            patched[field] = current.get(field)
    exercises[exercise_index] = patched
    updated_semantic_review = semantic_review
    if updated_semantic_review is None and isinstance(data.get("semantic_review"), dict):
        previous_review = data["semantic_review"]
        target_number = str(current.get("number") or exercise_index + 1)
        review_items = []
        target_found = False
        for item in previous_review.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("number") or "") == target_number:
                review_items.append({"number": current.get("number") or exercise_index + 1, "status": "not_reviewed", "risks": []})
                target_found = True
            else:
                review_items.append(item)
        if not target_found:
            review_items.append({"number": current.get("number") or exercise_index + 1, "status": "not_reviewed", "risks": []})
        updated_semantic_review = {
            **previous_review,
            "status": "failed",
            "review_scope": "stale_after_edit",
            "items": review_items,
            "error": "题目内容已修改，原语义复核结论已失效。",
        }
    updated_data = {**data, "exercises": exercises, "history_id": history_id}
    if isinstance(updated_semantic_review, dict):
        updated_data["semantic_review"] = updated_semantic_review
    updated = _with_current_quality(updated_data)
    saved = save_practice_record(
        updated,
        request=record.get("request") if isinstance(record.get("request"), dict) else None,
        change_reason=change_reason,
    )
    return load_practice_record(str(saved["history_id"]))


@_store_synchronized
def rename_practice_record(history_id: str, title: str) -> dict[str, Any]:
    """Persist a user-edited task name without changing generated content."""
    clean_title = _clean_task_title(title)
    if not clean_title:
        raise ValueError("任务名称不能为空。")
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    record = json.loads(path.read_text(encoding="utf-8"))
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    record["title"] = clean_title
    record["request"] = {**request, "task_title": clean_title}
    record["updated_at"] = _now()
    _write_json_atomic(path, record)
    return {"ok": True, "task_id": history_id, "title": clean_title}


@_store_synchronized
def undo_last_practice_revision(history_id: str) -> dict[str, Any]:
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    record = json.loads(path.read_text(encoding="utf-8"))
    revisions = record.get("revisions") if isinstance(record.get("revisions"), list) else []
    if not revisions:
        raise ValueError("当前记录没有可撤销的修改。")
    target = revisions[-1]
    target_data = target.get("data") if isinstance(target, dict) and isinstance(target.get("data"), dict) else None
    if not target_data:
        raise ValueError("历史版本内容不完整，无法恢复。")
    current_data = record.get("data") if isinstance(record.get("data"), dict) else {}
    redo_revision = {
        "revision_id": f"revision_{datetime.now():%Y%m%d%H%M%S}_{uuid4().hex[:8]}",
        "created_at": _now(),
        "reason": "undo",
        "summary": _revision_summary(current_data, target_data),
        "data": strip_practice_answer_content(current_data),
    }
    record["data"] = {**strip_practice_answer_content(target_data), "history_id": history_id}
    record["updated_at"] = _now()
    record["revisions"] = [*revisions[:-1], redo_revision][-_MAX_REVISIONS:]
    _write_json_atomic(path, record)
    return load_practice_record(history_id)


@_store_synchronized
def delete_practice_record(history_id: str) -> dict[str, Any]:
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    path.unlink()
    return {"ok": True, "history_id": history_id}
