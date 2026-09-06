import json

import pytest

from app.exam_unit_delivery import preserve_exam_units
from app.unit_delivery import build_unit_package, read_manifest


def inputs(stage):
    items = [{"question_id": key, "number": str(number), "section": "名词解释",
              "question_type": "名词解释", "stem": "晶格常数"}
             for key, number in [("a", 7), ("b", 9)]]
    fragments = [{
        "schema_version": "answer_book.answer_fragment.v4", "question_id": item["question_id"],
        "number": item["number"], "section": item["section"],
        "answer": "晶格常数是描述晶胞几何尺寸和形状的参数。", "evidence_ids": ["e1"],
        "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "晶格常数描述晶胞的边长及轴间夹角，是表征晶体结构的基本参数。"}]}],
        "formulas": [], "warnings": [],
    } for item in items]
    path = stage / "answer_fragments.json"
    path.write_text(json.dumps({"provider": "configured", "fragments": fragments}))
    (stage / "answer_drafts.json").write_text(json.dumps({"drafts": fragments}))
    return {"items": items}, path


@pytest.mark.parametrize("profile", ["question_only", "textbook"])
def test_word_failure_is_local_and_success_survives_resume(tmp_path, monkeypatch, profile):
    import app.exam_unit_delivery as module

    exam, path = inputs(tmp_path)
    original = module.build_docx_from_fragments
    calls = []

    def build(source, target):
        qid = json.loads(source.read_text())["fragments"][0]["question_id"]
        calls.append(qid)
        if qid == "b":
            raise RuntimeError("injected document fault")
        return original(source, target)

    monkeypatch.setattr(module, "build_docx_from_fragments", build)
    arguments = dict(structured_exam=exam, fragments_json=path, selection_data={"analysis_profile": profile})
    manifest = preserve_exam_units(tmp_path, **arguments)
    assert manifest["available_count"] == 1, manifest
    assert manifest["missing"][0]["object_id"] == "b"
    package = build_unit_package(tmp_path / "unit_delivery", manifest["revision"])
    assert package.is_file()
    preserve_exam_units(tmp_path, **arguments)
    assert calls.count("a") == 1
    assert calls.count("b") == 2


def test_stop_does_not_erase_prior_committed_unit(tmp_path):
    exam, path = inputs(tmp_path)
    checks = 0

    def checkpoint():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("stop requested")

    with pytest.raises(RuntimeError, match="stop requested"):
        preserve_exam_units(tmp_path, structured_exam=exam, fragments_json=path,
                            selection_data={"analysis_profile": "question_only"}, checkpoint=checkpoint)
    manifest = read_manifest(tmp_path / "unit_delivery")
    assert manifest["available_count"] == 1, manifest
    assert build_unit_package(tmp_path / "unit_delivery", manifest["revision"]).is_file()


def test_retry_stopped_before_work_keeps_previous_snapshot(tmp_path):
    exam, path = inputs(tmp_path)
    arguments = dict(structured_exam=exam, fragments_json=path, selection_data={"analysis_profile": "question_only"})
    previous = preserve_exam_units(tmp_path, **arguments)

    def stop():
        raise RuntimeError("stop requested")

    with pytest.raises(RuntimeError, match="stop requested"):
        preserve_exam_units(tmp_path, **arguments, checkpoint=stop)
    assert read_manifest(tmp_path / "unit_delivery")["revision"] == previous["revision"]


def test_original_number_mismatch_blocks_only_affected_unit(tmp_path):
    exam, path = inputs(tmp_path)
    source = json.loads(path.read_text())
    source["fragments"][0]["number"] = "99"
    path.write_text(json.dumps(source))
    manifest = preserve_exam_units(tmp_path, structured_exam=exam, fragments_json=path,
                                   selection_data={"analysis_profile": "question_only"})
    assert manifest["available_count"] == 1
    assert manifest["missing"][0]["object_id"] == "a"
    assert "用户确认结构" in manifest["units"][0]["issues"][0]
