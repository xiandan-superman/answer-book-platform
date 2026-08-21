from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .paths import CACHE_DIR, ensure_project_dirs
from .textbook_index import BLOCK_FIELDS, PAGE_MAP_FIELDS, build_textbook_index_for_files, write_csv

TEXTBOOK_INDEX_CACHE_DIR = CACHE_DIR / "textbook_indexes"
INDEX_CACHE_VERSION = "content-block-assets-v5-source-aware-pages"


def _file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _normalized_citation_names(files: list[Path], citation_names_by_path: dict[str, str] | None) -> dict[str, str]:
    raw = citation_names_by_path or {}
    normalized: dict[str, str] = {}
    for path in files:
        resolved = str(path.resolve())
        name = str(raw.get(resolved) or raw.get(str(path)) or "").strip()
        if not name:
            shared = _shared_install_manifest(path)
            if shared is not None:
                _, manifest = shared
                names = manifest.get("citation_names_by_file")
                if isinstance(names, dict):
                    name = str(names.get(path.name) or "").strip()
        if name:
            normalized[resolved] = name
    return normalized


def _shared_install_manifest(path: Path) -> tuple[Path, dict[str, Any]] | None:
    """Find the manifest written beside a downloaded shared textbook package."""
    current = path.resolve().parent
    while current != current.parent:
        manifest_path = current / ".shared_library_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            if isinstance(manifest, dict):
                return manifest_path, manifest
            return None
        current = current.parent
    return None


def shared_install_cache_key(files: list[Path], *, require_complete_source_set: bool = True) -> str | None:
    """Return a published cache key for a valid shared-textbook installation.

    Shared packages preserve source content and ship their completed index.  File
    timestamp precision differs between macOS and Windows, so cache reuse must
    not depend on a byte-for-byte match of ``st_mtime_ns`` after installation.
    """
    if not files:
        return None
    manifests = [_shared_install_manifest(path) for path in files]
    if any(item is None for item in manifests):
        return None
    manifest_paths = {str(item[0]) for item in manifests if item is not None}
    if len(manifest_paths) != 1:
        return None
    _, manifest = manifests[0]  # type: ignore[index]
    cache_key = str(manifest.get("cache_key") or "").strip()
    source_names = manifest.get("source_file_names")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", cache_key) or not isinstance(source_names, list):
        return None
    expected_names = [Path(str(name)).name for name in source_names if str(name).strip()]
    selected_names = [path.name for path in files]
    if not expected_names or len(set(expected_names)) != len(expected_names):
        return None
    if any(name not in expected_names for name in selected_names):
        return None
    if require_complete_source_set and set(selected_names) != set(expected_names):
        return None
    return cache_key


def textbook_index_key(files: list[Path], citation_names_by_path: dict[str, str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    citation_names = _normalized_citation_names(files, citation_names_by_path)
    manifest = []
    for path in sorted(files, key=lambda p: p.name.lower()):
        row = _file_signature(path.resolve())
        citation_name = citation_names.get(str(path.resolve()))
        if citation_name:
            row["citation_textbook"] = citation_name
        manifest.append(row)
    shared_key = shared_install_cache_key(files)
    if shared_key:
        return shared_key, manifest
    raw = json.dumps({"version": INDEX_CACHE_VERSION, "files": manifest}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24], manifest


def _cache_paths(key: str) -> dict[str, Path]:
    root = TEXTBOOK_INDEX_CACHE_DIR / key
    return {
        "root": root,
        "blocks": root / "textbook_blocks.csv",
        "page_map": root / "textbook_page_map.csv",
        "status": root / "textbook_index_status.json",
        "package_audit": root / "textbook_package_audit.json",
        "manifest": root / "manifest.json",
    }


def _cache_is_complete(root: Path) -> bool:
    return (
        (root / "textbook_blocks.csv").is_file()
        and (root / "textbook_page_map.csv").is_file()
        and (root / "textbook_index_status.json").is_file()
        and (root / "manifest.json").is_file()
    )


def _manifest_signature(row: dict[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(row.get("name") or ""),
        int(row.get("size") or 0),
        int(row.get("mtime_ns") or 0),
        str(row.get("citation_textbook") or ""),
    )


def _manifest_counter(files: list[dict[str, Any]], *, include_mtime: bool = True) -> Counter[tuple[Any, ...]]:
    counter: Counter[tuple[Any, ...]] = Counter()
    for row in files:
        try:
            signature = _manifest_signature(row)
            counter[signature if include_mtime else (signature[0], signature[1], signature[3])] += 1
        except (TypeError, ValueError):
            continue
    return counter


def _counter_contains(container: Counter[tuple[Any, ...]], subset: Counter[tuple[Any, ...]]) -> bool:
    return all(container.get(key, 0) >= count for key, count in subset.items())


def _load_cache_status(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / "textbook_index_status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_cache_manifest(root: Path) -> dict[str, Any] | None:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _csv_integrity(
    path: Path,
    expected_fields: list[str],
    *,
    expected_row_count: int | None = None,
    identity_field: str = "",
) -> int | None:
    """Validate a cached CSV as data, not merely as an existing file."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not set(expected_fields).issubset(reader.fieldnames):
                return None
            count = 0
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    return None
                if identity_field and not str(row.get(identity_field) or "").strip():
                    return None
                count += 1
    except (OSError, UnicodeError, csv.Error):
        return None
    if expected_row_count is not None and count != expected_row_count:
        return None
    return count


def validated_textbook_index_cache(
    root: Path,
    *,
    expected_key: str = "",
    expected_manifest: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return parsed cache metadata only when the complete cache is coherent.

    A partial write or manually damaged JSON/CSV must become a cache miss.  It
    must never be copied into a task merely because the four filenames exist.
    """
    if not _cache_is_complete(root):
        return None
    status = _load_cache_status(root)
    manifest = _load_cache_manifest(root)
    if (
        not isinstance(status, dict)
        or not {"textbook_count", "block_count", "page_map_ok"}.issubset(status)
        or manifest is None
    ):
        return None
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list) or not all(isinstance(row, dict) for row in manifest_files):
        return None
    if expected_key and str(manifest.get("key") or "") != expected_key:
        return None
    if expected_manifest is not None:
        actual = _manifest_counter(manifest_files, include_mtime=False)
        expected = _manifest_counter(expected_manifest, include_mtime=False)
        if not actual or actual != expected:
            return None
    expected_blocks: int | None = None
    if "block_count" in status:
        try:
            expected_blocks = int(status["block_count"])
        except (TypeError, ValueError):
            return None
        if expected_blocks < 0:
            return None
    if _csv_integrity(
        root / "textbook_blocks.csv",
        BLOCK_FIELDS,
        expected_row_count=expected_blocks,
        identity_field="block_id",
    ) is None:
        return None
    if _csv_integrity(root / "textbook_page_map.csv", PAGE_MAP_FIELDS) is None:
        return None
    return status, manifest


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _dedupe_package_audits(package_audits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for audit in package_audits:
        if not isinstance(audit, dict):
            continue
        source_name = Path(str(audit.get("source_zip") or "")).name
        key = f"{str(audit.get('package_id') or '')}|{source_name}" or str(audit.get("source_zip") or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(audit)
    return unique


def _rebind_package_audits(
    package_audits: list[dict[str, Any]],
    files: list[Path],
) -> list[dict[str, Any]]:
    """Bind content-addressed audit metadata to this installation.

    Index caches may be copied between workspaces.  Their scientific content
    remains reusable, but absolute ``source_zip`` and extracted ``root`` paths
    are installation-local provenance and must never retain the cache author's
    workspace path.
    """

    from . import textbook_package

    by_name = {path.name: path.resolve() for path in files}
    rebound: list[dict[str, Any]] = []
    for raw in _dedupe_package_audits(package_audits):
        audit = dict(raw)
        current = by_name.get(Path(str(audit.get("source_zip") or "")).name)
        package_id = str(audit.get("package_id") or "").strip()
        if current is not None:
            audit["source_zip"] = str(current)
        if package_id:
            audit["root"] = str(textbook_package.PACKAGE_CACHE_DIR / package_id)
        rebound.append(audit)
    return rebound


def _find_composable_cache_roots(
    manifest: list[dict[str, Any]], *, allow_shared_timestamp_fallback: bool = False
) -> list[Path] | None:
    if not TEXTBOOK_INDEX_CACHE_DIR.exists():
        return None
    # Prefer exact identity. Shared ZIP extraction can round mtime on Windows,
    # so only fully shared selections may fall back to stable source metadata.
    for include_mtime in (True, False) if allow_shared_timestamp_fallback else (True,):
        remaining = _manifest_counter(manifest, include_mtime=include_mtime)
        if not remaining:
            return None
        candidates: list[tuple[int, Path, Counter[tuple[Any, ...]]]] = []
        for cache_root in TEXTBOOK_INDEX_CACHE_DIR.iterdir():
            if not cache_root.is_dir():
                continue
            validated = validated_textbook_index_cache(cache_root)
            if validated is None:
                continue
            _, cache_manifest = validated
            files = cache_manifest.get("files")
            if not isinstance(files, list):
                continue
            counter = _manifest_counter([row for row in files if isinstance(row, dict)], include_mtime=include_mtime)
            if counter and _counter_contains(remaining, counter):
                candidates.append((sum(counter.values()), cache_root, counter))
        selected: list[Path] = []
        for _, cache_root, counter in sorted(candidates, key=lambda item: item[0], reverse=True):
            if not _counter_contains(remaining, counter):
                continue
            selected.append(cache_root)
            remaining.subtract(counter)
            remaining += Counter()
            if not remaining:
                return selected
    return None


def _compose_textbook_index_cache(
    key: str,
    manifest: list[dict[str, Any]],
    target_paths: dict[str, Path],
    source_roots: list[Path],
) -> dict[str, Any]:
    target_paths["root"].mkdir(parents=True, exist_ok=True)
    block_rows: list[dict[str, str]] = []
    page_rows: list[dict[str, str]] = []
    page_map_ok = True
    page_map_issues: list[dict[str, Any]] = []
    package_audits: list[dict[str, Any]] = []
    for source_root in source_roots:
        block_rows.extend(_read_csv_rows(source_root / "textbook_blocks.csv"))
        page_rows.extend(_read_csv_rows(source_root / "textbook_page_map.csv"))
        source_status = _load_cache_status(source_root)
        page_map_ok = page_map_ok and bool(source_status.get("page_map_ok", True))
        page_map_issues.extend(source_status.get("page_map_issues") or [])
        package_audits.extend(source_status.get("textbook_package_audits") or [])
        package_audit_path = source_root / "textbook_package_audit.json"
        if package_audit_path.is_file():
            try:
                package_audit = json.loads(package_audit_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                package_audit = {}
            packages = package_audit.get("packages") if isinstance(package_audit, dict) else None
            if isinstance(packages, list):
                package_audits.extend(packages)
    package_audits = _dedupe_package_audits(package_audits)
    write_csv(target_paths["blocks"], BLOCK_FIELDS, block_rows)
    write_csv(target_paths["page_map"], PAGE_MAP_FIELDS, page_rows)
    status: dict[str, Any] = {
        "textbook_count": len(manifest),
        "block_count": len(block_rows),
        "blocks_csv": str(target_paths["blocks"]),
        "page_map_csv": str(target_paths["page_map"]),
        "page_map_ok": page_map_ok,
        "page_map_issues": page_map_issues,
        "composed_from": [root.name for root in source_roots],
    }
    if package_audits:
        status["textbook_package_audits"] = package_audits
        target_paths["package_audit"].write_text(json.dumps({"packages": package_audits}, ensure_ascii=False, indent=2), encoding="utf-8")
    target_paths["status"].write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    target_paths["manifest"].write_text(json.dumps({"key": key, "files": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def _ensure_composed_textbook_index_cache(
    key: str, manifest: list[dict[str, Any]], paths: dict[str, Path], files: list[Path]
) -> dict[str, Any] | None:
    source_roots = _find_composable_cache_roots(
        manifest,
        allow_shared_timestamp_fallback=all(_shared_install_manifest(path) is not None for path in files),
    )
    if not source_roots or len(source_roots) < 2:
        return None
    return _compose_textbook_index_cache(key, manifest, paths, source_roots)


def _selected_files(selected_paths: list[str]) -> list[Path]:
    ensure_project_dirs()
    files = [Path(path).expanduser().resolve() for path in selected_paths]
    if not files:
        raise ValueError("Please select at least one textbook before building the index")
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(f"Textbook file not found: {path}")
    return files


def textbook_index_cache_status(selected_paths: list[str], citation_names_by_path: dict[str, str] | None = None) -> dict[str, Any]:
    files = _selected_files(selected_paths)
    citation_names = _normalized_citation_names(files, citation_names_by_path)
    key, manifest = textbook_index_key(files, citation_names)
    paths = _cache_paths(key)
    validated = validated_textbook_index_cache(
        paths["root"],
        expected_key=key,
        expected_manifest=manifest,
    )
    indexed = validated is not None
    status: dict[str, Any] = validated[0] if validated else {}
    if not indexed:
        status = _ensure_composed_textbook_index_cache(key, manifest, paths, files) or {}
        indexed = bool(status)
    package_audits = _rebind_package_audits(status.get("textbook_package_audits") or [], files)
    return {
        "ok": True,
        "indexed": indexed,
        "cached": indexed,
        "cache_key": key,
        "textbook_count": int(status.get("textbook_count", len(files))),
        "block_count": int(status.get("block_count", 0)),
        "page_map_ok": bool(status.get("page_map_ok", True)),
        "page_map_issues": status.get("page_map_issues") or [],
        "blocks_csv": str(paths["blocks"]) if paths["blocks"].exists() else "",
        "page_map_csv": str(paths["page_map"]) if paths["page_map"].exists() else "",
        "textbook_package_audit": str(paths["package_audit"]) if paths["package_audit"].exists() else "",
        "textbook_package_audits": package_audits,
        "manifest": manifest,
        "message": "所选教材已有可复用索引。" if indexed else "所选教材尚未建立索引，请先在教材管理页建立索引。",
    }


def prepare_textbook_index_cache(selected_paths: list[str], citation_names_by_path: dict[str, str] | None = None) -> dict[str, Any]:
    files = _selected_files(selected_paths)
    citation_names = _normalized_citation_names(files, citation_names_by_path)
    key, manifest = textbook_index_key(files, citation_names)
    paths = _cache_paths(key)
    validated = validated_textbook_index_cache(
        paths["root"],
        expected_key=key,
        expected_manifest=manifest,
    )
    cached = validated is not None
    if not cached:
        status = _ensure_composed_textbook_index_cache(key, manifest, paths, files)
        if status is None:
            paths["root"].mkdir(parents=True, exist_ok=True)
            result = build_textbook_index_for_files(files, paths["root"], citation_names_by_source=citation_names)
            status = asdict(result)
            paths["manifest"].write_text(json.dumps({"key": key, "files": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        status = validated[0]
    return {
        **textbook_index_cache_status(selected_paths, citation_names_by_path),
        "ok": True,
        "indexed": True,
        "cached": cached,
        "textbook_count": int(status.get("textbook_count", len(files))),
        "block_count": int(status.get("block_count", 0)),
        "page_map_ok": bool(status.get("page_map_ok", True)),
        "page_map_issues": status.get("page_map_issues") or [],
        "blocks_csv": str(paths["blocks"]),
        "page_map_csv": str(paths["page_map"]),
        "textbook_package_audit": str(paths["package_audit"]) if paths["package_audit"].exists() else "",
        "textbook_package_audits": _rebind_package_audits(status.get("textbook_package_audits") or [], files),
        "manifest": manifest,
        "message": "已建立索引可复用。" if cached else "教材索引已建立。",
    }


def require_textbook_index_cache(selected_paths: list[str], citation_names_by_path: dict[str, str] | None = None) -> dict[str, Any]:
    status = textbook_index_cache_status(selected_paths, citation_names_by_path)
    if not status.get("indexed"):
        raise ValueError("所选教材尚未建立索引，请先在教材管理页选择这些教材并点击“建立教材索引”。")
    return status


def install_textbook_index_cache(
    selected_paths: list[str],
    stage_dir: Path,
    citation_names_by_path: dict[str, str] | None = None,
) -> dict[str, Any]:
    prepared = require_textbook_index_cache(selected_paths, citation_names_by_path)
    stage_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prepared["blocks_csv"], stage_dir / "textbook_blocks.csv")
    shutil.copy2(prepared["page_map_csv"], stage_dir / "textbook_page_map.csv")
    package_audits = prepared.get("textbook_package_audits") or []
    if package_audits:
        (stage_dir / "textbook_package_audit.json").write_text(
            json.dumps({"packages": package_audits}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    status = {
        "textbook_count": prepared["textbook_count"],
        "block_count": prepared["block_count"],
        "blocks_csv": str(stage_dir / "textbook_blocks.csv"),
        "page_map_csv": str(stage_dir / "textbook_page_map.csv"),
        "cache_key": prepared["cache_key"],
        "cache_reused": prepared["cached"],
        "page_map_ok": prepared.get("page_map_ok", True),
        "page_map_issues": prepared.get("page_map_issues") or [],
        "textbook_package_audit": str(stage_dir / "textbook_package_audit.json") if (stage_dir / "textbook_package_audit.json").exists() else "",
    }
    (stage_dir / "textbook_index_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status
