from __future__ import annotations

import json

from app import textbook_index_cache
from tests.test_textbook_package import make_mineru_package


def test_damaged_textbook_cache_becomes_miss_and_is_rebuilt(tmp_path, monkeypatch) -> None:
    cache_root = tmp_path / "cache" / "textbook_indexes"
    package_cache = tmp_path / "cache" / "textbook_packages"
    source = tmp_path / "教材.zip"
    make_mineru_package(source)
    names = {str(source.resolve()): "测试教材"}
    monkeypatch.setattr(textbook_index_cache, "TEXTBOOK_INDEX_CACHE_DIR", cache_root)
    monkeypatch.setattr("app.textbook_package.PACKAGE_CACHE_DIR", package_cache)

    prepared = textbook_index_cache.prepare_textbook_index_cache([str(source)], names)
    cache_dir = cache_root / prepared["cache_key"]
    (cache_dir / "textbook_blocks.csv").write_text("broken\n", encoding="utf-8")

    damaged = textbook_index_cache.textbook_index_cache_status([str(source)], names)
    rebuilt = textbook_index_cache.prepare_textbook_index_cache([str(source)], names)

    assert damaged["indexed"] is False
    assert rebuilt["indexed"] is True
    assert rebuilt["cached"] is False
    assert textbook_index_cache.validated_textbook_index_cache(
        cache_dir,
        expected_key=prepared["cache_key"],
        expected_manifest=rebuilt["manifest"],
    ) is not None


def test_invalid_status_or_foreign_manifest_is_never_reported_as_reusable(tmp_path, monkeypatch) -> None:
    cache_root = tmp_path / "cache" / "textbook_indexes"
    package_cache = tmp_path / "cache" / "textbook_packages"
    source = tmp_path / "教材.zip"
    make_mineru_package(source)
    names = {str(source.resolve()): "测试教材"}
    monkeypatch.setattr(textbook_index_cache, "TEXTBOOK_INDEX_CACHE_DIR", cache_root)
    monkeypatch.setattr("app.textbook_package.PACKAGE_CACHE_DIR", package_cache)

    prepared = textbook_index_cache.prepare_textbook_index_cache([str(source)], names)
    cache_dir = cache_root / prepared["cache_key"]
    (cache_dir / "textbook_index_status.json").write_text("{not-json", encoding="utf-8")
    assert textbook_index_cache.textbook_index_cache_status([str(source)], names)["indexed"] is False

    textbook_index_cache.prepare_textbook_index_cache([str(source)], names)
    manifest_path = cache_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["name"] = "另一套教材.zip"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert textbook_index_cache.textbook_index_cache_status([str(source)], names)["indexed"] is False
