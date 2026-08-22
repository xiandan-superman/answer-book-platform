#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MAX_MEMBER_BYTES = 12 * 1024 * 1024
MAX_MEMBER_COUNT = 40
DEFAULT_QUOTA_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def display_local_time(value: Any, target_timezone: Any = None) -> str:
    """Format an ISO timestamp for people while keeping stored timestamps unchanged."""
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(target_timezone)
    except ValueError:
        return raw or "未知时间"
    return f"{local.year}年{local.month}月{local.day}日 {local:%H:%M}"


def default_root() -> Path:
    if os.sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Answer Book Support Receiver"
    if os.sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Answer Book Support Receiver"
    return Path.home() / ".local" / "share" / "answer-book-support-receiver"


def tailscale_ipv4() -> str:
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], check=True, capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def is_tailscale_source(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")


def device_identity(address: str) -> str:
    try:
        result = subprocess.run(
            ["tailscale", "whois", "--json", address], check=True, capture_output=True, text=True, timeout=3
        )
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return address
    node = value.get("Node") if isinstance(value, dict) and isinstance(value.get("Node"), dict) else {}
    user = value.get("UserProfile") if isinstance(value, dict) and isinstance(value.get("UserProfile"), dict) else {}
    return str(node.get("Name") or user.get("LoginName") or address).rstrip(".")


def manifest_context(manifest: dict[str, Any]) -> dict[str, Any]:
    context = manifest.get("context")
    return context if isinstance(context, dict) else {}


def issue_manifest(row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(str(row["manifest_json"] or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def task_kind_label(value: Any) -> str:
    return {
        "exam": "真题解析",
        "practice": "按题出题",
        "knowledge": "知识点出题",
        "format": "格式审查",
    }.get(str(value or ""), str(value or "任务"))


def task_stage_label(context: dict[str, Any]) -> str:
    operation = str(context.get("operation") or "")
    stage = str(context.get("task_stage") or "")
    label = {
        "analyze": "范围解析",
        "plan": "蓝图设计",
        "generate_from_plan": "题目生成",
        "generate_from_contract": "题目生成",
    }.get(operation) or {
        "analyzing": "范围解析",
        "planning": "蓝图设计",
        "generating": "题目生成",
        "failed": "执行失败",
    }.get(stage, stage)
    return f"{label}（失败）" if label and stage == "failed" and label != "执行失败" else label


def issue_summary(manifest: dict[str, Any]) -> str:
    context = manifest_context(manifest)
    task_title = str(context.get("task_title") or "").strip()
    if task_title:
        return " · ".join(filter(None, (
            task_kind_label(context.get("task_kind")),
            str(context.get("task_model_label") or context.get("task_model") or "").strip(),
            task_title,
        )))
    failure = manifest.get("failure_signature") if isinstance(manifest.get("failure_signature"), dict) else {}
    return " · ".join(
        str(value) for value in (manifest.get("scope"), context.get("page"), context.get("question_id"), failure.get("error_type")) if value
    ) or "用户问题反馈"


def issue_display_summary(row: dict[str, Any] | sqlite3.Row) -> str:
    """Render from the latest manifest so records created by older receivers are upgraded in place."""
    manifest = issue_manifest(row)
    summary = issue_summary(manifest)
    if summary != "用户问题反馈":
        return summary
    try:
        return str(row["summary"] or "用户问题反馈")
    except (KeyError, IndexError):
        return "用户问题反馈"


def diagnostic_score(manifest: dict[str, Any]) -> int:
    """Prefer the duplicate occurrence that retained the most diagnostic evidence."""
    coverage = manifest.get("diagnostic_coverage") if isinstance(manifest.get("diagnostic_coverage"), dict) else {}
    available = coverage.get("available") if isinstance(coverage.get("available"), dict) else {}
    legacy_counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    counts = coverage.get("counts") if isinstance(coverage.get("counts"), dict) else legacy_counts
    missing = coverage.get("missing_expected_evidence") if isinstance(coverage.get("missing_expected_evidence"), list) else []
    weights = {
        "failure_context": 120,
        "failure_diagnostic": 100,
        "model_diagnostics": 80,
        "task_lifecycle": 30,
        "backend_error_traces": 25,
        "runtime_context": 20,
        "model_call_summary": 15,
        "user_feedback": 10,
    }
    score = sum(weight for key, weight in weights.items() if available.get(key))
    score += min(20, int(counts.get("model_traces") or 0)) * 4
    score += min(40, int(counts.get("lifecycle_events") or 0))
    score += min(20, int(counts.get("backend_error_traces") or 0))
    score += min(20, int(counts.get("runtime_events") or 0))
    score += min(20, int(counts.get("frontend_events") or 0)) // 4
    score -= len(missing) * 20
    return score


def issue_task_metadata(manifest: dict[str, Any]) -> str:
    context = manifest_context(manifest)
    task_id = str(context.get("task_id") or "").strip()
    if not task_id:
        return ""
    parts = [f"任务：{task_id}"]
    stage = task_stage_label(context)
    if stage:
        parts.append(f"阶段：{stage}")
    batch_id = str(context.get("practice_batch_id") or "").strip()
    if batch_id:
        parts.append(f"任务批次：{batch_id}")
    report_group = str(context.get("report_group_id") or "").strip()
    if report_group:
        parts.append(f"反馈批次：{report_group[:12]}")
    return " · ".join(parts)


def codex_triage_prompt(report_id: Any) -> str:
    identifier = str(report_id or "").strip()
    return (
        f"请排查反馈 {identifier}。在 answer-book-platform 项目目录运行 "
        f"python3 scripts/inspect_support_report.py {identifier}，"
        "直接读取脚本输出的本地诊断文件，不要使用浏览器页面作为排查依据。"
    )


class Inbox:
    def __init__(self, root: Path, *, quota_bytes: int = DEFAULT_QUOTA_BYTES):
        self.root = root
        self.inbox = root / "inbox"
        self.tmp = root / "tmp"
        self.db_path = root / "support_reports.sqlite3"
        self.quota_bytes = max(MAX_UPLOAD_BYTES, int(quota_bytes))
        self.lock = threading.RLock()
        for path in (self.root, self.inbox, self.tmp):
            path.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS issues (
                    fingerprint TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    summary TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    resolved_at TEXT NOT NULL DEFAULT '',
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    versions_json TEXT NOT NULL DEFAULT '{}',
                    devices_json TEXT NOT NULL DEFAULT '{}',
                    bundle_path TEXT NOT NULL DEFAULT '',
                    bundle_sha256 TEXT NOT NULL DEFAULT '',
                    bundle_size INTEGER NOT NULL DEFAULT 0,
                    manifest_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    report_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    FOREIGN KEY(fingerprint) REFERENCES issues(fingerprint) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_issues_status_last_seen ON issues(status, last_seen DESC);
                """
            )

    def existing_receipt(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT r.report_id, r.fingerprint, i.report_id AS canonical_report_id FROM receipts r JOIN issues i USING(fingerprint) WHERE r.report_id = ?",
                (report_id,),
            ).fetchone()
        return dict(row) if row else None

    def store(self, temp_path: Path, manifest: dict[str, Any], sha256: str, device: str) -> dict[str, Any]:
        fingerprint = str(manifest["fingerprint"])
        incoming_report_id = str(manifest["report_id"])
        version = str((manifest.get("application") or {}).get("version") or "unknown")
        summary = issue_summary(manifest)
        target = self.inbox / f"{fingerprint}.zip"
        received_at = now_iso()
        with self.lock, self.connect() as connection:
            previous = connection.execute("SELECT * FROM issues WHERE fingerprint = ?", (fingerprint,)).fetchone()
            versions = json.loads(previous["versions_json"]) if previous else {}
            devices = json.loads(previous["devices_json"]) if previous else {}
            versions[version] = int(versions.get(version) or 0) + 1
            devices[device] = int(devices.get(device) or 0) + 1
            duplicate_content = bool(previous and previous["bundle_sha256"] == sha256 and target.is_file())
            canonical_id = str(previous["report_id"]) if previous else incoming_report_id
            previous_manifest = issue_manifest(previous) if previous else {}
            preserved_richer_bundle = bool(
                previous
                and target.is_file()
                and diagnostic_score(previous_manifest) > diagnostic_score(manifest)
            )
            bundle_replaced = not duplicate_content and not preserved_richer_bundle
            if bundle_replaced:
                os.replace(temp_path, target)
            else:
                temp_path.unlink(missing_ok=True)
            selected_manifest = previous_manifest if preserved_richer_bundle else manifest
            selected_summary = issue_summary(selected_manifest)
            selected_sha256 = str(previous["bundle_sha256"]) if preserved_richer_bundle else sha256
            if previous:
                status = "open" if previous["status"] == "resolved" else str(previous["status"])
                connection.execute(
                    """
                    UPDATE issues SET status=?, summary=?, last_seen=?, resolved_at='', occurrence_count=occurrence_count+1,
                        versions_json=?, devices_json=?, bundle_path=?, bundle_sha256=?, bundle_size=?, manifest_json=?
                    WHERE fingerprint=?
                    """,
                    (
                        status, selected_summary, received_at, json.dumps(versions, ensure_ascii=False), json.dumps(devices, ensure_ascii=False),
                        str(target) if target.exists() else str(previous["bundle_path"]), selected_sha256 if target.exists() else str(previous["bundle_sha256"]),
                        target.stat().st_size if target.exists() else int(previous["bundle_size"]), json.dumps(selected_manifest, ensure_ascii=False), fingerprint,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO issues(fingerprint, report_id, summary, first_seen, last_seen, versions_json, devices_json,
                        bundle_path, bundle_sha256, bundle_size, manifest_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint, canonical_id, summary, received_at, received_at, json.dumps(versions, ensure_ascii=False),
                        json.dumps(devices, ensure_ascii=False), str(target), sha256, target.stat().st_size, json.dumps(manifest, ensure_ascii=False),
                    ),
                )
            connection.execute(
                "INSERT OR IGNORE INTO receipts(report_id, fingerprint, sha256, received_at) VALUES(?, ?, ?, ?)",
                (incoming_report_id, fingerprint, sha256, received_at),
            )
        self.cleanup()
        return {
            "report_id": canonical_id,
            "duplicate": bool(previous),
            "duplicate_content": duplicate_content,
            "bundle_replaced": bundle_replaced,
            "preserved_richer_bundle": preserved_richer_bundle,
        }

    def list_issues(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM issues ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, last_seen DESC LIMIT ? OFFSET ?",
                (min(200, max(1, limit)), max(0, offset)),
            ).fetchall()
        return [dict(row) for row in rows]

    def issue(self, fingerprint: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM issues WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def issue_by_report_id(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT i.* FROM issues i
                WHERE i.report_id = ?
                   OR i.fingerprint = (SELECT fingerprint FROM receipts WHERE report_id = ? LIMIT 1)
                LIMIT 1
                """,
                (report_id, report_id),
            ).fetchone()
        return dict(row) if row else None

    def set_status(self, fingerprint: str, status: str) -> bool:
        if status not in {"open", "resolved"}:
            return False
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE issues SET status=?, resolved_at=? WHERE fingerprint=?",
                (status, now_iso() if status == "resolved" else "", fingerprint),
            )
        return cursor.rowcount > 0

    def delete(self, fingerprint: str) -> bool:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT bundle_path FROM issues WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not row:
                return False
            bundle_path = str(row["bundle_path"] or "")
            if bundle_path:
                Path(bundle_path).unlink(missing_ok=True)
            connection.execute("DELETE FROM issues WHERE fingerprint=?", (fingerprint,))
        return True

    def cleanup(self) -> None:
        now = datetime.now(timezone.utc)
        for part in self.tmp.glob("*.part"):
            try:
                if time.time() - part.stat().st_mtime > 3600:
                    part.unlink(missing_ok=True)
            except OSError:
                continue
        with self.lock, self.connect() as connection:
            rows = connection.execute("SELECT * FROM issues WHERE bundle_path != '' ORDER BY last_seen ASC").fetchall()
            for row in rows:
                reference = parse_time(row["resolved_at"] if row["status"] == "resolved" and row["resolved_at"] else row["last_seen"])
                retention = 7 if row["status"] == "resolved" else 30
                if now - reference > timedelta(days=retention):
                    Path(str(row["bundle_path"])).unlink(missing_ok=True)
                    connection.execute(
                        "UPDATE issues SET bundle_path='', bundle_sha256='', bundle_size=0 WHERE fingerprint=?",
                        (row["fingerprint"],),
                    )
            cutoff = (now - timedelta(days=180)).isoformat(timespec="seconds")
            connection.execute("DELETE FROM issues WHERE bundle_path='' AND last_seen < ?", (cutoff,))
            rows = connection.execute(
                "SELECT fingerprint, status, last_seen, bundle_path, bundle_size FROM issues WHERE bundle_path != '' ORDER BY CASE status WHEN 'resolved' THEN 0 ELSE 1 END, last_seen ASC"
            ).fetchall()
            total = sum(int(row["bundle_size"] or 0) for row in rows)
            soft_limit = int(self.quota_bytes * 0.8)
            target = soft_limit if total > soft_limit else self.quota_bytes
            for row in rows:
                if total <= target:
                    break
                Path(str(row["bundle_path"])).unlink(missing_ok=True)
                total -= int(row["bundle_size"] or 0)
                connection.execute(
                    "UPDATE issues SET bundle_path='', bundle_sha256='', bundle_size=0 WHERE fingerprint=?",
                    (row["fingerprint"],),
                )


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Cap request threads before they are created, not only inside handlers."""

    daemon_threads = True
    request_queue_size = 16

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], *, max_workers: int = 4):
        self._worker_slots = threading.BoundedSemaphore(max(1, int(max_workers)))
        super().__init__(server_address, handler_class)

    def process_request(self, request: Any, client_address: Any) -> None:
        self._worker_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def validate_bundle(path: Path, expected_report_id: str, expected_fingerprint: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBER_COUNT:
                raise ValueError("too_many_zip_members")
            total = 0
            for info in infos:
                name_path = Path(info.filename)
                if info.filename.startswith(("/", "\\")) or ".." in name_path.parts:
                    raise ValueError("unsafe_zip_path")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("zip_symlink_rejected")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError("zip_member_too_large")
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("zip_uncompressed_limit")
            info = zf.getinfo("manifest.json")
            if info.file_size > 64 * 1024:
                raise ValueError("manifest_too_large")
            manifest = json.loads(zf.read(info).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_support_bundle") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in {1, 2}:
        raise ValueError("unsupported_schema")
    if str(manifest.get("report_id") or "") != expected_report_id:
        raise ValueError("report_id_mismatch")
    if str(manifest.get("fingerprint") or "") != expected_fingerprint:
        raise ValueError("fingerprint_mismatch")
    return manifest


class UploadHandler(BaseHTTPRequestHandler):
    server_version = "AnswerBookSupportReceiver/1.0"
    _slots = threading.BoundedSemaphore(4)
    _rate_lock = threading.Lock()
    _recent_by_source: dict[str, list[float]] = {}

    @property
    def inbox(self) -> Inbox:
        return self.server.inbox  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[support-upload] {self.address_string()} {fmt % args}")

    def send_json(self, value: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self) -> bool:
        header = str(self.headers.get("Authorization") or "")
        return is_tailscale_source(str(self.client_address[0])) and header.startswith("Bearer ") and secrets.compare_digest(header[7:], self.token)

    def rate_allowed(self) -> bool:
        source = str(self.client_address[0])
        cutoff = time.time() - 60
        with self._rate_lock:
            recent = [stamp for stamp in self._recent_by_source.get(source, []) if stamp >= cutoff]
            if len(recent) >= 20:
                self._recent_by_source[source] = recent
                return False
            recent.append(time.time())
            self._recent_by_source[source] = recent
        return True

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self.send_json({"ok": True, "time": now_iso()})
            return
        self.send_json({"ok": False, "error": "not_found"}, 404)

    def do_POST(self) -> None:
        if not self._slots.acquire(blocking=False):
            self.send_json({"ok": False, "error": "receiver_busy"}, 429)
            return
        try:
            self._do_POST()
        finally:
            self._slots.release()

    def _do_POST(self) -> None:
        if urlparse(self.path).path != "/api/support-reports":
            self.send_json({"ok": False, "error": "not_found"}, 404)
            return
        if not self.authorized():
            self.send_json({"ok": False, "error": "unauthorized"}, 401)
            return
        if not self.rate_allowed():
            self.send_json({"ok": False, "error": "rate_limited"}, 429)
            return
        report_id = str(self.headers.get("X-Support-Report-ID") or "").strip()
        fingerprint = str(self.headers.get("X-Support-Fingerprint") or "").strip()
        expected_sha = str(self.headers.get("X-Support-SHA256") or "").strip().lower()
        if not report_id or not fingerprint or not expected_sha:
            self.send_json({"ok": False, "error": "missing_headers"}, 400)
            return
        existing = self.inbox.existing_receipt(report_id)
        if existing:
            self.send_json({"ok": True, "report_id": existing["canonical_report_id"], "duplicate": True, "idempotent": True})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_json({"ok": False, "error": "upload_size_rejected"}, 413)
            return
        self.inbox.cleanup()
        try:
            free_bytes = shutil.disk_usage(self.inbox.root).free
        except OSError:
            free_bytes = length + 16 * 1024 * 1024
        if free_bytes < length + 16 * 1024 * 1024:
            self.send_json({"ok": False, "error": "insufficient_storage"}, 507)
            return
        fd, raw_tmp = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=str(self.inbox.tmp))
        digest = hashlib.sha256()
        remaining = length
        try:
            with os.fdopen(fd, "wb") as handle:
                while remaining:
                    chunk = self.rfile.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("incomplete_upload")
                    handle.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path = Path(raw_tmp)
            if not secrets.compare_digest(digest.hexdigest(), expected_sha):
                raise ValueError("sha256_mismatch")
            manifest = validate_bundle(temp_path, report_id, fingerprint)
            result = self.inbox.store(temp_path, manifest, expected_sha, device_identity(str(self.client_address[0])))
            self.send_json({"ok": True, **result})
        except ValueError as exc:
            Path(raw_tmp).unlink(missing_ok=True)
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception:
            Path(raw_tmp).unlink(missing_ok=True)
            self.send_json({"ok": False, "error": "receiver_error"}, 500)


class AdminHandler(BaseHTTPRequestHandler):
    server_version = "AnswerBookSupportAdmin/1.0"

    @property
    def inbox(self) -> Inbox:
        return self.server.inbox  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[support-admin] {fmt % args}")

    def send_html(self, value: str, status: int = 200) -> None:
        raw = value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, target: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            query = parse_qs(urlparse(self.path).query)
            try:
                offset = max(0, int((query.get("offset") or ["0"])[0]))
            except ValueError:
                offset = 0
            rows = self.inbox.list_issues(limit=50, offset=offset)
            cards = []
            for row in rows:
                manifest = issue_manifest(row)
                summary = issue_display_summary(row)
                task_metadata = issue_task_metadata(manifest)
                codex_prompt = html.escape(codex_triage_prompt(row["report_id"]), quote=True)
                status_label = "待处理" if row["status"] == "open" else "已处理"
                raw_state = f"诊断包 {round(int(row['bundle_size'] or 0) / 1024)} KiB" if row["bundle_path"] else "原始包已清理"
                cards.append(f"""
                <article class="issue {html.escape(row['status'])}">
                  <header><a href="/issues/{quote(row['fingerprint'])}">{html.escape(row['report_id'])}</a><span>{status_label}</span></header>
                  <p>{html.escape(summary)}</p>
                  {f'<div class="task-meta">{html.escape(task_metadata)}</div>' if task_metadata else ''}
                  <small>出现 {row['occurrence_count']} 次 · 最近：{html.escape(display_local_time(row['last_seen']))} · {raw_state}</small>
                  <form method="post" action="/issues/{quote(row['fingerprint'])}/{'resolve' if row['status'] == 'open' else 'reopen'}"><button>{'标记已处理' if row['status'] == 'open' else '重新打开'}</button></form>
                  <button type="button" class="secondary" data-codex-prompt="{codex_prompt}" onclick="copyCodexPrompt(this)">复制给 Codex 排查</button>
                </article>""")
            navigation = '<p class="pagination">'
            if offset:
                navigation += f'<a href="/?offset={max(0, offset - 50)}">上一页</a> '
            if len(rows) == 50:
                navigation += f'<a href="/?offset={offset + 50}">下一页</a>'
            navigation += "</p>"
            self.send_html(page("问题反馈中心", ("".join(cards) or "<p>暂无问题反馈。</p>") + navigation))
            return
        match = path.strip("/").split("/")
        if len(match) == 2 and match[0] == "issues":
            row = self.inbox.issue(unquote(match[1]))
            if not row:
                self.send_html(page("未找到", "<p>问题不存在。</p>"), 404)
                return
            manifest = issue_manifest(row)
            summary = issue_display_summary(row)
            task_metadata = issue_task_metadata(manifest)
            codex_prompt = html.escape(codex_triage_prompt(row["report_id"]), quote=True)
            sections = [
                f"<h2>{html.escape(row['report_id'])}</h2><p>{html.escape(summary)}</p>"
                + (f'<div class="task-meta">{html.escape(task_metadata)}</div>' if task_metadata else "")
            ]
            bundle = Path(str(row["bundle_path"] or ""))
            if bundle.is_file():
                try:
                    with zipfile.ZipFile(bundle) as zf:
                        for name in ("manifest.json", "task_lifecycle.json", "runtime_error_context.json", "model_call_summary.json", "related_content.json", "model_diagnostics.json"):
                            try:
                                info = zf.getinfo(name)
                            except KeyError:
                                continue
                            if info.file_size <= 3 * 1024 * 1024:
                                content = zf.read(info).decode("utf-8", errors="replace")
                                sections.append(f"<details><summary>{html.escape(name)}</summary><pre>{html.escape(content)}</pre></details>")
                except zipfile.BadZipFile:
                    sections.append("<p>诊断包已损坏。</p>")
                sections.append(f'<p><a href="/issues/{quote(row["fingerprint"])}/download">下载原始诊断包</a></p>')
            sections.append(f"""
              <form method="post" action="/issues/{quote(row['fingerprint'])}/{'resolve' if row['status'] == 'open' else 'reopen'}"><button>{'标记已处理' if row['status'] == 'open' else '重新打开'}</button></form>
              <button type="button" class="secondary" data-codex-prompt="{codex_prompt}" onclick="copyCodexPrompt(this)">复制给 Codex 排查</button>
              <form method="post" action="/issues/{quote(row['fingerprint'])}/delete" onsubmit="return confirm('确定永久删除？')"><button class="danger">永久删除</button></form>
            """)
            self.send_html(page(row["report_id"], "".join(sections)))
            return
        if len(match) == 3 and match[0] == "issues" and match[2] == "download":
            row = self.inbox.issue(unquote(match[1]))
            bundle = Path(str(row["bundle_path"] or "")) if row else Path()
            if not row or not bundle.is_file():
                self.send_html(page("不可下载", "<p>原始诊断包不存在或已按保留规则清理。</p>"), 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(bundle.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{row["report_id"]}.zip"')
            self.end_headers()
            with bundle.open("rb") as handle:
                shutil.copyfileobj(handle, self.wfile, length=CHUNK_BYTES)
            return
        self.send_html(page("未找到", "<p>页面不存在。</p>"), 404)

    def do_POST(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "issues":
            self.redirect()
            return
        fingerprint = unquote(parts[1])
        if parts[2] == "resolve":
            self.inbox.set_status(fingerprint, "resolved")
        elif parts[2] == "reopen":
            self.inbox.set_status(fingerprint, "open")
        elif parts[2] == "delete":
            self.inbox.delete(fingerprint)
        self.redirect("/")


def page(title: str, content: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(title)}</title><style>
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;background:#f6f7fb;color:#18202b}}
    nav{{margin-bottom:24px}}nav a,a{{color:#1769aa}}.issue{{background:white;border:1px solid #dde2ea;border-radius:12px;padding:18px;margin:12px 0}}
    .issue header{{display:flex;justify-content:space-between;font-weight:700}}.issue.resolved{{opacity:.72}}small{{color:#637083}}
    .task-meta{{margin:6px 0 10px;padding:8px 10px;border-radius:7px;background:#eef5ff;color:#234a78;font-size:13px;word-break:break-all}}
    form{{display:inline-block;margin:10px 8px 0 0}}button{{padding:7px 12px;border:0;border-radius:7px;background:#1769aa;color:white;cursor:pointer}}button.secondary{{margin:10px 8px 0 0;background:#475569}}button.danger{{background:#b42318}}
    details{{background:white;border:1px solid #dde2ea;border-radius:10px;margin:12px 0;padding:12px}}pre{{white-space:pre-wrap;word-break:break-word;max-height:520px;overflow:auto}}
    </style><body><nav><a href="/">← 问题列表</a></nav><h1>{html.escape(title)}</h1>{content}
    <script>
    async function copyCodexPrompt(button) {{
      const promptText = button.dataset.codexPrompt || "";
      try {{
        await navigator.clipboard.writeText(promptText);
        const original = button.textContent;
        button.textContent = "已复制，直接发给 Codex";
        setTimeout(() => {{ button.textContent = original; }}, 1800);
      }} catch (_error) {{
        window.prompt("复制下面的排查指令", promptText);
      }}
    }}
    </script></body></html>"""


def ensure_token(path: Path, explicit: str = "") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = explicit.strip()
    if not token and path.exists():
        token = path.read_text(encoding="utf-8").strip()
    if not token:
        token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def cleanup_loop(inbox: Inbox, stop: threading.Event) -> None:
    while not stop.wait(3600):
        inbox.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Receive Answer Book Platform diagnostics over Tailscale")
    parser.add_argument("--root", default=str(default_root()))
    parser.add_argument("--upload-host", default="")
    parser.add_argument("--upload-port", type=int, default=8777)
    parser.add_argument("--admin-host", default="127.0.0.1")
    parser.add_argument("--admin-port", type=int, default=8778)
    parser.add_argument("--token", default="")
    parser.add_argument("--quota-mib", type=int, default=512)
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    token = ensure_token(root / "receiver_token", args.token)
    upload_host = str(args.upload_host or tailscale_ipv4()).strip()
    if not upload_host:
        raise SystemExit("Tailscale 尚未运行，无法确定接收地址。请启动 Tailscale，或用 --upload-host 指定测试地址。")
    inbox = Inbox(root, quota_bytes=max(32, args.quota_mib) * 1024 * 1024)
    upload_server = BoundedThreadingHTTPServer((upload_host, args.upload_port), UploadHandler, max_workers=4)
    upload_server.inbox = inbox  # type: ignore[attr-defined]
    upload_server.token = token  # type: ignore[attr-defined]
    admin_server = ThreadingHTTPServer((args.admin_host, args.admin_port), AdminHandler)
    admin_server.inbox = inbox  # type: ignore[attr-defined]
    stop = threading.Event()
    threads = [
        threading.Thread(target=upload_server.serve_forever, name="support-upload", daemon=True),
        threading.Thread(target=cleanup_loop, args=(inbox, stop), name="support-cleanup", daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(f"Tailscale 上传地址：http://{upload_host}:{args.upload_port}")
    print(f"本机管理页面：http://{args.admin_host}:{args.admin_port}")
    print(f"客户端配置 token：{token}")
    try:
        admin_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        upload_server.shutdown()
        admin_server.server_close()
        upload_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
