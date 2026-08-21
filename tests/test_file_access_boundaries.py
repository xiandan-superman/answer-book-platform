from __future__ import annotations

import pytest

from app import library_files, server


def test_task_file_access_allows_own_file_and_rejects_other_task(monkeypatch, tmp_path) -> None:
    own_stage = tmp_path / "task-1" / "stage"
    own_output = tmp_path / "task-1" / "output"
    other = tmp_path / "task-2" / "answer.docx"
    own_stage.mkdir(parents=True)
    own_output.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    own = own_output / "answer.docx"
    own.write_bytes(b"own")
    other.write_bytes(b"other")
    monkeypatch.setattr(server, "_task_file_roots", lambda task_id: [own_stage.resolve(), own_output.resolve()])

    assert server._safe_task_file("task-1", str(own)) == own.resolve()
    with pytest.raises(FileNotFoundError, match="not inside"):
        server._safe_task_file("task-1", str(other))


def test_task_file_access_rejects_symlink_to_outside(monkeypatch, tmp_path) -> None:
    own_stage = tmp_path / "stage"
    own_output = tmp_path / "output"
    outside = tmp_path / "private.txt"
    own_stage.mkdir()
    own_output.mkdir()
    outside.write_text("private", encoding="utf-8")
    link = own_output / "result.txt"
    link.symlink_to(outside)
    monkeypatch.setattr(server, "_task_file_roots", lambda task_id: [own_stage.resolve(), own_output.resolve()])

    with pytest.raises(FileNotFoundError, match="not inside"):
        server._safe_task_file("task-1", str(link))


def test_library_delete_rejects_outside_file_and_symlink(monkeypatch, tmp_path) -> None:
    exams = tmp_path / "exams"
    textbooks = tmp_path / "textbooks"
    outside = tmp_path / "outside.docx"
    exams.mkdir()
    textbooks.mkdir()
    outside.write_bytes(b"keep")
    link = exams / "linked.docx"
    link.symlink_to(outside)
    monkeypatch.setattr(library_files, "EXAMS_DIR", exams)
    monkeypatch.setattr(library_files, "TEXTBOOKS_DIR", textbooks)
    monkeypatch.setattr(library_files, "ensure_project_dirs", lambda: None)

    with pytest.raises(ValueError, match="outside"):
        library_files.delete_library_file("exam", str(outside))
    with pytest.raises(ValueError, match="outside"):
        library_files.delete_library_file("exam", str(link))
    assert outside.exists()
