from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import create_task


def test_prepare_cli_textbooks_discovers_and_indexes_exact_files(tmp_path, monkeypatch) -> None:
    first = tmp_path / "book-1.zip"
    second = tmp_path / "book-2.md"
    first.write_bytes(b"zip")
    second.write_text("text", encoding="utf-8")
    captured: list[str] = []

    def prepare(selected):
        captured.extend(selected)
        return {"indexed": True, "page_map_ok": True}

    monkeypatch.setattr(create_task, "prepare_textbook_index_cache", prepare)

    root, selected, status = create_task.prepare_cli_textbooks(str(tmp_path))

    assert root == str(tmp_path.resolve())
    assert selected == [str(first.resolve()), str(second.resolve())]
    assert captured == selected
    assert status["indexed"] is True


def test_prepare_cli_textbooks_fails_before_task_creation_when_empty(tmp_path) -> None:
    with pytest.raises(ValueError, match="No supported textbook files"):
        create_task.prepare_cli_textbooks(str(tmp_path))


def test_bind_cli_textbooks_persists_pipeline_contract(monkeypatch, tmp_path) -> None:
    record = SimpleNamespace(
        textbooks_dir="legacy",
        selected_textbooks=None,
        textbook_display_names=None,
    )
    saved: list[object] = []
    monkeypatch.setattr(create_task, "save_task", saved.append)

    result = create_task.bind_cli_textbooks(record, str(tmp_path), ["a.zip"])

    assert result is record
    assert record.textbooks_dir == str(tmp_path)
    assert record.selected_textbooks == ["a.zip"]
    assert record.textbook_display_names == {}
    assert saved == [record]
