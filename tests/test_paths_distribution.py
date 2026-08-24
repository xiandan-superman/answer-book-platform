from __future__ import annotations

from pathlib import Path

from app import paths
from app.paths import _data_root, _default_user_data_root


def test_packaged_macos_data_root_is_outside_the_app_bundle() -> None:
    assert _default_user_data_root(platform_name="darwin", home=Path("/Users/tester")) == Path(
        "/Users/tester/Library/Application Support/Answer Book Platform"
    )


def test_packaged_linux_data_root_uses_user_data_location() -> None:
    assert _default_user_data_root(platform_name="linux", home=Path("/home/tester")) == Path(
        "/home/tester/.local/share/answer-book-platform"
    )


def test_source_checkout_data_root_is_also_outside_replaceable_code(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ANSWER_BOOK_DATA_DIR", raising=False)
    assert _data_root(tmp_path) == _default_user_data_root().resolve()


def test_legacy_migration_never_overwrites_existing_user_data(monkeypatch, tmp_path) -> None:
    project = tmp_path / "program"
    data = tmp_path / "user-data"
    (project / "textbooks").mkdir(parents=True)
    (project / "textbooks" / "existing.pdf").write_text("legacy", encoding="utf-8")
    (project / "textbooks" / "legacy-only.pdf").write_text("copy me", encoding="utf-8")
    (project / "config").mkdir()
    (project / "config" / "api_keys.json").write_text('{"key":"legacy"}', encoding="utf-8")
    (data / "textbooks").mkdir(parents=True)
    (data / "textbooks" / "existing.pdf").write_text("user current", encoding="utf-8")
    (data / "config").mkdir()
    (data / "config" / "api_keys.json").write_text('{"key":"user current"}', encoding="utf-8")

    monkeypatch.setattr(paths, "PROJECT_ROOT", project)
    monkeypatch.setattr(paths, "DATA_ROOT", data)
    monkeypatch.delenv("ANSWER_BOOK_DATA_DIR", raising=False)
    monkeypatch.delattr(paths.sys, "frozen", raising=False)

    paths._migrate_legacy_source_data()

    assert (data / "textbooks" / "existing.pdf").read_text(encoding="utf-8") == "user current"
    assert (data / "textbooks" / "legacy-only.pdf").read_text(encoding="utf-8") == "copy me"
    assert (data / "config" / "api_keys.json").read_text(encoding="utf-8") == '{"key":"user current"}'
    assert (data / "runtime" / "source_data_migration_v1.done").is_file()
