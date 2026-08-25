from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .paths import CONFIG_DIR, DATA_ROOT, LOCAL_CONFIG_DIR, PROJECT_ROOT
from .version import get_app_version

UPDATE_SOURCE_SCHEMA = "answer_book.update_source.v1"
UPDATE_MANIFEST_SCHEMA = "answer_book.update_manifest.v1"
DEFAULT_MANIFEST_ASSET = "update-manifest.json"
UPDATE_CACHE_SECONDS = 600
MAX_UPDATE_BYTES = 2 * 1024 * 1024 * 1024
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_RELEASE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STATUS_CACHE: dict[str, Any] = {}
_STATUS_CACHE_LOCK = threading.Lock()
_UPDATE_PROGRESS: dict[str, Any] = {}
_UPDATE_PROGRESS_LOCK = threading.RLock()
_UPDATE_THREAD: threading.Thread | None = None


class UpdateError(RuntimeError):
    pass


def installation_kind() -> str:
    if getattr(sys, "frozen", False):
        return "desktop_app"
    if (PROJECT_ROOT / ".git").exists():
        return "source_checkout"
    return "source_archive"


def platform_asset_key(*, system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).strip().lower()
    machine = (machine or platform.machine()).strip().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    if system == "darwin":
        return f"macos-{architecture}"
    if system == "windows":
        return f"windows-{architecture}"
    if system == "linux":
        return f"linux-{architecture}"
    return f"{system or 'unknown'}-{architecture}"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _repository_from_git_remote() -> str:
    if not (PROJECT_ROOT / ".git").exists():
        return ""
    for remote in ("github", "origin"):
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        url = result.stdout.strip()
        match = re.search(r"github\.com[/:]([^/\s]+/[^/\s]+?)(?:\.git)?$", url)
        if match:
            return match.group(1).removesuffix(".git")
    return ""


def load_update_source() -> dict[str, Any]:
    bundled = _read_json(CONFIG_DIR / "update.json")
    local = _read_json(LOCAL_CONFIG_DIR / "update.json")
    release = _read_json(PROJECT_ROOT / "RELEASE_MANIFEST.json").get("update") or {}
    raw: dict[str, Any] = {}
    for source in (bundled, release if isinstance(release, dict) else {}, local):
        raw.update(source)
    repository = str(os.environ.get("ANSWER_BOOK_UPDATE_REPOSITORY") or raw.get("repository") or _repository_from_git_remote()).strip()
    if repository and not _REPOSITORY_PATTERN.fullmatch(repository):
        repository = ""
    channel = str(os.environ.get("ANSWER_BOOK_UPDATE_CHANNEL") or raw.get("channel") or "stable").strip().lower()
    if channel not in {"stable", "beta"}:
        channel = "stable"
    manifest_url = str(os.environ.get("ANSWER_BOOK_UPDATE_MANIFEST_URL") or raw.get("manifest_url") or "").strip()
    if not manifest_url and repository:
        manifest_url = f"https://raw.githubusercontent.com/{repository}/main/update-{channel}.json"
    if manifest_url and not manifest_url.startswith("https://"):
        manifest_url = ""
    return {
        "schema_version": UPDATE_SOURCE_SCHEMA,
        "enabled": bool(repository),
        "repository": repository,
        "channel": channel,
        "manifest_url": manifest_url,
        "manifest_asset": str(raw.get("manifest_asset") or DEFAULT_MANIFEST_ASSET).strip(),
        "source_remote": str(raw.get("source_remote") or "origin").strip(),
        "source_branch": str(raw.get("source_branch") or "main").strip(),
    }


def _version_parts(value: str) -> tuple[tuple[int, ...], tuple[tuple[int, str], ...]]:
    text = str(value or "").strip().lower().lstrip("v").split("+", 1)[0]
    main, separator, prerelease = text.partition("-")
    numeric = tuple(int(item) for item in re.findall(r"\d+", main)) or (0,)
    pre_parts: list[tuple[int, str]] = []
    if separator:
        for item in re.split(r"[._-]+", prerelease):
            if item.isdigit():
                pre_parts.append((0, f"{int(item):020d}"))
            elif item:
                pre_parts.append((1, item))
    return numeric, tuple(pre_parts)


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_numeric, candidate_pre = _version_parts(candidate)
    current_numeric, current_pre = _version_parts(current)
    width = max(len(candidate_numeric), len(current_numeric))
    candidate_numeric += (0,) * (width - len(candidate_numeric))
    current_numeric += (0,) * (width - len(current_numeric))
    if candidate_numeric != current_numeric:
        return candidate_numeric > current_numeric
    if not candidate_pre and current_pre:
        return True
    if candidate_pre and not current_pre:
        return False
    return candidate_pre > current_pre


def _github_json(url: str, *, timeout: int = 20) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Answer-Book-Platform-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("更新仓库或发布版本不存在。") from exc
        raise UpdateError(f"检查更新失败：GitHub HTTP {exc.code}。") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise UpdateError(f"检查更新失败：{str(exc)[:180]}") from exc


def _select_release(releases: Any, channel: str) -> dict[str, Any]:
    if not isinstance(releases, list):
        raise UpdateError("GitHub 发布信息格式无效。")
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if channel == "stable" and release.get("prerelease"):
            continue
        return release
    raise UpdateError("当前更新频道暂无可用版本。")


def _release_assets(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(asset.get("name") or ""): asset
        for asset in release.get("assets") or []
        if isinstance(asset, dict) and str(asset.get("name") or "").strip()
    }


def _release_manifest(release: dict[str, Any], manifest_asset: str) -> dict[str, Any]:
    asset = _release_assets(release).get(manifest_asset)
    if not asset:
        raise UpdateError(f"发布版缺少 {manifest_asset}，为避免下载错误文件，本次不更新。")
    url = str(asset.get("browser_download_url") or "")
    manifest = _github_json(url)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != UPDATE_MANIFEST_SCHEMA:
        raise UpdateError("发布版更新清单无效。")
    return manifest


def _manifest_feed(source: dict[str, Any]) -> dict[str, Any]:
    url = str(source.get("manifest_url") or "").strip()
    if not url.startswith("https://"):
        raise UpdateError("更新清单地址无效。")
    manifest = _github_json(url)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != UPDATE_MANIFEST_SCHEMA:
        raise UpdateError("更新清单格式无效。")
    return manifest


def _release_from_manifest(source: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    repository = str(source.get("repository") or "").strip()
    version = str(manifest.get("version") or "").strip()
    tag_name = str(manifest.get("release_tag") or (f"v{version}" if version else "")).strip()
    if not _REPOSITORY_PATTERN.fullmatch(repository) or not _RELEASE_TAG_PATTERN.fullmatch(tag_name):
        raise UpdateError("更新清单中的仓库或发布标签无效。")
    encoded_tag = urllib.parse.quote(tag_name, safe="")
    assets: list[dict[str, Any]] = []
    for entry in (manifest.get("platforms") or {}).values():
        if not isinstance(entry, dict):
            continue
        raw_name = str(entry.get("asset_name") or "").strip()
        asset_name = Path(raw_name).name
        if not asset_name or asset_name != raw_name:
            continue
        assets.append(
            {
                "name": asset_name,
                "size": int(entry.get("size_bytes") or 0),
                "browser_download_url": (
                    f"https://github.com/{repository}/releases/download/{encoded_tag}/"
                    f"{urllib.parse.quote(asset_name, safe='')}"
                ),
            }
        )
    return {
        "tag_name": tag_name,
        "name": str(manifest.get("release_name") or version or tag_name),
        "html_url": f"https://github.com/{repository}/releases/tag/{encoded_tag}",
        "published_at": str(manifest.get("published_at") or ""),
        "body": str(manifest.get("notes") or ""),
        "assets": assets,
    }


def _asset_for_installation(
    release: dict[str, Any],
    manifest: dict[str, Any],
    kind: str,
) -> dict[str, Any] | None:
    if kind == "source_checkout":
        return None
    key = "source" if kind == "source_archive" else platform_asset_key()
    platform_entry = (manifest.get("platforms") or {}).get(key)
    if not isinstance(platform_entry, dict):
        return None
    asset_name = Path(str(platform_entry.get("asset_name") or "")).name
    release_asset = _release_assets(release).get(asset_name)
    sha256 = str(platform_entry.get("sha256") or "").strip().lower()
    if not release_asset or not _SHA256_PATTERN.fullmatch(sha256):
        return None
    return {
        "platform": key,
        "name": asset_name,
        "size_bytes": int(platform_entry.get("size_bytes") or release_asset.get("size") or 0),
        "sha256": sha256,
        "download_url": str(release_asset.get("browser_download_url") or ""),
        "dependency_fingerprint": str(platform_entry.get("dependency_fingerprint") or "").strip().lower(),
    }


def _current_dependency_fingerprint() -> str:
    from .dependency_profiles import runtime_dependency_fingerprint

    return runtime_dependency_fingerprint(PROJECT_ROOT, sys.version_info[:2], platform_name=sys.platform)


def _build_update_status(source: dict[str, Any]) -> dict[str, Any]:
    current = get_app_version()
    kind = installation_kind()
    base = {
        "ok": True,
        "enabled": bool(source.get("enabled")),
        "current_version": current,
        "installation_kind": kind,
        "platform": platform_asset_key(),
        "repository": source.get("repository", ""),
        "channel": source.get("channel", "stable"),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "update_available": False,
    }
    if not source.get("enabled"):
        return {**base, "message": "更新源将在 GitHub 首次发布时自动写入。"}
    manifest = _manifest_feed(source)
    release = _release_from_manifest(source, manifest)
    latest = str(manifest.get("version") or str(release.get("tag_name") or "").lstrip("v")).strip()
    if not latest:
        raise UpdateError("发布版没有版本号。")
    available = is_newer_version(latest, current)
    asset = _asset_for_installation(release, manifest, kind)
    action = (
        "pull_source"
        if kind == "source_checkout"
        else ("replace_source" if kind == "source_archive" else "download_installer")
    )
    source_entry = (manifest.get("platforms") or {}).get("source")
    remote_dependency_fingerprint = ""
    if isinstance(source_entry, dict):
        from .dependency_profiles import dependency_profile_key

        profile_fingerprints = source_entry.get("dependency_fingerprints")
        if isinstance(profile_fingerprints, dict):
            remote_dependency_fingerprint = str(
                profile_fingerprints.get(dependency_profile_key(sys.version_info[:2], platform_name=sys.platform)) or ""
            ).strip().lower()
        if not remote_dependency_fingerprint:
            remote_dependency_fingerprint = str(source_entry.get("dependency_fingerprint") or "").strip().lower()
    dependency_update_required = bool(
        remote_dependency_fingerprint
        and remote_dependency_fingerprint != _current_dependency_fingerprint()
    )
    if available and kind != "source_checkout" and not asset:
        return {
            **base,
            "latest_version": latest,
            "release_name": str(release.get("name") or release.get("tag_name") or latest),
            "release_page": str(release.get("html_url") or ""),
            "release_notes": str(manifest.get("notes") or release.get("body") or "")[:4000],
            "published_at": str(release.get("published_at") or ""),
            "message": "发布版已存在，但没有适用于当前系统且通过校验的安装包。",
            "update_available": False,
            "release_incomplete": True,
        }
    return {
        **base,
        "latest_version": latest,
        "release_tag": str(manifest.get("release_tag") or release.get("tag_name") or f"v{latest}"),
        "release_name": str(release.get("name") or release.get("tag_name") or latest),
        "release_page": str(release.get("html_url") or ""),
        "release_notes": str(manifest.get("notes") or release.get("body") or "")[:4000],
        "published_at": str(release.get("published_at") or ""),
        "update_available": available,
        "action": action if available else "none",
        "asset": asset,
        "dependency_update_required": dependency_update_required,
        "message": f"发现新版本 {latest}。" if available else "当前已是最新版本。",
    }


def check_for_updates(*, refresh: bool = False) -> dict[str, Any]:
    source = load_update_source()
    cache_key = json.dumps(source, ensure_ascii=False, sort_keys=True)
    now = time.time()
    with _STATUS_CACHE_LOCK:
        cached = _STATUS_CACHE.get(cache_key)
        if not refresh and isinstance(cached, dict) and now - float(cached.get("_cached_at") or 0) < UPDATE_CACHE_SECONDS:
            return {key: value for key, value in cached.items() if key != "_cached_at"}
    status = _build_update_status(source)
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE.clear()
        _STATUS_CACHE[cache_key] = {**status, "_cached_at": now}
    return status


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_asset(
    status: dict[str, Any],
    progress: Callable[..., Any] | None = None,
) -> Path:
    raw_asset = status.get("asset")
    asset: dict[str, Any] = raw_asset if isinstance(raw_asset, dict) else {}
    name = Path(str(asset.get("name") or "")).name
    expected_sha256 = str(asset.get("sha256") or "").lower()
    expected_size = int(asset.get("size_bytes") or 0)
    url = str(asset.get("download_url") or "")
    if not name or not _SHA256_PATTERN.fullmatch(expected_sha256) or not url.startswith("https://github.com/"):
        raise UpdateError("更新包信息不完整，已停止下载。")
    if expected_size < 0 or expected_size > MAX_UPDATE_BYTES:
        raise UpdateError("更新包大小超出安全限制。")
    target_dir = DATA_ROOT / "runtime" / "updates" / str(status.get("latest_version") or "unknown")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    if target.exists() and _sha256(target) == expected_sha256:
        if progress:
            progress("verifying", 78, "已找到校验通过的更新包，正在准备安装。", downloaded_bytes=expected_size, total_bytes=expected_size)
        return target
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Answer-Book-Platform-Updater"})
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPDATE_BYTES:
                    raise UpdateError("更新包超出安全限制。")
                stream.write(chunk)
                if progress:
                    ratio = min(1.0, written / expected_size) if expected_size else 0.0
                    progress(
                        "downloading",
                        12 + int(ratio * 58) if expected_size else 35,
                        "正在下载更新包，请保持程序运行。",
                        downloaded_bytes=written,
                        total_bytes=expected_size,
                    )
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    if expected_size and written != expected_size:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"更新包大小校验失败：应为 {expected_size}，实际 {written}。")
    if progress:
        progress("verifying", 74, "下载完成，正在校验更新包完整性。", downloaded_bytes=written, total_bytes=expected_size or written)
    actual_sha256 = _sha256(partial)
    if actual_sha256 != expected_sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("更新包 SHA256 校验失败，已删除未验证文件。")
    partial.replace(target)
    return target


def _open_update_file(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("os.startfile unavailable")
            startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise UpdateError(f"更新包已下载，但无法自动打开：{path}") from exc


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise UpdateError(f"源码更新失败：{str(detail)[:300]}") from exc


def _pull_source_update(source: dict[str, Any], release_tag: str) -> dict[str, Any]:
    if installation_kind() != "source_checkout":
        raise UpdateError("当前不是 Git 源码安装。")
    dirty = _git("status", "--porcelain", "--untracked-files=no", timeout=10).stdout.strip()
    if dirty:
        raise UpdateError("程序源码有未保存修改，为避免覆盖已停止更新。")
    remote = str(source.get("source_remote") or "origin")
    branch = str(source.get("source_branch") or "main")
    tag = str(release_tag or "").strip()
    if not _RELEASE_TAG_PATTERN.fullmatch(tag):
        raise UpdateError("源码更新标签无效。")
    current_branch = _git("branch", "--show-current", timeout=10).stdout.strip()
    if current_branch != branch:
        raise UpdateError(f"当前分支为 {current_branch or '游离状态'}，更新源要求分支 {branch}，未自动切换。")
    before = _git("rev-parse", "HEAD", timeout=10).stdout.strip()
    _git("fetch", "--prune", remote, f"refs/tags/{tag}:refs/tags/{tag}", timeout=180)
    try:
        _git("merge-base", "--is-ancestor", "HEAD", tag, timeout=10)
    except UpdateError as exc:
        raise UpdateError("本地与正式版本标签已分叉，不能自动快进更新。") from exc
    _git("merge", "--ff-only", tag, timeout=180)
    after = _git("rev-parse", "HEAD", timeout=10).stdout.strip()
    return {
        "ok": True,
        "action": "source_updated",
        "changed": before != after,
        "before_revision": before[:12],
        "after_revision": after[:12],
        "restart_required": before != after,
        "message": "更新已拉取，请重启程序使用新版本。" if before != after else "当前源码已是最新版本。",
    }


def _schedule_supervised_restart() -> bool:
    if os.environ.get("ANSWER_BOOK_LAUNCHED_BY_SUPERVISOR") != "1":
        return False
    timer = threading.Timer(1.5, lambda: os._exit(75))
    timer.daemon = True
    timer.start()
    return True


def _stage_source_archive_update(
    status: dict[str, Any],
    progress: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    target = _download_asset(status, progress)
    if progress:
        progress("preparing", 86, "更新包校验通过，正在准备安全替换和重启。")
    runtime_dir = DATA_ROOT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "pending-source-update.json"
    temporary = plan_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({
        "schema_version": "answer_book.pending_source_update.v1",
        "archive": str(target),
        "current_version": get_app_version(),
        "version": status.get("latest_version"),
        "dependency_update_required": bool(status.get("dependency_update_required")),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(plan_path)
    automatic = _schedule_supervised_restart()
    return {
        "ok": True,
        "action": "source_update_staged",
        "changed": True,
        "restart_required": True,
        "automatic_restart": automatic,
        "dependency_update_required": bool(status.get("dependency_update_required")),
        "latest_version": status.get("latest_version"),
        "message": (
            "源码更新包已校验，程序将自动更新并重启。"
            if automatic
            else "源码更新包已校验。请关闭程序并重新双击启动文件完成更新。"
        ),
    }


def apply_update(progress: Callable[..., Any] | None = None) -> dict[str, Any]:
    if progress:
        progress("checking", 5, "正在确认最新正式版本和安装方式。")
    status = check_for_updates(refresh=True)
    if not status.get("update_available"):
        if progress:
            progress("completed", 100, status.get("message") or "当前已是最新版本。")
        return {
            "ok": True,
            "changed": False,
            "latest_version": status.get("latest_version") or status.get("current_version"),
            "restart_required": False,
            "message": status.get("message") or "当前已是最新版本。",
        }
    source = load_update_source()
    if status.get("action") == "pull_source":
        if progress:
            progress("installing", 35, "正在安全拉取正式版本标签。")
        release_tag = str(status.get("release_tag") or f"v{status.get('latest_version') or ''}")
        result = _pull_source_update(source, release_tag)
        result["latest_version"] = status.get("latest_version")
        if progress:
            progress("preparing", 88, "源码已完成快进校验，正在准备重启。")
        if result.get("restart_required"):
            automatic = _schedule_supervised_restart()
            result["automatic_restart"] = automatic
            if automatic:
                result["message"] = "源码已更新，程序将自动重启。"
        return result
    if status.get("action") == "replace_source":
        return _stage_source_archive_update(status, progress)
    target = _download_asset(status, progress)
    if progress:
        progress("preparing", 88, "更新包校验通过，正在打开安装程序。")
    _open_update_file(target)
    return {
        "ok": True,
        "action": "installer_opened",
        "changed": True,
        "download_path": str(target),
        "latest_version": status.get("latest_version"),
        "restart_required": True,
        "message": "更新包已验证并打开。请完成安装后重启程序；API Key、教材、任务和输出不会被覆盖。",
    }


def _update_progress_path() -> Path:
    return DATA_ROOT / "runtime" / "update-progress.json"


def _persist_update_progress(value: dict[str, Any]) -> None:
    path = _update_progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _set_update_progress(
    status: str,
    percent: int,
    message: str,
    **details: Any,
) -> dict[str, Any]:
    with _UPDATE_PROGRESS_LOCK:
        current = dict(_UPDATE_PROGRESS)
        current.update({
            "schema_version": "answer_book.update_progress.v1",
            "ok": status != "failed",
            "operation_id": current.get("operation_id") or f"update_{int(time.time())}_{os.getpid()}",
            "status": status,
            "percent": max(0, min(100, int(percent))),
            "message": str(message or "正在处理更新。")[:500],
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            **{key: value for key, value in details.items() if value is not None},
        })
        if not current.get("started_at"):
            current["started_at"] = current["updated_at"]
        _UPDATE_PROGRESS.clear()
        _UPDATE_PROGRESS.update(current)
        _persist_update_progress(current)
        return dict(current)


def update_progress() -> dict[str, Any]:
    with _UPDATE_PROGRESS_LOCK:
        if not _UPDATE_PROGRESS:
            _UPDATE_PROGRESS.update(_read_json(_update_progress_path()))
        current = dict(_UPDATE_PROGRESS)
    if not current:
        return {
            "ok": True,
            "status": "idle",
            "percent": 0,
            "message": "尚未开始更新。",
        }
    target_version = str(current.get("latest_version") or "")
    if target_version and current.get("status") in {
        "restarting", "awaiting_restart", "extracting", "backing_up",
        "installing", "verifying_install", "dependencies", "starting",
    }:
        if not is_newer_version(target_version, get_app_version()):
            return _set_update_progress(
                "completed",
                100,
                f"已更新到 {get_app_version()}。",
                latest_version=target_version,
                restart_required=False,
            )
    return current


def _run_update_operation() -> None:
    try:
        result = apply_update(_set_update_progress)
        latest_version = str(result.get("latest_version") or get_app_version())
        details = {
            "latest_version": latest_version,
            "restart_required": bool(result.get("restart_required")),
            "automatic_restart": bool(result.get("automatic_restart")),
            "result": result,
        }
        if result.get("restart_required") and result.get("automatic_restart"):
            _set_update_progress("restarting", 96, "更新已准备完成，程序正在自动重启。", **details)
        elif result.get("restart_required"):
            _set_update_progress("awaiting_restart", 100, str(result.get("message") or "请重启程序完成更新。"), **details)
        else:
            _set_update_progress("completed", 100, str(result.get("message") or "更新检查已完成。"), **details)
    except Exception as exc:
        _set_update_progress("failed", 100, str(exc)[:500], error=str(exc)[:500])


def start_update() -> dict[str, Any]:
    global _UPDATE_THREAD
    with _UPDATE_PROGRESS_LOCK:
        if _UPDATE_THREAD is not None and _UPDATE_THREAD.is_alive():
            return {**update_progress(), "accepted": False, "already_running": True}
        _UPDATE_PROGRESS.clear()
        initial = _set_update_progress(
            "checking",
            2,
            "已开始更新，正在确认正式版本。",
            current_version=get_app_version(),
        )
        _UPDATE_THREAD = threading.Thread(
            target=_run_update_operation,
            name="platform-update-worker",
            daemon=True,
        )
        _UPDATE_THREAD.start()
        return {**initial, "accepted": True, "already_running": False}
