from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
from dataclasses import asdict, replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .answer_coverage_audit import audit_answer_coverage
from .api_key_config import api_key_file_info
from .audit_review_gate import get_pending_review_decision, submit_review_decision
from .capabilities.quality_metrics import build_quality_metrics_report
from .delivery_package import build_task_delivery_package
from .environment import check_environment, repair_environment
from .exam_structure_review import get_pending_exam_structure_review, submit_exam_structure_review
from .exercise_generation import (
    audit_practice_blueprint,
    ensure_practice_blueprint_defaults,
    generate_plan_draft,
    generate_practice_from_contract,
    generate_practice_from_plan,
    generate_practice_set,
    plan_practice_set,
    reconcile_practice_generation,
    regenerate_plan_item,
    regenerate_practice_exercise,
    scope_cover_summary,
    validate_practice_mode_contract,
)
from .final_acceptance import build_final_acceptance_report
from .http_errors import public_error_payload
from .hybrid_client import HybridClientError, hybrid_settings_payload, save_hybrid_enabled
from .lan_access import ensure_lan_access_config, lan_access_enabled, lan_access_info, lan_credentials
from .library_files import delete_library_file, save_library_upload_stream, scan_library_files
from .llm_client import LLMError, OpenAICompatibleClient, parse_json_content
from .local_config import update_dotenv_values
from .page_map_admin import page_map_summary, write_page_map_rows
from .paths import PROJECT_ROOT, TEXTBOOKS_DIR, WEB_DIR, ensure_project_dirs
from .pipeline import output_dir, stage_dir
from .practice_export import (
    build_practice_question_docx,
    resolve_practice_export_payload,
    validate_docx_output,
    validate_practice_export,
)
from .practice_export_jobs import (
    create_or_reuse_practice_export_job,
    load_practice_export_job,
    practice_export_download,
    recover_practice_export_jobs,
    retry_practice_export_job,
)
from .practice_jobs import (
    cancel_practice_job,
    cleanup_practice_jobs,
    create_or_reuse_practice_job,
    delete_jobs_for_history,
    delete_practice_job,
    list_practice_jobs,
    load_practice_job,
    recover_practice_jobs,
    rename_practice_job,
)
from .practice_queue import (
    enqueue_practice_job,
    recover_practice_queue,
    start_practice_queue_consumer,
    stop_practice_queue_consumer,
)
from .practice_store import (
    PracticeEditConflict,
    build_practice_continuation_payload,
    delete_practice_record,
    find_completed_by_plan,
    list_practice_records,
    load_practice_record,
    rename_practice_record,
    save_practice_record,
    undo_last_practice_revision,
    update_practice_exercise,
)
from .process_lock import platform_process_lock
from .prompts import build_answer_fragment_prompt
from .read_snapshot import READ_SNAPSHOTS
from .review_export import build_question_review, write_question_review_csv
from .runtime_monitor import append_exception_log, append_runtime_log, build_system_status, read_runtime_logs, task_health_summary
from .settings import (
    DEFAULT_MODEL_MAX_TOKENS,
    get_provider,
    list_providers,
    provider_model_supports_vision,
    provider_supports_image_generation,
    resolve_provider_model,
)
from .shared_textbook_library import (
    fetch_remote_shared_library_catalog,
    get_shared_library_settings,
    publish_shared_textbook_library,
    save_shared_library_settings,
    shared_library_catalog,
    shared_library_package_path,
    sync_shared_textbook_library,
)
from .support_reporting import start_support_retry_worker, stop_support_retry_worker, submit_support_report, support_status
from .task_contracts import present_error, public_support_id
from .task_control import delete_task
from .task_diagnostics import build_task_diagnostics
from .task_read_model import build_exam_run, build_practice_runs
from .task_result_view import build_task_result_view
from .task_runner import control_exam_task, start_exam_task
from .task_store import create_task, list_tasks, load_task, recover_interrupted_tasks, save_task, task_dir
from .textbook_index_cache import prepare_textbook_index_cache, require_textbook_index_cache, textbook_index_cache_status
from .update_manager import UpdateError, apply_update, check_for_updates
from .v4_schema import validate_v4_answer_fragment
from .version import get_app_version, get_source_revision, get_version, release_manifest_status
from .word_format_tasks import (
    apply_word_format_task,
    create_word_format_task,
    delete_word_format_task,
    list_word_format_tasks,
    save_word_format_profile_settings,
    word_format_download_path,
    word_format_settings_payload,
    word_format_task_payload,
)

PROGRESS_STAGE_ORDER = [
    "environment",
    "extract_exam",
    "exam_structure_review",
    "question_understanding",
    "figure_schema_planning",
    "textbook_index",
    "knowledge_planning",
    "retrieval",
    "evidence_selection",
    "answer_generation",
    "answer_coverage",
    "figures",
    "content_quality",
    "content_quality_model_repair",
    "figures_after_content_quality_model_repair",
    "content_quality_local_repair",
    "docx",
    "docx_model_repair",
    "docx_repair",
    "question_review",
    "render",
    "acceptance",
    "final_acceptance",
    "completed",
]


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _provider_key_issue(label: str, provider) -> str:
    env_name = str(getattr(provider, "api_key_env", "") or "").strip()
    hint = f"（请到“API 配置”页面配置 {env_name}）" if env_name else "（请到“API 配置”页面检查）"
    return f"{label} {getattr(provider, 'name', 'unknown')} 未配置 API Key {hint}"


def _provider_key_validation_errors(entries: list[tuple[str, object]]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for label, provider in entries:
        name = str(getattr(provider, "name", "") or "")
        key = (label, name)
        if key in seen:
            continue
        seen.add(key)
        if not str(getattr(provider, "api_key", "") or "").strip():
            errors.append(_provider_key_issue(label, provider))
    return errors


def _read_json_if_exists(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _task_quality_summary(task_id: str) -> dict:
    sdir = stage_dir(task_id)
    names = {
        "exam_structure": "exam_structure_audit.json",
        "retrieval": "retrieval_audit.json",
        "evidence_selection": "evidence_selection.json",
        "answer_coverage": "answer_coverage_audit.json",
        "content_quality": "content_quality_audit.json",
        "docx": "docx_audit.json",
        "render": "render_audit.json",
        "acceptance": "acceptance_report.json",
        "final_acceptance": "final_acceptance_report.json",
    }
    summary = {}
    for key, filename in names.items():
        data = _read_json_if_exists(sdir / filename)
        if isinstance(data, dict):
            item = {
                "ok": data.get("ok", data.get("status") == "passed"),
                "issue_count": data.get("issue_count", len(data.get("issues", [])) if isinstance(data.get("issues"), list) else 0),
                "warning_count": data.get("warning_count", len(data.get("warnings", [])) if isinstance(data.get("warnings"), list) else 0),
            }
            if key == "final_acceptance":
                item.update(
                    {
                        "status": str(data.get("status") or ""),
                        "delivery_tier": str(data.get("delivery_tier") or ""),
                        "delivery_ready": bool(data.get("delivery_ready", data.get("ok", False))),
                        "formal_acceptance_passed": bool(
                            data.get(
                                "formal_acceptance_passed",
                                str(data.get("status") or "") in {"passed", "passed_with_warnings"},
                            )
                        ),
                        "issues": list(data.get("issues", []))[:50],
                        "warnings": list(data.get("warnings", []))[:50],
                    }
                )
            summary[key] = item
        else:
            summary[key] = None
    return summary


def _task_model_token_feedback(task_id: str) -> list[dict]:
    sdir = stage_dir(task_id)
    feedback: list[dict] = []
    for filename in ("knowledge_plans.json", "evidence_selection.json", "answer_fragments.json"):
        data = _read_json_if_exists(sdir / filename)
        if isinstance(data, dict) and isinstance(data.get("model_token_feedback"), list):
            feedback.extend(item for item in data["model_token_feedback"] if isinstance(item, dict))
    return feedback


def _task_current_progress(task_id: str, current_stage: str | None = None):
    sdir = stage_dir(task_id)
    if current_stage == "question_understanding":
        return _read_json_if_exists(sdir / "question_understanding_progress.json")
    if current_stage == "knowledge_planning":
        return _read_json_if_exists(sdir / "knowledge_planning_progress.json")
    if current_stage == "evidence_selection":
        return _read_json_if_exists(sdir / "evidence_selection_progress.json")
    if current_stage == "answer_generation":
        return _read_json_if_exists(sdir / "answer_generation_progress.json")
    if current_stage == "figures":
        return _read_json_if_exists(sdir / "figure_progress.json")
    return (
        _read_json_if_exists(sdir / "question_understanding_progress.json")
        or _read_json_if_exists(sdir / "knowledge_planning_progress.json")
        or _read_json_if_exists(sdir / "evidence_selection_progress.json")
        or _read_json_if_exists(sdir / "answer_generation_progress.json")
        or _read_json_if_exists(sdir / "figure_progress.json")
    )


def _stage_order_index(stage: str | None) -> int:
    try:
        return PROGRESS_STAGE_ORDER.index(str(stage or ""))
    except ValueError:
        return -1


def _stage_progress_percent(stage: str | None) -> int:
    text = str(stage or "")
    if text == "completed":
        return 100
    index = max(0, _stage_order_index(text))
    return min(95, round(((index + 1) / len(PROGRESS_STAGE_ORDER)) * 100))


def _task_progress_summary(task_id: str, task_row: dict) -> dict:
    status_path = stage_dir(task_id) / "pipeline_status.json"
    pipeline = _read_json_if_exists(status_path) or {}
    stages = pipeline.get("stages") if isinstance(pipeline, dict) else []
    stages = stages if isinstance(stages, list) else []
    actionable = [stage for stage in stages if isinstance(stage, dict) and stage.get("stage") != "pipeline"]
    last_stage = actionable[-1] if actionable else None
    current_stage = str(task_row.get("current_stage") or "")
    effective_stage = current_stage
    if last_stage:
        last_name = str(last_stage.get("stage") or "")
        if _stage_order_index(last_name) >= _stage_order_index(effective_stage):
            effective_stage = last_name
    if task_row.get("status") == "completed":
        effective_stage = "completed"
    review_required = any(stage.get("status") == "review_required" for stage in actionable)
    return {
        "effective_current_stage": effective_stage,
        "progress_percent": _stage_progress_percent(effective_stage),
        "pipeline_last_stage": last_stage,
        "pipeline_review_required": review_required,
    }


def _parse_task_time(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _normalize_thinking_mode(value: object) -> str:
    text = str(value or "auto").strip().lower()
    if text in {"low", "medium", "high", "xhigh"}:
        return text
    if text in {"enabled", "enable", "on", "true"}:
        return "enabled"
    if text in {"disabled", "disable", "off", "false"}:
        return "disabled"
    return "auto"


def _optional_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _provider_test_protocol_override(provider, body: dict) -> tuple[object, bool]:
    raw_protocol = str(body.get("api_protocol") or "").strip().lower()
    if not raw_protocol:
        return provider, False
    aliases = {
        "responses_api": "responses",
        "openai_compatible": "chat_completions",
    }
    protocol = aliases.get(raw_protocol, raw_protocol)
    if protocol not in {"responses", "chat_completions"}:
        raise ValueError(f"Unsupported API protocol: {raw_protocol}")
    fallback = _optional_bool(
        body.get("responses_fallback_to_chat"),
        bool(getattr(provider, "responses_fallback_to_chat", True)),
    )
    return replace(
        provider,
        api_protocol=protocol,
        responses_fallback_to_chat=fallback,
    ), True


def _provider_test_protocol_summary(retry_report: object, requested_protocol: str) -> dict[str, object]:
    summary: dict[str, object] = {
        "api_protocol_requested": requested_protocol,
        "api_protocol_used": requested_protocol,
        "protocol_fallback": False,
    }
    if not isinstance(retry_report, dict):
        return summary
    attempts = retry_report.get("attempts")
    if not isinstance(attempts, list):
        return summary
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict) or attempt.get("error"):
            continue
        used = str(attempt.get("protocol_used") or requested_protocol)
        summary["api_protocol_used"] = used
        reason = str(attempt.get("protocol_fallback_reason") or "")
        if reason:
            summary["protocol_fallback"] = True
            summary["protocol_fallback_reason"] = reason
        break
    return summary


def _task_duration_summary(task_row: dict) -> dict:
    start = _parse_task_time(task_row.get("run_started_at") or task_row.get("created_at"))
    if not start:
        return {"duration_seconds": 0, "duration_text": "暂无"}
    status = str(task_row.get("status") or "")
    if status not in {"running", "queued", "paused"} and task_row.get("last_run_duration_seconds") is not None:
        seconds = max(0, int(task_row.get("last_run_duration_seconds") or 0))
    else:
        end = datetime.now() if status in {"running", "queued", "paused"} else (_parse_task_time(task_row.get("updated_at")) or datetime.now())
        seconds = max(0, int((end - start).total_seconds()))
    return {"duration_seconds": seconds, "duration_text": _format_duration(seconds)}


def _enrich_task_row(task_row: dict) -> dict:
    row = dict(task_row)
    task_id = str(row.get("task_id") or "")
    if task_id:
        row.update(_task_progress_summary(task_id, row))
        try:
            row["review_decision_pending"] = bool(get_pending_review_decision(task_id).get("pending"))
        except Exception:
            row["review_decision_pending"] = False
        try:
            row["exam_structure_review_pending"] = bool(get_pending_exam_structure_review(task_id).get("pending"))
        except Exception:
            row["exam_structure_review_pending"] = False
    row.update(_task_duration_summary(row))
    try:
        row["health"] = task_health_summary(row, kind="exam")
    except Exception:
        row["health"] = {"health_status": "unknown", "current_operation": "暂无运行记录"}
    return row


def _practice_task_row(record: dict) -> dict:
    task_kind = str(record.get("task_kind") or "practice")
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    phases = record.get("generation_phases") if isinstance(record.get("generation_phases"), list) else []
    if not phases:
        phases = [{"operation": "generate_from_plan", "label": "题目生成", "status": "completed"}]
    return {
        "task_id": record.get("history_id"),
        "task_kind": task_kind,
        "practice_batch_id": request.get("practice_batch_id") or "",
        "operation": "generate_from_plan",
        "generation_phases": phases,
        "is_generation_task": True,
        "exam_path": record.get("title") or ("知识点出题" if task_kind == "knowledge" else "按题出题"),
        "textbooks_dir": "知识点出题" if task_kind == "knowledge" else "按题出题",
        "status": "completed",
        "current_stage": "completed",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "question_count": record.get("question_count") or 0,
        "generation": record.get("generation") or {},
        "duration_seconds": 0,
        "duration_text": "已完成",
        "progress_percent": 100,
    }


def _practice_job_task_row(record: dict) -> dict:
    status = str(record.get("status") or "queued")
    stage = str(record.get("current_stage") or "planning")
    elapsed = max(0, int(record.get("elapsed_seconds") or 0))
    running_progress = min(88, 30 + elapsed // 15) if record.get("operation") == "generate_from_plan" else min(88, 35 + elapsed // 10)
    return {
        "task_id": record.get("job_id"),
        "task_kind": record.get("task_kind") or "practice",
        "practice_batch_id": record.get("practice_batch_id") or "",
        "is_generation_task": True,
        "is_generation_job": True,
        "operation": record.get("operation") or "",
        "exam_path": record.get("title") or (
            "范围解析" if record.get("operation") == "analyze"
            else "蓝图设计" if record.get("operation") == "plan"
            else "题目生成"
        ),
        "textbooks_dir": "知识点出题" if record.get("task_kind") == "knowledge" else "按题出题",
        "status": status,
        "current_stage": stage,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "error": record.get("error") or "",
        "progress_message": record.get("progress_message") or "",
        "elapsed_seconds": elapsed,
        "duration_text": "后台生成中" if status in {"queued", "running"} else ("生成失败" if status == "failed" else "已完成"),
        "progress_percent": 15 if status == "queued" else (running_progress if status == "running" else 100),
    }


def _practice_job_api_payload(record: dict) -> dict:
    support_id = public_support_id(
        str(record.get("support_id") or ""),
        task_id=str(record.get("job_id") or ""),
    )
    presentation = present_error(
        str(record.get("error") or ""),
        stage=str(record.get("current_stage") or ""),
        support_id=support_id,
    )
    payload = {
        **record,
        "error": presentation.message if presentation else "",
        "support_id": support_id if presentation else "",
        "warning_reason": presentation.message if presentation else str(record.get("warning_reason") or ""),
        "suggested_action": presentation.retry_hint if presentation else str(record.get("suggested_action") or ""),
        "error_presentation": asdict(presentation) if presentation else None,
    }
    payload.pop("diagnostic_context", None)
    payload.pop("failure_context", None)
    return payload


def _start_practice_job(operation: str, payload: dict) -> dict:
    record = create_or_reuse_practice_job(operation, payload)
    if record.get("deduplicated"):
        return record
    return enqueue_practice_job(str(record["job_id"]))


def _validate_answer_fragments_payload(data) -> list[str]:
    issues: list[str] = []
    if not isinstance(data, dict):
        return ["answer_fragments payload must be a JSON object"]
    fragments = data.get("fragments")
    if not isinstance(fragments, list):
        return ["answer_fragments.fragments must be a list"]
    seen = set()
    for idx, fragment in enumerate(fragments, start=1):
        if not isinstance(fragment, dict):
            issues.append(f"fragment {idx}: must be an object")
            continue
        qid = str(fragment.get("question_id") or "").strip()
        if not qid:
            issues.append(f"fragment {idx}: missing question_id")
        elif qid in seen:
            issues.append(f"fragment {idx}: duplicate question_id {qid}")
        else:
            seen.add(qid)
        for issue in validate_v4_answer_fragment(fragment):
            issues.append(f"fragment {idx}: {issue}")
    return issues


def _answer_fragments_response(task_id: str) -> dict:
    path = stage_dir(task_id) / "answer_fragments.json"
    if not path.exists():
        return {"exists": False, "path": str(path), "ok": False, "issues": ["answer_fragments.json not found"], "data": None}
    data = _read_json_if_exists(path)
    issues = _validate_answer_fragments_payload(data)
    return {"exists": True, "path": str(path), "ok": not issues, "issues": issues, "data": data}


def _task_file_roots(task_id: str) -> list[Path]:
    return [stage_dir(task_id).resolve(), output_dir(task_id).resolve()]


def _prepare_selected_textbooks(
    task_id: str,
    selected_paths: list[str],
    textbook_display_names: dict[str, str] | None = None,
) -> Path | None:
    if not selected_paths:
        return None
    selected_dir = task_dir(task_id) / "selected_textbooks"
    selected_dir.mkdir(parents=True, exist_ok=True)
    allowed_root = TEXTBOOKS_DIR.resolve()
    copied: list[str] = []
    display_names: dict[str, str] = {}
    raw_display_names = textbook_display_names or {}
    for raw_path in selected_paths:
        source = Path(str(raw_path)).expanduser().resolve()
        try:
            source.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError(f"Selected textbook is outside the textbook library: {source}") from exc
        if not source.is_file():
            raise ValueError(f"Selected textbook not found: {source}")
        target = selected_dir / source.name
        shutil.copy2(source, target)
        copied.append(str(source))
        display_name = str(raw_display_names.get(str(source)) or raw_display_names.get(str(raw_path)) or "").strip()
        if display_name:
            display_names[str(source)] = display_name
    record = load_task(task_id)
    record.textbooks_dir = str(selected_dir.resolve())
    record.selected_textbooks = copied
    record.textbook_display_names = display_names
    save_task(record)
    return selected_dir


def _safe_task_file(task_id: str, raw_path: str) -> Path:
    target = Path(raw_path).expanduser().resolve()
    for root in _task_file_roots(task_id):
        try:
            target.relative_to(root)
        except ValueError:
            continue
        else:
            if target.is_file():
                return target
    raise FileNotFoundError("file is not inside this task outputs")


def _index_version_label() -> str:
    """Inject the formal user-facing app version without internal build labels."""
    return f"v{get_app_version()}"


def _inject_index_version(html: str) -> str:
    """把首页版本占位替换为服务端版本标签，不依赖前端异步刷新。"""
    placeholder = ">版本加载中...</span>"
    if placeholder in html:
        return html.replace(placeholder, f">{_index_version_label()}</span>")
    return html


def _render_word_format_page() -> bytes:
    template = PROJECT_ROOT / "standalone_word_format_reviewer" / "web" / "index.html"
    settings_json = json.dumps(word_format_settings_payload(), ensure_ascii=False, separators=(",", ":"))
    settings_json = settings_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    routes_json = json.dumps(
        {
            "settings": "/api/word-format/settings",
            "audit": "/api/word-format/audit",
            "taskBase": "/api/word-format/tasks",
        },
        separators=(",", ":"),
    )
    html = template.read_text(encoding="utf-8")
    html = html.replace("__INITIAL_SETTINGS_JSON__", settings_json)
    html = html.replace("__API_ROUTES_JSON__", routes_json)
    html = html.replace("__PLATFORM_HOSTED_JSON__", "true")
    return html.encode("utf-8")


class PlatformHandler(BaseHTTPRequestHandler):
    server_version = "AnswerBookPlatform/1.0"
    MAX_JSON_BODY_BYTES = 8 * 1024 * 1024
    # 无需局域网鉴权即可访问的公开路径：仅版本元数据，保证前端版本标签在
    # 监控鉴权部署下也能加载（“版本号没加上”根因）。
    PUBLIC_LAN_PATHS = {"/api/version"}

    def request_id(self) -> str:
        current = getattr(self, "_support_request_id", "")
        if current:
            return current
        supplied = str(self.headers.get("X-Request-ID") or "").strip()
        current = supplied if re.fullmatch(r"[A-Za-z0-9_.-]{6,80}", supplied) else secrets.token_hex(8)
        self._support_request_id = current
        return current

    def log_message(self, fmt: str, *args) -> None:
        message = fmt % args
        print(f"[server] {self.address_string()} {message}")
        append_runtime_log("server", f"{self.address_string()} {message}")

    def send_json(self, value, status: int = 200) -> None:
        data = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", self.request_id())
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def send_download(self, target: Path) -> None:
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(target.name)}")
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def is_local_client(self) -> bool:
        address = str(self.client_address[0] or "")
        return address in {"127.0.0.1", "::1"} or address.startswith("::ffff:127.")

    def lan_request_allowed(self) -> bool:
        if self.is_local_client():
            return True
        if not lan_access_enabled():
            return False
        header = str(self.headers.get("Authorization") or "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
            username, password = raw.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        expected_username, expected_password = lan_credentials()
        return secrets.compare_digest(username, expected_username) and secrets.compare_digest(password, expected_password)

    def require_lan_auth(self) -> bool:
        if self.lan_request_allowed():
            return True
        data = _json_bytes({"error": "局域网访问需要监控账号和密码。"})
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Answer Book Platform LAN", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)
        return False

    def shared_library_publish_allowed(self) -> bool:
        """Only the local owner or approved Tailscale identities may publish releases."""
        identity = str(self.headers.get("Tailscale-User-Login") or "").strip().lower()
        if identity:
            allowed = {
                item.strip().lower()
                for item in str(os.environ.get("ANSWER_BOOK_SHARED_LIBRARY_PUBLISHERS", "")).split(",")
                if item.strip()
            }
            return identity in allowed
        return self.client_address[0] in {"127.0.0.1", "::1"}

    def read_json(self, max_bytes: int | None = None):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        limit = self.MAX_JSON_BODY_BYTES if max_bytes is None else max_bytes
        if length > limit:
            if limit > self.MAX_JSON_BODY_BYTES:
                raise ValueError("请求内容超过 70 MB，请选择不超过 50 MB 的 Word 文件。")
            raise ValueError("请求内容超过 8 MB，请减少单次提交的内容或文件数量。")
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'",
        )

    def request_origin_allowed(self) -> bool:
        origin = str(self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.netloc.lower() == str(self.headers.get("Host") or "").strip().lower()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            self._do_GET()
        except FileNotFoundError as exc:
            payload = public_error_payload(exc, status=404, path=parsed.path)
            append_runtime_log(
                "server",
                f"请求资源不存在 [{payload['support_id']}]：{exc}",
                "warning",
                {"path": parsed.path, "support_id": payload["support_id"], "error_type": exc.__class__.__name__},
            )
            append_exception_log(exc, path=parsed.path, support_id=payload["support_id"], request_id=self.request_id())
            self.send_json(payload, status=404)
        except ValueError as exc:
            payload = public_error_payload(exc, status=400, path=parsed.path)
            append_runtime_log(
                "server",
                f"请求参数错误 [{payload['support_id']}]：{exc}",
                "warning",
                {"path": parsed.path, "support_id": payload["support_id"], "error_type": exc.__class__.__name__},
            )
            append_exception_log(exc, path=parsed.path, support_id=payload["support_id"], request_id=self.request_id())
            self.send_json(payload, status=400)
        except Exception as exc:
            payload = public_error_payload(exc, status=500, path=parsed.path)
            append_runtime_log(
                "server",
                f"请求处理失败 [{payload['support_id']}]：{exc}",
                "error",
                {"path": parsed.path, "support_id": payload["support_id"], "error_type": exc.__class__.__name__},
            )
            append_exception_log(exc, path=parsed.path, support_id=payload["support_id"], request_id=self.request_id())
            self.send_json(payload, status=500)

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        # 版本元数据（VERSION + 远端短哈希）非敏感，前后端均需无鉴权读取，
        # 否则局域网监控鉴权部署下前端无法加载版本标签。
        version_is_public = parsed.path in self.PUBLIC_LAN_PATHS
        if not version_is_public and not self.require_lan_auth():
            return
        parts = [unquote(x) for x in parsed.path.strip("/").split("/") if x]
        if parsed.path == "/api/lan/access":
            self.send_json(
                lan_access_info(
                    self.server.server_port,
                    include_secret=self.is_local_client(),
                    bind_host=str(self.server.server_address[0]),
                )
            )
            return
        if parsed.path == "/api/version":
            manifest_status = release_manifest_status()
            self.send_json(
                {
                    "platform": "Answer Book Platform",
                    "version": get_version(),
                    "app_version": get_app_version(),
                    "source_revision": get_source_revision(),
                    "desktop_launch_id": str(os.environ.get("ANSWER_BOOK_DESKTOP_LAUNCH_ID") or ""),
                    "release_manifest": "RELEASE_MANIFEST.json",
                    "release_manifest_exists": manifest_status["exists"],
                    "release_manifest_status": manifest_status,
                }
            )
            return
        if parsed.path == "/api/update/status":
            if not self.is_local_client():
                self.send_json({"ok": False, "error": "只能在运行程序的本机检查和安装更新。"}, status=403)
                return
            refresh = parse_qs(parsed.query).get("refresh", ["0"])[0] in {"1", "true", "yes"}
            try:
                self.send_json(check_for_updates(refresh=refresh))
            except UpdateError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/environment":
            self.send_json(check_environment())
            return
        if parsed.path == "/api/hybrid/settings":
            self.send_json(hybrid_settings_payload())
            return
        if parsed.path == "/api/providers":
            self.send_json({name: cfg.redacted() for name, cfg in list_providers().items()})
            return
        if parsed.path == "/api/providers/key-file":
            info = api_key_file_info()
            info.pop("path", None)
            self.send_json(info)
            return
        if parsed.path == "/word-format":
            data = _render_word_format_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_security_headers()
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/word-format/settings":
            self.send_json(word_format_settings_payload())
            return
        if len(parts) == 4 and parts[:3] == ["api", "word-format", "tasks"]:
            try:
                self.send_json(word_format_task_payload(parts[3]))
            except FileNotFoundError as exc:
                self.send_json({"error": str(exc)}, status=404)
            return
        if len(parts) == 5 and parts[:3] == ["api", "word-format", "tasks"] and parts[4] == "download":
            try:
                target, filename = word_format_download_path(parts[3])
            except FileNotFoundError as exc:
                self.send_json({"error": str(exc)}, status=404)
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_security_headers()
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/practice/history":
            self.send_json(
                READ_SNAPSHOTS.get(
                    "practice_history",
                    lambda: {"records": list_practice_records()},
                )
            )
            return
        if len(parts) == 4 and parts[:3] == ["api", "practice", "export-jobs"]:
            self.send_json({"ok": True, "job": load_practice_export_job(parts[3])})
            return
        if len(parts) == 5 and parts[:3] == ["api", "practice", "export-jobs"] and parts[4] == "download":
            target, filename = practice_export_download(parts[3])
            data = target.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
            self.send_header("Cache-Control", "no-store")
            self.send_security_headers()
            self.end_headers()
            self.wfile.write(data)
            return
        if len(parts) == 4 and parts[:3] == ["api", "practice", "jobs"]:
            include_payload = (parse_qs(parsed.query).get("detail") or [""])[0].lower() in {"1", "true", "yes"}
            self.send_json(_practice_job_api_payload(load_practice_job(parts[3], include_payload=include_payload)))
            return
        if len(parts) == 4 and parts[:3] == ["api", "practice", "history"]:
            self.send_json(load_practice_record(parts[3]))
            return
        if parsed.path == "/api/library-files":
            self.send_json(scan_library_files())
            return
        if parsed.path == "/api/shared-textbook-library/settings":
            self.send_json(get_shared_library_settings())
            return
        if parsed.path == "/api/shared-textbook-library/catalog":
            self.send_json(shared_library_catalog())
            return
        if len(parts) == 6 and parts[:3] == ["api", "shared-textbook-library", "packages"] and parts[5] == "download":
            self.send_download(shared_library_package_path(parts[3], parts[4]))
            return
        if parsed.path == "/api/system/status":
            access_host = self.headers.get("Host", "")
            self.send_json(
                READ_SNAPSHOTS.get(
                    f"system_status:{access_host}",
                    lambda: build_system_status(access_host),
                )
            )
            return
        if parsed.path == "/api/system/logs":
            self.send_json({"logs": read_runtime_logs()})
            return
        if parsed.path == "/api/support/status":
            self.send_json(support_status())
            return
        if parsed.path == "/api/quality/metrics":
            self.send_json(build_quality_metrics_report())
            return
        if parsed.path == "/api/tasks":
            def build_task_list() -> dict:
                exam_tasks = []
                for task in list_tasks():
                    enriched = _enrich_task_row(task)
                    exam_tasks.append(
                        build_exam_run(
                            enriched,
                            _task_quality_summary(str(task.get("task_id") or "")),
                        )
                    )
                practice_tasks = build_practice_runs(
                    list_practice_jobs(limit=100),
                    list_practice_records(limit=100),
                )
                return {
                    "tasks": exam_tasks + practice_tasks + list_word_format_tasks(),
                    "schema_version": 1,
                }

            self.send_json(READ_SNAPSHOTS.get("task_list", build_task_list))
            return
        if len(parts) == 3 and parts[:2] == ["api", "tasks"]:
            task_id = parts[2]
            record = load_task(task_id)
            status_path = stage_dir(task_id) / "pipeline_status.json"
            report_path = stage_dir(task_id) / "acceptance_report.json"
            quality_summary = _task_quality_summary(task_id)
            self.send_json(
                {
                    "task": build_exam_run(_enrich_task_row(record.__dict__), quality_summary),
                    "pipeline_status": _read_json_if_exists(status_path),
                    "current_progress": _task_current_progress(task_id, record.current_stage),
                    "acceptance_report": _read_json_if_exists(report_path),
                    "quality_summary": quality_summary,
                    "model_token_feedback": _task_model_token_feedback(task_id),
                }
            )
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "page-map":
            task_id = parts[2]
            record = load_task(task_id)
            self.send_json(
                page_map_summary(
                    stage_dir(task_id) / "textbook_page_map.csv",
                    Path(record.textbooks_dir).expanduser() / "textbook_page_map.manual.csv",
                )
            )
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "diagnostics":
            task_id = parts[2]
            self.send_json(build_task_diagnostics(task_id))
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "review-decision":
            task_id = parts[2]
            self.send_json(get_pending_review_decision(task_id))
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "exam-structure-review":
            task_id = parts[2]
            self.send_json(get_pending_exam_structure_review(task_id))
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "result-view":
            task_id = parts[2]
            self.send_json(build_task_result_view(task_id))
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "answer-fragments":
            task_id = parts[2]
            self.send_json(_answer_fragments_response(task_id))
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "review":
            task_id = parts[2]
            self.send_json(build_question_review(stage_dir(task_id)))
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "final-acceptance":
            task_id = parts[2]
            acceptance = _read_json_if_exists(stage_dir(task_id) / "acceptance_report.json") or {}
            self.send_json(build_final_acceptance_report(stage_dir(task_id), output_dir(task_id), require_render=bool(acceptance.get("rendered", True))))
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "files":
            task_id = parts[2]
            files = []
            for root in (stage_dir(task_id), output_dir(task_id)):
                if root.exists():
                    for p in sorted(root.rglob("*")):
                        if p.is_file():
                            files.append(
                                {
                                    "path": str(p),
                                    "name": p.name,
                                    "size": p.stat().st_size,
                                    "kind": "stage" if str(p).startswith(str(stage_dir(task_id))) else "output",
                                    "download_url": f"/api/tasks/{quote(task_id)}/download?path={quote(str(p))}",
                                }
                            )
            self.send_json({"task_id": task_id, "files": files})
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "download":
            task_id = parts[2]
            query = parse_qs(parsed.query)
            raw_path = query.get("path", [""])[0]
            target = _safe_task_file(task_id, raw_path)
            data = target.read_bytes()
            mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            safe_name = quote(target.name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{safe_name}")
            self.end_headers()
            self.wfile.write(data)
            return
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "preview":
            task_id = parts[2]
            query = parse_qs(parsed.query)
            raw_path = query.get("path", [""])[0]
            target = _safe_task_file(task_id, raw_path)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self.request_origin_allowed():
            self.send_json({"ok": False, "error": "请求来源与当前服务地址不一致。", "error_code": "origin_rejected"}, status=403)
            return
        if not self.require_lan_auth():
            return
        try:
            if parsed.path == "/api/support/report":
                body = self.read_json(max_bytes=512 * 1024)
                result = submit_support_report(body if isinstance(body, dict) else {})
                append_runtime_log(
                    "support_report",
                    f"问题反馈 {result.get('report_id', '')}：{result.get('status', '')}",
                    payload={"report_id": result.get("report_id"), "status": result.get("status")},
                )
                self.send_json(result)
                return
            if parsed.path == "/api/update/apply":
                if not self.is_local_client():
                    self.send_json({"ok": False, "error": "只能在运行程序的本机安装更新。"}, status=403)
                    return
                try:
                    result = apply_update()
                except UpdateError as exc:
                    append_runtime_log("update", f"程序更新未完成：{exc}", "warning")
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                append_runtime_log(
                    "update",
                    str(result.get("message") or "已处理程序更新"),
                    payload={
                        "action": result.get("action"),
                        "latest_version": result.get("latest_version"),
                        "restart_required": result.get("restart_required"),
                    },
                )
                self.send_json(result)
                return
            if parsed.path == "/api/word-format/audit":
                body = self.read_json(max_bytes=70 * 1024 * 1024)
                result = create_word_format_task(body)
                append_runtime_log(
                    "word_format",
                    f"完成 Word 格式审查 {result.get('task_id', '')}",
                    payload={"mode": result.get("mode"), "status": result.get("status")},
                )
                self.send_json(result)
                return
            if parsed.path == "/api/word-format/settings":
                body = self.read_json()
                normalized = save_word_format_profile_settings(
                    str(body.get("profile") or ""),
                    body.get("settings"),
                )
                self.send_json({"profile": body.get("profile"), "settings": normalized, "message": "已设为该标准的永久默认"})
                return
            parts = [unquote(x) for x in parsed.path.strip("/").split("/") if x]
            if len(parts) == 5 and parts[:3] == ["api", "practice", "export-jobs"] and parts[4] == "retry":
                export_job = retry_practice_export_job(parts[3])
                self.send_json(
                    {"ok": True, "job": export_job},
                    status=200 if export_job.get("status") == "completed" else 202,
                )
                return
            if len(parts) == 5 and parts[:3] == ["api", "word-format", "tasks"] and parts[4] == "apply":
                try:
                    result = apply_word_format_task(parts[3])
                except FileNotFoundError as exc:
                    self.send_json({"error": str(exc)}, status=404)
                    return
                append_runtime_log("word_format", f"应用 Word 格式修改 {parts[3]}", payload={"status": result.get("status")})
                self.send_json(result)
                return
            if len(parts) == 5 and parts[:3] == ["api", "word-format", "tasks"] and parts[4] == "delete":
                try:
                    result = delete_word_format_task(parts[3])
                except FileNotFoundError as exc:
                    self.send_json({"error": str(exc)}, status=404)
                    return
                append_runtime_log("word_format", f"删除 Word 格式审查任务 {parts[3]}")
                self.send_json(result)
                return
            if parsed.path == "/api/practice/generate":
                body = self.read_json()
                result = generate_practice_set(body)
                record = save_practice_record(result, request=body)
                result = record["data"]
                append_runtime_log(
                    "practice",
                    f"生成专项练习 {result.get('quality', {}).get('generated_count', 0)} 题",
                    payload={
                        "provider": result.get("generation", {}).get("provider", ""),
                        "model": result.get("generation", {}).get("model", ""),
                        "status": result.get("quality", {}).get("status", ""),
                    },
                )
                self.send_json(result)
                return
            if parsed.path == "/api/practice/jobs":
                body = self.read_json()
                operation = str(body.get("operation") or "")
                payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
                record = _start_practice_job(operation, payload)
                append_runtime_log(
                    "practice",
                    f"创建后台出题任务 {record['job_id']}",
                    payload={"task_id": record["job_id"], "operation": operation, "status": "queued"},
                )
                self.send_json(
                    {
                        "job_id": record["job_id"],
                        "status": record["status"],
                        "deduplicated": bool(record.get("deduplicated")),
                    },
                    status=202,
                )
                return
            if len(parts := [unquote(x) for x in parsed.path.strip("/").split("/") if x]) == 5 and parts[:3] == ["api", "practice", "history"] and parts[4] == "continue":
                payload = build_practice_continuation_payload(parts[3])
                record = _start_practice_job("generate_from_plan", payload)
                append_runtime_log(
                    "practice",
                    f"继续未完成题目 {parts[3]}",
                    payload={"task_id": record["job_id"], "history_id": parts[3], "status": "queued"},
                )
                self.send_json(
                    {"job_id": record["job_id"], "status": record["status"], "deduplicated": bool(record.get("deduplicated"))},
                    status=202,
                )
                return
            if len(parts := [unquote(x) for x in parsed.path.strip("/").split("/") if x]) == 5 and parts[:3] == ["api", "practice", "jobs"] and parts[4] == "cancel":
                body = self.read_json()
                self.send_json(cancel_practice_job(parts[3], str(body.get("reason") or "用户取消出题任务")))
                return
            if len(parts := [unquote(x) for x in parsed.path.strip("/").split("/") if x]) == 5 and parts[:3] == ["api", "practice", "tasks"] and parts[4] == "title":
                body = self.read_json()
                task_id = parts[3]
                title = str(body.get("title") or "")
                result = rename_practice_record(task_id, title) if task_id.startswith("practice_") else rename_practice_job(task_id, title)
                append_runtime_log("practice", f"修改出题任务名称 {task_id}", payload={"task_id": task_id, "title": result.get("title", "")})
                self.send_json(result)
                return
            if parsed.path == "/api/practice/plan":
                body = self.read_json()
                self.send_json(plan_practice_set(body))
                return
            if parsed.path == "/api/practice/generate-from-plan":
                body = self.read_json()
                existing = None if body.get("fresh_generation") else find_completed_by_plan(body)
                if existing:
                    reused = {**existing.get("data", {})}
                    reused["generation"] = {**(reused.get("generation") or {}), "reused_history_id": existing.get("history_id"), "reused": True}
                    self.send_json(reused)
                    return
                result = generate_practice_from_plan(body)
                record = save_practice_record(result, request=body)
                self.send_json(record["data"])
                return
            if parsed.path == "/api/practice/generate-from-contract":
                body = self.read_json()
                existing = None if body.get("fresh_generation") else find_completed_by_plan(body)
                if existing:
                    reused = {**existing.get("data", {})}
                    reused["generation"] = {**(reused.get("generation") or {}), "reused_history_id": existing.get("history_id"), "reused": True}
                    self.send_json(reused)
                    return
                result = generate_practice_from_contract(body)
                record = save_practice_record(result, request=body)
                self.send_json(record["data"])
                return
            if parsed.path == "/api/practice/regenerate":
                body = self.read_json()
                result = regenerate_practice_exercise(body)
                self.send_json(result)
                return
            if parsed.path == "/api/practice/plan-draft":
                body = self.read_json()
                result = generate_plan_draft(body)
                self.send_json(result)
                return
            if parsed.path == "/api/practice/plan-item-regenerate":
                body = self.read_json()
                self.send_json(regenerate_plan_item(body))
                return
            if parsed.path == "/api/practice/plan-audit":
                body = self.read_json()
                plan = body.get("plan") if isinstance(body.get("plan"), dict) else {}
                plan = ensure_practice_blueprint_defaults(plan)
                selected = plan.get("selected_source_questions") if isinstance(plan.get("selected_source_questions"), list) else []
                scope = plan.get("source_scope") if isinstance(plan.get("source_scope"), dict) else {}
                blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
                items = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []
                plan["scope_cover"] = scope_cover_summary(scope, selected, items)
                plan["mode_contract"] = validate_practice_mode_contract(plan)
                plan["blueprint_audit"] = audit_practice_blueprint(plan)
                self.send_json({
                    "blueprint": plan["blueprint"],
                    "include_source_content_in_generation": plan.get("include_source_content_in_generation", True),
                    "scope_cover": plan["scope_cover"],
                    "mode_contract": plan["mode_contract"],
                    "blueprint_audit": plan["blueprint_audit"],
                })
                return
            if parsed.path == "/api/practice/history":
                body = self.read_json()
                data = body.get("data") if isinstance(body.get("data"), dict) else body
                request = body.get("request") if isinstance(body.get("request"), dict) else None
                history_id = str(data.get("history_id") or "") if isinstance(data, dict) else ""
                if history_id:
                    try:
                        latest = load_practice_record(history_id)
                    except FileNotFoundError:
                        latest = None
                    if latest is not None:
                        expected_version = str(data.get("_record_edit_version") or "")
                        current_version = str((latest.get("data") or {}).get("_record_edit_version") or "")
                        if not expected_version or expected_version != current_version:
                            self.send_json(
                                {
                                    "ok": False,
                                    "error": "这份练习已在另一个页面或窗口中修改，本次未覆盖较新内容。",
                                    "error_code": "practice_edit_conflict",
                                    "suggested_action": "请保留当前内容，重新打开该练习查看最新版本后再合并修改。",
                                },
                                status=409,
                            )
                            return
                if isinstance(data, dict):
                    data = reconcile_practice_generation(data)
                record = save_practice_record(data, request=request, change_reason=str(body.get("change_reason") or "save"))
                self.send_json(load_practice_record(str(record["history_id"])))
                return
            if len(parts := [unquote(x) for x in parsed.path.strip("/").split("/") if x]) == 5 and parts[:3] == ["api", "practice", "history"] and parts[4] == "exercise":
                body = self.read_json()
                try:
                    result = update_practice_exercise(
                        parts[3],
                        int(body.get("exercise_index")),
                        body.get("exercise") if isinstance(body.get("exercise"), dict) else {},
                        change_reason=str(body.get("change_reason") or "regenerate_question"),
                        semantic_review=(
                            body.get("semantic_review")
                            if isinstance(body.get("semantic_review"), dict)
                            else None
                        ),
                        practice_updates=(
                            body.get("practice_updates")
                            if isinstance(body.get("practice_updates"), dict)
                            else None
                        ),
                        expected_edit_version=str(body.get("expected_edit_version") or ""),
                    )
                except PracticeEditConflict as exc:
                    self.send_json(
                        {
                            "ok": False,
                            "error": str(exc),
                            "error_code": "practice_edit_conflict",
                            "suggested_action": "请保留当前编辑内容，重新打开该题查看最新版本后再合并修改。",
                        },
                        status=409,
                    )
                    return
                self.send_json(result)
                return
            if len(parts := [unquote(x) for x in parsed.path.strip("/").split("/") if x]) == 5 and parts[:3] == ["api", "practice", "history"] and parts[4] == "undo":
                self.send_json(undo_last_practice_revision(parts[3]))
                return
            if parsed.path == "/api/tasks/bulk-delete":
                body = self.read_json()
                task_ids = body.get("task_ids") if isinstance(body.get("task_ids"), list) else []
                unique_ids = list(dict.fromkeys(str(item or "").strip() for item in task_ids if str(item or "").strip()))[:100]
                results = []
                for task_id in unique_ids:
                    try:
                        if task_id.startswith("word_format_"):
                            result = delete_word_format_task(task_id)
                        elif task_id.startswith("generation_"):
                            result = delete_practice_job(task_id)
                        elif task_id.startswith("practice_"):
                            result = delete_practice_record(task_id)
                            result = {**result, **delete_jobs_for_history(task_id), "task_id": task_id}
                        else:
                            result = delete_task(task_id)
                        results.append({"task_id": task_id, **result})
                    except Exception as exc:
                        results.append({"task_id": task_id, "ok": False, "message": str(exc) or exc.__class__.__name__})
                deleted = sum(1 for item in results if item.get("ok"))
                failed = len(results) - deleted
                append_runtime_log("task_control", f"批量删除任务：成功 {deleted}，未删除 {failed}", payload={"task_ids": unique_ids})
                self.send_json({"ok": failed == 0, "deleted": deleted, "failed": failed, "results": results})
                return
            if len(parts := [unquote(x) for x in parsed.path.strip("/").split("/") if x]) == 5 and parts[:3] == ["api", "practice", "history"] and parts[4] == "delete":
                result = delete_practice_record(parts[3])
                append_runtime_log("practice", f"删除模拟出题记录 {parts[3]}", payload=result)
                self.send_json(result)
                return
            if parsed.path in {"/api/practice/export", "/api/practice/export/prepare"}:
                request_started = time.perf_counter()
                body = self.read_json()
                latest_export_data = None
                history_id = str(body.get("history_id") or "").strip()
                if history_id:
                    try:
                        latest_record = load_practice_record(history_id)
                        latest_export_data = latest_record.get("data") if isinstance(latest_record.get("data"), dict) else None
                    except FileNotFoundError:
                        latest_export_data = None
                export_data = resolve_practice_export_payload(body, latest_export_data)
                export_validation = validate_practice_export(export_data)
                if not export_validation.get("ok"):
                    issues = export_validation.get("blocking_issues") or []
                    append_runtime_log(
                        "practice_export",
                        f"题目 Word 导出前门禁未通过：{len(issues)} 项阻断",
                        "warning",
                        {
                            "history_id": history_id,
                            "export_scope": str(body.get("export_scope") or "all"),
                            "selected_count": len(body.get("selected_exercise_ids") or []),
                            "issues": [str(issue) for issue in issues[:8]],
                        },
                    )
                    self.send_json(
                        {
                            "ok": False,
                            "error": "题目 Word 未生成：导出前质量门禁未通过。",
                            "issues": issues,
                            "issue_count": len(issues),
                            "suggested_action": "请根据题号定位失败项，重新生成对应题目后再导出。",
                            "human_review_required": False,
                        },
                        status=422,
                    )
                    return
                export_kind = str((parse_qs(parsed.query).get("kind") or ["questions"])[0]).strip().lower()
                if export_kind != "questions":
                    self.send_json({"ok": False, "error": "专项练习仅支持题目 Word 导出。"}, status=400)
                    return
                filename_text = ("知识点模拟题" if str(export_data.get("source_mode") or "exam") == "knowledge" else "按题出题") + "-题目"
                if export_validation.get("release_level") == "review_candidate":
                    filename_text += "-待复核"
                filename_text += ".docx"
                if parsed.path == "/api/practice/export/prepare":
                    export_job = create_or_reuse_practice_export_job(
                        export_data,
                        filename_text,
                    )
                    self.send_json(
                        {"ok": True, "job": export_job},
                        status=200 if export_job.get("status") == "completed" else 202,
                    )
                    return
                build_started = time.perf_counter()
                data = build_practice_question_docx(export_data)
                build_seconds = time.perf_counter() - build_started
                filename = quote(filename_text)
                docx_report = validate_docx_output(data, export_data)
                if not docx_report.get("ok"):
                    self.send_json(
                        {
                            "ok": False,
                            "error": "生成的 Word 未通过完整性校验。",
                            "issues": docx_report.get("issues") or [],
                        },
                        status=422,
                    )
                    return
                elapsed_seconds = time.perf_counter() - request_started
                append_runtime_log(
                    "practice_export",
                    f"题目 Word 同步生成完成：{len(export_data.get('exercises') or [])} 题，耗时 {elapsed_seconds:.2f} 秒",
                    payload={
                        "build_seconds": round(build_seconds, 3),
                        "elapsed_seconds": round(elapsed_seconds, 3),
                        "size_bytes": len(data),
                    },
                )
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/api/tasks":
                body = self.read_json()
                provider = get_provider(str(body.get("provider") or "").strip() or None)
                model = resolve_provider_model(provider, body.get("model"))
                model_thinking = _normalize_thinking_mode(body.get("model_thinking") or body.get("thinking_mode"))
                def resolve_text_role(provider_key: str, model_key: str) -> tuple[str, str]:
                    role_provider_name = str(body.get(provider_key) or provider.name).strip()
                    role_provider = get_provider(role_provider_name)
                    fallback_model = model if role_provider.name == provider.name else role_provider.default_model
                    role_model = resolve_provider_model(role_provider, body.get(model_key) or fallback_model)
                    return role_provider.name, role_model

                reasoning_provider_name, reasoning_model = resolve_text_role("reasoning_provider", "reasoning_model")
                answer_provider_name, answer_model = resolve_text_role("answer_provider", "answer_model")
                answer_provider_config = get_provider(answer_provider_name)
                direct_answer_multimodal = provider_model_supports_vision(answer_provider_config, answer_model)
                if body.get("correctness_provider") or body.get("correctness_model"):
                    correctness_provider_name, correctness_model = resolve_text_role(
                        "correctness_provider", "correctness_model"
                    )
                else:
                    correctness_provider_name, correctness_model = answer_provider_name, answer_model
                vision_provider_name = str(body.get("vision_provider") or "").strip()
                vision_model = str(body.get("vision_model") or "").strip()
                image_provider_name = str(body.get("image_provider") or "").strip()
                image_model = str(body.get("image_model") or "").strip()
                if vision_provider_name and not direct_answer_multimodal:
                    vision_provider = get_provider(vision_provider_name)
                    if not vision_model:
                        vision_model = str(getattr(vision_provider, "vision_model", "") or vision_provider.default_model or "").strip()
                    if not getattr(vision_provider, "supports_vision", False) or not vision_model:
                        raise ValueError(f"Provider {vision_provider.name} is not configured for vision_model")
                if image_provider_name:
                    image_provider = get_provider(image_provider_name)
                    if not provider_supports_image_generation(image_provider):
                        raise ValueError(f"Provider {image_provider.name} is not configured for image generation")
                    if not image_model:
                        image_model = str(getattr(image_provider, "image_model", "") or "").strip()
                    if not image_model:
                        raise ValueError(f"Provider {image_provider.name} is not configured for image_model")
                key_checks = [
                    ("基础/作图规则模型", provider),
                    ("知识点与教材依据模型", get_provider(reasoning_provider_name)),
                    ("答案生成模型", get_provider(answer_provider_name)),
                    ("高风险正确性复核模型", get_provider(correctness_provider_name)),
                ]
                if vision_provider_name and not direct_answer_multimodal:
                    key_checks.append(("读图模型", get_provider(vision_provider_name)))
                if image_provider_name and image_model:
                    key_checks.append(("作图生图模型", get_provider(image_provider_name)))
                key_errors = _provider_key_validation_errors(key_checks)
                if key_errors:
                    raise ValueError("；".join(key_errors))
                selected_textbooks = body.get("selected_textbooks") or []
                if not isinstance(selected_textbooks, list):
                    raise ValueError("selected_textbooks must be a list")
                if not selected_textbooks:
                    raise ValueError("请先选择已建立索引的教材。教材上传和索引建立已前置到教材管理页，解析流程不再临时建立索引。")
                textbook_display_names = body.get("textbook_display_names") or {}
                if not isinstance(textbook_display_names, dict):
                    raise ValueError("textbook_display_names must be an object")
                require_textbook_index_cache(
                    [str(x) for x in selected_textbooks],
                    {str(key): str(value) for key, value in textbook_display_names.items()},
                )
                textbooks_dir = str(body.get("textbooks_dir", "") or TEXTBOOKS_DIR)
                record = create_task(
                    exam_path=str(body.get("exam_path", "")),
                    textbooks_dir=textbooks_dir,
                    provider=provider.name,
                    model=model,
                    model_thinking=model_thinking,
                    reasoning_provider=reasoning_provider_name,
                    reasoning_model=reasoning_model,
                    answer_provider=answer_provider_name,
                    answer_model=answer_model,
                    correctness_provider=correctness_provider_name,
                    correctness_model=correctness_model,
                    vision_provider=vision_provider_name,
                    vision_model=vision_model,
                    image_provider=image_provider_name,
                    image_model=image_model,
                )
                append_runtime_log(
                    "task",
                    f"创建任务 {record.task_id}",
                    payload={
                        "task_id": record.task_id,
                        "provider": provider.name,
                        "model": model,
                        "model_thinking": model_thinking,
                        "reasoning_provider": reasoning_provider_name,
                        "reasoning_model": reasoning_model,
                        "answer_provider": answer_provider_name,
                        "answer_model": answer_model,
                        "correctness_provider": correctness_provider_name,
                        "correctness_model": correctness_model,
                        "vision_provider": vision_provider_name,
                        "vision_model": vision_model,
                        "image_provider": image_provider_name,
                        "image_model": image_model,
                    },
                )
                selected_dir = _prepare_selected_textbooks(
                    record.task_id,
                    [str(x) for x in selected_textbooks],
                    {str(key): str(value) for key, value in textbook_display_names.items()},
                )
                if selected_dir is not None:
                    record = load_task(record.task_id)
                self.send_json({"task": record.__dict__})
                return
            if parsed.path == "/api/library-upload":
                query = parse_qs(parsed.query)
                kind = query.get("kind", [""])[0]
                filename = query.get("filename", [""])[0]
                length = int(self.headers.get("Content-Length", "0") or "0")
                saved = save_library_upload_stream(kind, filename, self.rfile, length)
                self.send_json({"ok": True, "file": saved, "library": scan_library_files()})
                return
            if parsed.path == "/api/environment/repair":
                body = self.read_json()
                action = str(body.get("action") or "")
                append_runtime_log("environment", f"用户确认环境修复：{action}", payload={"action": action})
                result = repair_environment(action)
                append_runtime_log("environment", f"环境修复完成：{action}", "info" if result.get("ok") else "warning", {"action": action, "ok": result.get("ok")})
                self.send_json(result, status=200 if result.get("ok") else 500)
                return
            if parsed.path == "/api/library-delete":
                body = self.read_json()
                result = delete_library_file(str(body.get("kind") or ""), str(body.get("path") or ""))
                append_runtime_log("library", f"删除文件：{result.get('deleted', {}).get('name', '')}", payload=result)
                self.send_json({"ok": True, **result, "library": scan_library_files()})
                return
            if parsed.path == "/api/textbook-index/prepare":
                body = self.read_json()
                selected_textbooks = body.get("selected_textbooks") or []
                if not isinstance(selected_textbooks, list):
                    raise ValueError("selected_textbooks must be a list")
                textbook_display_names = body.get("textbook_display_names") or {}
                if not isinstance(textbook_display_names, dict):
                    raise ValueError("textbook_display_names must be an object")
                self.send_json(
                    prepare_textbook_index_cache(
                        [str(x) for x in selected_textbooks],
                        {str(key): str(value) for key, value in textbook_display_names.items()},
                    )
                )
                return
            if parsed.path == "/api/textbook-index/status":
                body = self.read_json()
                selected_textbooks = body.get("selected_textbooks") or []
                if not isinstance(selected_textbooks, list):
                    raise ValueError("selected_textbooks must be a list")
                textbook_display_names = body.get("textbook_display_names") or {}
                if not isinstance(textbook_display_names, dict):
                    raise ValueError("textbook_display_names must be an object")
                self.send_json(
                    textbook_index_cache_status(
                        [str(x) for x in selected_textbooks],
                        {str(key): str(value) for key, value in textbook_display_names.items()},
                    )
                )
                return
            if parsed.path == "/api/shared-textbook-library/settings":
                body = self.read_json()
                self.send_json(save_shared_library_settings(str(body.get("remote_url") or "")))
                return
            if parsed.path == "/api/hybrid/settings":
                if not self.is_local_client():
                    self.send_json({"ok": False, "error": "只能在运行程序的本机修改混合云开关。"}, status=403)
                    return
                body = self.read_json()
                if not isinstance(body.get("enabled"), bool):
                    raise ValueError("enabled must be a boolean")
                try:
                    result = save_hybrid_enabled(bool(body["enabled"]))
                except HybridClientError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                append_runtime_log(
                    "hybrid_settings",
                    "混合云执行已开启" if result["enabled"] else "混合云执行已关闭，改为本机执行",
                )
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/shared-textbook-library/remote-catalog":
                body = self.read_json()
                self.send_json(fetch_remote_shared_library_catalog(str(body.get("remote_url") or "")))
                return
            if parsed.path == "/api/shared-textbook-library/publish":
                if not self.shared_library_publish_allowed():
                    self.send_json({"error": "只有教材库主机本机管理员可发布教材。通过 Tailscale 远程发布时，请配置 ANSWER_BOOK_SHARED_LIBRARY_PUBLISHERS。"}, status=403)
                    return
                body = self.read_json()
                selected = body.get("selected_textbooks") or []
                if not isinstance(selected, list):
                    raise ValueError("selected_textbooks must be a list")
                names = body.get("textbook_display_names") or {}
                if not isinstance(names, dict):
                    raise ValueError("textbook_display_names must be an object")
                result = publish_shared_textbook_library(
                    [str(item) for item in selected],
                    {str(key): str(value) for key, value in names.items()},
                    library_id=str(body.get("library_id") or ""),
                    title=str(body.get("title") or ""),
                    version=str(body.get("version") or ""),
                )
                append_runtime_log("shared_textbook_library", f"发布共享教材 {result['library_id']}@{result['version']}", payload=result)
                self.send_json(result)
                return
            if parsed.path == "/api/shared-textbook-library/sync":
                body = self.read_json()
                result = sync_shared_textbook_library(
                    str(body.get("library_id") or ""),
                    str(body.get("version") or ""),
                    remote_url=str(body.get("remote_url") or ""),
                )
                append_runtime_log("shared_textbook_library", f"同步共享教材 {result['library_id']}@{result['version']}", payload=result)
                self.send_json(result)
                return
            if parsed.path == "/api/providers/local-keys":
                body = self.read_json()
                keys = body.get("keys", {})
                if not isinstance(keys, dict):
                    raise ValueError("keys must be an object")
                result = update_dotenv_values({str(k): str(v) for k, v in keys.items()})
                ok = bool(result.get("updated"))
                self.send_json(
                    {
                        "ok": True,
                        "updated": ok,
                        "env_exists": result.get("env_exists"),
                        "providers": {name: cfg.redacted() for name, cfg in list_providers().items()},
                    },
                )
                return
            parts = [unquote(x) for x in parsed.path.strip("/").split("/") if x]
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "run":
                task_id = parts[2]
                current = load_task(task_id)
                if current.status in {"running", "paused"}:
                    self.send_json(
                        {"ok": False, "error": "任务仍在运行或等待人工处理，不能重复启动。", "task_id": task_id},
                        status=409,
                    )
                    return
                body = self.read_json()
                render = bool(body.get("render"))
                use_model = not bool(body.get("no_model"))
                reuse_fragments = bool(body.get("reuse_fragments"))
                if render:
                    env = check_environment()
                    if not env.get("document_tools", {}).get("pdf_render_available"):
                        self.send_json(
                            {
                                "ok": False,
                                "error": "PDF/PNG 渲染工具不可用：请安装 Microsoft Word 可自动化组件或 LibreOffice 后重试。",
                                "environment": {
                                    "document_tools": env.get("document_tools", {}),
                                    "microsoft_word": env.get("microsoft_word", {}),
                                    "executables": env.get("executables", {}),
                                },
                            },
                            status=400,
                        )
                        return
                append_runtime_log("pipeline", f"启动任务 {task_id}", payload={"task_id": task_id, "render": render, "use_model": use_model, "reuse_fragments": reuse_fragments})

                start_exam_task(
                    task_id,
                    use_model=use_model,
                    render=render,
                    reuse_fragments=reuse_fragments,
                )
                self.send_json({"ok": True, "task_id": task_id, "status": "started"})
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "control":
                task_id = parts[2]
                body = self.read_json()
                action = str(body.get("action") or "")
                result = control_exam_task(task_id, action)
                append_runtime_log("task_control", f"任务 {task_id} 控制操作：{action}", "info" if result.get("ok") else "warning", {"task_id": task_id, "action": action, "result": result})
                self.send_json(result)
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "review-decision":
                task_id = parts[2]
                body = self.read_json()
                result = submit_review_decision(task_id, str(body.get("decision") or ""), str(body.get("note") or ""))
                append_runtime_log("review_decision", f"任务 {task_id} 审查决策：{body.get('decision')}", payload={"task_id": task_id, "decision": body.get("decision")})
                self.send_json(result)
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "exam-structure-review":
                task_id = parts[2]
                body = self.read_json()
                updates = body.get("updates") or body.get("items") or []
                if not isinstance(updates, list):
                    raise ValueError("updates must be a list")
                result = submit_exam_structure_review(task_id, updates, str(body.get("decision") or "confirm"), str(body.get("note") or ""))
                append_runtime_log("exam_structure_review", f"任务 {task_id} 真题结构确认：{body.get('decision') or 'confirm'}", payload={"task_id": task_id, "updated_count": len(updates)})
                self.send_json(result)
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "delete":
                task_id = parts[2]
                result = delete_task(task_id)
                append_runtime_log("task_control", f"任务 {task_id} 删除操作", "info" if result.get("ok") else "warning", {"task_id": task_id, "result": result})
                self.send_json(result, status=200 if result.get("ok") else 409)
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "page-map":
                task_id = parts[2]
                record = load_task(task_id)
                body = self.read_json()
                rows = body.get("rows")
                if not isinstance(rows, list):
                    raise ValueError("rows must be a list")
                manual_csv = Path(record.textbooks_dir).expanduser() / "textbook_page_map.manual.csv"
                write_page_map_rows(manual_csv, rows)
                self.send_json(
                    page_map_summary(
                        stage_dir(task_id) / "textbook_page_map.csv",
                        manual_csv,
                    )
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "answer-fragments":
                task_id = parts[2]
                body = self.read_json()
                data = body.get("data")
                allow_invalid = bool(body.get("allow_invalid"))
                issues = _validate_answer_fragments_payload(data)
                if issues and not allow_invalid:
                    self.send_json({"ok": False, "saved": False, "issues": issues}, status=400)
                    return
                path = stage_dir(task_id) / "answer_fragments.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                coverage = None
                structured_exam_path = stage_dir(task_id) / "structured_exam.json"
                if structured_exam_path.exists():
                    coverage = audit_answer_coverage(
                        json.loads(structured_exam_path.read_text(encoding="utf-8")),
                        data,
                        stage_dir(task_id) / "answer_coverage_audit.json",
                    )
                self.send_json({"ok": not issues and (coverage is None or coverage["ok"]), "saved": True, "issues": issues, "coverage": coverage, "path": str(path)})
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "review-export":
                task_id = parts[2]
                review = build_question_review(stage_dir(task_id))
                output = stage_dir(task_id) / "question_review.csv"
                write_question_review_csv(review, output)
                self.send_json({"ok": True, "path": str(output), "row_count": len(review.get("review_rows", []))})
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "delivery-package":
                task_id = parts[2]
                body = self.read_json()
                result = build_task_delivery_package(
                    task_id,
                    stage_dir(task_id),
                    output_dir(task_id),
                )
                status = 200 if result.get("ok") else 400
                self.send_json(result, status=status)
                return
            if parsed.path == "/api/validate-answer-fragment":
                body = self.read_json()
                issues = validate_v4_answer_fragment(body)
                self.send_json({"ok": not issues, "issues": issues})
                return
            if parsed.path == "/api/provider-test":
                body = self.read_json()
                provider = get_provider(str(body.get("provider") or "").strip() or None)
                temp_key = str(body.get("api_key") or "").strip()
                if temp_key:
                    provider = replace(provider, api_key=temp_key)
                provider, protocol_overridden = _provider_test_protocol_override(provider, body)
                provider = replace(provider, thinking_mode=_normalize_thinking_mode(body.get("model_thinking") or body.get("thinking_mode")))
                client = OpenAICompatibleClient(provider)
                model = resolve_provider_model(provider, body.get("model"))
                messages = [
                    {"role": "system", "content": "Return exactly this JSON object and no other text: {\"ping\":\"pong\"}"},
                    {"role": "user", "content": "Return the JSON object now."},
                ]
                try:
                    parsed_content = client.chat_json_object(messages, model=model, max_tokens=DEFAULT_MODEL_MAX_TOKENS, attempts=1)
                except LLMError as exc:
                    error_payload = {"ok": False, "provider": provider.name, "model": model, "error": str(exc)}
                    if protocol_overridden:
                        error_payload["api_protocol_requested"] = provider.api_protocol
                    self.send_json(error_payload, status=400)
                    return
                retry_report = getattr(client, "last_json_retry_report", {})
                used_model = model
                for attempt in reversed(retry_report.get("attempts", []) if isinstance(retry_report, dict) else []):
                    if not attempt.get("error") and attempt.get("model"):
                        used_model = str(attempt.get("model"))
                        break
                response_payload = {"ok": True, "provider": provider.name, "model": used_model, "thinking_mode": provider.thinking_mode, "content": parsed_content, "retry_report": retry_report}
                if protocol_overridden:
                    response_payload.update(_provider_test_protocol_summary(retry_report, provider.api_protocol))
                self.send_json(response_payload)
                return
            if parsed.path == "/api/generate-answer-fragment-demo":
                body = self.read_json()
                provider = get_provider(str(body.get("provider") or "").strip() or None)
                client = OpenAICompatibleClient(provider)
                messages = build_answer_fragment_prompt(body.get("question") or {}, body.get("evidence") or [])
                model = resolve_provider_model(provider, body.get("model"))
                result = client.chat_json(messages, model=model)
                parsed_content = parse_json_content(result.content)
                issues = validate_v4_answer_fragment(parsed_content)
                self.send_json({
                    "ok": not issues,
                    "issues": issues,
                    "provider": result.provider,
                    "model": result.model,
                    "answer_fragment": parsed_content,
                })
                return
            self.send_json({"error": "not found"}, status=404)
        except ValueError as exc:
            payload = public_error_payload(exc, status=400, path=parsed.path)
            append_runtime_log(
                "server",
                f"请求参数错误 [{payload['support_id']}]：{exc}",
                "warning",
                {"path": parsed.path, "support_id": payload["support_id"], "error_type": exc.__class__.__name__},
            )
            append_exception_log(exc, path=parsed.path, support_id=payload["support_id"], request_id=self.request_id())
            self.send_json(payload, status=400)
        except Exception as exc:
            payload = public_error_payload(exc, status=500, path=parsed.path)
            append_runtime_log(
                "server",
                f"请求处理失败 [{payload['support_id']}]：{exc}",
                "error",
                {"path": parsed.path, "support_id": payload["support_id"], "error_type": exc.__class__.__name__},
            )
            append_exception_log(exc, path=parsed.path, support_id=payload["support_id"], request_id=self.request_id())
            self.send_json(payload, status=500)

    def serve_static(self, request_path: str) -> None:
        path = request_path.lstrip("/") or "index.html"
        full = (WEB_DIR / path).resolve()
        try:
            full.relative_to(WEB_DIR.resolve())
            inside_web_root = True
        except ValueError:
            inside_web_root = False
        if not inside_web_root or not full.exists() or not full.is_file():
            full = WEB_DIR / "index.html"
        data = full.read_bytes()
        # 服务端把版本号直接注入首页，保证首次进入就能看到版本标签，
        # 不依赖前端异步 refresh()（解决“首次进入无版本号”）。
        if full.name == "index.html":
            data = _inject_index_version(data.decode("utf-8", errors="replace")).encode("utf-8")
        content_type = mimetypes.guess_type(str(full))[0] or "application/octet-stream"
        if full.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif full.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif full.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # 本地平台持续迭代，HTML/JS/CSS 不允许浏览器沿用旧版本。
        # 否则界面已更新但 Chrome 仍可能执行旧脚本，造成控件和布局不一致。
        if full.suffix in {".html", ".js", ".css"}:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)


def run(host: str = "127.0.0.1", port: int = 8766) -> None:
    ensure_project_dirs()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        ensure_lan_access_config()
    with platform_process_lock(purpose=f"web-server {host}:{port}"):
        # Reserve the listening address before changing any durable task state
        # or starting workers. A failed duplicate/port-conflicting launch must
        # have no opportunity to consume queued work.
        server = ThreadingHTTPServer((host, port), PlatformHandler)
        try:
            recovered = recover_interrupted_tasks("server_startup")
            if recovered:
                for record in recovered:
                    start_exam_task(
                        str(record["task_id"]),
                        use_model=bool(record.get("use_model", True)),
                        render=bool(record.get("render", True)),
                        reuse_fragments=True,
                        remember_options=False,
                        thread_name_prefix="exam-resume",
                    )
                append_runtime_log("task_recovery", f"服务启动时恢复 {len(recovered)} 个真题解析任务", "warning", {"tasks": recovered})
            cleanup = cleanup_practice_jobs()
            if cleanup["removed_count"]:
                append_runtime_log(
                    "practice_cleanup",
                    f"服务启动时清理 {cleanup['removed_count']} 个过期出题临时任务",
                    payload={"removed_bytes": cleanup["removed_bytes"]},
                )
            export_recovery = recover_practice_export_jobs()
            if export_recovery["resumed"] or export_recovery["completed_from_cache"] or export_recovery["failed"]:
                append_runtime_log(
                    "task_recovery",
                    "服务启动时恢复 Word 导出任务",
                    "warning",
                    export_recovery,
                )
            # Practice jobs are durable. A browser refresh/page switch never
            # owns the worker, and a local server restart requeues unfinished jobs.
            recovered_generation = recover_practice_jobs(fail_interrupted=False)
            if recovered_generation:
                queue_recovery = recover_practice_queue(recovered_generation)
                append_runtime_log(
                    "task_recovery",
                    f"服务启动时检查 {len(recovered_generation)} 个出题任务",
                    "warning",
                    queue_recovery,
                )
            start_practice_queue_consumer()
            start_support_retry_worker()
            print(f"Answer Book Platform v1 running at http://{host}:{port}")
            append_runtime_log("server", f"服务启动 http://{host}:{port}", payload={"host": host, "port": port})
            server.serve_forever()
        finally:
            stop_support_retry_worker()
            stop_practice_queue_consumer()
            server.server_close()
