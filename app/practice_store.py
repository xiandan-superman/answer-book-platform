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
from .task_titles import friendly_material_title

PRACTICE_HISTORY_DIR = DATA_ROOT / "practice_history"
PRACTICE_JOB_DIR = DATA_ROOT / "practice_jobs"
_PRACTICE_ANSWER_FIELDS = {
    "answer",
    "answer_summary",
    "analysis",
    "explanation",
    "solution_steps",
    "solution",
    "solutions",
    "verification_note",
}
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
        material_name = friendly_material_title(name)
        if material_name:
            return material_name
    return _clean_task_title(fallback)


def _with_current_quality(data: dict[str, Any]) -> dict[str, Any]:
    """Apply the current deterministic gate without rewriting audit history."""
    from .exercise_generation import reconcile_practice_generation
    from .task_contracts import practice_completion_issue_contract

    current = strip_practice_answer_content(reconcile_practice_generation(data))
    current.pop("completion_issues", None)
    current["completion_issues"] = practice_completion_issue_contract(current)
    return _with_edit_versions(current)


def _status_for_data(data: dict[str, Any]) -> str:
    from .task_contracts import practice_completion_issue_contract

    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    completion = practice_completion_issue_contract(data)
    generated_count = int(completion.get("generated_count") or quality.get("generated_count") or 0)
    if generated_count == 0 and any(
        item.get("code") == "configuration_blocked"
        for item in completion.get("issues") or [] if isinstance(item, dict)
    ):
        return "failed"
    return "completed_with_issues" if completion.get("issues") else "completed"


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


def _stable_practice_source_refs(data: dict[str, Any], exercise: dict[str, Any]) -> list[str]:
    """Resolve immutable source bindings from the matching blueprint item."""
    plan_item_id = str(exercise.get("parent_plan_item_id") or exercise.get("plan_item_id") or "").strip()
    blueprint = data.get("blueprint") if isinstance(data.get("blueprint"), dict) else {}
    planned_item = next(
        (
            item
            for item in (blueprint.get("exercise_plan") or [])
            if isinstance(item, dict)
            and str(item.get("plan_item_id") or "").strip() == plan_item_id
        ),
        {},
    )
    candidates = (
        planned_item.get("source_refs")
        or exercise.get("source_refs")
        or [planned_item.get("source_question_id") or exercise.get("source_question_id")]
    )
    if not isinstance(candidates, list):
        candidates = [candidates]
    refs: list[str] = []
    for value in candidates or []:
        source_ref = str(value or "").strip()[:80]
        if source_ref and source_ref not in refs:
            refs.append(source_ref)
        if len(refs) >= 3:
            break
    return refs


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
    for field in ("history_id", "quality", "generation", "completion_issues"):
        copied.pop(field, None)
    encoded = json.dumps(copied, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _blueprint_fingerprint(blueprint: dict[str, Any] | None) -> str:
    """Hash only the normalized confirmed blueprint, never provider credentials."""
    normalized = blueprint if isinstance(blueprint, dict) else {}
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _successful_exercises_by_plan_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    successful: dict[str, dict[str, Any]] = {}
    for item in data.get("exercises") or []:
        if not isinstance(item, dict) or item.get("generation_status") == "failed":
            continue
        plan_item_id = str(item.get("plan_item_id") or "").strip()
        if plan_item_id and plan_item_id not in successful:
            successful[plan_item_id] = item
    return successful


def _unfinished_plan_item_ids(data: dict[str, Any], blueprint: dict[str, Any]) -> list[str]:
    successful = _successful_exercises_by_plan_id(data)
    planned = {
        str(item.get("plan_item_id") or "").strip()
        for item in blueprint.get("exercise_plan") or []
        if isinstance(item, dict) and str(item.get("plan_item_id") or "").strip()
    }
    return sorted(plan_item_id for plan_item_id in planned if plan_item_id not in successful)


def _continuation_snapshot(record: dict[str, Any], data: dict[str, Any], blueprint: dict[str, Any]) -> dict[str, Any]:
    revisions = [item for item in record.get("revisions") or [] if isinstance(item, dict)]
    return {
        "schema_version": "answer_book.practice_continuation.v1",
        "history_id": str(record.get("history_id") or ""),
        "history_updated_at": str(record.get("updated_at") or ""),
        "history_record_version": int(record.get("record_version") or 1),
        "revision_count": len(revisions),
        "blueprint_fingerprint": _blueprint_fingerprint(blueprint),
        "unfinished_plan_item_ids": _unfinished_plan_item_ids(data, blueprint),
    }


def _continuation_key(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"practice_continuation_v1_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _recover_legacy_image_route(history_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Recover image-route fields omitted by history records written before this fix."""

    recovered = copy_request(request)
    if str(recovered.get("image_orchestration") or "") != "main_model_tool_loop":
        return recovered
    if str(recovered.get("image_provider") or "").strip() and str(recovered.get("image_model") or "").strip():
        return recovered
    for path in sorted(PRACTICE_JOB_DIR.glob("generation_*.json"), reverse=True):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(job.get("history_id") or "") != str(history_id or ""):
            continue
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if str(payload.get("image_orchestration") or "") != "main_model_tool_loop":
            continue
        image_provider = str(payload.get("image_provider") or "").strip()
        image_model = str(payload.get("image_model") or "").strip()
        if image_provider and image_model:
            recovered["image_provider"] = image_provider[:100]
            recovered["image_model"] = image_model[:200]
            recovered["image_orchestration"] = "main_model_tool_loop"
            recovered["image_route_recovered_from_job"] = True
            break
    return recovered


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
            "recognized_content": str(row.get("recognized_content") or ""),
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
            "recognized_content": str(row.get("recognized_content") or ""),
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
            completion = data.get("completion_issues") if isinstance(data.get("completion_issues"), dict) else {}
            completion_codes = {
                str(item.get("code") or "")
                for item in completion.get("issues") or []
                if isinstance(item, dict)
            }
            reusable = (
                bool(exercises)
                and not completion_codes.intersection({"configuration_blocked", "generation_incomplete"})
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
    from .task_contracts import practice_completion_issue_contract
    public_data.pop("completion_issues", None)
    public_data["completion_issues"] = practice_completion_issue_contract(public_data)
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
    content_changed = not previous_data or _content_fingerprint(previous_data) != _content_fingerprint(public_data)
    record = {
        "history_id": history_id,
        "status": _status_for_data(public_data),
        "created_at": created_at,
        "updated_at": _now(),
        "record_version": int(existing.get("record_version") or 0) + (1 if content_changed else 0),
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
    try:
        from .image_artifacts import mark_final_adopted_assets

        mark_final_adopted_assets(record["data"])
    except Exception:
        # Artifact metadata must never turn an otherwise valid saved exercise
        # set into a failed or unsaved user result.
        pass
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
        resource_id = str(item.get("resource_id") or "").strip()
        if re.fullmatch(r"psrc_[0-9a-f]{64}", resource_id):
            row["resource_id"] = resource_id
        upload_item_id = str(item.get("upload_item_id") or "")[:200]
        sha256 = str(item.get("sha256") or "").strip().lower()
        if upload_item_id:
            row["upload_item_id"] = upload_item_id
        if re.fullmatch(r"[0-9a-f]{64}", sha256):
            row["sha256"] = sha256
        if not row["data_url"] and not row.get("resource_id"):
            missing_sources.append(name)
        source_files.append(row)
    source_status = "ready" if source_files and not missing_sources else ("blocked" if source_files or request.get("source_file_names") else "not_required")
    return {
        "practice_batch_id": str(request.get("practice_batch_id") or "")[:100],
        "continuation_key": str(request.get("continuation_key") or "")[:120],
        "continuation_attempt_id": str(request.get("continuation_attempt_id") or "")[:120],
        "continuation_snapshot": (
            copy_request(request.get("continuation_snapshot"))
            if isinstance(request.get("continuation_snapshot"), dict)
            else {}
        ),
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
        # The orchestration mode and its concrete image route are one atomic
        # user choice.  Persist all three so continuation/regeneration cannot
        # silently lose the image service or drift onto another route.
        "image_orchestration": str(request.get("image_orchestration") or "legacy_figure_pipeline")[:40],
        "image_provider": str(request.get("image_provider") or "")[:100],
        "image_model": str(request.get("image_model") or "")[:200],
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
                "recognized_content": str(item.get("recognized_content") or "")[:18000],
                "source_content": str(item.get("source_content") or item.get("source_text") or "")[:18000],
                "content_refs": item.get("content_refs") if isinstance(item.get("content_refs"), list) else [],
                "evidence_refs": item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else [],
                "visual_evidence_refs": item.get("visual_evidence_refs") if isinstance(item.get("visual_evidence_refs"), list) else [],
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


@_store_synchronized
def build_practice_continuation_payload(history_id: str, *, attempt_id: str = "") -> dict[str, Any]:
    """Build a trusted request that reuses successes and generates only unfinished slots."""
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    record = json.loads(path.read_text(encoding="utf-8"))
    data = _with_current_quality(record.get("data") if isinstance(record.get("data"), dict) else {})
    request = _recover_legacy_image_route(
        history_id,
        record.get("request") if isinstance(record.get("request"), dict) else {},
    )
    blueprint = data.get("blueprint") if isinstance(data.get("blueprint"), dict) else {}
    exercise_plan = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []
    if not exercise_plan:
        raise ValueError("历史记录缺少已确认蓝图，无法安全继续未完成项。")
    snapshot = _continuation_snapshot(record, data, blueprint)
    unfinished_ids = snapshot["unfinished_plan_item_ids"]
    if not unfinished_ids:
        raise ValueError("这份练习没有未完成题目，无需继续生成。")
    total_count = len(exercise_plan)
    continuation_key = _continuation_key(snapshot)
    source_scope = data.get("source_scope") if isinstance(data.get("source_scope"), dict) else request.get("source_scope_checkpoint") or {}
    source_analysis = data.get("source_analysis") if isinstance(data.get("source_analysis"), dict) else request.get("source_analysis") or {}
    selected = data.get("selected_source_questions") if isinstance(data.get("selected_source_questions"), list) else request.get("selected_source_questions") or []
    plan = {
        "schema_version": "answer_book.practice_plan.v1",
        "source_mode": data.get("source_mode") or request.get("source_mode") or "exam",
        "knowledge_title": data.get("knowledge_title") or request.get("knowledge_title") or "",
        "source_scope": source_scope,
        "source_analysis": source_analysis,
        "selected_source_questions": selected,
        "include_source_content_in_generation": data.get("include_source_content_in_generation") is not False,
        "blueprint": blueprint,
    }
    difficulty_counts = {
        level: sum(1 for item in exercise_plan if isinstance(item, dict) and str(item.get("difficulty") or "进阶") == level)
        for level in ("基础", "进阶", "挑战")
    }
    return {
        **copy_request(request),
        "plan": plan,
        "source_scope": source_scope,
        "source_analysis": source_analysis,
        "selected_source_questions": selected,
        "count": total_count,
        "difficulty_counts": difficulty_counts,
        "question_types": list(dict.fromkeys(
            str(item.get("question_type") or "综合题") for item in exercise_plan if isinstance(item, dict)
        )),
        "resume_from_history_id": history_id,
        "reset_generation_retry_state": True,
        "fresh_generation": True,
        "continuation_key": continuation_key,
        "continuation_attempt_id": str(attempt_id or "").strip()[:120],
        "continuation_snapshot": snapshot,
        "practice_batch_id": f"continue_{continuation_key.rsplit('_', 1)[-1][:24]}",
        "task_title": _material_task_title(request, record.get("title")),
    }


def copy_request(request: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe copy kept local so continuation never mutates stored input."""
    return json.loads(json.dumps(request, ensure_ascii=False))


def _merge_continuation_semantic_review(
    latest: dict[str, Any],
    incoming: dict[str, Any],
    *,
    latest_success_ids: set[str],
    merged_exercises: list[dict[str, Any]],
) -> dict[str, Any] | None:
    latest_review = latest.get("semantic_review") if isinstance(latest.get("semantic_review"), dict) else None
    incoming_review = incoming.get("semantic_review") if isinstance(incoming.get("semantic_review"), dict) else None
    if not latest_review and not incoming_review:
        return None
    latest_by_number = {
        str(item.get("number") or ""): item
        for item in (latest_review or {}).get("items") or []
        if isinstance(item, dict)
    }
    incoming_by_number = {
        str(item.get("number") or ""): item
        for item in (incoming_review or {}).get("items") or []
        if isinstance(item, dict)
    }
    items: list[dict[str, Any]] = []
    for index, exercise in enumerate(merged_exercises, start=1):
        if exercise.get("generation_status") == "failed":
            continue
        number = str(exercise.get("number") or index)
        plan_item_id = str(exercise.get("plan_item_id") or "")
        selected = (
            latest_by_number.get(number)
            if plan_item_id in latest_success_ids
            else incoming_by_number.get(number)
        ) or incoming_by_number.get(number) or latest_by_number.get(number)
        items.append(dict(selected) if isinstance(selected, dict) else {"number": exercise.get("number") or index, "status": "not_reviewed", "risks": []})
    all_passed = bool(items) and all(str(item.get("status") or "") == "passed" for item in items)
    base = incoming_review or latest_review or {}
    return {
        **base,
        "status": "passed" if all_passed else "failed",
        "review_scope": "merged_continuation",
        "items": items,
        **({} if all_passed else {"error": str(base.get("error") or "合并后的题目包含尚未完成语义复核的内容。")}),
    }


@_store_synchronized
def save_practice_continuation_record(
    data: dict[str, Any],
    *,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Merge a continuation result without replacing newer successful questions."""
    history_id = str(request.get("resume_from_history_id") or data.get("history_id") or "").strip()
    if not history_id:
        raise ValueError("继续任务缺少历史记录 ID，已阻止覆盖保存。")
    path = _path(history_id)
    if not path.exists():
        raise FileNotFoundError("练习记录不存在。")
    existing = json.loads(path.read_text(encoding="utf-8"))
    latest = existing.get("data") if isinstance(existing.get("data"), dict) else {}
    latest_blueprint = latest.get("blueprint") if isinstance(latest.get("blueprint"), dict) else {}
    snapshot = request.get("continuation_snapshot") if isinstance(request.get("continuation_snapshot"), dict) else {}
    expected_blueprint = str(snapshot.get("blueprint_fingerprint") or "")
    if not expected_blueprint or expected_blueprint != _blueprint_fingerprint(latest_blueprint):
        raise PracticeEditConflict("继续任务的蓝图已被修改，本次结果未覆盖较新的历史内容。")

    latest_success = _successful_exercises_by_plan_id(latest)
    incoming_by_id = {
        str(item.get("plan_item_id") or "").strip(): item
        for item in data.get("exercises") or []
        if isinstance(item, dict) and str(item.get("plan_item_id") or "").strip()
    }
    latest_by_id = {
        str(item.get("plan_item_id") or "").strip(): item
        for item in latest.get("exercises") or []
        if isinstance(item, dict) and str(item.get("plan_item_id") or "").strip()
    }
    merged_exercises: list[dict[str, Any]] = []
    for item in latest_blueprint.get("exercise_plan") or []:
        if not isinstance(item, dict):
            continue
        plan_item_id = str(item.get("plan_item_id") or "").strip()
        selected = latest_success.get(plan_item_id) or incoming_by_id.get(plan_item_id) or latest_by_id.get(plan_item_id)
        if isinstance(selected, dict):
            merged_exercises.append(copy_request(selected))

    merged = {
        **copy_request(data),
        "history_id": history_id,
        "blueprint": copy_request(latest_blueprint),
        "exercises": merged_exercises,
    }
    semantic_review = _merge_continuation_semantic_review(
        latest,
        merged,
        latest_success_ids=set(latest_success),
        merged_exercises=merged_exercises,
    )
    if semantic_review is not None:
        merged["semantic_review"] = semantic_review
    generation = merged.get("generation") if isinstance(merged.get("generation"), dict) else {}
    merged["generation"] = {
        **generation,
        "continuation_merge": {
            "base_updated_at": str(snapshot.get("history_updated_at") or ""),
            "committed_over_updated_at": str(existing.get("updated_at") or ""),
            "latest_successes_preserved": len(latest_success),
        },
    }
    return save_practice_record(merged, request=request, change_reason="continue_unfinished")


def list_practice_records(limit: int = 30) -> list[dict[str, Any]]:
    PRACTICE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for _path_value, record in _canonical_history_records():
        data = _with_current_quality(record.get("data") if isinstance(record.get("data"), dict) else {})
        request = record.get("request") if isinstance(record.get("request"), dict) else {}
        source_mode = str(request.get("source_mode") or "exam")
        quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
        generation = data.get("generation") if isinstance(data.get("generation"), dict) else {}
        completion = data.get("completion_issues") if isinstance(data.get("completion_issues"), dict) else {}
        total_count = int(completion.get("total_count") or quality.get("total_count") or len(data.get("exercises") or []))
        generated_count = int(completion.get("generated_count") or quality.get("generated_count") or 0)
        rows.append(
            {
                "history_id": record.get("history_id"),
                "title": _material_task_title(request, record.get("title")),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "source_excerpt": record.get("source_excerpt"),
                "question_count": generated_count,
                "generated_count": generated_count,
                "total_count": total_count,
                "unfinished_count": int(completion.get("unfinished_count") or max(0, total_count - generated_count)),
                "failed_count": int(completion.get("failed_count") or quality.get("failed_count") or 0),
                "configuration_blocked": generation.get("configuration_blocked") is True,
                "route_blocked": generation.get("route_blocked") is True,
                "requires_configuration": generation.get("requires_configuration") is True,
                "generation": generation,
                "quality": quality,
                "completion_issues": completion,
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
                    # Basenames only: enough to distinguish automatic titles
                    # from user renames without exposing local paths/payloads.
                    "source_file_names": [
                        str(name or "")
                        for name in (
                            request.get("source_file_names")
                            or [
                                item.get("name")
                                for item in request.get("source_files") or []
                                if isinstance(item, dict)
                            ]
                        )
                        if str(name or "").strip()
                    ],
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
    request = _recover_legacy_image_route(
        history_id,
        record.get("request") if isinstance(record.get("request"), dict) else {},
    )
    record["request"] = request
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
    practice_updates: dict[str, Any] | None = None,
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
    stable_source_refs = _stable_practice_source_refs(data, current)
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
    if stable_source_refs:
        patched["source_question_id"] = stable_source_refs[0]
        patched["source_refs"] = stable_source_refs
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
    practice_updates = practice_updates if isinstance(practice_updates, dict) else {}
    incoming_blueprint_item = (
        practice_updates.get("blueprint_item")
        if isinstance(practice_updates.get("blueprint_item"), dict)
        else None
    )
    if incoming_blueprint_item:
        target_plan_item_id = str(current.get("parent_plan_item_id") or current.get("plan_item_id") or "").strip()
        incoming_plan_item_id = str(incoming_blueprint_item.get("plan_item_id") or "").strip()
        blueprint = dict(data.get("blueprint") or {})
        plan_items = [dict(item) for item in (blueprint.get("exercise_plan") or []) if isinstance(item, dict)]
        target_index = next(
            (
                index for index, item in enumerate(plan_items)
                if str(item.get("plan_item_id") or "").strip() == target_plan_item_id
            ),
            -1,
        )
        if not target_plan_item_id or incoming_plan_item_id != target_plan_item_id or target_index < 0:
            raise ValueError("局部复审返回的蓝图项与当前题目不一致，本次未保存。")
        existing_item = plan_items[target_index]
        merged_item = {**existing_item, **incoming_blueprint_item}
        for field in (
            "plan_item_id",
            "number",
            "source_question_id",
            "source_refs",
            "required_knowledge_points",
            "required_constraints",
        ):
            if field in existing_item:
                merged_item[field] = existing_item[field]
        plan_items[target_index] = merged_item
        blueprint["exercise_plan"] = plan_items
        updated_data["blueprint"] = blueprint
        from .exercise_generation import audit_practice_blueprint

        verified_audit = audit_practice_blueprint(updated_data)
        if target_plan_item_id in set(verified_audit.get("local_blocking_item_ids") or []):
            raise ValueError("局部复审未清除该蓝图项的问题，本次未覆盖原记录。")
        updated_data["blueprint_audit"] = verified_audit
        if isinstance(practice_updates.get("blueprint_audit_repair"), dict):
            updated_data["blueprint_audit_repair"] = practice_updates["blueprint_audit_repair"]
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
