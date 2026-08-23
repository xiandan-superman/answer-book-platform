#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import signal
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_UPLOAD_BYTES = 768 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
DEFAULT_QUOTA_BYTES = 30 * 1024 * 1024 * 1024
TERMINAL = {"completed", "failed", "cancelled"}
METADATA_HEADER_ENCODING = "percent-utf8-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_component(value: Any, fallback: str = "unknown") -> str:
    text = "".join(character if character.isalnum() or character in "._-" else "_" for character in str(value or ""))
    return text[:160] or fallback


def is_private_source(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")


def is_safe_bind(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_metadata_header(value: object, encoding: object = "") -> str:
    raw = str(value or "").strip()
    if str(encoding or "").strip() != METADATA_HEADER_ENCODING:
        return raw
    try:
        return unquote(raw, encoding="utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_job_header_encoding") from exc


class JobStore:
    def __init__(self, root: Path, *, quota_bytes: int, project_root: Path | None = None):
        self.root = root
        self.project_root = (project_root or Path(__file__).resolve().parents[1]).resolve()
        self.db_path = root / "hybrid_jobs.sqlite3"
        self.quota_bytes = max(MAX_UPLOAD_BYTES * 2, quota_bytes)
        self.lock = threading.RLock()
        for name in ("uploads", "results", "logs", "tenants"):
            (root / name).mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    input_path TEXT NOT NULL,
                    result_path TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(tenant_id, idempotency_key)
                )
                """
            )
            # A process interruption is distinguishable from a model failure.
            # Requeue it once and retain the recovery phase in the job history.
            db.execute(
                """
                UPDATE jobs
                SET status='queued', phase='recovered_after_restart', updated_at=?, error=''
                WHERE status='running' AND attempt < 2
                """,
                (utc_now(),),
            )
            db.execute(
                """
                UPDATE jobs
                SET status='failed', phase='cloud_infrastructure', updated_at=?, finished_at=?,
                    error='Cloud worker was interrupted repeatedly during server restart.'
                WHERE status='running'
                """,
                (utc_now(), utc_now()),
            )

    def used_bytes(self) -> int:
        total = 0
        for folder in (self.root / "uploads", self.root / "results", self.root / "tenants"):
            for path in folder.rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        return total

    def get(self, job_id: str, tenant_id: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM jobs WHERE job_id=? AND tenant_id=?", (job_id, tenant_id)).fetchone()

    def by_idempotency(self, tenant_id: str, key: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM jobs WHERE tenant_id=? AND idempotency_key=?", (tenant_id, key)).fetchone()

    def create(self, *, tenant_id: str, client_id: str, task_id: str, idempotency_key: str, input_sha256: str, input_path: Path) -> sqlite3.Row:
        if self.used_bytes() + input_path.stat().st_size > self.quota_bytes:
            raise RuntimeError("Cloud hybrid storage quota is full")
        job_id = secrets.token_hex(16)
        final_input = self.root / "uploads" / f"{job_id}.zip"
        os.replace(input_path, final_input)
        result_path = self.root / "results" / f"{job_id}.zip"
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO jobs(job_id,tenant_id,client_id,task_id,idempotency_key,input_sha256,status,phase,created_at,updated_at,input_path,result_path)
                VALUES(?,?,?,?,?,?, 'queued','cloud_queue',?,?,?,?)
                """,
                (job_id, tenant_id, client_id, task_id, idempotency_key, input_sha256, now, now, str(final_input), str(result_path)),
            )
        return self.get(job_id, tenant_id)  # type: ignore[return-value]

    def claim_next(self) -> sqlite3.Row | None:
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                db.commit()
                return None
            now = utc_now()
            db.execute(
                "UPDATE jobs SET status='running', phase='cloud_pipeline', started_at=?, updated_at=?, attempt=attempt+1 WHERE job_id=? AND status='queued'",
                (now, now, row["job_id"]),
            )
            db.commit()
            return db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()

    def update(self, job_id: str, **values: Any) -> None:
        allowed = {"status", "phase", "progress_json", "updated_at", "finished_at", "error", "result_sha256", "cancel_requested"}
        fields = [(name, value) for name, value in values.items() if name in allowed]
        if not fields:
            return
        if "updated_at" not in values:
            fields.append(("updated_at", utc_now()))
        sql = ", ".join(f"{name}=?" for name, _ in fields)
        with self.connect() as db:
            db.execute(f"UPDATE jobs SET {sql} WHERE job_id=?", (*[value for _, value in fields], job_id))

    def request_cancel(self, row: sqlite3.Row) -> None:
        if row["status"] == "queued":
            self.update(row["job_id"], status="cancelled", phase="cancelled", cancel_requested=1, finished_at=utc_now())
        elif row["status"] == "running":
            self.update(row["job_id"], cancel_requested=1, phase="cancelling")

    def health_summary(self, tenant_id: str) -> dict[str, Any]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM jobs WHERE tenant_id=? GROUP BY status",
                (tenant_id,),
            ).fetchall()
            integrity = str(db.execute("PRAGMA quick_check").fetchone()[0])
        disk = shutil.disk_usage(self.root)
        provider_path = self.root / "tenants" / safe_component(tenant_id) / "runtime" / "config" / "providers.local.json"
        example_path = self.project_root / "config" / "providers.example.json"
        hosts: set[str] = set()
        provider_config_error = ""

        def read_mapping(path: Path) -> dict[str, Any]:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} must contain a JSON object")
            return value

        def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
            merged = dict(base)
            for key, value in override.items():
                current = merged.get(key)
                if isinstance(current, dict) and isinstance(value, dict):
                    merged[key] = merge(current, value)
                else:
                    merged[key] = value
            return merged

        try:
            base = read_mapping(example_path)
            local = read_mapping(provider_path)
            config = merge(base, local)
            providers = config.get("providers")
            if not isinstance(providers, dict):
                raise ValueError("providers must be a JSON object")
            active_provider = str(config.get("active_provider") or "").strip()
            selected = [providers.get(active_provider)] if active_provider else list(providers.values())
            for value in selected:
                if not isinstance(value, dict):
                    continue
                host = urlparse(str(value.get("base_url") or "")).hostname
                if host:
                    hosts.add(host)
            if not hosts:
                raise ValueError("active provider has no base_url")
        except (OSError, ValueError, json.JSONDecodeError, AttributeError) as exc:
            provider_config_error = str(exc)[:300]

        def resolve(host: str) -> tuple[str, bool, str]:
            try:
                addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                return host, bool(addresses), ""
            except OSError as exc:
                return host, False, str(exc)[:200]

        dns: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(hosts)))) as pool:
            futures = [pool.submit(resolve, host) for host in sorted(hosts)[:12]]
            try:
                for future in as_completed(futures, timeout=8):
                    host, ok, error = future.result()
                    dns[host] = {"ok": ok, "error": error}
            except TimeoutError:
                pass
        for host in sorted(hosts)[:12]:
            dns.setdefault(host, {"ok": False, "error": "dns_check_timeout"})
        free_ok = disk.free >= 5 * 1024 * 1024 * 1024
        provider_config_ok = not provider_config_error and bool(hosts)
        dns_ok = provider_config_ok and bool(dns) and all(item["ok"] for item in dns.values())
        return {
            "ok": integrity == "ok" and free_ok and provider_config_ok and dns_ok,
            "time": utc_now(),
            "sqlite_integrity": integrity,
            "storage": {
                "free_bytes": disk.free,
                "total_bytes": disk.total,
                "quota_bytes": self.quota_bytes,
                "used_by_service_bytes": self.used_bytes(),
                "free_ok": free_ok,
            },
            "queue": {row["status"]: row["count"] for row in rows},
            "provider_config_ok": provider_config_ok,
            "provider_config_error": provider_config_error,
            "provider_dns": dns,
            "provider_dns_ok": dns_ok,
        }


class WorkerLoop(threading.Thread):
    def __init__(self, store: JobStore, *, project_root: Path, max_runtime_seconds: int):
        super().__init__(name="hybrid-cloud-worker", daemon=True)
        self.store = store
        self.project_root = project_root
        self.max_runtime_seconds = max_runtime_seconds
        self.stop_event = threading.Event()
        self.process_lock = threading.RLock()
        self.active_process: subprocess.Popen[bytes] | None = None
        self.active_job_id = ""

    def stop(self) -> None:
        self.stop_event.set()
        with self.process_lock:
            if self.active_process and self.active_process.poll() is None:
                self.active_process.terminate()

    def cancel(self, job_id: str) -> None:
        with self.process_lock:
            if self.active_job_id == job_id and self.active_process and self.active_process.poll() is None:
                self.active_process.terminate()

    def progress(self, row: sqlite3.Row) -> dict[str, Any]:
        task_path = self.store.root / "tenants" / safe_component(row["tenant_id"]) / "runtime" / "tasks" / row["task_id"] / "task.json"
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            "current_stage": task.get("current_stage", ""),
            "current_operation": task.get("current_operation", ""),
            "completed_count": task.get("completed_count", 0),
            "total_count": task.get("total_count", 0),
            "last_heartbeat_at": task.get("last_heartbeat_at", ""),
        }

    def run(self) -> None:
        while not self.stop_event.is_set():
            row = self.store.claim_next()
            if row is None:
                self.stop_event.wait(0.5)
                continue
            self.run_job(row)

    def run_job(self, row: sqlite3.Row) -> None:
        tenant_root = self.store.root / "tenants" / safe_component(row["tenant_id"])
        data_root = tenant_root / "runtime"
        data_root.mkdir(parents=True, exist_ok=True)
        log_path = self.store.root / "logs" / f"{row['job_id']}.log"
        command = [
            sys.executable,
            str(self.project_root / "scripts" / "hybrid_cloud_worker.py"),
            "--input", row["input_path"],
            "--result", row["result_path"],
            "--data-root", str(data_root),
            "--job-id", row["job_id"],
            "--tenant-id", row["tenant_id"],
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.project_root)
        started = time.monotonic()
        timed_out = False
        cancelled = False
        with log_path.open("ab") as log:
            process = subprocess.Popen(command, cwd=self.project_root, env=environment, stdout=log, stderr=subprocess.STDOUT)
            with self.process_lock:
                self.active_process = process
                self.active_job_id = row["job_id"]
            while process.poll() is None:
                current = self.store.get(row["job_id"], row["tenant_id"])
                if current and current["cancel_requested"]:
                    cancelled = True
                    process.terminate()
                elif time.monotonic() - started > self.max_runtime_seconds:
                    timed_out = True
                    process.terminate()
                progress = self.progress(row)
                if progress:
                    self.store.update(row["job_id"], progress_json=json.dumps(progress, ensure_ascii=False))
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    continue
            with self.process_lock:
                self.active_process = None
                self.active_job_id = ""
        result_path = Path(row["result_path"])
        worker_status_path = data_root / "tasks" / row["task_id"] / "stage_outputs" / "hybrid_cloud_worker.json"
        try:
            worker_status = json.loads(worker_status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            worker_status = {}
        result_sha = sha256_file(result_path) if result_path.is_file() else ""
        if not result_sha:
            interruption_phase = "cancelled" if cancelled else "cloud_timeout" if timed_out else "cloud_infrastructure"
            interruption_error = (
                "Cancelled by client"
                if cancelled
                else f"Cloud job exceeded {self.max_runtime_seconds} seconds"
                if timed_out
                else str(worker_status.get("error") or f"Cloud worker exited with code {process.returncode}")
            )
            subprocess.run(
                [
                    sys.executable,
                    str(self.project_root / "scripts" / "hybrid_package_failure.py"),
                    "--data-root", str(data_root),
                    "--task-id", row["task_id"],
                    "--job-id", row["job_id"],
                    "--tenant-id", row["tenant_id"],
                    "--result", row["result_path"],
                    "--phase", interruption_phase,
                    "--error", interruption_error[:1000],
                ],
                cwd=self.project_root,
                env={**environment, "PYTHONPATH": str(self.project_root)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
            result_sha = sha256_file(result_path) if result_path.is_file() else ""
        if cancelled:
            self.store.update(row["job_id"], status="cancelled", phase="cancelled", finished_at=utc_now(), error="Cancelled by client", result_sha256=result_sha)
        elif timed_out:
            self.store.update(row["job_id"], status="failed", phase="cloud_timeout", finished_at=utc_now(), error=f"Cloud job exceeded {self.max_runtime_seconds} seconds", result_sha256=result_sha)
        elif process.returncode == 0 and result_sha:
            self.store.update(row["job_id"], status="completed", phase="awaiting_download", finished_at=utc_now(), error="", result_sha256=result_sha)
        else:
            error = str(worker_status.get("error") or f"Cloud worker exited with code {process.returncode}")
            self.store.update(row["job_id"], status="failed", phase="cloud_pipeline", finished_at=utc_now(), error=error[:4000], result_sha256=result_sha)


class HybridServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], *, store: JobStore, tokens: dict[str, str], worker: WorkerLoop):
        super().__init__(address, handler)
        self.store = store
        self.tokens = tokens
        self.worker = worker


class Handler(BaseHTTPRequestHandler):
    server: HybridServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def json_response(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authenticate(self) -> str:
        if not is_private_source(self.client_address[0]):
            return ""
        header = str(self.headers.get("Authorization") or "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        for expected, tenant in self.server.tokens.items():
            if hmac.compare_digest(token, expected):
                return tenant
        return ""

    def job_path(self) -> tuple[str, str]:
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if len(parts) >= 4 and parts[:3] == ["api", "v1", "jobs"]:
            return parts[3], parts[4] if len(parts) > 4 else ""
        return "", ""

    def public_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            progress = json.loads(row["progress_json"] or "{}")
        except json.JSONDecodeError:
            progress = {}
        return {
            "job_id": row["job_id"],
            "task_id": row["task_id"],
            "status": row["status"],
            "phase": row["phase"],
            "progress": progress,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error": row["error"],
            "attempt": row["attempt"],
            "result_available": bool(row["result_sha256"] and Path(row["result_path"]).is_file()),
            "result_sha256": row["result_sha256"],
        }

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            if not is_private_source(self.client_address[0]):
                self.json_response(403, {"ok": False})
                return
            self.json_response(200, {"ok": True, "service": "answer-book-hybrid", "time": utc_now()})
            return
        tenant = self.authenticate()
        if not tenant:
            self.json_response(401, {"ok": False, "error": "unauthorized"})
            return
        if urlparse(self.path).path == "/api/v1/health":
            health = self.server.store.health_summary(tenant)
            # The authenticated request itself succeeded even when a component
            # is unhealthy.  Always return the snapshot so clients can persist
            # the exact reason before refusing new work.
            self.json_response(200, {"ok": True, "health": health})
            return
        job_id, action = self.job_path()
        row = self.server.store.get(job_id, tenant) if job_id else None
        if row is None:
            self.json_response(404, {"ok": False, "error": "job_not_found"})
            return
        if action == "result":
            result = Path(row["result_path"])
            if not row["result_sha256"] or not result.is_file():
                self.json_response(409, {"ok": False, "error": "result_not_available"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(result.stat().st_size))
            self.send_header("X-Content-SHA256", row["result_sha256"])
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with result.open("rb") as stream:
                while chunk := stream.read(CHUNK_BYTES):
                    self.wfile.write(chunk)
            return
        if action:
            self.json_response(404, {"ok": False, "error": "not_found"})
            return
        self.json_response(200, {"ok": True, "job": self.public_row(row)})

    def do_POST(self) -> None:
        tenant = self.authenticate()
        if not tenant:
            self.json_response(401, {"ok": False, "error": "unauthorized"})
            return
        path = urlparse(self.path).path
        if path == "/api/v1/jobs":
            self.create_job(tenant)
            return
        job_id, action = self.job_path()
        if action != "cancel":
            self.json_response(404, {"ok": False, "error": "not_found"})
            return
        row = self.server.store.get(job_id, tenant)
        if row is None:
            self.json_response(404, {"ok": False, "error": "job_not_found"})
            return
        if row["status"] not in TERMINAL:
            self.server.store.request_cancel(row)
            self.server.worker.cancel(job_id)
        current = self.server.store.get(job_id, tenant)
        self.json_response(200, {"ok": True, "job": self.public_row(current)})  # type: ignore[arg-type]

    def create_job(self, tenant: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.json_response(413, {"ok": False, "error": "invalid_upload_size"})
            return
        metadata_encoding = self.headers.get("X-Metadata-Encoding") or ""
        try:
            task_id = decode_metadata_header(self.headers.get("X-Task-ID"), metadata_encoding)
            client_id = decode_metadata_header(self.headers.get("X-Client-ID"), metadata_encoding)
            key = decode_metadata_header(self.headers.get("X-Idempotency-Key"), metadata_encoding)
        except ValueError:
            self.json_response(400, {"ok": False, "error": "invalid_job_header_encoding"})
            return
        expected_sha = str(self.headers.get("X-Content-SHA256") or "").strip().lower()
        if not task_id or not client_id or not key or len(key) > 200 or len(expected_sha) != 64:
            self.json_response(400, {"ok": False, "error": "missing_job_headers"})
            return
        existing = self.server.store.by_idempotency(tenant, key)
        if existing:
            if not hmac.compare_digest(existing["input_sha256"], expected_sha):
                self.json_response(409, {"ok": False, "error": "idempotency_payload_mismatch"})
                return
            self.json_response(200, {"ok": True, "idempotent_replay": True, "job": self.public_row(existing)})
            return
        temporary = self.server.store.root / "uploads" / f".upload-{secrets.token_hex(12)}.tmp"
        digest = hashlib.sha256()
        remaining = length
        try:
            with temporary.open("wb") as stream:
                while remaining:
                    chunk = self.rfile.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise RuntimeError("upload_ended_early")
                    stream.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            actual_sha = digest.hexdigest()
            if not hmac.compare_digest(actual_sha, expected_sha):
                self.json_response(400, {"ok": False, "error": "upload_sha256_mismatch"})
                return
            # Parse the signed-by-hash bundle before admitting it to the durable queue.
            env = os.environ.copy()
            check = subprocess.run(
                [sys.executable, "-c", (
                    "import json,sys,tempfile; from pathlib import Path; "
                    "from app.hybrid_contract import safe_extract_bundle; "
                    "d=tempfile.TemporaryDirectory(); m=safe_extract_bundle(Path(sys.argv[1]),Path(d.name)); print(json.dumps(m))"
                ), str(temporary)],
                cwd=self.server.worker.project_root,
                env={**env, "PYTHONPATH": str(self.server.worker.project_root)},
                capture_output=True,
                text=True,
                timeout=90,
            )
            if check.returncode != 0:
                self.json_response(400, {"ok": False, "error": "invalid_hybrid_bundle"})
                return
            manifest = json.loads(check.stdout.strip().splitlines()[-1])
            if manifest.get("bundle_kind") != "input" or manifest.get("task_id") != task_id or manifest.get("tenant_id") != tenant:
                self.json_response(400, {"ok": False, "error": "bundle_identity_mismatch"})
                return
            row = self.server.store.create(
                tenant_id=tenant,
                client_id=client_id,
                task_id=task_id,
                idempotency_key=key,
                input_sha256=actual_sha,
                input_path=temporary,
            )
            self.json_response(202, {"ok": True, "job": self.public_row(row)})
        except Exception as exc:
            self.json_response(500, {"ok": False, "error": str(exc)[:300]})
        finally:
            temporary.unlink(missing_ok=True)


def load_tokens(path: Path | None) -> dict[str, str]:
    value: dict[str, str] = {}
    if path and path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("tokens") if isinstance(raw, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("token") and row.get("tenant_id"):
                    value[str(row["token"])] = safe_component(row["tenant_id"])
    environment_token = str(os.environ.get("ANSWER_BOOK_HYBRID_TOKEN") or "").strip()
    environment_tenant = safe_component(os.environ.get("ANSWER_BOOK_HYBRID_TENANT") or "default")
    if environment_token:
        value[environment_token] = environment_tenant
    if not value:
        raise RuntimeError("No hybrid API token is configured")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Private Answer Book hybrid cloud queue.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--tokens")
    parser.add_argument("--quota-gb", type=float, default=30.0)
    parser.add_argument("--max-runtime-seconds", type=int, default=21600)
    args = parser.parse_args()
    if not is_safe_bind(args.host):
        raise RuntimeError("Hybrid service may bind only to loopback or a Tailscale address")
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parents[1]
    store = JobStore(root, quota_bytes=int(args.quota_gb * 1024 * 1024 * 1024), project_root=project_root)
    worker = WorkerLoop(store, project_root=project_root, max_runtime_seconds=max(60, args.max_runtime_seconds))
    tokens = load_tokens(Path(args.tokens).resolve() if args.tokens else None)
    server = HybridServer((args.host, args.port), Handler, store=store, tokens=tokens, worker=worker)
    worker.start()

    def stop(*_: Any) -> None:
        worker.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        worker.stop()
        worker.join(timeout=10)
        server.server_close()


if __name__ == "__main__":
    main()
