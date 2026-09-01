from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import practice_requirement_presets as presets


def _use_store(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    target = root / "config" / "practice_requirement_presets.json"
    monkeypatch.setattr(presets, "PRESETS_FILE", target)
    return target


def test_requirement_presets_are_saved_in_local_user_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = _use_store(monkeypatch, tmp_path)

    created = presets.update_practice_requirement_presets("create", text="  侧重跨知识点\n综合  ")

    assert created["storage"] == "local_user_data"
    assert created["items"][0]["text"] == "侧重跨知识点 综合"
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1
    assert target.parent == tmp_path / "config"


def test_requirement_presets_support_update_delete_and_duplicate_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _use_store(monkeypatch, tmp_path)
    first = presets.update_practice_requirement_presets("create", text="不超出材料范围")["items"][0]
    presets.update_practice_requirement_presets("create", text="增加计算推导")

    with pytest.raises(ValueError, match="已经保存"):
        presets.update_practice_requirement_presets("create", text="不超出材料范围")

    updated = presets.update_practice_requirement_presets("update", preset_id=first["id"], text="限定材料范围")
    assert [item["text"] for item in updated["items"]] == ["限定材料范围", "增加计算推导"]

    deleted = presets.update_practice_requirement_presets("delete", preset_id=first["id"])
    assert [item["text"] for item in deleted["items"]] == ["增加计算推导"]


def test_requirement_presets_reject_blank_and_overlong_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_store(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="不能为空"):
        presets.update_practice_requirement_presets("create", text="  \n ")
    with pytest.raises(ValueError, match="不能超过"):
        presets.update_practice_requirement_presets("create", text="长" * (presets.MAX_TEXT_LENGTH + 1))
