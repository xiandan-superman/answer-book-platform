from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from app import hybrid_client, hybrid_contract, task_store
from app.hybrid_contract import HybridContractError
from app.task_store import TaskRecord
from scripts.hybrid_cloud_server import JobStore, decode_metadata_header


def _record(task_id: str) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        exam_path="/private/exam.docx",
        textbooks_dir="/private/textbooks",
        provider="provider",
        model="model",
        status="created",
        created_at="2026-08-22 12:00:00",
        updated_at="2026-08-22 12:00:00",
        selected_textbooks=["/private/textbook.pdf"],
    )


def test_hybrid_metadata_headers_round_trip_chinese_as_ascii(tmp_path, monkeypatch) -> None:
    class Response:
        status = 202

        @staticmethod
        def read(_limit: int) -> bytes:
            return b'{"ok": true, "job": {"job_id": "job-1"}}'

    class Connection:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        @staticmethod
        def putrequest(_method: str, _path: str) -> None:
            return None

        def putheader(self, key: str, value: str) -> None:
            value.encode("latin-1")
            self.headers[key] = value

        @staticmethod
        def endheaders() -> None:
            return None

        @staticmethod
        def send(_chunk: bytes) -> None:
            return None

        @staticmethod
        def getresponse() -> Response:
            return Response()

        @staticmethod
        def close() -> None:
            return None

    archive = tmp_path / "input.zip"
    archive.write_bytes(b"bundle")
    connection = Connection()
    client = hybrid_client.HybridHttpClient(
        {
            "base_url": "http://127.0.0.1:8781",
            "token": "token",
            "client_id": "测试客户端",
        }
    )
    monkeypatch.setattr(client, "connection", lambda: connection)

    key = hybrid_client._idempotency_key("2025真题_单题", "a" * 64)
    result = client.upload("2025真题_单题", archive, idempotency_key=key)

    assert result["job"]["job_id"] == "job-1"
    assert all(value.isascii() for value in connection.headers.values())
    encoding = connection.headers["X-Metadata-Encoding"]
    assert decode_metadata_header(connection.headers["X-Task-ID"], encoding) == "2025真题_单题"
    assert decode_metadata_header(connection.headers["X-Client-ID"], encoding) == "测试客户端"
    assert decode_metadata_header(connection.headers["X-Idempotency-Key"], encoding) == key


def test_hybrid_idempotency_key_is_stable_ascii_and_migrates_legacy_unicode() -> None:
    input_sha = "b" * 64
    expected = hybrid_client._idempotency_key("中文任务", input_sha)

    assert expected == hybrid_client._idempotency_key("中文任务", input_sha)
    assert len(expected) == 64
    assert expected.isascii()
    assert hybrid_client._stored_idempotency_key(
        f"中文任务:{input_sha}", task_id="中文任务", input_sha256=input_sha
    ) == expected
    assert hybrid_client._stored_idempotency_key(
        "safe-existing-key", task_id="中文任务", input_sha256=input_sha
    ) == "safe-existing-key"


def test_server_only_decodes_metadata_when_encoding_version_is_declared() -> None:
    encoded = hybrid_client._encode_metadata_header("中文任务 / q1")

    assert encoded.isascii()
    assert decode_metadata_header(encoded, hybrid_client.METADATA_HEADER_ENCODING) == "中文任务 / q1"
    assert decode_metadata_header("plain%20legacy", "") == "plain%20legacy"


def test_hybrid_config_merges_build_time_bundle_and_local_override(tmp_path, monkeypatch) -> None:
    example = tmp_path / "hybrid_cloud.example.json"
    bundled = tmp_path / "hybrid_cloud.json"
    local = tmp_path / "local.json"
    example.write_text(json.dumps({"enabled": False, "poll_interval_seconds": 3}), encoding="utf-8")
    bundled.write_text(json.dumps({"enabled": True, "base_url": "http://cloud.private:8781", "token": "bundled"}), encoding="utf-8")
    local.write_text(json.dumps({"poll_interval_seconds": 8}), encoding="utf-8")
    monkeypatch.setattr(hybrid_client, "DEFAULT_CONFIG_PATH", example)
    monkeypatch.setattr(hybrid_client, "BUNDLED_CONFIG_PATH", bundled)
    monkeypatch.setattr(hybrid_client, "LOCAL_CONFIG_PATH", local)
    for name in ("ANSWER_BOOK_HYBRID_URL", "ANSWER_BOOK_HYBRID_TOKEN", "ANSWER_BOOK_HYBRID_TENANT", "ANSWER_BOOK_HYBRID_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    value = hybrid_client.load_hybrid_config()

    assert value["enabled"] is True
    assert value["base_url"] == "http://cloud.private:8781"
    assert value["token"] == "bundled"
    assert value["poll_interval_seconds"] == 8


def test_bundled_server_credentials_do_not_enable_hybrid_by_default(tmp_path, monkeypatch) -> None:
    example = tmp_path / "hybrid_cloud.example.json"
    bundled = tmp_path / "hybrid_cloud.json"
    local = tmp_path / "local.json"
    example.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    bundled.write_text(
        json.dumps({"enabled": False, "base_url": "http://cloud.private:8781", "token": "bundled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(hybrid_client, "DEFAULT_CONFIG_PATH", example)
    monkeypatch.setattr(hybrid_client, "BUNDLED_CONFIG_PATH", bundled)
    monkeypatch.setattr(hybrid_client, "LOCAL_CONFIG_PATH", local)
    for name in ("ANSWER_BOOK_HYBRID_URL", "ANSWER_BOOK_HYBRID_TOKEN", "ANSWER_BOOK_HYBRID_TENANT", "ANSWER_BOOK_HYBRID_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    settings = hybrid_client.hybrid_settings_payload()

    assert settings["available"] is True
    assert settings["enabled"] is False
    assert settings["execution_mode"] == "local"
    assert settings["server_host"] == "cloud.private"
    assert "token" not in settings


def test_local_owner_can_toggle_hybrid_execution_without_rewriting_credentials(tmp_path, monkeypatch) -> None:
    example = tmp_path / "hybrid_cloud.example.json"
    bundled = tmp_path / "hybrid_cloud.json"
    local = tmp_path / "local.json"
    example.write_text(json.dumps({"enabled": False, "poll_interval_seconds": 3}), encoding="utf-8")
    bundled.write_text(
        json.dumps({"enabled": False, "base_url": "http://cloud.private:8781", "token": "bundled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(hybrid_client, "DEFAULT_CONFIG_PATH", example)
    monkeypatch.setattr(hybrid_client, "BUNDLED_CONFIG_PATH", bundled)
    monkeypatch.setattr(hybrid_client, "LOCAL_CONFIG_PATH", local)
    monkeypatch.delenv("ANSWER_BOOK_HYBRID_ENABLED", raising=False)

    enabled = hybrid_client.save_hybrid_enabled(True)
    disabled = hybrid_client.save_hybrid_enabled(False)

    assert enabled["enabled"] is True
    assert disabled["enabled"] is False
    assert json.loads(local.read_text(encoding="utf-8")) == {"enabled": False}
    assert "token" not in local.read_text(encoding="utf-8")


def test_hybrid_input_bundle_rebinds_assets_and_never_serializes_credentials(tmp_path, monkeypatch) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks)
    record = _record("task-hybrid")
    root = task_store.task_dir(record.task_id)
    stage = root / "stage_outputs"
    stage.mkdir(parents=True)
    task_store.save_task(record)
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    (stage / "structured_exam.json").write_text(
        json.dumps({"items": [{"question_id": "q1", "image_refs": [str(stage / "source_images" / "q1.png")]}]}),
        encoding="utf-8",
    )
    (stage / "source_images").mkdir()
    (stage / "source_images" / "q1.png").write_bytes(b"question")
    with (stage / "textbook_blocks.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["block_id", "asset_path"])
        writer.writeheader()
        writer.writerow({"block_id": "b1", "asset_path": str(image)})
    (stage / "textbook_page_map.csv").write_text("page\n1\n", encoding="utf-8")
    (stage / "textbook_index_status.json").write_text(json.dumps({"page_map_ok": True}), encoding="utf-8")

    bundle = tmp_path / "input.zip"
    result = hybrid_contract.create_input_bundle(record.task_id, bundle, tenant_id="tenant-a", client_id="client-a")

    assert result["manifest"]["contains_credentials"] is False
    assert result["manifest"]["rebound_asset_reference_count"] == 1
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        blocks = archive.read("stage_outputs/textbook_blocks.csv").decode("utf-8-sig")
        all_text = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if name.endswith((".json", ".csv", ".txt"))
        )
    assert any(name.startswith("stage_outputs/hybrid_textbook_assets/") for name in names)
    assert hybrid_contract.TASK_ROOT_PLACEHOLDER in blocks
    assert "api_key" not in manifest
    assert "/private/exam.docx" not in all_text


def test_safe_extract_rejects_path_traversal(tmp_path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.txt", "bad")
        output.writestr("manifest.json", json.dumps({"schema_version": hybrid_contract.CONTRACT_VERSION, "task_id": "task"}))
    with pytest.raises(HybridContractError, match="unsafe path|escapes"):
        hybrid_contract.safe_extract_bundle(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_job_store_recovers_once_and_enforces_tenant_lookup(tmp_path) -> None:
    store = JobStore(tmp_path, quota_bytes=2 * 768 * 1024 * 1024)
    upload = tmp_path / "one.zip"
    upload.write_bytes(b"one")
    row = store.create(
        tenant_id="tenant-a",
        client_id="client",
        task_id="task",
        idempotency_key="same",
        input_sha256="1" * 64,
        input_path=upload,
    )
    assert store.get(row["job_id"], "tenant-b") is None
    claimed = store.claim_next()
    assert claimed and claimed["status"] == "running"

    recovered = JobStore(tmp_path, quota_bytes=2 * 768 * 1024 * 1024)
    current = recovered.get(row["job_id"], "tenant-a")
    assert current and current["status"] == "queued"
    assert current["phase"] == "recovered_after_restart"


def test_cloud_health_checks_storage_sqlite_and_provider_dns(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "config" / "providers.example.json").write_text(
        json.dumps({"active_provider": "model", "providers": {"model": {"base_url": "https://models.example.test/v1"}}}),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "data", quota_bytes=2 * 768 * 1024 * 1024, project_root=project)
    config = tmp_path / "data" / "tenants" / "tenant-a" / "runtime" / "config"
    config.mkdir(parents=True)
    (config / "providers.local.json").write_text(
        json.dumps({"providers": {"model": {"default_model": "model-v2"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.hybrid_cloud_server.socket.getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))])

    health = store.health_summary("tenant-a")

    assert health["ok"] is True
    assert health["sqlite_integrity"] == "ok"
    assert health["provider_config_ok"] is True
    assert health["provider_dns_ok"] is True
    assert health["provider_dns"]["models.example.test"]["ok"] is True


def test_cloud_health_reports_provider_configuration_failure(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "config" / "providers.example.json").write_text(
        json.dumps({"active_provider": "missing", "providers": {}}),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "data", quota_bytes=2 * 768 * 1024 * 1024, project_root=project)
    config = tmp_path / "data" / "tenants" / "tenant-a" / "runtime" / "config"
    config.mkdir(parents=True)
    (config / "providers.local.json").write_text("{}", encoding="utf-8")

    health = store.health_summary("tenant-a")

    assert health["ok"] is False
    assert health["provider_config_ok"] is False
    assert "base_url" in health["provider_config_error"]
