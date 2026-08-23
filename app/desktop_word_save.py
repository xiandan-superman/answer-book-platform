from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .paths import CACHE_DIR


_JOB_ID_PATTERN = re.compile(r"^practice_word_[a-zA-Z0-9_-]{8,96}$")
_WORD_FORMAT_TASK_ID_PATTERN = re.compile(r"^word_format_[a-z0-9_]{1,96}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_DOCX_REQUIRED_MEMBERS = {"[Content_Types].xml", "word/document.xml"}


class DesktopWordSaveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sanitize_docx_filename(value: str, fallback: str = "专项练习-题目.docx") -> str:
    candidate = unicodedata.normalize("NFKC", str(value or "")).replace("\\", "/").split("/")[-1]
    candidate = "".join("_" if ord(character) < 32 or character in '<>:"/\\|?*' else character for character in candidate)
    candidate = candidate.strip().rstrip(". ")
    if not candidate:
        candidate = fallback
    if not candidate.lower().endswith(".docx"):
        candidate = f"{candidate}.docx"
    stem = candidate[:-5].strip().rstrip(". ") or "专项练习-题目"
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem[:175]}.docx"


def _validate_docx(path: Path) -> tuple[int, str]:
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise DesktopWordSaveError("validation_failed", "保存后的文件无法读取，请重新选择位置。") from exc
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size <= 0:
        raise DesktopWordSaveError("validation_failed", "保存后的文件为空或不是普通文件，请重新保存。")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None or not _DOCX_REQUIRED_MEMBERS.issubset(archive.namelist()):
                raise DesktopWordSaveError("invalid_docx", "保存后的文件未通过 Word 完整性校验，请重新保存。")
    except (OSError, zipfile.BadZipFile) as exc:
        raise DesktopWordSaveError("invalid_docx", "保存后的文件不是有效的 Word 文档，请重新保存。") from exc
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return file_stat.st_size, digest.hexdigest()


def _open_directory(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DesktopWordSaveBridge:
    """Controlled pywebview bridge for server-owned Word export jobs."""

    def __init__(
        self,
        *,
        save_dialog_type: int = 30,
        export_resolver: Callable[[str], tuple[Path, str]] | None = None,
        cache_root_provider: Callable[[], Path] | None = None,
        registry_path: Path | None = None,
    ) -> None:
        if export_resolver is None or cache_root_provider is None:
            from . import practice_export_jobs

            export_resolver = export_resolver or (
                lambda job_id: practice_export_jobs.practice_export_download(job_id, refresh_from_disk=True)
            )
            cache_root_provider = cache_root_provider or (lambda: practice_export_jobs.EXPORT_CACHE_DIR)
        self._export_resolver = export_resolver
        self._cache_root_provider = cache_root_provider
        self._registry_path = registry_path or (CACHE_DIR / "desktop_word_saves.json")
        self._save_dialog_type = save_dialog_type
        self._window: Any = None
        self._lock = threading.RLock()
        self._session_records: dict[str, dict[str, Any]] = {}

    def bind_window(self, window: Any) -> None:
        self._window = window

    def _resolve_source(self, job_id: str) -> tuple[Path, str]:
        if not _JOB_ID_PATTERN.fullmatch(str(job_id or "")):
            raise DesktopWordSaveError("invalid_job_id", "Word 导出任务标识无效，请重新生成。")
        try:
            raw_source, server_filename = self._export_resolver(job_id)
        except (FileNotFoundError, ValueError) as exc:
            raise DesktopWordSaveError("export_unavailable", str(exc) or "Word 导出文件不可用，请重新生成。") from exc
        source = Path(raw_source)
        if source.is_symlink():
            raise DesktopWordSaveError("invalid_source", "Word 导出缓存来源无效，请重新生成。")
        try:
            resolved_source = source.resolve(strict=True)
            cache_root = Path(self._cache_root_provider()).resolve(strict=True)
            resolved_source.relative_to(cache_root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise DesktopWordSaveError("invalid_source", "Word 导出缓存来源不受信任，请重新生成。") from exc
        if not resolved_source.is_file():
            raise DesktopWordSaveError("invalid_source", "Word 导出缓存不是有效文件，请重新生成。")
        _validate_docx(resolved_source)
        return resolved_source, _sanitize_docx_filename(server_filename)

    def _resolve_word_format_source(self, task_id: str) -> tuple[Path, str]:
        if not _WORD_FORMAT_TASK_ID_PATTERN.fullmatch(str(task_id or "")):
            raise DesktopWordSaveError("invalid_task_id", "Word 格式审查任务标识无效，请重新打开任务。")
        from . import word_format_tasks

        try:
            raw_source, server_filename = word_format_tasks.word_format_download_path(task_id)
        except (FileNotFoundError, ValueError) as exc:
            raise DesktopWordSaveError(
                "format_output_unavailable",
                str(exc) or "修改后的 Word 文件不可用，请重新运行格式审查。",
            ) from exc

        source = Path(raw_source)
        tasks_root = Path(word_format_tasks.TASKS_DIR)
        expected_task_dir = tasks_root / task_id
        if source.is_symlink() or expected_task_dir.is_symlink():
            raise DesktopWordSaveError("invalid_source", "Word 格式审查文件来源无效，请重新运行任务。")
        try:
            resolved_tasks_root = tasks_root.resolve(strict=True)
            resolved_task_dir = expected_task_dir.resolve(strict=True)
            resolved_task_dir.relative_to(resolved_tasks_root)
            resolved_source = source.resolve(strict=True)
            resolved_source.relative_to(resolved_task_dir)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise DesktopWordSaveError(
                "invalid_source",
                "Word 格式审查文件来源不受信任，请重新运行任务。",
            ) from exc
        if not resolved_source.is_file():
            raise DesktopWordSaveError("invalid_source", "Word 格式审查文件不是有效文件，请重新运行任务。")
        _validate_docx(resolved_source)
        return resolved_source, _sanitize_docx_filename(server_filename, "格式已修改.docx")

    def _choose_target(self, filename: str) -> Path | None:
        if self._window is None:
            raise DesktopWordSaveError("desktop_unavailable", "桌面保存窗口尚未就绪，请稍后重试。")
        selected = self._window.create_file_dialog(
            self._save_dialog_type,
            directory=str(Path.home()),
            save_filename=filename,
            file_types=("Word 文档 (*.docx)",),
        )
        if not selected:
            return None
        raw_target = selected if isinstance(selected, str) else selected[0]
        target = Path(str(raw_target)).expanduser()
        if not target.name.lower().endswith(".docx"):
            target = target.with_name(f"{target.name}.docx")
        if not target.parent.is_dir():
            raise DesktopWordSaveError("invalid_target", "所选文件夹不可用，请重新选择保存位置。")
        if target.exists() and (target.is_dir() or target.is_symlink()):
            raise DesktopWordSaveError("invalid_target", "所选目标不是可覆盖的普通文件，请更换文件名。")
        return target.resolve(strict=False)

    def _copy_atomically(self, source: Path, target: Path) -> tuple[int, str]:
        temporary_path: Path | None = None
        try:
            with source.open("rb") as source_stream, tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            source_size, source_hash = _validate_docx(source)
            temporary_size, temporary_hash = _validate_docx(temporary_path)
            if temporary_size != source_size or temporary_hash != source_hash:
                raise DesktopWordSaveError("validation_failed", "保存内容校验不一致，请重新选择位置。")
            os.replace(temporary_path, target)
            temporary_path = None
            target_size, target_hash = _validate_docx(target)
            if target_size != source_size or target_hash != source_hash:
                raise DesktopWordSaveError("validation_failed", "保存后的文件校验不一致，请重新保存。")
            return target_size, target_hash
        except DesktopWordSaveError:
            raise
        except PermissionError as exc:
            raise DesktopWordSaveError("permission_denied", "没有权限写入所选位置，请选择其他文件夹。") from exc
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                message = "磁盘空间不足，Word 未保存；请释放空间或选择其他磁盘。"
                code = "disk_full"
            elif exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
                message = "没有权限写入所选位置，请选择其他文件夹。"
                code = "permission_denied"
            else:
                message = "写入 Word 时发生磁盘错误，未确认保存成功；请重新选择位置。"
                code = "write_failed"
            raise DesktopWordSaveError(code, message) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, dict):
            return {}
        return {
            str(job_id): record
            for job_id, record in records.items()
            if (_JOB_ID_PATTERN.fullmatch(str(job_id)) or _WORD_FORMAT_TASK_ID_PATTERN.fullmatch(str(job_id)))
            and isinstance(record, dict)
        }

    def _record_save(self, job_id: str, result: dict[str, Any]) -> bool:
        self._session_records[job_id] = dict(result)
        temporary = self._registry_path.with_suffix(".json.tmp")
        try:
            records = self._load_registry()
            records[job_id] = dict(result)
            if len(records) > 100:
                ordered = sorted(records.items(), key=lambda item: str(item[1].get("saved_at") or ""), reverse=True)
                records = dict(ordered[:100])
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"schema_version": 1, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self._registry_path)
            return True
        except (OSError, TypeError, ValueError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def save_practice_word(self, job_id: str, suggested_filename: str = "") -> dict[str, Any]:
        with self._lock:
            try:
                source, server_filename = self._resolve_source(str(job_id or ""))
                filename = _sanitize_docx_filename(suggested_filename, server_filename)
                target = self._choose_target(filename)
                if target is None:
                    return {"status": "cancelled", "message": "已取消保存。Word 仍保留，可点击“重新保存”。"}
                size_bytes, sha256 = self._copy_atomically(source, target)
                result = {
                    "status": "saved",
                    "job_id": job_id,
                    "filename": target.name,
                    "path": str(target),
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "saved_at": _now(),
                }
                result["receipt_persisted"] = self._record_save(job_id, result)
                return result
            except DesktopWordSaveError as exc:
                return {"status": "error", "code": exc.code, "message": exc.message}
            except Exception:
                return {
                    "status": "error",
                    "code": "save_failed",
                    "message": "Word 保存未完成，未确认任何文件已写入；请重新选择位置。",
                }

    def save_word_format(self, task_id: str, suggested_filename: str = "") -> dict[str, Any]:
        with self._lock:
            try:
                source, server_filename = self._resolve_word_format_source(str(task_id or ""))
                filename = _sanitize_docx_filename(suggested_filename, server_filename)
                target = self._choose_target(filename)
                if target is None:
                    return {"status": "cancelled", "message": "已取消保存。修改后的 Word 仍保留，可点击“重新保存”。"}
                size_bytes, sha256 = self._copy_atomically(source, target)
                result = {
                    "status": "saved",
                    "task_id": task_id,
                    "job_id": task_id,
                    "filename": target.name,
                    "path": str(target),
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "saved_at": _now(),
                }
                result["receipt_persisted"] = self._record_save(task_id, result)
                return result
            except DesktopWordSaveError as exc:
                return {"status": "error", "code": exc.code, "message": exc.message}
            except Exception:
                return {
                    "status": "error",
                    "code": "save_failed",
                    "message": "Word 保存未完成，未确认任何文件已写入；请重新选择位置。",
                }

    def open_practice_word_folder(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if not _JOB_ID_PATTERN.fullmatch(str(job_id or "")):
                return {"status": "error", "code": "invalid_job_id", "message": "Word 导出任务标识无效。"}
            record = self._session_records.get(job_id) or self._load_registry().get(job_id)
            if not isinstance(record, dict):
                return {"status": "error", "code": "save_receipt_missing", "message": "未找到这次保存的位置，请重新保存。"}
            saved_path = Path(str(record.get("path") or ""))
            if not saved_path.is_file() or not saved_path.parent.is_dir():
                return {"status": "error", "code": "saved_file_missing", "message": "原保存文件已移动或删除，请重新保存。"}
            try:
                _open_directory(saved_path.parent)
            except OSError:
                return {"status": "error", "code": "open_folder_failed", "message": "无法打开所在文件夹，请按页面显示的路径查找。"}
            return {"status": "opened", "path": str(saved_path.parent)}
