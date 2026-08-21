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
from urllib.parse import quote, urlparse

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
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


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
        path = urlparse(self.path).path
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
            filename = f"{Path(meta['filename']).stem}_格式已修改.docx"
            data = output.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
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

        job_id = uuid.uuid4().hex
        job = _job_dir(job_id)
        job.mkdir(parents=True)
        source = job / "source.docx"
        source.write_bytes(content)
        meta = {"job_id": job_id, "filename": filename, "profile": profile, "mode": mode, "header_text": header_text, "task_options": task_options, "created_at": time.time()}
        _write_json(job / "meta.json", meta)
        try:
            report = audit_docx(source, profile, header_text, task_options)
        except Exception as exc:
            shutil.rmtree(job, ignore_errors=True)
            raise ValueError(f"无法读取该DOCX文件：{exc}") from exc
        _write_json(job / "audit.json", report)
        result = {"job_id": job_id, "mode": mode, "report": report, "download_url": None, "final_report": None}
        if mode == "auto":
            final_report = repair_docx(source, job / "modified.docx", profile, header_text, task_options)
            _write_json(job / "final_audit.json", final_report)
            result["download_url"] = f"/api/jobs/{job_id}/download"
            result["final_report"] = final_report
        self._send_json(200, result)

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
        final_report = repair_docx(source, job / "modified.docx", meta["profile"], meta["header_text"], meta.get("task_options"))
        _write_json(job / "final_audit.json", final_report)
        self._send_json(200, {"job_id": job_id, "download_url": f"/api/jobs/{job_id}/download", "final_report": final_report})

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
