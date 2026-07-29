from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import CACHE_DIR, SHARED_TEXTBOOK_LIBRARY_DIR, TEXTBOOKS_DIR, ensure_project_dirs
from .textbook_index_cache import TEXTBOOK_INDEX_CACHE_DIR, require_textbook_index_cache
from .textbook_package import is_textbook_package, prepare_textbook_package


SHARED_LIBRARY_SCHEMA_VERSION = "answer_book.shared_textbook_library.v1"
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_PACKAGE_ASSET_RE = re.compile(r"(?:^|[\\/])textbook_packages[\\/]([0-9a-f]{24})([\\/].*)?$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_component(value: str, label: str) -> str:
    text = str(value or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"{label} 只能包含小写字母、数字、点、下划线和连字符，且长度不超过 80。")
    return text


def _slug(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:80] or "textbook-library"


def _library_root(root: Path | None = None) -> Path:
    target = (root or SHARED_TEXTBOOK_LIBRARY_DIR).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _catalog_path(root: Path | None = None) -> Path:
    return _library_root(root) / "catalog.json"


def _settings_path(root: Path | None = None) -> Path:
    return _library_root(root) / "client_settings.json"


def _published_root(root: Path | None = None) -> Path:
    target = _library_root(root) / "published"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _normalize_remote_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    match = re.fullmatch(r"(https?)://([^/]+)(?:/.*)?", raw, flags=re.IGNORECASE)
    if not match:
        raise ValueError("共享教材库地址必须是完整的 http(s) 地址。")
    scheme, host = match.group(1).lower(), match.group(2).lower()
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    host_name = host.split(":", 1)[0].strip("[]")
    if scheme != "https" and host_name not in local_hosts:
        raise ValueError("共享教材库必须使用 HTTPS；仅本机调试允许 http://127.0.0.1。")
    return raw


def get_shared_library_settings(root: Path | None = None) -> dict[str, Any]:
    raw = _load_json(_settings_path(root), {})
    remote_url = _normalize_remote_url(str(raw.get("remote_url") or "")) if raw.get("remote_url") else ""
    return {"ok": True, "remote_url": remote_url}


def save_shared_library_settings(remote_url: str, root: Path | None = None) -> dict[str, Any]:
    normalized = _normalize_remote_url(remote_url)
    _write_json_atomic(_settings_path(root), {"remote_url": normalized})
    return {"ok": True, "remote_url": normalized}


def _empty_catalog() -> dict[str, Any]:
    return {"schema_version": SHARED_LIBRARY_SCHEMA_VERSION, "libraries": []}


def _read_catalog(root: Path | None = None) -> dict[str, Any]:
    data = _load_json(_catalog_path(root), _empty_catalog())
    if not isinstance(data, dict) or not isinstance(data.get("libraries"), list):
        return _empty_catalog()
    return {"schema_version": SHARED_LIBRARY_SCHEMA_VERSION, "libraries": data["libraries"]}


def shared_library_catalog(root: Path | None = None) -> dict[str, Any]:
    catalog = _read_catalog(root)
    libraries: list[dict[str, Any]] = []
    for entry in catalog["libraries"]:
        if not isinstance(entry, dict):
            continue
        versions = [item for item in entry.get("versions") or [] if isinstance(item, dict) and item.get("status") == "ready"]
        if versions:
            libraries.append({**entry, "versions": versions})
    return {"ok": True, "schema_version": SHARED_LIBRARY_SCHEMA_VERSION, "libraries": libraries}


def _source_manifest(files: list[Path], citation_names: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    package_ids: dict[str, str] = {}
    seen_names: set[str] = set()
    for source in files:
        name = source.name
        if name in seen_names:
            raise ValueError("共享教材包不支持同名源文件，请先重命名教材文件。")
        seen_names.add(name)
        row = {
            "name": name,
            "sha256": _sha256_file(source),
            "size": source.stat().st_size,
            "mtime_ns": source.stat().st_mtime_ns,
            "citation_textbook": citation_names.get(str(source.resolve()), ""),
        }
        if is_textbook_package(source):
            package = prepare_textbook_package(source)
            row["textbook_package_id"] = package.package_id
            package_ids[name] = package.package_id
        rows.append(row)
    return rows, package_ids


def _copy_into_zip(archive: zipfile.ZipFile, source: Path, arcname: str) -> None:
    with source.open("rb") as src, archive.open(arcname, "w") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def publish_shared_textbook_library(
    selected_paths: list[str],
    citation_names_by_path: dict[str, str] | None = None,
    *,
    library_id: str = "",
    title: str = "",
    version: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    ensure_project_dirs()
    selected = [Path(path).expanduser().resolve() for path in selected_paths]
    if not selected:
        raise ValueError("请先选择至少一本已建立索引的教材。")
    status = require_textbook_index_cache(selected_paths, citation_names_by_path)
    citation_names = {str(path.resolve()): str((citation_names_by_path or {}).get(str(path.resolve())) or (citation_names_by_path or {}).get(str(path)) or "").strip() for path in selected}
    source_rows, package_ids = _source_manifest(selected, citation_names)
    resolved_title = str(title or "").strip() or next((row["citation_textbook"] for row in source_rows if row["citation_textbook"]), selected[0].stem)
    safe_library_id = _safe_component(library_id or _slug(resolved_title), "教材标识")
    safe_version = _safe_component(version or _utc_timestamp(), "版本号")
    cache_root = Path(status["blocks_csv"]).parent.resolve()
    required_cache_files = ["textbook_blocks.csv", "textbook_page_map.csv", "textbook_index_status.json", "manifest.json"]
    missing = [name for name in required_cache_files if not (cache_root / name).is_file()]
    if missing:
        raise ValueError(f"教材索引缓存不完整，缺少：{', '.join(missing)}")
    library_root = _library_root(root)
    published_dir = _published_root(library_root) / safe_library_id
    published_dir.mkdir(parents=True, exist_ok=True)
    target = published_dir / f"{safe_version}.zip"
    if target.exists():
        raise ValueError(f"共享教材库中已存在 {safe_library_id}@{safe_version}，请使用新的版本号。")

    manifest = {
        "schema_version": SHARED_LIBRARY_SCHEMA_VERSION,
        "library_id": safe_library_id,
        "title": resolved_title,
        "version": safe_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cache_key": status["cache_key"],
        "source_files": source_rows,
        "textbook_package_ids": package_ids,
        "textbook_count": status["textbook_count"],
        "block_count": status["block_count"],
        "page_map_ok": status.get("page_map_ok", True),
    }
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{safe_version}.", suffix=".zip", dir=published_dir)
    os.close(fd)
    temp = Path(raw_tmp)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for source in selected:
                _copy_into_zip(archive, source, f"sources/{source.name}")
            for path in sorted(cache_root.rglob("*")):
                if path.is_file():
                    _copy_into_zip(archive, path, f"cache/{path.relative_to(cache_root).as_posix()}")
        package_sha256 = _sha256_file(temp)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    package_size = target.stat().st_size
    # A published library has one current distributable package. Keeping every
    # prior package made identical textbook sets appear as duplicate downloads.
    removed_versions: list[str] = []
    for old_package in published_dir.glob("*.zip"):
        if old_package == target:
            continue
        old_package.unlink()
        removed_versions.append(old_package.stem)

    catalog = _read_catalog(library_root)
    libraries = [item for item in catalog["libraries"] if isinstance(item, dict) and item.get("library_id") != safe_library_id]
    versions = [
        {
            "version": safe_version,
            "status": "ready",
            "created_at": manifest["created_at"],
            "package_sha256": package_sha256,
            "package_size": package_size,
            "textbook_count": manifest["textbook_count"],
            "block_count": manifest["block_count"],
            "page_map_ok": manifest["page_map_ok"],
        }
    ]
    libraries.append({"library_id": safe_library_id, "title": resolved_title, "versions": versions})
    libraries.sort(key=lambda item: str(item.get("title") or item.get("library_id") or "").lower())
    _write_json_atomic(_catalog_path(library_root), {"schema_version": SHARED_LIBRARY_SCHEMA_VERSION, "libraries": libraries})
    return {
        "ok": True,
        "library_id": safe_library_id,
        "title": resolved_title,
        "version": safe_version,
        "package_sha256": package_sha256,
        "package_size": package_size,
        "textbook_count": manifest["textbook_count"],
        "block_count": manifest["block_count"],
        "removed_versions": sorted(removed_versions),
    }


def shared_library_package_path(library_id: str, version: str, root: Path | None = None) -> Path:
    safe_library_id = _safe_component(library_id, "教材标识")
    safe_version = _safe_component(version, "版本号")
    target = _published_root(root) / safe_library_id / f"{safe_version}.zip"
    if not target.is_file():
        raise FileNotFoundError("共享教材版本不存在或尚未发布。")
    return target


def _safe_extract_package(archive_path: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"共享教材包中包含不安全路径：{info.filename}")
            output = (target / name).resolve()
            try:
                output.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"共享教材包中包含不安全路径：{info.filename}") from exc
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, output.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


def _rebase_asset_path(value: str, package_roots: dict[str, Path]) -> str:
    raw = str(value or "").strip()
    match = _PACKAGE_ASSET_RE.search(raw)
    if not match:
        return raw
    package_id = match.group(1)
    root = package_roots.get(package_id)
    if root is None:
        return raw
    suffix = (match.group(2) or "").lstrip("/\\")
    return str((root / suffix).resolve())


def _rebase_cache_asset_paths(cache_root: Path, package_roots: dict[str, Path]) -> None:
    blocks = cache_root / "textbook_blocks.csv"
    if not blocks.exists():
        raise ValueError("共享教材包缺少 textbook_blocks.csv。")
    with blocks.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return
    with blocks.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
    if "asset_path" not in fieldnames:
        return
    for row in rows:
        row["asset_path"] = _rebase_asset_path(row.get("asset_path", ""), package_roots)
    with blocks.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _replace_directory_atomically(source: Path, target: Path) -> None:
    backup: Path | None = None
    if target.exists():
        backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
        target.rename(backup)
    try:
        source.rename(target)
    except Exception:
        if backup is not None and backup.exists():
            backup.rename(target)
        raise
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


def install_shared_textbook_package(
    archive_path: Path,
    *,
    expected_sha256: str = "",
    textbooks_root: Path | None = None,
    index_cache_root: Path | None = None,
) -> dict[str, Any]:
    ensure_project_dirs()
    archive_path = archive_path.expanduser().resolve()
    if not archive_path.is_file():
        raise FileNotFoundError("共享教材下载文件不存在。")
    actual_sha256 = _sha256_file(archive_path)
    if expected_sha256 and actual_sha256.lower() != str(expected_sha256).strip().lower():
        raise ValueError("共享教材包校验失败：SHA-256 不匹配。")
    with tempfile.TemporaryDirectory(prefix="shared-textbook-install-", dir=CACHE_DIR) as raw_temp:
        temp = Path(raw_temp)
        extracted = temp / "package"
        extracted.mkdir()
        _safe_extract_package(archive_path, extracted)
        manifest = _load_json(extracted / "manifest.json", {})
        if not isinstance(manifest, dict) or manifest.get("schema_version") != SHARED_LIBRARY_SCHEMA_VERSION:
            raise ValueError("共享教材包格式不受支持。")
        library_id = _safe_component(str(manifest.get("library_id") or ""), "教材标识")
        version = _safe_component(str(manifest.get("version") or ""), "版本号")
        cache_key = _safe_component(str(manifest.get("cache_key") or ""), "索引标识")
        source_rows = manifest.get("source_files") or []
        if not isinstance(source_rows, list) or not source_rows:
            raise ValueError("共享教材包没有源教材清单。")
        source_dir = extracted / "sources"
        for row in source_rows:
            if not isinstance(row, dict):
                raise ValueError("共享教材包源教材清单无效。")
            name = Path(str(row.get("name") or "")).name
            source = source_dir / name
            if not name or not source.is_file() or _sha256_file(source) != str(row.get("sha256") or ""):
                raise ValueError(f"共享教材包源文件校验失败：{name or 'unknown'}")
        cache_source = extracted / "cache"
        required_cache_files = ["textbook_blocks.csv", "textbook_page_map.csv", "textbook_index_status.json", "manifest.json"]
        if any(not (cache_source / name).is_file() for name in required_cache_files):
            raise ValueError("共享教材包缺少完整教材索引缓存。")

        textbook_target_root = (textbooks_root or TEXTBOOKS_DIR).resolve() / "shared" / library_id / version
        target_cache = (index_cache_root or TEXTBOOK_INDEX_CACHE_DIR).resolve() / cache_key
        existing_manifest = textbook_target_root / ".shared_library_manifest.json"
        repairing_incomplete_install = False
        if existing_manifest.exists():
            current = _load_json(existing_manifest, {})
            if current.get("package_sha256") == actual_sha256:
                expected_sources = [textbook_target_root / Path(str(row.get("name") or "")).name for row in source_rows]
                expected_source_names = [path.name for path in expected_sources]
                sources_ready = all(path.is_file() for path in expected_sources)
                cache_ready = all((target_cache / name).is_file() for name in required_cache_files)
                manifest_ready = (
                    current.get("cache_key") == cache_key
                    and sorted(str(name) for name in current.get("source_file_names") or []) == sorted(expected_source_names)
                )
                if sources_ready and cache_ready and manifest_ready:
                    return {
                        "ok": True,
                        "installed": False,
                        "message": "该共享教材版本已安装。",
                        "library_id": library_id,
                        "version": version,
                        "selected_textbooks": [str(path) for path in expected_sources],
                    }
                repairing_incomplete_install = True
            else:
                raise ValueError("本机已有同名共享教材版本，但包内容不同。请使用新的版本号。")

        stage_textbooks = temp / "textbooks"
        stage_textbooks.mkdir()
        for row in source_rows:
            source = source_dir / Path(str(row["name"])).name
            target = stage_textbooks / source.name
            shutil.copy2(source, target)
            mtime_ns = int(row.get("mtime_ns") or 0)
            if mtime_ns > 0:
                os.utime(target, ns=(mtime_ns, mtime_ns))
        package_roots: dict[str, Path] = {}
        for row in source_rows:
            source = stage_textbooks / Path(str(row["name"])).name
            package_id = str(row.get("textbook_package_id") or "")
            if package_id:
                package = prepare_textbook_package(source)
                if package.package_id != package_id:
                    raise ValueError(f"教材图片资源包校验失败：{source.name}")
                package_roots[package_id] = package.root
        _write_json_atomic(stage_textbooks / ".shared_library_manifest.json", {
            "schema_version": SHARED_LIBRARY_SCHEMA_VERSION,
            "library_id": library_id,
            "version": version,
            "package_sha256": actual_sha256,
            "cache_key": cache_key,
            "source_file_names": [Path(str(row["name"])).name for row in source_rows],
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "citation_names_by_file": {
                Path(str(row["name"])).name: str(row.get("citation_textbook") or "").strip()
                for row in source_rows
                if str(row.get("citation_textbook") or "").strip()
            },
        })
        stage_cache = temp / "cache"
        shutil.copytree(cache_source, stage_cache)
        _rebase_cache_asset_paths(stage_cache, package_roots)
        textbook_target_root.parent.mkdir(parents=True, exist_ok=True)
        target_cache.parent.mkdir(parents=True, exist_ok=True)
        _replace_directory_atomically(stage_textbooks, textbook_target_root)
        try:
            _replace_directory_atomically(stage_cache, target_cache)
        except Exception:
            shutil.rmtree(textbook_target_root, ignore_errors=True)
            raise
    selected_paths = [str(textbook_target_root / Path(str(row["name"])).name) for row in source_rows]
    display_names = {
        path: str(row.get("citation_textbook") or "").strip()
        for path, row in zip(selected_paths, source_rows)
        if str(row.get("citation_textbook") or "").strip()
    }
    return {
        "ok": True,
        "installed": True,
        "message": "共享教材已重新安装并修复不完整本地版本。" if repairing_incomplete_install else "共享教材已下载、校验并安装到本机。",
        "library_id": library_id,
        "version": version,
        "title": str(manifest.get("title") or library_id),
        "cache_key": cache_key,
        "selected_textbooks": selected_paths,
        "textbook_display_names": display_names,
    }


def _fetch_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        try:
            payload = json.loads(_curl_fetch(url, timeout=timeout).decode("utf-8"))
        except Exception as curl_exc:
            raise ValueError(f"无法读取共享教材库：{exc}；curl 回退失败：{curl_exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"无法读取共享教材库：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("共享教材库返回了无效目录。")
    return payload


def _curl_fetch(url: str, *, timeout: int, output_path: Path | None = None) -> bytes:
    """Use the OS TLS stack when Python's SSL stack cannot connect to Tailscale Serve."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("系统未找到 curl")
    command = [
        curl,
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--connect-timeout",
        str(min(timeout, 30)),
        "--max-time",
        str(timeout),
        url,
    ]
    if output_path is not None:
        command[1:1] = ["--output", str(output_path)]
    try:
        result = subprocess.run(command, capture_output=output_path is None, check=False, timeout=timeout + 5)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("curl 连接超时") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip() if result.stderr else ""
        raise RuntimeError(detail or f"curl 退出码 {result.returncode}")
    return result.stdout if output_path is None else b""


def _download_remote_file(url: str, target: Path, timeout: int = 180) -> None:
    request = urllib.request.Request(url, headers={"Accept": "application/zip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        return
    except urllib.error.URLError as exc:
        target.unlink(missing_ok=True)
        try:
            _curl_fetch(url, timeout=timeout, output_path=target)
        except Exception as curl_exc:
            target.unlink(missing_ok=True)
            raise ValueError(f"下载共享教材失败：{exc}；curl 回退失败：{curl_exc}") from exc


def fetch_remote_shared_library_catalog(remote_url: str, *, root: Path | None = None) -> dict[str, Any]:
    source = _normalize_remote_url(remote_url or get_shared_library_settings(root).get("remote_url", ""))
    if not source:
        raise ValueError("请先填写共享教材库地址。")
    payload = _fetch_json(f"{source}/api/shared-textbook-library/catalog")
    if payload.get("schema_version") != SHARED_LIBRARY_SCHEMA_VERSION:
        raise ValueError("共享教材库版本不兼容。")
    return {"ok": True, "remote_url": source, "libraries": payload.get("libraries") or []}


def sync_shared_textbook_library(
    library_id: str,
    version: str,
    *,
    remote_url: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    safe_library_id = _safe_component(library_id, "教材标识")
    safe_version = _safe_component(version, "版本号")
    catalog = fetch_remote_shared_library_catalog(remote_url, root=root)
    source = str(catalog["remote_url"])
    library = next((item for item in catalog.get("libraries") or [] if isinstance(item, dict) and item.get("library_id") == safe_library_id), None)
    if library is None:
        raise ValueError("共享教材库中未找到所选教材。")
    release = next((item for item in library.get("versions") or [] if isinstance(item, dict) and item.get("version") == safe_version and item.get("status") == "ready"), None)
    if release is None:
        raise ValueError("共享教材库中未找到所选版本。")
    expected_sha256 = str(release.get("package_sha256") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("共享教材库版本缺少有效校验值。")
    with tempfile.NamedTemporaryFile(prefix="shared-textbook-download-", suffix=".zip", dir=CACHE_DIR, delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        download = f"{source}/api/shared-textbook-library/packages/{safe_library_id}/{safe_version}/download"
        _download_remote_file(download, temp_path)
        result = install_shared_textbook_package(temp_path, expected_sha256=expected_sha256)
    except urllib.error.URLError as exc:
        raise ValueError(f"下载共享教材失败：{exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
    return {**result, "remote_url": source}
