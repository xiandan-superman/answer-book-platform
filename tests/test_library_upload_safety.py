from __future__ import annotations

import io

import pytest

from app import library_files


@pytest.fixture
def library_roots(monkeypatch: pytest.MonkeyPatch, tmp_path):
    exams = tmp_path / "exams"
    textbooks = tmp_path / "textbooks"
    monkeypatch.setattr(library_files, "EXAMS_DIR", exams)
    monkeypatch.setattr(library_files, "TEXTBOOKS_DIR", textbooks)
    monkeypatch.setattr(
        library_files,
        "ensure_project_dirs",
        lambda: (exams.mkdir(exist_ok=True), textbooks.mkdir(exist_ok=True)),
    )
    return exams, textbooks


def test_same_name_different_content_is_renamed_without_overwrite(library_roots) -> None:
    exams, _ = library_roots
    first = library_files.save_library_upload("exam", "真题.docx", b"first")
    second = library_files.save_library_upload("exam", "真题.docx", b"second")

    assert first["name"] == "真题.docx"
    assert second["name"] == "真题 (2).docx"
    assert second["renamed"] is True
    assert (exams / "真题.docx").read_bytes() == b"first"
    assert (exams / "真题 (2).docx").read_bytes() == b"second"


def test_same_name_same_content_reuses_existing_file(library_roots) -> None:
    exams, _ = library_roots
    library_files.save_library_upload("exam", "真题.docx", b"same")
    result = library_files.save_library_upload("exam", "真题.docx", b"same")

    assert result["reused"] is True
    assert [path.name for path in exams.iterdir()] == ["真题.docx"]


def test_stream_upload_rejects_declared_oversize_before_reading(library_roots) -> None:
    class MustNotRead(io.BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("oversized request body must not be read")

    with pytest.raises(ValueError, match="文件过大"):
        library_files.save_library_upload_stream(
            "exam",
            "真题.docx",
            MustNotRead(b""),
            library_files.EXAM_UPLOAD_MAX_BYTES + 1,
        )


def test_stream_upload_rejects_truncated_body_and_leaves_no_file(library_roots) -> None:
    exams, _ = library_roots
    with pytest.raises(ValueError, match="内容不完整"):
        library_files.save_library_upload_stream("exam", "真题.docx", io.BytesIO(b"short"), 10)

    assert list(exams.iterdir()) == []


def test_upload_filename_cannot_escape_library(library_roots) -> None:
    exams, _ = library_roots
    result = library_files.save_library_upload("exam", "../../真题.docx", b"content")

    assert result["path"] == str((exams / "真题.docx").resolve())


def test_windows_reserved_filename_characters_are_sanitized(library_roots) -> None:
    result = library_files.save_library_upload("exam", "题目:解答?.docx", b"content")
    assert ":" not in result["name"]
    assert "?" not in result["name"]
    assert result["path"].endswith(".docx")


def test_packaged_development_exams_are_not_shown_in_user_library(library_roots) -> None:
    exams, _ = library_roots
    exams.mkdir(parents=True, exist_ok=True)
    (exams / "demo_物理化学真题.docx").write_bytes(b"fixture")
    (exams / "用户真题.docx").write_bytes(b"user")

    listed = library_files.scan_library_files()["exams"]

    assert [item["name"] for item in listed] == ["用户真题.docx"]


@pytest.mark.parametrize("filename", ["CON.docx", "nul.any.docx", "COM1.docx", "lpt9.docx"])
def test_windows_reserved_device_names_are_sanitized(library_roots, filename: str) -> None:
    result = library_files.save_library_upload("exam", filename, b"content")
    assert result["name"].startswith("_")


@pytest.mark.parametrize("filename", ["notes.md", "notes.txt"])
def test_textbook_upload_rejects_plain_text_formats(library_roots, filename: str) -> None:
    with pytest.raises(ValueError, match="Unsupported file type"):
        library_files.save_library_upload("textbook", filename, b"content")
