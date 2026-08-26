from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .model_diagnostics import MODEL_DIAGNOSTICS_DIR
from .task_store import TaskRecord, load_task, task_dir

CONTRACT_VERSION = "answer_book.hybrid.v1"
TASK_ROOT_PLACEHOLDER = "__HYBRID_TASK_ROOT__"
MAX_ARCHIVE_BYTES = 768 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_MEMBER_COUNT = 20_000
TEXT_REBIND_SUFFIXES = {".json", ".jsonl", ".csv", ".txt", ".md"}
INPUT_STAGE_FILES = {
    "structured_exam.json",
    "exam_structure_audit.json",
    "exam_structure_review.json",
    "textbook_blocks.csv",
    "textbook_page_map.csv",
    "textbook_index_status.json",
    "textbook_package_audit.json",
}
INPUT_STAGE_DIRS = {"source_images", "question_snapshots", "hybrid_textbook_assets"}
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{1,160}$")


class HybridContractError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_task_id(value: Any) -> str:
    task_id = str(value or "").strip()
    if not _TASK_ID_RE.fullmatch(task_id) or task_id in {".", ".."}:
        raise HybridContractError("Invalid hybrid task id")
    return task_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitized_task_payload(record: TaskRecord) -> dict[str, Any]:
    fields = (
        "provider",
        "model",
        "model_thinking",
        "reasoning_provider",
        "reasoning_model",
        "answer_provider",
        "answer_model",
        "correctness_provider",
        "correctness_model",
        "vision_provider",
        "vision_model",
        "image_provider",
        "image_model",
    )
    return {name: getattr(record, name) for name in fields}


def _copy_selected_stage(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in INPUT_STAGE_FILES:
        path = source / name
        if path.is_file():
            shutil.copy2(path, target / name)
    for name in INPUT_STAGE_DIRS:
        path = source / name
        if path.is_dir():
            shutil.copytree(path, target / name, dirs_exist_ok=True)


def _copy_textbook_assets(stage: Path) -> int:
    blocks = stage / "textbook_blocks.csv"
    if not blocks.is_file():
        return 0
    with blocks.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "asset_path" not in fieldnames:
        return 0
    copied: dict[str, str] = {}
    for row in rows:
        source_text = str(row.get("asset_path") or "").strip()
        if not source_text:
            continue
        source = Path(source_text).expanduser()
        if not source.is_file():
            continue
        resolved = str(source.resolve())
        relative = copied.get(resolved)
        if not relative:
            digest = sha256_file(source)
            suffix = source.suffix.lower()[:12]
            relative = f"hybrid_textbook_assets/{digest}{suffix}"
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(source, destination)
            copied[resolved] = relative
        row["asset_path"] = f"{TASK_ROOT_PLACEHOLDER}/stage_outputs/{relative}"
    with blocks.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(copied)


def _sanitize_source_columns(path: Path) -> None:
    if not path.is_file():
        return
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    source_columns = [name for name in ("source_file", "source_path") if name in fieldnames]
    if not source_columns:
        return
    for row in rows:
        for name in source_columns:
            value = str(row.get(name) or "").strip()
            if value:
                row[name] = f"hybrid_source/{Path(value).name}"
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _rebind_packaged_asset_references(stage: Path) -> int:
    structured_path = stage / "structured_exam.json"
    if not structured_path.is_file():
        return 0
    assets: dict[str, str] = {}
    duplicates: set[str] = set()
    for folder_name in ("source_images", "question_snapshots"):
        folder = stage / folder_name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if path.name in assets:
                duplicates.add(path.name)
            else:
                assets[path.name] = path.relative_to(stage).as_posix()
    for name in duplicates:
        assets.pop(name, None)
    value = json.loads(structured_path.read_text(encoding="utf-8"))
    changed = 0

    def visit(item: Any) -> Any:
        nonlocal changed
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, str) and ("/" in item or "\\" in item):
            relative = assets.get(Path(item).name)
            if relative:
                changed += 1
                return f"{TASK_ROOT_PLACEHOLDER}/stage_outputs/{relative}"
        return item

    rebound = visit(value)
    if changed:
        structured_path.write_text(json.dumps(rebound, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def _replace_text_in_tree(root: Path, replacements: Iterable[tuple[str, str]]) -> None:
    pairs = [(old, new) for old, new in replacements if old and old != new]
    if not pairs:
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_REBIND_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        updated = text
        for old, new in pairs:
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def _iter_archive_files(root: Path) -> list[Path]:
    files = [path for path in root.rglob("*") if path.is_file()]
    if len(files) > MAX_MEMBER_COUNT:
        raise HybridContractError("Hybrid bundle contains too many files")
    total = 0
    for path in files:
        size = path.stat().st_size
        if size > MAX_MEMBER_BYTES:
            raise HybridContractError(f"Hybrid bundle member is too large: {path.name}")
        total += size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise HybridContractError("Hybrid bundle is too large after extraction")
    return files


def _stable_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_json(item)
            for key, item in sorted(value.items())
            if key not in {"created_at", "updated_at", "exam_structure_reviewed_at"}
        }
    if isinstance(value, list):
        return [_stable_json(item) for item in value]
    return value


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(_iter_archive_files(root), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        if path.suffix.lower() == ".json":
            try:
                normalized = json.dumps(
                    _stable_json(json.loads(path.read_text(encoding="utf-8-sig"))),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                normalized = path.read_bytes()
            digest.update(normalized)
        else:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _write_zip(root: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    files = _iter_archive_files(root)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in files:
                archive.write(path, path.relative_to(root).as_posix())
        if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
            raise HybridContractError("Compressed hybrid bundle exceeds the upload limit")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "member_count": len(files),
    }


def create_input_bundle(task_id: str, destination: Path, *, tenant_id: str, client_id: str) -> dict[str, Any]:
    task_id = validate_task_id(task_id)
    record = load_task(task_id)
    source_task_root = task_dir(task_id).resolve()
    source_stage = source_task_root / "stage_outputs"
    required = (source_stage / "structured_exam.json", source_stage / "textbook_blocks.csv", source_stage / "textbook_page_map.csv", source_stage / "textbook_index_status.json")
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise HybridContractError("Hybrid preprocessing is incomplete: " + ", ".join(missing))
    with tempfile.TemporaryDirectory(prefix="answer-book-hybrid-input-") as raw_tmp:
        root = Path(raw_tmp)
        stage = root / "stage_outputs"
        _copy_selected_stage(source_stage, stage)
        asset_count = _copy_textbook_assets(stage)
        _sanitize_source_columns(stage / "textbook_blocks.csv")
        _sanitize_source_columns(stage / "textbook_page_map.csv")
        rebound_asset_reference_count = _rebind_packaged_asset_references(stage)
        _replace_text_in_tree(stage, [(str(source_task_root), TASK_ROOT_PLACEHOLDER)])
        _replace_text_in_tree(stage, [(str(Path(record.exam_path).expanduser()), f"hybrid_input/{Path(record.exam_path).name}")])
        manifest = {
            "schema_version": CONTRACT_VERSION,
            "bundle_kind": "input",
            "task_id": task_id,
            "tenant_id": str(tenant_id).strip(),
            "client_id": str(client_id).strip(),
            "created_at": utc_now(),
            "task": sanitized_task_payload(record),
            "source_task_root": TASK_ROOT_PLACEHOLDER,
            "input_fingerprint": _tree_fingerprint(root),
            "textbook_asset_count": asset_count,
            "rebound_asset_reference_count": rebound_asset_reference_count,
            "contains_credentials": False,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**_write_zip(root, destination), "manifest": manifest}


def _safe_member_path(root: Path, name: str) -> Path:
    if not name or name.startswith(("/", "\\")):
        raise HybridContractError("Hybrid archive contains an absolute path")
    normalized = name.replace("\\", "/")
    parts = Path(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HybridContractError("Hybrid archive contains an unsafe path")
    target = (root / Path(*parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise HybridContractError("Hybrid archive escapes its extraction root") from exc
    return target


def safe_extract_bundle(archive_path: Path, destination: Path) -> dict[str, Any]:
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise HybridContractError("Hybrid archive exceeds the compressed size limit")
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBER_COUNT:
            raise HybridContractError("Hybrid archive contains too many members")
        for member in members:
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise HybridContractError("Hybrid archive contains a symbolic link")
            if member.file_size > MAX_MEMBER_BYTES:
                raise HybridContractError("Hybrid archive member exceeds the size limit")
            total += member.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise HybridContractError("Hybrid archive exceeds the extraction size limit")
            target = _safe_member_path(destination, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise HybridContractError("Hybrid archive is missing manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != CONTRACT_VERSION:
        raise HybridContractError("Unsupported hybrid contract version")
    validate_task_id(manifest.get("task_id"))
    return manifest


def materialize_input_bundle(archive_path: Path, cloud_task_root: Path) -> dict[str, Any]:
    manifest = safe_extract_bundle(archive_path, cloud_task_root)
    if manifest.get("bundle_kind") != "input":
        raise HybridContractError("Expected a hybrid input bundle")
    _replace_text_in_tree(cloud_task_root, [(TASK_ROOT_PLACEHOLDER, str(cloud_task_root.resolve()))])
    return manifest


def rebind_task_root(root: Path, target_task_root: Path, *old_roots: Path) -> None:
    replacements = [(TASK_ROOT_PLACEHOLDER, str(target_task_root.resolve()))]
    replacements.extend((str(path.resolve()), str(target_task_root.resolve())) for path in old_roots)
    _replace_text_in_tree(root, replacements)


def create_result_bundle(
    task_id: str,
    cloud_task_root: Path,
    destination: Path,
    *,
    cloud_job_id: str,
    tenant_id: str,
    require_handoff: bool = True,
) -> dict[str, Any]:
    task_id = validate_task_id(task_id)
    stage = cloud_task_root / "stage_outputs"
    handoff_path = stage / "hybrid_handoff.json"
    if require_handoff and not handoff_path.is_file():
        raise HybridContractError("Cloud task did not produce hybrid_handoff.json")
    with tempfile.TemporaryDirectory(prefix="answer-book-hybrid-result-") as raw_tmp:
        root = Path(raw_tmp)
        shutil.copytree(stage, root / "stage_outputs")
        diagnostics = MODEL_DIAGNOSTICS_DIR / task_id
        if diagnostics.is_dir():
            shutil.copytree(diagnostics, root / "model_diagnostics")
        _replace_text_in_tree(root, [(str(cloud_task_root.resolve()), TASK_ROOT_PLACEHOLDER)])
        manifest = {
            "schema_version": CONTRACT_VERSION,
            "bundle_kind": "result",
            "task_id": task_id,
            "tenant_id": tenant_id,
            "cloud_job_id": cloud_job_id,
            "created_at": utc_now(),
            "remote_task_root": TASK_ROOT_PLACEHOLDER,
            "outcome": "completed" if handoff_path.is_file() else "failed",
            "contains_credentials": False,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**_write_zip(root, destination), "manifest": manifest}


def import_result_bundle(task_id: str, archive_path: Path) -> dict[str, Any]:
    task_id = validate_task_id(task_id)
    local_task_root = task_dir(task_id).resolve()
    with tempfile.TemporaryDirectory(prefix="answer-book-hybrid-import-") as raw_tmp:
        extracted = Path(raw_tmp)
        manifest = safe_extract_bundle(archive_path, extracted)
        if manifest.get("bundle_kind") != "result" or manifest.get("task_id") != task_id:
            raise HybridContractError("Hybrid result does not match the local task")
        stage = extracted / "stage_outputs"
        if manifest.get("outcome") == "completed" and not (stage / "hybrid_handoff.json").is_file():
            raise HybridContractError("Hybrid result is incomplete: missing handoff evidence")
        rebound_asset_reference_count = _rebind_packaged_asset_references(stage)
        _replace_text_in_tree(extracted, [(TASK_ROOT_PLACEHOLDER, str(local_task_root))])
        shutil.copytree(stage, local_task_root / "stage_outputs", dirs_exist_ok=True)
        diagnostics = extracted / "model_diagnostics"
        if diagnostics.is_dir():
            shutil.copytree(diagnostics, MODEL_DIAGNOSTICS_DIR / task_id, dirs_exist_ok=True)
        receipt = {
            "schema_version": "answer_book.hybrid_import.v1",
            "task_id": task_id,
            "cloud_job_id": manifest.get("cloud_job_id", ""),
            "result_sha256": sha256_file(archive_path),
            "imported_at": utc_now(),
            "status": "imported",
            "rebound_asset_reference_count": rebound_asset_reference_count,
        }
        receipt_path = local_task_root / "stage_outputs" / "hybrid_import_receipt.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        return receipt
