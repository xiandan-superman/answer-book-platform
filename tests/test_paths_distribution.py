from __future__ import annotations

from pathlib import Path

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
