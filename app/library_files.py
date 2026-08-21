from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, BinaryIO

from .paths import EXAMS_DIR, TEXTBOOKS_DIR, ensure_project_dirs
from .textbook_index_cache import (
    TEXTBOOK_INDEX_CACHE_DIR,
    shared_install_cache_key,
    validated_textbook_index_cache,
)

EXAM_EXTENSIONS = {".docx"}
TEXTBOOK_EXTENSIONS = {".pdf", ".docx", ".json", ".md", ".txt", ".zip"}
EXAM_UPLOAD_MAX_BYTES = 100 * 1024 * 1024
TEXTBOOK_UPLOAD_MAX_BYTES = 512 * 1024 * 1024
_UPLOAD_LOCK = threading.RLock()
VOLUME_MARKER_RE = re.compile(
    r"^(?P<title>.+?)(?P<volume>[上下中])(?:册|卷|部)?(?:第?(?P<part>[一二三四五六七八九十\d]+)分?册?)?$"
)
NUMBERED_VOLUME_RE = re.compile(r"^(?P<title>.+?)(?:第)?(?P<part>[一二三四五六七八九十\d]+)(?:册|卷|部|分册|部分)$")
PAGE_RANGE_SUFFIX_RE = re.compile(r"^(?P<title>.+?)(?P<start>\d+)\s*[-~至]\s*(?P<end>\d+)$")
TRAILING_PART_RE = re.compile(r"^(?P<title>.*[^\d])(?P<part>[1-9]\d*)$")


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = re.sub(r"[\x00-\x1f]", "", name)
    if not name or name in {".", ".."}:
        raise ValueError("Invalid filename")
    return name


def library_upload_limit(kind: str) -> int:
    if kind == "exam":
        return EXAM_UPLOAD_MAX_BYTES
    if kind == "textbook":
        return TEXTBOOK_UPLOAD_MAX_BYTES
    raise ValueError("Upload kind must be exam or textbook")


def _upload_target(kind: str, filename: str) -> tuple[Path, str]:
    safe_name = _safe_filename(filename)
    if kind == "exam":
        target_dir, allowed = EXAMS_DIR, EXAM_EXTENSIONS
    elif kind == "textbook":
        target_dir, allowed = TEXTBOOKS_DIR, TEXTBOOK_EXTENSIONS
    else:
        raise ValueError("Upload kind must be exam or textbook")
    if Path(safe_name).suffix.lower() not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported file type for {kind}: {Path(safe_name).suffix}. Allowed: {allowed_text}")
    return target_dir, safe_name


def _non_overwriting_target(target: Path, uploaded: Path) -> tuple[Path, bool]:
    if not target.exists():
        return target, False
    if not target.is_symlink() and target.is_file() and _fingerprint(target) == _fingerprint(uploaded):
        return target, True
    for index in range(2, 10_000):
        candidate = target.with_name(f"{target.stem} ({index}){target.suffix}")
        if not candidate.exists():
            return candidate, False
    raise ValueError("同名文件过多，请修改文件名后再上传。")


def _file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    info = {
        "name": path.name,
        "path": str(path.resolve()),
        "size": stat.st_size,
        "updated_at": stat.st_mtime,
        "extension": path.suffix.lower(),
    }
    shared_root = TEXTBOOKS_DIR.resolve()
    current = path.parent.resolve()
    while current != shared_root and shared_root in current.parents:
        manifest = current / ".shared_library_manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                citation_name = str((data.get("citation_names_by_file") or {}).get(path.name) or "").strip()
                if citation_name:
                    info["citation_textbook"] = citation_name
                info["shared_library_id"] = str(data.get("library_id") or "")
                info["shared_library_version"] = str(data.get("version") or "")
            except (OSError, json.JSONDecodeError):
                pass
            break
        current = current.parent
    return info


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_stem(path: Path) -> str:
    stem = path.stem.lower()
    stem = re.sub(r"[\s_\-（）()【】\\[\\].]+", "", stem)
    stem = re.sub(r"(副本|copy|复制|修订版|最新版|final|v\\d+)$", "", stem)
    return stem


def _clean_group_title(value: str) -> str:
    text = re.sub(r"[\s_\-（）()【】\[\].]+", "", str(value or "").strip())
    return text


def _filename_group_candidate(name: str) -> dict[str, str] | None:
    stem = Path(name).stem.strip()
    if not stem:
        return None
    page_range = PAGE_RANGE_SUFFIX_RE.match(stem)
    if page_range and len(_clean_group_title(page_range.group("title"))) >= 4:
        title = page_range.group("title").strip()
        return {
            "key": f"filename:{_clean_group_title(title).lower()}",
            "name": title,
            "reason": "文件名包含连续页码范围分段",
            "confidence": "high",
            "part": f"{page_range.group('start')}-{page_range.group('end')}",
        }
    normalized = _clean_group_title(stem)
    if len(normalized) < 4:
        return None

    edition = re.match(r"^(?P<title>.+?第\s*[一二三四五六七八九十\d]+\s*版)(?P<volume>[上下中])(?P<part>\d+)?$", normalized)
    if edition:
        title = edition.group("title")
        return {
            "key": f"filename:{title.lower()}",
            "name": title,
            "reason": "文件名包含同一版次的连续分册标记",
            "confidence": "high",
            "part": f"{edition.group('volume')}{edition.group('part') or ''}",
        }

    marker = VOLUME_MARKER_RE.match(normalized)
    if marker and len(marker.group("title")) >= 4:
        title = marker.group("title")
        return {
            "key": f"filename:{title.lower()}",
            "name": title,
            "reason": "文件名包含上册、中册或下册标记",
            "confidence": "high",
            "part": f"{marker.group('volume')}{marker.group('part') or ''}",
        }

    numbered = NUMBERED_VOLUME_RE.match(normalized)
    if numbered and len(numbered.group("title")) >= 4:
        title = numbered.group("title")
        return {
            "key": f"filename:{title.lower()}",
            "name": title,
            "reason": "文件名包含连续册、卷或分册标记",
            "confidence": "high",
            "part": numbered.group("part"),
        }

    trailing = TRAILING_PART_RE.match(normalized)
    if trailing and len(trailing.group("title")) >= 6:
        title = trailing.group("title")
        return {
            "key": f"filename:{title.lower()}",
            "name": title,
            "reason": "文件名仅以连续数字区分分段",
            "confidence": "medium",
            "part": trailing.group("part"),
        }
    return None


def textbook_group_suggestions(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggest textbook sets from authoritative package metadata before filename heuristics."""
    groups: dict[str, dict[str, Any]] = {}
    consumed: set[str] = set()
    for item in files:
        citation = str(item.get("citation_textbook") or "").strip()
        path = str(item.get("path") or "")
        if not citation or not path:
            continue
        key = f"citation:{_clean_group_title(citation).lower()}"
        group = groups.setdefault(
            key,
            {
                "key": key,
                "name": citation,
                "confidence": "high",
                "reason": "共享教材包声明了相同的教材引用名称",
                "files": [],
            },
        )
        group["files"].append(item)
    result: list[dict[str, Any]] = []
    for group in groups.values():
        unique = {str(item.get("path") or "") for item in group["files"]}
        if len(unique) < 2:
            continue
        result.append(group)
        consumed.update(unique)

    filename_groups: dict[str, dict[str, Any]] = {}
    for item in files:
        path = str(item.get("path") or "")
        if not path or path in consumed:
            continue
        candidate = _filename_group_candidate(str(item.get("name") or ""))
        if candidate is None:
            continue
        group = filename_groups.setdefault(
            candidate["key"],
            {**candidate, "files": [], "parts": set()},
        )
        group["files"].append(item)
        group["parts"].add(candidate["part"])
    for group in filename_groups.values():
        unique = {str(item.get("path") or "") for item in group["files"]}
        if len(unique) < 2 or len(group["parts"]) < 2:
            continue
        group.pop("parts", None)
        result.append(group)

    return sorted(result, key=lambda group: (group["confidence"] != "high", str(group["name"]).lower()))


def _scan_dir(root: Path, extensions: set[str]) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    files = []
    for path in sorted(root.rglob("*"), key=lambda p: p.name.lower()):
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in extensions:
            files.append(_file_info(path))
    return files


def _file_index_identity(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return path.name, stat.st_size, stat.st_mtime_ns


def attach_textbook_index_statuses(files: list[dict[str, Any]]) -> None:
    """Annotate library files that belong to a valid reusable textbook index."""
    by_identity: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for item in files:
        identity = _file_index_identity(Path(str(item.get("path") or "")))
        if identity is None:
            continue
        by_identity.setdefault(identity, []).append(item)
        item["index_status"] = {"indexed": False, "cache_count": 0, "page_map_ok": None}

    if not by_identity or not TEXTBOOK_INDEX_CACHE_DIR.exists():
        return

    # Shared packages carry a published cache key.  Do this before legacy
    # timestamp matching because Windows can round ``st_mtime_ns`` on copy.
    shared_item_cache_keys: dict[int, str] = {}
    for item in files:
        path = Path(str(item.get("path") or ""))
        cache_key = shared_install_cache_key([path], require_complete_source_set=False)
        if not cache_key:
            continue
        cache_root = TEXTBOOK_INDEX_CACHE_DIR / cache_key
        validated = validated_textbook_index_cache(cache_root, expected_key=cache_key)
        if validated is None:
            continue
        status, _ = validated
        item["index_status"] = {
            "indexed": True,
            "cache_count": 1,
            "page_map_ok": bool(status.get("page_map_ok", True)),
        }
        shared_item_cache_keys[id(item)] = cache_key

    for cache_root in TEXTBOOK_INDEX_CACHE_DIR.iterdir():
        if not cache_root.is_dir():
            continue
        validated = validated_textbook_index_cache(cache_root)
        if validated is None:
            continue
        status, manifest = validated
        for entry in manifest.get("files", []):
            if not isinstance(entry, dict):
                continue
            try:
                identity = (str(entry.get("name") or ""), int(entry.get("size")), int(entry.get("mtime_ns")))
            except (TypeError, ValueError):
                continue
            for item in by_identity.get(identity, []):
                if shared_item_cache_keys.get(id(item)) == cache_root.name:
                    continue
                current = item["index_status"]
                current["indexed"] = True
                current["cache_count"] = int(current.get("cache_count") or 0) + 1
                current["page_map_ok"] = bool(status.get("page_map_ok", True))


def _duplicate_issues(label: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_content: dict[str, list[dict[str, Any]]] = {}
    for item in files:
        path = Path(item["path"])
        by_name.setdefault(_normalized_stem(path), []).append(item)
        try:
            by_content.setdefault(_fingerprint(path), []).append(item)
        except OSError:
            continue
    issues: list[dict[str, Any]] = []
    for group in by_name.values():
        if len(group) > 1:
            issues.append(
                {
                    "scope": label,
                    "reason": "文件名高度相似，建议确认是否重复",
                    "files": [{"name": x["name"], "path": x["path"]} for x in group],
                }
            )
    for group in by_content.values():
        if len(group) > 1:
            issues.append(
                {
                    "scope": label,
                    "reason": "文件内容完全相同，建议只保留一个",
                    "files": [{"name": x["name"], "path": x["path"]} for x in group],
                }
            )
    return issues


def scan_library_files() -> dict[str, Any]:
    ensure_project_dirs()
    exams = _scan_dir(EXAMS_DIR, EXAM_EXTENSIONS)
    textbooks = _scan_dir(TEXTBOOKS_DIR, TEXTBOOK_EXTENSIONS)
    attach_textbook_index_statuses(textbooks)
    duplicate_issues = _duplicate_issues("真题", exams) + _duplicate_issues("教材", textbooks)
    return {
        "exams_root": str(EXAMS_DIR.resolve()),
        "textbooks_root": str(TEXTBOOKS_DIR.resolve()),
        "exams": exams,
        "textbooks": textbooks,
        "textbook_groups": textbook_group_suggestions(textbooks),
        "duplicate_review": {
            "ok": not duplicate_issues,
            "issue_count": len(duplicate_issues),
            "issues": duplicate_issues,
        },
    }


def save_library_upload(kind: str, filename: str, data: bytes) -> dict[str, Any]:
    return save_library_upload_stream(kind, filename, io.BytesIO(data), len(data))


def save_library_upload_stream(
    kind: str,
    filename: str,
    stream: BinaryIO,
    length: int,
) -> dict[str, Any]:
    ensure_project_dirs()
    target_dir, safe_name = _upload_target(kind, filename)
    limit = library_upload_limit(kind)
    if length <= 0:
        raise ValueError("Uploaded file is empty")
    if length > limit:
        raise ValueError(f"上传文件过大；当前类型单个文件上限为 {limit // (1024 * 1024)} MB。")
    target_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=target_dir)
    temporary = Path(temporary_name)
    remaining = length
    try:
        with os.fdopen(descriptor, "wb") as output:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("上传内容不完整，请重新选择文件上传。")
                output.write(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        with _UPLOAD_LOCK:
            requested = target_dir / safe_name
            target, reused = _non_overwriting_target(requested, temporary)
            if reused:
                info = _file_info(target)
                info.update({"reused": True, "renamed": False, "requested_name": safe_name})
                return info
            os.replace(temporary, target)
            info = _file_info(target)
            info.update(
                {
                    "reused": False,
                    "renamed": target.name != safe_name,
                    "requested_name": safe_name,
                }
            )
            return info
    finally:
        temporary.unlink(missing_ok=True)


def delete_library_file(kind: str, raw_path: str) -> dict[str, Any]:
    ensure_project_dirs()
    if kind == "exam":
        root = EXAMS_DIR.resolve()
        allowed = EXAM_EXTENSIONS
    elif kind == "textbook":
        root = TEXTBOOKS_DIR.resolve()
        allowed = TEXTBOOK_EXTENSIONS
    else:
        raise ValueError("Delete kind must be exam or textbook")
    target = Path(raw_path).expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("File is outside the library directory") from exc
    if target.suffix.lower() not in allowed:
        raise ValueError("Unsupported file type")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("File not found")
    info = _file_info(target)
    target.unlink()
    return {"ok": True, "deleted": info}
