"""User-facing storage management for cached textbook artifacts.

The platform keeps two growing caches beside the user's library files:

- ``cache/textbook_packages/<id>`` — extracted MinerU ZIP packages (hundreds of
  MB each). They are keyed by content hash, so deleting one never loses data:
  the next task that references the original ZIP re-extracts it on demand.
- ``cache/textbook_indexes/<key>`` — built textbook index CSVs. A cache entry
  is only safe to delete when no current library file combination still maps
  to it; otherwise the user would be forced to rebuild indexes.

Both caches live in the durable user-data directory and previously had no
cleanup path at all.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .library_files import scan_library_files
from .paths import CACHE_DIR, ensure_project_dirs
from .textbook_index_cache import TEXTBOOK_INDEX_CACHE_DIR, validated_textbook_index_cache
from .textbook_package import PACKAGE_CACHE_DIR


def _dir_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _dir_mtime(path: Path) -> float:
    try:
        return max(item.stat().st_mtime for item in path.iterdir())
    except (OSError, ValueError):
        return 0.0


def _index_cache_in_use() -> set[str]:
    """Cache keys whose manifest still matches a current library file."""
    textbooks = [Path(str(item.get("path") or "")) for item in scan_library_files().get("textbooks") or []]
    files = [path for path in textbooks if path.is_file()]
    in_use: set[str] = set()
    for cache_root in _safe_iter_dirs(TEXTBOOK_INDEX_CACHE_DIR):
        validated = validated_textbook_index_cache(cache_root)
        if validated is None:
            continue
        _, manifest = validated
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows:
            continue
        # The manifest lists every file that produced this index; the entry is
        # "in use" when any listed file still exists unchanged in the library.
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            try:
                size = int(row.get("size") or 0)
            except (TypeError, ValueError):
                continue
            match = any(
                path.name == name and path.stat().st_size == size
                for path in files
            )
            if match:
                in_use.add(cache_root.name)
                break
    return in_use


def _safe_iter_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)


def _package_title(package_root: Path) -> str:
    manifest_path = package_root / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            title = str(data.get("title") or "").strip()
            if title:
                return title
    marker = package_root / ".extracted_from"
    if marker.is_file():
        source = Path(marker.read_text(encoding="utf-8", errors="ignore").strip() or "0")
        return source.name
    return package_root.name


def storage_overview() -> dict[str, Any]:
    """Summarize every cleanable cache area for the storage settings page."""
    ensure_project_dirs()
    now = time.time()

    packages: list[dict[str, Any]] = []
    referenced_sources: set[str] = set()
    try:
        from pathlib import Path as _P
        textbooks = scan_library_files().get("textbooks") or []
        referenced_sources = {
            str(_P(str(item.get("path") or "")).resolve()) for item in textbooks
        }
    except Exception:
        pass

    for root in _safe_iter_dirs(PACKAGE_CACHE_DIR):
        marker = root / ".extracted_from"
        source = marker.read_text(encoding="utf-8", errors="ignore").strip() if marker.is_file() else ""
        age_days = max(0.0, (now - _dir_mtime(root)) / 86400)
        packages.append({
            "id": root.name,
            "title": _package_title(root),
            "source_path": source,
            "source_still_in_library": bool(source) and source in referenced_sources,
            "size_bytes": _dir_size(root),
            "age_days": round(age_days, 1),
            "deletable": True,
        })

    index_cache_in_use = _index_cache_in_use()
    indexes: list[dict[str, Any]] = []
    for root in _safe_iter_dirs(TEXTBOOK_INDEX_CACHE_DIR):
        validated = validated_textbook_index_cache(root)
        coherent = validated is not None
        status, manifest = validated if coherent else ({}, {})
        file_names: list[str] = []
        rows = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        for row in rows:
            if isinstance(row, dict) and row.get("name"):
                file_names.append(str(row["name"]))
        indexes.append({
            "key": root.name,
            "file_count": len(file_names),
            "file_names": file_names[:20],
            "block_count": int(status.get("block_count") or 0) if coherent else 0,
            "coherent": coherent,
            "in_use": root.name in index_cache_in_use,
            "size_bytes": _dir_size(root),
            "deletable": not coherent or root.name not in index_cache_in_use,
        })
        # In-use index entries are hidden from the deletion list entirely so a
        # bulk "delete all" cannot break the user's current textbook setup.
        if indexes[-1]["in_use"]:
            indexes[-1]["deletable"] = False

    areas = [
        {
            "kind": "textbook_packages",
            "label": "教材解压缓存（删除后按需自动重建）",
            "entries": packages,
            "total_bytes": sum(item["size_bytes"] for item in packages),
        },
        {
            "kind": "textbook_indexes",
            "label": "教材索引缓存（当前在用的不可删除）",
            "entries": indexes,
            "total_bytes": sum(item["size_bytes"] for item in indexes),
        },
    ]
    return {
        "ok": True,
        "areas": areas,
        "cleanable_bytes": sum(
            item["size_bytes"]
            for area in areas
            for item in area["entries"]
            if item.get("deletable")
        ),
    }


def cleanup_storage(kind: str, ids: list[str] | None = None) -> dict[str, Any]:
    """Delete cache entries.

    ``ids=None`` deletes every deletable entry of ``kind``; an explicit id list
    deletes only those entries. Entries marked not-deletable are always skipped
    with a reason rather than silently kept.
    """
    ensure_project_dirs()
    overview = storage_overview()
    areas = {area["kind"]: area for area in overview.get("areas") or []}
    area = areas.get(kind)
    if area is None:
        raise ValueError(f"未知的清理类别：{kind}")
    requested = {str(item) for item in ids} if ids else None
    deleted: list[str] = []
    skipped: list[dict[str, Any]] = []
    freed_bytes = 0
    roots = PACKAGE_CACHE_DIR if kind == "textbook_packages" else TEXTBOOK_INDEX_CACHE_DIR
    for item in area["entries"]:
        identifier = str(item.get("id") or item.get("key") or "")
        if requested is not None and identifier not in requested:
            continue
        target = roots / identifier
        if not item.get("deletable"):
            skipped.append({"id": identifier, "reason": "当前仍在使用，不能删除"})
            continue
        if not target.is_dir():
            skipped.append({"id": identifier, "reason": "条目已不存在"})
            continue
        size = int(item.get("size_bytes") or 0)
        try:
            shutil.rmtree(target)
        except OSError as exc:
            skipped.append({"id": identifier, "reason": f"删除失败：{exc}"})
            continue
        freed_bytes += size
        deleted.append(identifier)
    return {"ok": True, "deleted": deleted, "skipped": skipped, "freed_bytes": freed_bytes}
