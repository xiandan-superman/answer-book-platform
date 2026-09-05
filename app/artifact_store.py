from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .model_diagnostics import MODEL_DIAGNOSTICS_DIR
from .paths import DATA_ROOT

ARTIFACT_REPORT_SCHEMA = "answer_book.artifact_integrity.v1"


def fsync_directory_best_effort(directory: Path) -> None:
    """Flush a directory entry where supported without rejecting Windows writes."""

    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            # Windows may return EBADF for directory handles.  The file itself
            # was already flushed before the atomic replace.
            pass
    finally:
        os.close(directory_fd)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_immutable_file(path: Path, *, sha256: str, size_bytes: int | None = None) -> bool:
    candidate = Path(path)
    try:
        if not candidate.is_file():
            return False
        if size_bytes is not None and candidate.stat().st_size != int(size_bytes):
            return False
        return sha256_file(candidate) == str(sha256 or "")
    except OSError:
        return False


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{target.name}-", dir=str(target.parent))
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        fsync_directory_best_effort(target.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not str(value.get("schema_version") or "").startswith("answer_book.image_artifacts."):
        return None
    return value


def build_artifact_integrity_report(
    *,
    data_root: Path = DATA_ROOT,
    diagnostics_root: Path = MODEL_DIAGNOSTICS_DIR,
    maximum_manifests: int = 5000,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    adopted = 0
    manifests = 0
    for manifest_path in Path(data_root).rglob("manifest.json"):
        if manifests >= max(1, int(maximum_manifests)):
            counts["manifest_scan_truncated"] += 1
            break
        payload = _read_manifest(manifest_path)
        if payload is None:
            continue
        manifests += 1
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            counts["manifest_artifacts_not_list"] += 1
            continue
        for item in artifacts:
            if not isinstance(item, dict):
                counts["manifest_item_invalid"] += 1
                continue
            counts["image_artifact_count"] += 1
            if item.get("adopted"):
                adopted += 1
            raw_path = str(item.get("path") or "")
            digest = str(item.get("sha256") or "")
            size = item.get("size_bytes")
            if not raw_path or len(digest) != 64:
                counts["image_artifact_metadata_incomplete"] += 1
            else:
                artifact_path = Path(raw_path)
                try:
                    artifact_path.resolve().relative_to(manifest_path.parent.resolve())
                except (OSError, ValueError):
                    artifact_path = manifest_path.parent / artifact_path.name
                if not verify_immutable_file(
                    artifact_path,
                    sha256=digest,
                    size_bytes=int(size) if isinstance(size, int) else None,
                ):
                    counts["image_artifact_digest_mismatch_or_missing"] += 1

    diagnostics = 0
    for attachment in Path(diagnostics_root).glob("*/attachments/*"):
        if not attachment.is_file():
            continue
        diagnostics += 1
        expected = attachment.stem
        if len(expected) != 64 or not verify_immutable_file(attachment, sha256=expected):
            counts["diagnostic_attachment_digest_mismatch"] += 1

    violations = sum(value for key, value in counts.items() if key not in {"image_artifact_count", "manifest_scan_truncated"})
    return {
        "schema_version": ARTIFACT_REPORT_SCHEMA,
        "mode": "read_only",
        "authority": "integrity_observation",
        "manifest_count": manifests,
        "image_artifact_count": counts["image_artifact_count"],
        "adopted_image_artifact_count": adopted,
        "diagnostic_attachment_count": diagnostics,
        "integrity_violation_count": violations,
        "finding_counts": {key: value for key, value in sorted(counts.items()) if key != "image_artifact_count" and value},
        "read_validation": "sha256",
        "content_or_paths_included": False,
        "added_model_calls": 0,
        "added_tokens": 0,
        "added_network_requests": 0,
    }
