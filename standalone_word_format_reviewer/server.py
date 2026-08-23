from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import shutil
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .format_engine import PROFILE_LABELS, audit_docx, default_task_options, normalize_task_options, repair_docx


ROOT = Path(__file__).resolve().parent
WEB_FILE = ROOT / "web" / "index.html"
LUCIDE_FILE = ROOT.parent / "web" / "vendor" / "lucide.min.js"
JOB_ROOT = Path(tempfile.gettempdir()) / "answer_book_word_format_reviewer"
SETTINGS_FILE = Path(os.environ.get("WORD_FORMAT_REVIEWER_SETTINGS_FILE") or (Path.home() / "Library" / "Application Support" / "AnswerBookWordFormatReviewer" / "settings.json"))
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_REQUEST_BYTES = 70 * 1024 * 1024


def _safe_filename(value: str) -> str:
    name = Path(value or "document.docx").name
    stem = "".join(ch for ch in Path(name).stem if ch not in '\\/:*?"<>|').strip() or "document"
    return f"{stem[:100]}.docx"


def _job_dir(job_id: str) -> Path:
    if not job_id or any(ch not in "0123456789abcdef" for ch in job_id) or len(job_id) != 32:
        raise ValueError("无效任务编号")
    return JOB_ROOT / job_id


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _cleanup_old_jobs(max_age_seconds: int = 24 * 60 * 60) -> None:
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for child in JOB_ROOT.iterdir():
        if child.is_dir() and now - child.stat().st_mtime > max_age_seconds:
            shutil.rmtree(child, ignore_errors=True)


def _saved_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        value = _read_json(SETTINGS_FILE)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _settings_payload() -> dict:
    saved = _saved_settings()
    profiles = {}
    for profile in PROFILE_LABELS:
        defaults = default_task_options(profile)
        saved_profile = saved.get(profile) if isinstance(saved.get(profile), dict) else None
        effective = normalize_task_options(profile, saved_profile)
        profiles[profile] = {"defaults": defaults, "saved": saved_profile, "effective": effective}
    return {"profiles": profiles}


def _render_web_page() -> bytes:
    settings_json = json.dumps(_settings_payload(), ensure_ascii=False, separators=(",", ":"))
    settings_json = settings_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    routes_json = json.dumps(
        {"settings": "/api/settings", "audit": "/api/audit", "taskBase": "/api/jobs"},
        separators=(",", ":"),
    )
    html = WEB_FILE.read_text(encoding="utf-8")
    html = html.replace("__INITIAL_SETTINGS_JSON__", settings_json)
    html = html.replace("__API_ROUTES_JSON__", routes_json)
    html = html.replace("__PLATFORM_HOSTED_JSON__", "false")
    return html.encode("utf-8")


def _save_profile_settings(profile: str, settings: dict) -> dict:
    normalized = normalize_task_options(profile, settings)
    saved = _saved_settings()
    saved[profile] = normalized
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    _write_json(temporary, saved)
    temporary.replace(SETTINGS_FILE)
    SETTINGS_FILE.chmod(0o600)
    return normalized


def _with_display_filename(report: dict, filename: str) -> dict:
    report["source_name"] = filename
    return report


def _issue_key(issue: dict) -> tuple[str, str, str]:
    return (
        str(issue.get("code") or ""),
        str(issue.get("location") or ""),
        str(issue.get("item") or ""),
    )


def _report_changes(original: dict | None, final: dict | None) -> dict:
    original_issues = original.get("issues", []) if isinstance(original, dict) else []
    final_issues = final.get("issues", []) if isinstance(final, dict) else []
    final_keys = {_issue_key(issue) for issue in final_issues if isinstance(issue, dict)}
    original_keys = {_issue_key(issue) for issue in original_issues if isinstance(issue, dict)}
    resolved = [issue for issue in original_issues if isinstance(issue, dict) and _issue_key(issue) not in final_keys]
    remaining = [issue for issue in final_issues if isinstance(issue, dict) and _issue_key(issue) in original_keys]
    introduced = [issue for issue in final_issues if isinstance(issue, dict) and _issue_key(issue) not in original_keys]
    return {"resolved": resolved, "remaining": remaining, "introduced": introduced}


def _download_filename(meta: dict) -> str:
    return f"{Path(str(meta.get('filename') or 'document.docx')).stem}_格式已修改.docx"


def _job_payload(job_id: str) -> dict:
    job = _job_dir(job_id)
    meta = _read_json(job / "meta.json")
    report = _read_json(job / "audit.json") if (job / "audit.json").exists() else None
    final_report = _read_json(job / "final_audit.json") if (job / "final_audit.json").exists() else None
    output_exists = (job / "modified.docx").exists()
    filename = str(meta.get("filename") or "document.docx")
    return {
        "job_id": job_id,
        "mode": meta.get("mode"),
        "status": meta.get("status") or ("completed" if output_exists else "needs_input"),
        "filename": filename,
        "suggested_filename": _download_filename(meta) if output_exists else None,
        "report": report,
        "final_report": final_report,
        "changes": _report_changes(report, final_report),
        "download_url": f"/api/jobs/{job_id}/download" if output_exists else None,
        "source_download_url": f"/api/jobs/{job_id}/source",
        "source_preview_url": f"/api/jobs/{job_id}/preview?version=source",
        "modified_preview_url": f"/api/jobs/{job_id}/preview?version=modified" if output_exists else None,
        "can_restore": output_exists,
        "error": meta.get("error"),
    }


def _job_cancelled(job: Path) -> bool:
    try:
        return _read_json(job / "meta.json").get("status") in {"cancel_requested", "canceled"}
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _mark_job(job: Path, **updates: object) -> dict:
    meta = _read_json(job / "meta.json")
    if meta.get("status") in {"cancel_requested", "canceled"} and updates.get("status") not in {"cancel_requested", "canceled"}:
        updates.pop("status", None)
    meta.update(updates)
    meta["updated_at"] = time.time()
    _write_json(job / "meta.json", meta)
    return meta


class WordFormatHandler(BaseHTTPRequestHandler):
    server_version = "WordFormatReviewer/0.1"

    def _send_json(self, status: int, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("请求为空或超过70 MB限制")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求内容不是有效JSON") from exc

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            data = _render_web_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/vendor/lucide.min.js" and LUCIDE_FILE.exists():
            data = LUCIDE_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/settings":
            self._send_json(200, _settings_payload())
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            try:
                self._send_json(200, _job_payload(parts[2]))
            except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError):
                self._send_json(404, {"error": "审查任务不存在或已过期"})
            return
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "download":
            try:
                job = _job_dir(parts[2])
                meta = _read_json(job / "meta.json")
                output = job / "modified.docx"
                if not output.exists():
                    raise FileNotFoundError
            except (ValueError, FileNotFoundError, OSError):
                self._send_json(404, {"error": "修改后的文件不存在或已过期"})
                return
            filename = _download_filename(meta)
            data = output.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "source":
            try:
                job = _job_dir(parts[2])
                meta = _read_json(job / "meta.json")
                source = job / "source.docx"
                if not source.exists():
                    raise FileNotFoundError
            except (ValueError, FileNotFoundError, OSError):
                self._send_json(404, {"error": "原始文件不存在或已过期"})
                return
            data = source.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(str(meta.get('filename') or 'document.docx'))}")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "preview":
            version = str(parse_qs(parsed.query).get("version", ["source"])[0])
            try:
                job = _job_dir(parts[2])
                source = job / ("modified.docx" if version == "modified" else "source.docx")
                if not source.exists():
                    raise FileNotFoundError
                preview = job / f"preview_{version}.pdf"
                if not preview.exists() or preview.stat().st_mtime < source.stat().st_mtime:
                    from app.render_word import export_docx_to_pdf

                    export_docx_to_pdf(source, preview)
                data = preview.read_bytes()
            except (ValueError, FileNotFoundError, OSError):
                self._send_json(404, {"error": "预览文件不存在或已过期"})
                return
            except Exception as exc:
                self._send_json(503, {"error": f"暂时无法生成预览：{exc}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", "inline")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        self._send_json(404, {"error": "页面不存在"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/audit":
                self._handle_audit()
                return
            if path == "/api/settings":
                self._handle_settings()
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "apply":
                self._handle_apply(parts[2])
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                self._handle_cancel(parts[2])
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "restore":
                self._handle_restore(parts[2])
                return
            self._send_json(404, {"error": "接口不存在"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": f"处理失败：{exc}"})

    def _handle_audit(self) -> None:
        payload = self._read_json()
        profile = str(payload.get("profile") or "")
        if profile not in PROFILE_LABELS:
            raise ValueError("请选择真题答案或讲义标准")
        mode = str(payload.get("mode") or "review")
        if mode not in {"review", "auto"}:
            raise ValueError("处理模式无效")
        header_text = str(payload.get("header_text") or "").strip()
        if len(header_text) > 200:
            raise ValueError("页眉文字不能超过200个字符")
        raw_options = payload.get("task_options")
        if not isinstance(raw_options, dict):
            raw_options = _settings_payload()["profiles"][profile]["effective"]
        task_options = normalize_task_options(profile, raw_options)
        filename = _safe_filename(str(payload.get("filename") or "document.docx"))
        try:
            content = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("上传文件内容无效") from exc
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("请选择不超过50 MB的DOCX文件")
        if not content.startswith(b"PK"):
            raise ValueError("文件不是有效的DOCX压缩包")

        requested_job_id = str(payload.get("job_id") or "")
        if requested_job_id:
            _job_dir(requested_job_id)
        job_id = requested_job_id or uuid.uuid4().hex
        job = _job_dir(job_id)
        try:
            job.mkdir(parents=True)
        except FileExistsError as exc:
            raise ValueError("该审查任务已存在，请刷新页面恢复或重新开始") from exc
        source = job / "source.docx"
        source.write_bytes(content)
        meta = {"job_id": job_id, "filename": filename, "profile": profile, "mode": mode, "header_text": header_text, "task_options": task_options, "status": "running", "created_at": time.time(), "updated_at": time.time()}
        _write_json(job / "meta.json", meta)
        try:
            report = _with_display_filename(audit_docx(source, profile, header_text, task_options), filename)
        except Exception as exc:
            shutil.rmtree(job, ignore_errors=True)
            raise ValueError(f"无法读取该DOCX文件：{exc}") from exc
        _write_json(job / "audit.json", report)
        if _job_cancelled(job):
            _mark_job(job, status="canceled")
            self._send_json(409, {"error": "任务已取消", "job_id": job_id, "status": "canceled"})
            return
        _mark_job(job, status="needs_input")
        if _job_cancelled(job):
            _mark_job(job, status="canceled")
            self._send_json(409, {"error": "任务已取消", "job_id": job_id, "status": "canceled"})
            return
        if mode == "auto":
            final_report = _with_display_filename(repair_docx(source, job / "modified.docx", profile, header_text, task_options), filename)
            _write_json(job / "final_audit.json", final_report)
            if _job_cancelled(job):
                (job / "modified.docx").unlink(missing_ok=True)
                (job / "final_audit.json").unlink(missing_ok=True)
                _mark_job(job, status="canceled")
                self._send_json(409, {"error": "任务已取消", "job_id": job_id, "status": "canceled"})
                return
            _mark_job(job, status="completed")
            if _job_cancelled(job):
                (job / "modified.docx").unlink(missing_ok=True)
                (job / "final_audit.json").unlink(missing_ok=True)
                _mark_job(job, status="canceled")
                self._send_json(409, {"error": "任务已取消", "job_id": job_id, "status": "canceled"})
                return
        self._send_json(200, _job_payload(job_id))

    def _handle_settings(self) -> None:
        payload = self._read_json()
        profile = str(payload.get("profile") or "")
        if profile not in PROFILE_LABELS:
            raise ValueError("请选择真题答案或讲义标准")
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("缺少要保存的标准配置")
        normalized = _save_profile_settings(profile, settings)
        self._send_json(200, {"profile": profile, "settings": normalized, "message": "已设为该标准的永久默认"})

    def _handle_apply(self, job_id: str) -> None:
        job = _job_dir(job_id)
        try:
            meta = _read_json(job / "meta.json")
            source = job / "source.docx"
            if not source.exists():
                raise FileNotFoundError
        except (OSError, FileNotFoundError):
            self._send_json(404, {"error": "审查任务不存在或已过期"})
            return
        _mark_job(job, status="running", operation="apply")
        final_report = _with_display_filename(repair_docx(source, job / "modified.docx", meta["profile"], meta["header_text"], meta.get("task_options")), str(meta.get("filename") or "document.docx"))
        _write_json(job / "final_audit.json", final_report)
        if _job_cancelled(job):
            (job / "modified.docx").unlink(missing_ok=True)
            (job / "final_audit.json").unlink(missing_ok=True)
            _mark_job(job, status="canceled")
            self._send_json(409, {"error": "任务已取消", "job_id": job_id, "status": "canceled"})
            return
        _mark_job(job, status="completed", operation=None)
        if _job_cancelled(job):
            (job / "modified.docx").unlink(missing_ok=True)
            (job / "final_audit.json").unlink(missing_ok=True)
            _mark_job(job, status="canceled", operation=None)
            self._send_json(409, {"error": "任务已取消", "job_id": job_id, "status": "canceled"})
            return
        self._send_json(200, _job_payload(job_id))

    def _handle_cancel(self, job_id: str) -> None:
        try:
            job = _job_dir(job_id)
            meta = _read_json(job / "meta.json")
        except (ValueError, FileNotFoundError, OSError):
            self._send_json(404, {"error": "审查任务不存在或已过期"})
            return
        status = str(meta.get("status") or "")
        if status == "running":
            _mark_job(job, status="cancel_requested")
            self._send_json(202, {"job_id": job_id, "status": "cancel_requested", "message": "已请求取消，当前步骤结束后将不保留修改结果"})
            return
        self._send_json(200, {"job_id": job_id, "status": status, "message": "任务已不在运行"})

    def _handle_restore(self, job_id: str) -> None:
        try:
            job = _job_dir(job_id)
            _read_json(job / "meta.json")
            if not (job / "source.docx").exists() or not (job / "audit.json").exists():
                raise FileNotFoundError
        except (ValueError, FileNotFoundError, OSError):
            self._send_json(404, {"error": "原始文件或审查任务不存在"})
            return
        for name in ("modified.docx", "final_audit.json", "preview_modified.pdf"):
            (job / name).unlink(missing_ok=True)
        _mark_job(job, status="needs_input", operation=None)
        self._send_json(200, _job_payload(job_id))

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def run(host: str = "127.0.0.1", port: int = 8788) -> None:
    _cleanup_old_jobs()
    server = ThreadingHTTPServer((host, port), WordFormatHandler)
    print(f"Word格式审查修改原型已启动：http://{host}:{port}")
    print("上传文件仅保存在系统临时目录，24小时后自动清理。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="独立Word格式审查修改原型")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
