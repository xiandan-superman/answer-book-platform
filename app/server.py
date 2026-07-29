from __future__ import annotations

import base64
import json
import mimetypes
import os
import secrets
import shutil
import threading
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .environment import check_environment, repair_environment
from .answer_coverage_audit import audit_answer_coverage
from .audit_review_gate import get_pending_review_decision, submit_review_decision
from .delivery_package import build_task_delivery_package
from .exam_structure_review import get_pending_exam_structure_review, submit_exam_structure_review
from .exercise_generation import (
    generate_practice_from_plan,
    generate_practice_set,
    plan_practice_set,
    regenerate_practice_exercise,
)
from .final_acceptance import build_final_acceptance_report
from .local_config import update_dotenv_values
from .library_files import delete_library_file, save_library_upload, scan_library_files
from .lan_access import ensure_lan_access_config, lan_access_enabled, lan_access_info, lan_credentials
from .llm_client import LLMError, OpenAICompatibleClient, parse_json_content
from .page_map_admin import page_map_summary, write_page_map_rows
from .pipeline import PipelineOptions, output_dir, run_pipeline, stage_dir
from .paths import PROJECT_ROOT, TEXTBOOKS_DIR, WEB_DIR, ensure_project_dirs
from .practice_export import build_practice_docx
from .practice_store import list_practice_records, load_practice_record, save_practice_record
from .prompts import build_answer_fragment_prompt
from .review_export import build_question_review, write_question_review_csv
from .runtime_monitor import append_runtime_log, build_system_status, read_runtime_logs
from .settings import DEFAULT_MODEL_MAX_TOKENS, get_provider, list_providers, provider_supports_image_generation, resolve_provider_model
from .task_store import create_task, list_tasks, load_task, recover_interrupted_tasks, save_task, task_dir
from .task_diagnostics import build_task_diagnostics
from .task_control import control_task, delete_task
from .task_result_view import build_task_result_view
from .textbook_index_cache import prepare_textbook_index_cache, require_textbook_index_cache, textbook_index_cache_status
from .shared_textbook_library import (
    fetch_remote_shared_library_catalog,
    get_shared_library_settings,
    publish_shared_textbook_library,
    save_shared_library_settings,
    shared_library_catalog,
    shared_library_package_path,
    sync_shared_textbook_library,
)
from .v4_schema import validate_v4_answer_fragment
from .version import get_source_revision, get_version


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
    "docx_user_allowed_candidate",
    "docx_placeholder",
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
    hint = f"（环境变量 {env_name}）" if env_name else "（providers.local.json 或 .env）"
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
    }
    summary = {}
    for key, filename in names.items():
        data = _read_json_if_exists(sdir / filename)
        if isinstance(data, dict):
            summary[key] = {
                "ok": data.get("ok", data.get("status") == "passed"),
                "issue_count": data.get("issue_count", len(data.get("issues", [])) if isinstance(data.get("issues"), list) else 0),
                "warning_count": data.get("warning_count", len(data.get("warnings", [])) if isinstance(data.get("warnings"), list) else 0),
            }
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
    if text in {"enabled", "enable", "on", "true"}:
        return "enabled"
    if text in {"disabled", "disable", "off", "false"}:
        return "disabled"
    return "auto"


def _task_duration_summary(task_row: dict) -> dict:
    start = _parse_task_time(task_row.get("created_at"))
    if not start:
        return {"duration_seconds": 0, "duration_text": "暂无"}
    status = str(task_row.get("status") or "")
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
    return row


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


class PlatformHandler(BaseHTTPRequestHandler):
    server_version = "AnswerBookPlatform/1.0"

    def log_message(self, fmt: str, *args) -> None:
        message = fmt % args
        print(f"[server] {self.address_string()} {message}")
        append_runtime_log("server", f"{self.address_string()} {message}")

    def send_json(self, value, status: int = 200) -> None:
        data = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_download(self, target: Path) -> None:
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(target.name)}")
        self.send_header("Cache-Control", "no-store")
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

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_GET(self) -> None:
        if not self.require_lan_auth():
            return
        parsed = urlparse(self.path)
        parts = [unquote(x) for x in parsed.path.strip("/").split("/") if x]
        if parsed.path == "/api/lan/access":
            self.send_json(lan_access_info(self.server.server_port, include_secret=self.is_local_client()))
            return
        if parsed.path == "/api/version":
            self.send_json(
                {
                    "platform": "Answer Book Platform",
                    "version": get_version(),
                    "source_revision": get_source_revision(),
                    "release_manifest": "RELEASE_MANIFEST.json",
                    "release_manifest_exists": (PROJECT_ROOT / "RELEASE_MANIFEST.json").exists(),
                }
            )
            return
        if parsed.path == "/api/environment":
            self.send_json(check_environment())
            return
        if parsed.path == "/api/providers":
            self.send_json({name: cfg.redacted() for name, cfg in list_providers().items()})
            return
        if parsed.path == "/api/practice/history":
            self.send_json({"records": list_practice_records()})
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
            self.send_json(build_system_status(self.headers.get("Host", "")))
            return
        if parsed.path == "/api/system/logs":
            self.send_json({"logs": read_runtime_logs()})
            return
        if parsed.path == "/api/tasks":
            self.send_json({"tasks": [_enrich_task_row(task) for task in list_tasks()]})
            return
        if len(parts) == 3 and parts[:2] == ["api", "tasks"]:
            task_id = parts[2]
            record = load_task(task_id)
            status_path = stage_dir(task_id) / "pipeline_status.json"
            report_path = stage_dir(task_id) / "acceptance_report.json"
            self.send_json(
                {
                    "task": _enrich_task_row(record.__dict__),
                    "pipeline_status": _read_json_if_exists(status_path),
                    "current_progress": _task_current_progress(task_id, record.current_stage),
                    "acceptance_report": _read_json_if_exists(report_path),
                    "quality_summary": _task_quality_summary(task_id),
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
        if not self.require_lan_auth():
            return
        parsed = urlparse(self.path)
        try:
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
            if parsed.path == "/api/practice/plan":
                body = self.read_json()
                self.send_json(plan_practice_set(body))
                return
            if parsed.path == "/api/practice/generate-from-plan":
                body = self.read_json()
                result = generate_practice_from_plan(body)
                record = save_practice_record(result, request=body)
                self.send_json(record["data"])
                return
            if parsed.path == "/api/practice/regenerate":
                body = self.read_json()
                result = regenerate_practice_exercise(body)
                self.send_json(result)
                return
            if parsed.path == "/api/practice/history":
                body = self.read_json()
                data = body.get("data") if isinstance(body.get("data"), dict) else body
                request = body.get("request") if isinstance(body.get("request"), dict) else None
                record = save_practice_record(data, request=request)
                self.send_json(record)
                return
            if parsed.path == "/api/practice/export":
                body = self.read_json()
                data = build_practice_docx(body)
                filename = quote("研究生专项练习.docx")
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
                provider_name = str(body.get("provider") or "openai")
                provider = get_provider(provider_name)
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
                vision_provider_name = str(body.get("vision_provider") or "").strip()
                vision_model = str(body.get("vision_model") or "").strip()
                image_provider_name = str(body.get("image_provider") or "").strip()
                image_model = str(body.get("image_model") or "").strip()
                if vision_provider_name:
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
                ]
                if vision_provider_name:
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
                data = self.rfile.read(length) if length > 0 else b""
                saved = save_library_upload(kind, filename, data)
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
                        "ok": ok,
                        "env_exists": result.get("env_exists"),
                        "providers": {name: cfg.redacted() for name, cfg in list_providers().items()},
                    },
                    status=200 if ok else 400,
                )
                return
            parts = [unquote(x) for x in parsed.path.strip("/").split("/") if x]
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "run":
                task_id = parts[2]
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

                def worker():
                    try:
                        run_pipeline(
                            task_id,
                            PipelineOptions(
                                use_model=use_model,
                                allow_demo_without_key=not use_model,
                                render_with_word=render,
                                reuse_fragments=reuse_fragments,
                            ),
                        )
                    except Exception as exc:
                        print(f"[pipeline] task {task_id} failed: {exc}")
                        append_runtime_log("pipeline", f"任务 {task_id} 执行失败：{exc}", "error", {"task_id": task_id})

                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                self.send_json({"ok": True, "task_id": task_id, "status": "started"})
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "control":
                task_id = parts[2]
                body = self.read_json()
                action = str(body.get("action") or "")
                result = control_task(task_id, action)
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
                    allow_warnings=True,
                    allow_review_acknowledgement=bool(body.get("allow_review_acknowledgement")),
                    review_policy=str(body.get("review_policy") or "ask"),
                )
                status = 200 if result.get("ok") or result.get("status") in {"review_ack_required", "review_decision_required"} else 400
                self.send_json(result, status=status)
                return
            if parsed.path == "/api/validate-answer-fragment":
                body = self.read_json()
                issues = validate_v4_answer_fragment(body)
                self.send_json({"ok": not issues, "issues": issues})
                return
            if parsed.path == "/api/provider-test":
                body = self.read_json()
                provider = get_provider(str(body.get("provider") or "openai"))
                temp_key = str(body.get("api_key") or "").strip()
                if temp_key:
                    provider = replace(provider, api_key=temp_key)
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
                    self.send_json({"ok": False, "provider": provider.name, "model": model, "error": str(exc)}, status=400)
                    return
                retry_report = getattr(client, "last_json_retry_report", {})
                used_model = model
                for attempt in reversed(retry_report.get("attempts", []) if isinstance(retry_report, dict) else []):
                    if not attempt.get("error") and attempt.get("model"):
                        used_model = str(attempt.get("model"))
                        break
                self.send_json({"ok": True, "provider": provider.name, "model": used_model, "thinking_mode": provider.thinking_mode, "content": parsed_content, "retry_report": retry_report})
                return
            if parsed.path == "/api/generate-answer-fragment-demo":
                body = self.read_json()
                provider = get_provider(str(body.get("provider") or "openai"))
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
            append_runtime_log("server", f"请求参数错误：{exc}", "warning", {"path": parsed.path})
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            append_runtime_log("server", f"请求处理失败：{exc}", "error", {"path": parsed.path})
            self.send_json({"error": str(exc)}, status=500)

    def serve_static(self, request_path: str) -> None:
        path = request_path.lstrip("/") or "index.html"
        full = (WEB_DIR / path).resolve()
        if not str(full).startswith(str(WEB_DIR.resolve())) or not full.exists() or not full.is_file():
            full = WEB_DIR / "index.html"
        data = full.read_bytes()
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
        self.end_headers()
        self.wfile.write(data)


def run(host: str = "127.0.0.1", port: int = 8766) -> None:
    ensure_project_dirs()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        ensure_lan_access_config()
    recovered = recover_interrupted_tasks("server_startup")
    if recovered:
        append_runtime_log("task_recovery", f"服务启动时标记 {len(recovered)} 个中断任务", "warning", {"tasks": recovered})
    server = ThreadingHTTPServer((host, port), PlatformHandler)
    print(f"Answer Book Platform v1 running at http://{host}:{port}")
    append_runtime_log("server", f"服务启动 http://{host}:{port}", payload={"host": host, "port": port})
    server.serve_forever()
