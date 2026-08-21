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
