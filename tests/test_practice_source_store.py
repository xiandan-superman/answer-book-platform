from __future__ import annotations

import base64

from app import practice_inputs, practice_source_store


def _upload(name: str, content: bytes, mime: str = "text/plain") -> dict:
    encoded = base64.b64encode(content).decode("ascii")
    return {"name": name, "type": mime, "size": len(content), "data_url": f"data:{mime};base64,{encoded}"}


def test_inline_source_is_replaced_with_durable_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_source_store, "OBJECT_ROOT", tmp_path / "objects")
    monkeypatch.setattr(practice_source_store, "CACHE_ROOT", tmp_path / "cache")
    payload = practice_source_store.persist_practice_source_files({"source_files": [_upload("材料.txt", b"alpha beta")]})

    source = payload["source_files"][0]
    assert source["resource_id"].startswith("psrc_")
    assert "data_url" not in source
    assert practice_source_store.load_practice_source_file(source) == b"alpha beta"


def test_extracted_source_is_reused_from_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_source_store, "OBJECT_ROOT", tmp_path / "objects")
    monkeypatch.setattr(practice_source_store, "CACHE_ROOT", tmp_path / "cache")
    payload = practice_source_store.persist_practice_source_files({"source_files": [_upload("材料.txt", "知识材料".encode())]})

    first = practice_inputs.parse_practice_sources(payload)
    monkeypatch.setattr(practice_inputs, "_decode_file", lambda _item: (_ for _ in ()).throw(AssertionError("should use cache")))
    second = practice_inputs.parse_practice_sources(payload)

    assert second == first
    assert "知识材料" in second["text"]
