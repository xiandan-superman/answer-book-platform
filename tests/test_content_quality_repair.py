from __future__ import annotations

import json

from app.content_quality_audit import UNIT_RE
from app.content_quality_repair import repair_content_quality_locally


def test_percentage_is_recognized_as_a_unit() -> None:
    assert UNIT_RE.search("α相质量分数为73.3%")


def test_missing_calculation_note_is_derived_from_validated_contract(tmp_path) -> None:
    path = tmp_path / "fragments.json"
    path.write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "blocks": [],
                        "_draft": {"calculation_contract": {"partitions": [{"basis": "总质量"}]}},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audit = {
        "issues": [{"question_id": "q1", "code": "calculation_missing_mistake_notes"}],
        "warnings": [],
    }

    report = repair_content_quality_locally(path, audit)

    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]
    assert report["changed"] is True
    assert repaired["blocks"][0]["label"] == "易错点及注意事项"
    assert "计算基准" in repaired["blocks"][0]["segments"][0]["text"]
    assert repaired["_draft"]["mistake_notes"]


def test_internal_repair_provenance_is_removed_but_subject_note_is_preserved(tmp_path) -> None:
    path = tmp_path / "fragments.json"
    path.write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "blocks": [
                            {
                                "label": "易错点及注意事项",
                                "segments": [
                                    {
                                        "type": "text",
                                        "text": "原答案称塑性较低有误，已修正。比较时需同时考虑冷却速率和缺陷。",
                                    }
                                ],
                            }
                        ],
                        "_draft": {
                            "mistake_notes": ["原答案称塑性较低有误，已修正。比较时需同时考虑冷却速率和缺陷。"]
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audit = {
        "issues": [{"question_id": "q1", "code": "internal_repair_provenance_leak"}],
        "warnings": [],
    }

    report = repair_content_quality_locally(path, audit)

    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]
    note = repaired["blocks"][0]["segments"][0]["text"]
    assert report["changed"] is True
    assert note == "比较时需同时考虑冷却速率和缺陷。"
    assert repaired["_draft"]["mistake_notes"] == [note]


def test_internal_repair_provenance_is_removed_from_analysis_and_steps(tmp_path) -> None:
    path = tmp_path / "fragments.json"
    path.write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "answer": "结论正确。",
                        "blocks": [
                            {"label": "解析", "segments": [{"type": "text", "text": "机理说明。原答案方向有误，已修正。"}]},
                            {"label": "解题步骤", "segments": [{"type": "text", "text": "先比较数值。修复后再检查。"}]},
                        ],
                        "_draft": {"analysis": "机理说明。原答案方向有误，已修正。", "mistake_notes": []},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audit = {"issues": [{"question_id": "q1", "code": "internal_repair_provenance_leak"}], "warnings": []}

    repair_content_quality_locally(path, audit)

    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]
    assert repaired["blocks"][0]["segments"][0]["text"] == "机理说明。"
    assert repaired["blocks"][1]["segments"][0]["text"] == "先比较数值。"
    assert repaired["_draft"]["analysis"] == "机理说明。"


def test_missing_analysis_is_recovered_from_model_draft_without_invention(tmp_path) -> None:
    path = tmp_path / "fragments.json"
    path.write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "blocks": [
                            {"label": "解题步骤", "segments": [{"type": "text", "text": "代入计算。"}]}
                        ],
                        "_draft": {"analysis": "先明确边界条件，再建立能量平衡。"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audit = {"issues": [{"question_id": "q1", "code": "missing_analysis"}], "warnings": []}

    report = repair_content_quality_locally(path, audit)

    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]
    assert report["changed"] is True
    assert [block["label"] for block in repaired["blocks"]] == ["解析", "解题步骤"]
    assert repaired["blocks"][0]["segments"][0]["text"] == "先明确边界条件，再建立能量平衡。"


def test_missing_analysis_is_not_fabricated_when_draft_has_no_analysis(tmp_path) -> None:
    path = tmp_path / "fragments.json"
    path.write_text(
        json.dumps({"fragments": [{"question_id": "q1", "blocks": [], "_draft": {}}]}),
        encoding="utf-8",
    )
    audit = {"issues": [{"question_id": "q1", "code": "missing_analysis"}], "warnings": []}

    report = repair_content_quality_locally(path, audit)

    assert report["changed"] is False
    assert json.loads(path.read_text(encoding="utf-8"))["fragments"][0]["blocks"] == []


def test_unsupported_xrd_spacing_trend_is_removed_without_touching_question_source(tmp_path) -> None:
    path = tmp_path / "fragments.json"
    path.write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "subquestions": [{"stem": "请分析峰间距是否随角度变化。"}],
                        "blocks": [
                            {
                                "label": "解析",
                                "segments": [
                                    {
                                        "type": "text",
                                        "text": "标注各晶面。峰间距随2θ增大而逐渐增大，但",
                                    },
                                    {"type": "formula_ref", "formula_id": "f1"},
                                    {"type": "text", "text": "θ的相对次序可由N表示。"},
                                ],
                            }
                        ],
                        "_draft": {
                            "analysis_segments": [
                                {"text": "峰间距随2θ增大而逐渐增大，但sin²θ按N排列。"}
                            ]
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audit = {
        "issues": [{"question_id": "q1", "code": "xrd_unsupported_peak_spacing_trend"}],
        "warnings": [],
    }

    report = repair_content_quality_locally(path, audit)

    repaired = json.loads(path.read_text(encoding="utf-8"))["fragments"][0]
    assert report["changed"] is True
    assert repaired["subquestions"][0]["stem"] == "请分析峰间距是否随角度变化。"
    assert repaired["blocks"][0]["segments"][0]["text"] == "标注各晶面。"
    assert repaired["blocks"][0]["segments"][1] == {"type": "formula_ref", "formula_id": "f1"}
    assert "逐渐增大" not in repaired["_draft"]["analysis_segments"][0]["text"]
