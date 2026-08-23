from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import ssl
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .hybrid_contract import create_input_bundle, import_result_bundle, sha256_file
from .hybrid_local import complete_hybrid_local_delivery, prepare_hybrid_input
from .paths import CONFIG_DIR, LOCAL_CONFIG_DIR
from .task_control import TaskCancelled, read_task_control
from .task_store import load_task, task_dir, update_task, update_task_hybrid

DEFAULT_CONFIG_PATH = CONFIG_DIR / "hybrid_cloud.example.json"
BUNDLED_CONFIG_PATH = CONFIG_DIR / "hybrid_cloud.json"
LOCAL_CONFIG_PATH = LOCAL_CONFIG_DIR / "hybrid_cloud.json"
CHUNK_BYTES = 1024 * 1024


class HybridClientError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_hybrid_config() -> dict[str, Any]:
    value = _read_json(DEFAULT_CONFIG_PATH)
    value.update(_read_json(BUNDLED_CONFIG_PATH))
    value.update(_read_json(LOCAL_CONFIG_PATH))
    if os.environ.get("ANSWER_BOOK_HYBRID_URL"):
        value["base_url"] = os.environ["ANSWER_BOOK_HYBRID_URL"]
    if os.environ.get("ANSWER_BOOK_HYBRID_TOKEN"):
        value["token"] = os.environ["ANSWER_BOOK_HYBRID_TOKEN"]
    if os.environ.get("ANSWER_BOOK_HYBRID_TENANT"):
        value["tenant_id"] = os.environ["ANSWER_BOOK_HYBRID_TENANT"]
    enabled = str(os.environ.get("ANSWER_BOOK_HYBRID_ENABLED") or value.get("enabled") or "").lower()
    value["enabled"] = enabled in {"1", "true", "yes", "on"}
    return value


def hybrid_enabled() -> bool:
    config = load_hybrid_config()
    return bool(config.get("enabled") and config.get("base_url") and config.get("token"))


def hybrid_settings_payload() -> dict[str, Any]:
    """Expose execution mode without disclosing the bundled server credential."""

    config = load_hybrid_config()
    parsed = urlsplit(str(config.get("base_url") or ""))
    available = bool(parsed.hostname and config.get("token"))
    environment_locked = "ANSWER_BOOK_HYBRID_ENABLED" in os.environ
    if environment_locked:
        source = "environment"
    elif LOCAL_CONFIG_PATH.is_file() and "enabled" in _read_json(LOCAL_CONFIG_PATH):
        source = "local"
    elif BUNDLED_CONFIG_PATH.is_file() and "enabled" in _read_json(BUNDLED_CONFIG_PATH):
        source = "bundled"
    else:
        source = "default"
    enabled = bool(config.get("enabled") and available)
    return {
        "available": available,
        "enabled": enabled,
        "execution_mode": "hybrid" if enabled else "local",
        "server_host": str(parsed.hostname or ""),
        "setting_source": source,
        "environment_locked": environment_locked,
        "message": (
            "真题解析将上传必要任务材料并由混合云服务器执行。"
            if enabled
            else "默认在当前电脑执行，任务材料不上传到混合云服务器。"
        ),
    }


def save_hybrid_enabled(enabled: bool) -> dict[str, Any]:
    if "ANSWER_BOOK_HYBRID_ENABLED" in os.environ:
        raise HybridClientError("混合云开关当前由启动环境锁定，无法在页面修改。")
    current = _read_json(LOCAL_CONFIG_PATH)
    if enabled:
        merged = load_hybrid_config()
        if not str(merged.get("base_url") or "").strip() or not str(merged.get("token") or "").strip():
            raise HybridClientError("当前安装包未配置可用的混合云服务器。")
    current["enabled"] = bool(enabled)
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LOCAL_CONFIG_PATH.with_suffix(f"{LOCAL_CONFIG_PATH.suffix}.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, LOCAL_CONFIG_PATH)
    try:
        LOCAL_CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
    return hybrid_settings_payload()


def _event(task_id: str, event: str, **detail: Any) -> None:
    path = task_dir(task_id) / "stage_outputs" / "hybrid_client_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_detail = {
        key: value
        for key, value in detail.items()
        if key.lower() not in {"token", "authorization", "api_key", "password", "secret"}
    }
    row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **safe_detail}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


class HybridHttpClient:
    def __init__(self, config: dict[str, Any]):
        parsed = urlsplit(str(config.get("base_url") or "").rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HybridClientError("混合云地址无效。")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.base_path = parsed.path.rstrip("/")
        self.token = str(config.get("token") or "").strip()
        self.client_id = str(config.get("client_id") or socket.gethostname()).strip()
        self.tenant_id = str(config.get("tenant_id") or "default").strip()
        self.timeout = max(10, int(config.get("request_timeout_seconds") or 120))

    def connection(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=ssl.create_default_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def json_request(self, method: str, path: str, *, body: bytes | None = None) -> dict[str, Any]:
        connection = self.connection()
        try:
            headers = self.headers()
            if body is not None:
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(body))
            connection.request(method, self.base_path + path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024)
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HybridClientError(f"云端返回了无法识别的响应（HTTP {response.status}）。") from exc
            if response.status >= 400 or not value.get("ok"):
                raise HybridClientError(str(value.get("error") or f"HTTP {response.status}"))
            return value
        finally:
            connection.close()

    def upload(self, task_id: str, archive: Path, *, idempotency_key: str) -> dict[str, Any]:
        connection = self.connection()
        try:
            headers = {
                **self.headers(),
                "Content-Type": "application/zip",
                "Content-Length": str(archive.stat().st_size),
                "X-Task-ID": task_id,
                "X-Client-ID": self.client_id,
                "X-Idempotency-Key": idempotency_key,
                "X-Content-SHA256": sha256_file(archive),
            }
            connection.putrequest("POST", self.base_path + "/api/v1/jobs")
            for key, value in headers.items():
                connection.putheader(key, value)
            connection.endheaders()
            with archive.open("rb") as stream:
                while chunk := stream.read(CHUNK_BYTES):
                    connection.send(chunk)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024)
            value = json.loads(raw.decode("utf-8"))
            if response.status >= 400 or not value.get("ok"):
                raise HybridClientError(str(value.get("error") or f"HTTP {response.status}"))
            return value
        finally:
            connection.close()

    def download(self, job_id: str, destination: Path, *, expected_sha256: str) -> dict[str, Any]:
        connection = self.connection()
        temporary = destination.with_name(f".{destination.name}.tmp")
        digest = hashlib.sha256()
        try:
            connection.request("GET", self.base_path + f"/api/v1/jobs/{job_id}/result", headers=self.headers())
            response = connection.getresponse()
            if response.status != 200:
                raw = response.read(1024 * 1024)
                raise HybridClientError(raw.decode("utf-8", errors="replace")[:500])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as stream:
                while chunk := response.read(CHUNK_BYTES):
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            actual = digest.hexdigest()
            header_sha = str(response.getheader("X-Content-SHA256") or "")
            expected = expected_sha256 or header_sha
            if expected and actual != expected:
                raise HybridClientError("云端结果下载校验失败。")
            os.replace(temporary, destination)
            return {"path": str(destination), "sha256": actual, "size_bytes": destination.stat().st_size}
        finally:
            temporary.unlink(missing_ok=True)
            connection.close()


def _submission_metadata(path: Path) -> dict[str, Any]:
    return _read_json(path)


def _submit_job(task_id: str, client: HybridHttpClient, transfer_dir: Path) -> dict[str, Any]:
    input_bundle = transfer_dir / "input.zip"
    submission_path = transfer_dir / "submission.json"
    submission = _submission_metadata(submission_path)
    reusable_upload = (
        input_bundle.is_file()
        and submission.get("input_sha256") == sha256_file(input_bundle)
        and submission.get("idempotency_key")
        and load_task(task_id).hybrid_phase == "uploading"
    )
    if reusable_upload:
        input_sha = str(submission["input_sha256"])
        idempotency_key = str(submission["idempotency_key"])
        size_bytes = input_bundle.stat().st_size
        _event(task_id, "upload_resumed", input_sha256=input_sha)
    else:
        _event(task_id, "local_preprocess_started")
        prepare_hybrid_input(task_id)
        bundle = create_input_bundle(
            task_id,
            input_bundle,
            tenant_id=client.tenant_id,
            client_id=client.client_id,
        )
        input_sha = str(bundle["sha256"])
        idempotency_key = f"{task_id}:{input_sha}"
        size_bytes = int(bundle["size_bytes"])
        submission_path.write_text(
            json.dumps(
                {
                    "schema_version": "answer_book.hybrid_submission.v1",
                    "task_id": task_id,
                    "input_sha256": input_sha,
                    "idempotency_key": idempotency_key,
                    "size_bytes": size_bytes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    update_task(task_id, status="running", current_stage="hybrid_upload", error="")
    update_task_hybrid(task_id, hybrid_phase="uploading", cloud_status="uploading", cloud_error="")
    _event(task_id, "upload_started", input_sha256=input_sha, size_bytes=size_bytes)
    for attempt in range(1, 4):
        try:
            response = client.upload(task_id, input_bundle, idempotency_key=idempotency_key)
            job = response["job"]
            job_id = str(job["job_id"])
            update_task(task_id, status="running", current_stage="cloud_queue", error="")
            update_task_hybrid(task_id, hybrid_phase="cloud_queue", cloud_job_id=job_id, cloud_status=str(job["status"]), cloud_error="")
            _event(task_id, "upload_completed", cloud_job_id=job_id, idempotent_replay=bool(response.get("idempotent_replay")))
            return job
        except (OSError, http.client.HTTPException, HybridClientError) as exc:
            _event(task_id, "upload_error", attempt=attempt, error=str(exc))
            if attempt >= 3:
                raise HybridClientError(f"上传云端失败，已重试 {attempt} 次：{exc}") from exc
            time.sleep(attempt)
    raise HybridClientError("上传云端失败。")


def _wait_for_job(task_id: str, client: HybridHttpClient, config: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job["job_id"])
    deadline = time.monotonic() + max(60, int(config.get("max_wait_seconds") or 21600))
    interval = max(1, int(config.get("poll_interval_seconds") or 3))
    max_network_errors = max(3, int(config.get("max_consecutive_network_errors") or 10))
    consecutive_errors = 0
    last_phase = ""
    while time.monotonic() < deadline:
        control = read_task_control(task_id)
        if control.get("action") == "cancel":
            try:
                client.json_request("POST", f"/api/v1/jobs/{job_id}/cancel", body=b"{}")
            except Exception as cancel_error:
                _event(task_id, "cloud_cancel_delivery_error", cloud_job_id=job_id, error=str(cancel_error))
            update_task_hybrid(task_id, hybrid_phase="cancelled", cloud_status="cancel_requested")
            update_task(task_id, status="cancelled", current_stage="cancelled", error="用户取消任务")
            _event(task_id, "hybrid_cancelled", cloud_job_id=job_id)
            raise TaskCancelled("用户取消任务")
        if control.get("action") == "pause":
            if load_task(task_id).status != "paused":
                update_task(task_id, status="paused", error="用户暂停任务")
            time.sleep(1)
            continue
        try:
            job = client.json_request("GET", f"/api/v1/jobs/{job_id}")["job"]
            consecutive_errors = 0
        except (OSError, http.client.HTTPException, HybridClientError) as exc:
            consecutive_errors += 1
            _event(task_id, "poll_network_error", count=consecutive_errors, error=str(exc))
            if consecutive_errors >= max_network_errors:
                raise HybridClientError("与云端连续失联，任务编号已保留，可恢复查询。") from exc
            time.sleep(interval)
            continue
        phase = str(job.get("phase") or job.get("status") or "cloud_pipeline")
        if phase != last_phase:
            _event(task_id, "cloud_phase", cloud_job_id=job_id, status=job.get("status"), phase=phase, progress=job.get("progress"))
            last_phase = phase
        progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
        update_task(task_id, status="running", current_stage=str(progress.get("current_stage") or phase), error="")
        update_task_hybrid(task_id, hybrid_phase=phase, cloud_status=str(job.get("status") or ""), cloud_error=str(job.get("error") or ""))
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(interval)
    raise HybridClientError("云端任务超过本机等待上限，任务编号已保留。")


def _record_hybrid_failure(task_id: str, exc: Exception) -> None:
    sdir = task_dir(task_id) / "stage_outputs"
    sdir.mkdir(parents=True, exist_ok=True)
    phase = load_task(task_id).hybrid_phase or "hybrid_client"
    (sdir / "hybrid_client_error.json").write_text(
        json.dumps(
            {
                "schema_version": "answer_book.hybrid_failure.v1",
                "phase": phase,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "traceback": traceback.format_exc(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    current = load_task(task_id)
    if current.status not in {"failed", "cancelled"}:
        update_task(task_id, status="failed", current_stage=phase, error=str(exc))
    _event(task_id, "hybrid_failed", phase=phase, error=str(exc))


def run_hybrid_task(task_id: str, *, render_with_word: bool) -> dict[str, Any]:
    config = load_hybrid_config()
    if not hybrid_enabled():
        raise HybridClientError("混合云尚未启用。")
    client = HybridHttpClient(config)
    transfer_dir = task_dir(task_id) / "hybrid_transfer"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    result_bundle = transfer_dir / "result.zip"
    import_receipt = task_dir(task_id) / "stage_outputs" / "hybrid_import_receipt.json"
    try:
        health = client.json_request("GET", "/api/v1/health").get("health", {})
        health_path = task_dir(task_id) / "stage_outputs" / "hybrid_cloud_preflight.json"
        health_path.parent.mkdir(parents=True, exist_ok=True)
        health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        if not health.get("ok"):
            raise HybridClientError("云端自检未通过，已禁止提交新任务。")
        record = load_task(task_id)
        if record.cloud_status == "completed" and import_receipt.is_file():
            _event(task_id, "local_delivery_resumed", cloud_job_id=record.cloud_job_id)
            return complete_hybrid_local_delivery(task_id, render_with_word=render_with_word, use_model=True)

        job: dict[str, Any] | None = None
        if record.cloud_job_id and record.cloud_status not in {"failed", "cancelled"}:
            try:
                job = client.json_request("GET", f"/api/v1/jobs/{record.cloud_job_id}")["job"]
                _event(task_id, "cloud_monitoring_resumed", cloud_job_id=record.cloud_job_id, status=job.get("status"))
            except HybridClientError as exc:
                if "job_not_found" not in str(exc):
                    raise
                _event(task_id, "cloud_job_missing", cloud_job_id=record.cloud_job_id)
        if job is None:
            job = _submit_job(task_id, client, transfer_dir)
        if job.get("status") not in {"completed", "failed", "cancelled"}:
            job = _wait_for_job(task_id, client, config, job)

        job_id = str(job["job_id"])
        if job.get("result_available"):
            receipt = _read_json(import_receipt)
            if receipt.get("result_sha256") != job.get("result_sha256"):
                update_task(task_id, status="running", current_stage="hybrid_download", error="")
                update_task_hybrid(task_id, hybrid_phase="downloading", cloud_status=str(job.get("status") or ""))
                download = client.download(job_id, result_bundle, expected_sha256=str(job.get("result_sha256") or ""))
                _event(task_id, "download_completed", cloud_job_id=job_id, **download)
                receipt = import_result_bundle(task_id, result_bundle)
                _event(task_id, "result_imported", cloud_job_id=job_id, result_sha256=receipt["result_sha256"])

        if job.get("status") != "completed":
            error = str(job.get("error") or f"云端任务状态：{job.get('status')}")
            failure = {
                "schema_version": "answer_book.hybrid_failure.v1",
                "phase": str(job.get("phase") or "cloud_pipeline"),
                "cloud_job_id": job_id,
                "cloud_status": job.get("status"),
                "result_available": bool(job.get("result_available")),
                "error": error,
            }
            (task_dir(task_id) / "stage_outputs" / "hybrid_cloud_failure.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
            update_task_hybrid(task_id, hybrid_phase="cloud_failed", cloud_status=str(job.get("status") or "failed"), cloud_error=error)
            update_task(task_id, status="failed", current_stage=str(job.get("phase") or "cloud_pipeline"), error=error)
            raise HybridClientError(error)
        if not import_receipt.is_file():
            raise HybridClientError("云端报告完成，但没有可验证的结果包，已禁止进入 Word 阶段。")
        update_task_hybrid(task_id, hybrid_phase="local_delivery", cloud_status="completed", cloud_error="")
        return complete_hybrid_local_delivery(task_id, render_with_word=render_with_word, use_model=True)
    except TaskCancelled:
        raise
    except Exception as exc:
        _record_hybrid_failure(task_id, exc)
        raise
