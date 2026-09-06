import json
import zipfile

import pytest

from app.unit_delivery import (
    build_unit_package,
    dependency_units,
    publish_manifest,
    read_manifest,
    store_unit,
)


def test_dependencies_are_transitive_and_missing_members_block_whole_group():
    groups = dependency_units([
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "dependency_group_id": "shared"},
        {"id": "c", "dependency_group_id": "shared", "depends_on": ["absent"]},
        {"id": "d"},
    ], id_field="id")
    assert groups[0]["object_ids"] == ["a", "b", "c"]
    assert not groups[0]["dependency_complete"]
    assert groups[1]["dependency_complete"]


@pytest.mark.parametrize("items", [[{"id": "a"}, {"id": "a"}], [{}], [{"id": " a"}]])
def test_ambiguous_identity_fails_closed(items):
    with pytest.raises(ValueError):
        dependency_units(items, id_field="id")


def test_explicit_group_and_variant_group_both_constrain_partition():
    groups = dependency_units([
        {"id": "a", "dependency_group_id": "shared", "parent_plan_item_id": "p"},
        {"id": "b", "dependency_group_id": "other", "parent_plan_item_id": "p"},
        {"id": "c", "dependency_group_id": "shared"},
    ], id_field="id")
    assert len(groups) == 1
    assert groups[0]["object_ids"] == ["a", "b", "c"]


def test_pinned_package_is_immutable_and_lists_omissions(tmp_path):
    expected = [{"object_id": "a", "number": "7"}, {"object_id": "b", "number": "9"}]
    ready = store_unit(tmp_path, b"checked-word", revision="input-v1", object_ids=["a"], warnings=[])
    first = publish_manifest(tmp_path, expected=expected, units=[ready])
    package = build_unit_package(tmp_path, first["revision"])
    original = package.read_bytes()
    second = publish_manifest(tmp_path, expected=expected, units=[])
    assert read_manifest(tmp_path)["revision"] == second["revision"]
    assert build_unit_package(tmp_path, first["revision"]).read_bytes() == original
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("范围与缺失清单.json"))
        assert manifest["missing"] == [expected[1]]
        assert manifest["formal_set_acceptance"] is False
        assert archive.read("分题成果_001.docx") == b"checked-word"
        assert len([name for name in archive.namelist() if name.endswith(".docx")]) == 1


def test_corrupt_artifact_cannot_be_downloaded_even_with_cached_zip(tmp_path):
    ready = store_unit(tmp_path, b"checked", revision="v1", object_ids=["a"], warnings=[])
    manifest = publish_manifest(tmp_path, expected=[{"object_id": "a"}], units=[ready])
    build_unit_package(tmp_path, manifest["revision"])
    (tmp_path / "artifacts" / f"{ready['sha256']}.docx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="校验失败"):
        build_unit_package(tmp_path, manifest["revision"])


def test_corrupt_manifest_and_untrusted_revision_rejected(tmp_path):
    manifest = publish_manifest(tmp_path, expected=[], units=[])
    (tmp_path / "manifests" / f"{manifest['revision']}.json").write_text("{}")
    with pytest.raises(ValueError, match="校验失败"):
        read_manifest(tmp_path)
    with pytest.raises(ValueError, match="版本标识"):
        read_manifest(tmp_path, "../../escape")


@pytest.mark.parametrize("source_mode", ["exam", "knowledge"])
def test_practice_bad_question_does_not_erase_real_word(tmp_path, source_mode):
    from app.practice_unit_delivery import preserve_practice_units

    data = {
        "source_mode": source_mode,
        "exercises": [
            {"exercise_id": "good", "plan_item_id": "plan_good", "number": 7, "question_type": "简答题",
             "stem": "说明晶格常数的物理意义。", "generation_status": "completed"},
            {"exercise_id": "bad", "plan_item_id": "plan_bad", "number": 9, "stem": "失败题不应交付",
             "generation_status": "failed"},
        ],
    }
    manifest = preserve_practice_units(tmp_path, data)
    assert manifest["available_count"] == 1, manifest
    assert manifest["missing"] == [{"object_id": "bad", "number": "9"}]
    with zipfile.ZipFile(build_unit_package(tmp_path, manifest["revision"])) as package:
        import io

        with zipfile.ZipFile(io.BytesIO(package.read("分题成果_001.docx"))) as docx:
            xml = docx.read("word/document.xml").decode()
            assert "晶格常数" in xml
            assert "第 7 题" in xml
            assert "未包含原题号：9" in xml
            assert "失败题不应交付" not in xml


def test_incomplete_variant_group_is_not_split(tmp_path):
    from app.practice_unit_delivery import preserve_practice_units

    manifest = preserve_practice_units(tmp_path, {"exercises": [
        {"exercise_id": "a", "parent_plan_item_id": "p", "variant_count": 2,
         "stem": "说明晶格常数的物理意义。", "generation_status": "completed"},
    ]})
    assert manifest["available_count"] == 0
    assert "变式组" in manifest["units"][0]["issues"][0]


def test_practice_word_reuses_unchanged_units_but_not_changed_assets(tmp_path, monkeypatch):
    from app import practice_unit_delivery as module

    asset = tmp_path / "source.png"
    asset.write_bytes(b"source-v1")
    data = {"exercises": [
        {"exercise_id": key, "number": index, "stem": "说明晶格常数的物理意义。",
         "generation_status": "completed", "source_metadata": {"path": str(asset)} if key == "a" else {}}
        for index, key in enumerate(["a", "b"], 1)
    ]}
    original = module.build_practice_question_docx
    calls = []

    def build(scoped):
        calls.append(scoped["exercises"][0]["exercise_id"])
        return original(scoped)

    monkeypatch.setattr(module, "build_practice_question_docx", build)
    first = module.preserve_practice_units(tmp_path / "units", data)
    assert first["available_count"] == 2
    second = module.preserve_practice_units(tmp_path / "units", data)
    assert second["revision"] == first["revision"]
    assert calls == ["a", "b"]
    asset.write_bytes(b"source-v2")
    third = module.preserve_practice_units(tmp_path / "units", data)
    assert third["available_count"] == 2
    assert calls == ["a", "b", "a"]
