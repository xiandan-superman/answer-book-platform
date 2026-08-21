import json

from app.exam_audit import audit_exam_structure


def test_answer_below_cue_is_normal_inside_short_answer_section(tmp_path) -> None:
    output = tmp_path / "audit.json"
    audit_exam_structure(
        {
            "items": [
                {
                    "question_id": "q1",
                    "section": "四、简答题",
                    "stem": "观察单胞并回答下列问题：说明其点阵类型。",
                }
            ]
        },
        output,
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert not any("stem says 回答下列问题" in warning for warning in report["warnings"])


def test_per_item_total_score_blocks_silent_item_merging(tmp_path) -> None:
    output = tmp_path / "audit.json"
    issues = audit_exam_structure(
        {
            "items": [
                {
                    "question_id": "term_1",
                    "section": "一、名词解释",
                    "section_raw": "一、名词解释（5分/小题，共30分）",
                    "stem": "再结晶温度 2、相平衡条件 3、上坡扩散 4、空间点阵",
                },
                {
                    "question_id": "term_5",
                    "section": "一、名词解释",
                    "section_raw": "一、名词解释（5分/小题，共30分）",
                    "stem": "堆垛层错 6、临界分切应力",
                },
            ]
        },
        output,
    )

    assert any("implies 6 items" in issue and "extracted 2" in issue for issue in issues)
