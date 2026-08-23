from __future__ import annotations

import errno
import json
import os
import zipfile
from pathlib import Path

from app import desktop_word_save
from app import word_format_tasks
from app.desktop_word_save import DesktopWordSaveBridge


JOB_ID = "practice_word_aaaaaaaaaaaaaaaaaaaaaaaa"
FORMAT_TASK_ID = "word_format_20260824_120000_aaaaaaaa"


def _write_docx(path: Path, document_text: str = "fixture") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{document_text}</w:t></w:r></w:p></w:body></w:document>')


class FakeWindow:
    def __init__(self, selected: Path | None) -> None:
        self.selected = selected
        self.calls: list[dict] = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.calls.append({"dialog_type": dialog_type, **kwargs})
        return None if self.selected is None else (str(self.selected),)


def _bridge(tmp_path: Path, selected: Path | None, source: Path | None = None) -> tuple[DesktopWordSaveBridge, FakeWindow]:
    cache_root = tmp_path / "cache"
    cache_root.mkdir(exist_ok=True)
    source = source or (cache_root / "source.docx")
    if not source.exists():
        _write_docx(source)
    bridge = DesktopWordSaveBridge(
        save_dialog_type=30,
        export_resolver=lambda job_id: (source, "服务端题目.docx"),
        cache_root_provider=lambda: cache_root,
        registry_path=tmp_path / "registry.json",
    )
    window = FakeWindow(selected)
    bridge.bind_window(window)
    return bridge, window


def test_native_save_dialog_writes_and_verifies_valid_docx(tmp_path: Path) -> None:
    target = tmp_path / "用户选择.docx"
    bridge, window = _bridge(tmp_path, target)

    result = bridge.save_practice_word(JOB_ID, "../建议/题目?.docx")

    assert result["status"] == "saved"
    assert result["path"] == str(target)
    assert result["size_bytes"] == target.stat().st_size > 0
    assert len(result["sha256"]) == 64
    assert result["receipt_persisted"] is True
    assert window.calls[0]["dialog_type"] == 30
    assert window.calls[0]["save_filename"] == "题目_.docx"
    assert window.calls[0]["file_types"] == ("Word 文档 (*.docx)",)
    with zipfile.ZipFile(target) as archive:
        assert "word/document.xml" in archive.namelist()


def test_cancel_keeps_source_and_writes_nothing(tmp_path: Path) -> None:
    bridge, _window = _bridge(tmp_path, None)

    result = bridge.save_practice_word(JOB_ID, "取消.docx")

    assert result["status"] == "cancelled"
    assert list(tmp_path.glob("*.docx")) == []
    assert not (tmp_path / "registry.json").exists()


def test_existing_file_is_atomically_replaced_after_native_dialog_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "重名.docx"
    target.write_bytes(b"old")
    bridge, _window = _bridge(tmp_path, target)

    result = bridge.save_practice_word(JOB_ID, "重名.docx")

    assert result["status"] == "saved"
    assert target.read_bytes() != b"old"
    assert not list(tmp_path.glob(".重名.docx.*.tmp"))


def test_invalid_job_id_is_rejected_before_export_lookup(tmp_path: Path) -> None:
    looked_up = []
    bridge = DesktopWordSaveBridge(
        export_resolver=lambda job_id: looked_up.append(job_id),  # type: ignore[arg-type]
        cache_root_provider=lambda: tmp_path,
        registry_path=tmp_path / "registry.json",
    )
    bridge.bind_window(FakeWindow(tmp_path / "target.docx"))

    result = bridge.save_practice_word("../../secret", "target.docx")

    assert result == {"status": "error", "code": "invalid_job_id", "message": "Word 导出任务标识无效，请重新生成。"}
    assert looked_up == []


def test_source_must_resolve_inside_controlled_cache(tmp_path: Path) -> None:
    outside = tmp_path / "outside.docx"
    _write_docx(outside)
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    bridge = DesktopWordSaveBridge(
        export_resolver=lambda _job_id: (outside, "outside.docx"),
        cache_root_provider=lambda: cache_root,
        registry_path=tmp_path / "registry.json",
    )
    bridge.bind_window(FakeWindow(tmp_path / "target.docx"))

    result = bridge.save_practice_word(JOB_ID, "target.docx")

    assert result["status"] == "error"
    assert result["code"] == "invalid_source"
    assert not (tmp_path / "target.docx").exists()


def test_symlink_source_and_invalid_docx_are_rejected(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    real_source = cache_root / "real.docx"
    _write_docx(real_source)
    link_source = cache_root / "link.docx"
    link_source.symlink_to(real_source)
    bridge, _window = _bridge(tmp_path, tmp_path / "target.docx", source=link_source)
    assert bridge.save_practice_word(JOB_ID, "target.docx")["code"] == "invalid_source"

    invalid_source = cache_root / "invalid.docx"
    invalid_source.write_bytes(b"not a docx")
    invalid_bridge, _window = _bridge(tmp_path, tmp_path / "target.docx", source=invalid_source)
    invalid = invalid_bridge.save_practice_word(JOB_ID, "target.docx")
    assert invalid["status"] == "error"
    assert invalid["code"] == "invalid_docx"


def test_disk_full_returns_public_error_and_removes_temporary_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "disk-full.docx"
    bridge, _window = _bridge(tmp_path, target)
    original_replace = os.replace

    def fail_target_replace(source, destination):
        if Path(destination) == target:
            raise OSError(errno.ENOSPC, "secret disk details")
        return original_replace(source, destination)

    monkeypatch.setattr(desktop_word_save.os, "replace", fail_target_replace)
    result = bridge.save_practice_word(JOB_ID, "disk-full.docx")

    assert result["status"] == "error"
    assert result["code"] == "disk_full"
    assert "secret" not in result["message"]
    assert not target.exists()
    assert not list(tmp_path.glob(".disk-full.docx.*.tmp"))


def test_permission_error_keeps_existing_target_and_returns_retryable_message(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "protected.docx"
    target.write_bytes(b"existing user file")
    bridge, _window = _bridge(tmp_path, target)

    def deny_temporary_file(**_kwargs):
        raise PermissionError(errno.EACCES, "private ACL detail")

    monkeypatch.setattr(desktop_word_save.tempfile, "NamedTemporaryFile", deny_temporary_file)
    result = bridge.save_practice_word(JOB_ID, "protected.docx")

    assert result["status"] == "error"
    assert result["code"] == "permission_denied"
    assert "private ACL" not in result["message"]
    assert target.read_bytes() == b"existing user file"


def test_open_folder_uses_persisted_job_receipt_not_a_frontend_path(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "saved.docx"
    bridge, _window = _bridge(tmp_path, target)
    assert bridge.save_practice_word(JOB_ID, "saved.docx")["status"] == "saved"
    opened = []
    monkeypatch.setattr(desktop_word_save, "_open_directory", opened.append)

    restarted_bridge, _window = _bridge(tmp_path, tmp_path / "unused.docx")
    result = restarted_bridge.open_practice_word_folder(JOB_ID)

    assert result == {"status": "opened", "path": str(tmp_path)}
    assert opened == [tmp_path]
    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert registry["records"][JOB_ID]["path"] == str(target)


def test_word_format_native_save_returns_actual_path(tmp_path: Path, monkeypatch) -> None:
    tasks_root = tmp_path / "tasks"
    task_dir = tasks_root / FORMAT_TASK_ID
    task_dir.mkdir(parents=True)
    source = task_dir / "modified.docx"
    _write_docx(source, "formatted")
    target = tmp_path / "用户保存位置.docx"
    monkeypatch.setattr(word_format_tasks, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(
        word_format_tasks,
        "word_format_download_path",
        lambda task_id: (source, "讲义_格式已修改.docx") if task_id == FORMAT_TASK_ID else (_ for _ in ()).throw(FileNotFoundError()),
    )
    bridge = DesktopWordSaveBridge(registry_path=tmp_path / "registry.json")
    window = FakeWindow(target)
    bridge.bind_window(window)

    result = bridge.save_word_format(FORMAT_TASK_ID, "")

    assert result["status"] == "saved"
    assert result["task_id"] == FORMAT_TASK_ID
    assert result["path"] == str(target)
    assert result["filename"] == target.name
    assert result["receipt_persisted"] is True
    assert window.calls[0]["save_filename"] == "讲义_格式已修改.docx"
    assert target.read_bytes() == source.read_bytes()


def test_word_format_invalid_task_id_is_rejected_before_lookup(tmp_path: Path, monkeypatch) -> None:
    looked_up = []
    monkeypatch.setattr(word_format_tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(word_format_tasks, "word_format_download_path", lambda task_id: looked_up.append(task_id))
    bridge = DesktopWordSaveBridge(registry_path=tmp_path / "registry.json")
    bridge.bind_window(FakeWindow(tmp_path / "target.docx"))

    result = bridge.save_word_format("practice_word_aaaaaaaaaaaaaaaaaaaaaaaa", "target.docx")

    assert result["status"] == "error"
    assert result["code"] == "invalid_task_id"
    assert looked_up == []


def test_word_format_source_must_stay_in_its_own_task_directory(tmp_path: Path, monkeypatch) -> None:
    tasks_root = tmp_path / "tasks"
    (tasks_root / FORMAT_TASK_ID).mkdir(parents=True)
    other_task_dir = tasks_root / "word_format_20260824_120000_bbbbbbbb"
    other_task_dir.mkdir()
    outside_source = other_task_dir / "modified.docx"
    _write_docx(outside_source)
    monkeypatch.setattr(word_format_tasks, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(
        word_format_tasks,
        "word_format_download_path",
        lambda _task_id: (outside_source, "越界.docx"),
    )
    target = tmp_path / "target.docx"
    bridge = DesktopWordSaveBridge(registry_path=tmp_path / "registry.json")
    bridge.bind_window(FakeWindow(target))

    result = bridge.save_word_format(FORMAT_TASK_ID, "target.docx")

    assert result["status"] == "error"
    assert result["code"] == "invalid_source"
    assert not target.exists()
