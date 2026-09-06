"""Immutable, explicitly scoped deliverables independent of set assembly.

Only domain adapters may supply validated bytes. A generated checkpoint is
never accepted here as a deliverable. Manifests describe omissions, not failed
bodies, and remain downloadable by revision after a newer run starts.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .artifact_store import atomic_write_json, fsync_directory_best_effort, verify_immutable_file
from .output_checkpoints import content_sha256

UNIT_DELIVERY_CONTRACT = "answer_book.unit_delivery.v1"


def dependency_units(items: list[dict[str, Any]], *, id_field: str) -> list[dict[str, Any]]:
    """Partition explicit dependencies; never infer relationships from prose.

    A compound question already represented by one item stays indivisible.
    Declared variants and dependency groups also stay together. Missing and
    duplicate identities fail closed instead of silently yielding a subset.
    """
    ids = [str(item.get(id_field) or "") for item in items]
    if not all(ids) or any(key != key.strip() for key in ids) or len(ids) != len(set(ids)):
        raise ValueError("交付对象身份缺失或重复，不能确认覆盖范围。")
    parents = dict(zip(ids, ids))
    missing: set[str] = set()
    groups: dict[tuple[str, str], str] = {}

    def root(key: str) -> str:
        while parents[key] != key:
            key = parents[key]
        return key

    for item, key in zip(items, ids):
        for field in ("dependency_group_id", "parent_plan_item_id"):
            value = str(item.get(field) or "")
            if value:
                group = (field, value)
                if group in groups:
                    parents[root(key)] = root(groups[group])
                groups[group] = key
        dependencies = item.get("depends_on") or []
        if not isinstance(dependencies, list):
            missing.add(key)
            continue
        for dependency in dependencies:
            target = str(dependency)
            if target not in parents:
                missing.add(key)
            else:
                parents[root(key)] = root(target)
    units: dict[str, list[dict[str, Any]]] = {}
    for item, key in zip(items, ids):
        units.setdefault(root(key), []).append(item)
    return [{
        "items": rows,
        "object_ids": [str(row[id_field]) for row in rows],
        "dependency_complete": not any(str(row[id_field]) in missing for row in rows),
    } for rows in units.values()]


def _digest(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("成果版本标识无效。")
    return value


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(dir=path.parent, prefix=".delivery-")
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory_best_effort(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def store_unit(root: Path, content: bytes, *, revision: str, object_ids: list[str], warnings: list[Any]) -> dict[str, Any]:
    digest = hashlib.sha256(content).hexdigest()
    target = root / "artifacts" / f"{digest}.docx"
    if not verify_immutable_file(target, sha256=digest):
        _write_bytes(target, content)
    return {
        "revision": revision,
        "object_ids": object_ids,
        "status": "deliverable",
        "sha256": digest,
        "size_bytes": len(content),
        "warnings": warnings,
        "scope": "unit_only",
        "formal_set_acceptance": False,
    }


def publish_manifest(root: Path, *, expected: list[dict[str, Any]], units: list[dict[str, Any]]) -> dict[str, Any]:
    ready = {key for unit in units if unit.get("status") == "deliverable" for key in unit["object_ids"]}
    manifest = {
        "schema_version": UNIT_DELIVERY_CONTRACT,
        "scope": "unit_only",
        "formal_set_acceptance": False,
        "expected": expected,
        "units": units,
        "available_count": len(ready),
        "missing": [item for item in expected if item["object_id"] not in ready],
        "notice": "分题成果，不代表完整试卷通过验收；保留原题编号，未完成项见缺失清单。",
    }
    revision = content_sha256(manifest)
    atomic_write_json(root / "manifests" / f"{revision}.json", manifest)
    atomic_write_json(root / "latest.json", {"revision": revision})
    return {**manifest, "revision": revision}


def read_manifest(root: Path, revision: str = "") -> dict[str, Any]:
    if not revision:
        try:
            revision = str(json.loads((root / "latest.json").read_text(encoding="utf-8"))["revision"])
        except FileNotFoundError:
            return {}
    revision = _digest(revision)
    manifest = json.loads((root / "manifests" / f"{revision}.json").read_text(encoding="utf-8"))
    if content_sha256(manifest) != revision or manifest.get("schema_version") != UNIT_DELIVERY_CONTRACT:
        raise ValueError("成果清单校验失败，请重新检查任务。")
    return {**manifest, "revision": revision}


def unit_artifact(root: Path, manifest: dict[str, Any], digest: str) -> Path:
    digest = _digest(digest)
    if not any(unit.get("sha256") == digest and unit.get("status") == "deliverable" for unit in manifest.get("units", [])):
        raise ValueError("该成果未通过本版本的局部验收。")
    path = root / "artifacts" / f"{digest}.docx"
    if not verify_immutable_file(path, sha256=digest):
        raise ValueError("成果文件丢失或校验失败，未提供下载。")
    return path


def build_unit_package(root: Path, revision: str) -> Path:
    manifest = read_manifest(root, revision)
    if not manifest.get("available_count"):
        raise ValueError("尚无通过局部验收的成果。")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        # Fixed ZIP metadata makes retry bytes identical for a pinned revision.
        def write(name: str, content: str | bytes) -> None:
            archive.writestr(zipfile.ZipInfo(name), content)
        write("范围与缺失清单.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        write("请先阅读.txt", manifest["notice"])
        for index, unit in enumerate(manifest["units"], start=1):
            if unit.get("status") != "deliverable":
                continue
            path = unit_artifact(root, manifest, unit["sha256"])
            content = path.read_bytes()
            # Verify the bytes actually packaged, not merely a pre-open stat.
            if hashlib.sha256(content).hexdigest() != unit["sha256"]:
                raise ValueError("成果在读取期间变化，未提供下载。")
            write(f"分题成果_{index:03d}.docx", content)
    path = root / "packages" / f"{_digest(revision)}.zip"
    content = stream.getvalue()
    if not verify_immutable_file(path, sha256=hashlib.sha256(content).hexdigest()):
        _write_bytes(path, content)
    return path
