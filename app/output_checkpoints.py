"""Task-local, content-addressed candidates; never a publication authority.

Generation and repair can stop after any object. Retain exact source and
processed versions without replacing the accepted collection. Consumers must
revalidate dependencies and their own gates before reuse or delivery.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .artifact_store import atomic_write_json, long_path, path_exists, read_text, sha256_file


def file_dependencies(value: Any) -> dict[str, str | None]:
    """Fingerprint explicit local asset references, including missing files."""
    result: dict[str, str | None] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key == "image_refs" and isinstance(item, list):
                    for reference in item:
                        if isinstance(reference, str):
                            visit({"path": reference})
                        else:
                            visit(reference)
                    continue
                if key in {"path", "asset_path", "image_path"} and isinstance(item, str) and item:
                    path = Path(item)
                    if not item.startswith(("http:", "https:", "data:")):
                        result[item] = sha256_file(path) if path.is_file() else None
                else:
                    visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return result


def content_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def save_output_checkpoint(
    stage_dir: Path,
    *,
    stage: str,
    object_id: str,
    source: Any,
    candidate: dict[str, Any],
    diagnostics: Any,
    dependencies: Any = None,
) -> Path:
    """Commit a complete immutable snapshot before advancing its latest pointer.

    IDs are hashed rather than interpreted as paths. A crash can leave an
    unreferenced snapshot, but cannot point to a partially written candidate.
    Different stages and tasks never share a mutable latest pointer.
    """
    if not stage or not object_id:
        raise ValueError("Checkpoint requires stage and object identity")
    payload = {
        "schema_version": "answer_book.output_checkpoint.v1",
        "stage": stage,
        "object_id": object_id,
        "delivery_status": "not_evaluated",
        "source": source,
        "source_sha256": content_sha256(source),
        "candidate": candidate,
        "candidate_sha256": content_sha256(candidate),
        "asset_digests": file_dependencies(candidate),
        "diagnostics": diagnostics,
        "dependencies_sha256": content_sha256(dependencies) if dependencies is not None else None,
    }
    digest = content_sha256(payload)
    directory = stage_dir / "output_checkpoints" / content_sha256([stage, object_id])
    snapshot = directory / f"{digest}.json"
    if path_exists(snapshot):
        if json.loads(read_text(snapshot)) != payload:
            raise ValueError("Output checkpoint integrity mismatch")
    else:
        atomic_write_json(snapshot, payload)
    atomic_write_json(directory / "latest.json", {"snapshot": snapshot.name, "sha256": digest})
    # Preserve the public Path return type while making direct caller access
    # work beyond MAX_PATH on Windows as well as through our internal helpers.
    return Path(long_path(snapshot))


def load_output_checkpoint(stage_dir: Path, *, stage: str, object_id: str, dependencies: Any) -> dict[str, Any] | None:
    """Reuse only a complete, intact checkpoint bound to current dependencies.

    Old snapshots without dependency identity are archives, not reusable work.
    Corruption or a changed dependency is a cache miss, never implicit approval.
    """
    directory = stage_dir / "output_checkpoints" / content_sha256([stage, object_id])
    try:
        pointer = json.loads(read_text(directory / "latest.json"))
        digest = str(pointer["sha256"])
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return None
        if pointer["snapshot"] != f"{digest}.json":
            return None
        payload = json.loads(read_text(directory / pointer["snapshot"]))
        if (
            content_sha256(payload) != digest
            or payload.get("schema_version") != "answer_book.output_checkpoint.v1"
            or payload.get("stage") != stage
            or payload.get("object_id") != object_id
            or payload.get("dependencies_sha256") != content_sha256(dependencies)
            or payload.get("candidate_sha256") != content_sha256(payload.get("candidate"))
            or payload.get("source_sha256") != content_sha256(payload.get("source"))
            or payload.get("asset_digests") != file_dependencies(payload.get("candidate"))
        ):
            return None
        return payload
    except (OSError, ValueError, KeyError, TypeError):
        return None
