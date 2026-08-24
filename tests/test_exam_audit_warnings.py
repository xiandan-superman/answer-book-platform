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


def test_mixed_section_unanimous_short_answer_children_do_not_warn(tmp_path) -> None:
    output = tmp_path / "audit.json"
    audit_exam_structure(
        {
            "items": [
                {
                    "question_id": "q1",
                    "section": "四、计算题",
                    "section_raw": "四、计算题",
                    "question_type": "计算题",
                    "stem": "固态扩散，回答下列问题：",
                    "subquestions": [
                        {"number": "1", "question_type": "简答题", "stem": "说明扩散机制。"},
                        {"number": "2", "question_type": "简答题", "stem": "讨论影响因素。"},
                    ],
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


def test_source_coverage_audits_inline_numbered_terms_individually(tmp_path) -> None:
    output = tmp_path / "audit.json"
    issues = audit_exam_structure(
        {
            "source_paragraphs": ["一、名词解释（每小题2分）", "1、扩散第一定律 2、位错 3、二次再结晶"],
            "items": [
                {"question_id": "term_1", "section": "一、名词解释", "stem": "扩散第一定律"},
                {"question_id": "term_2", "section": "一、名词解释", "stem": "位错"},
                {"question_id": "term_3", "section": "一、名词解释", "stem": "二次再结晶"},
            ],
        },
        output,
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert not any("source paragraph not covered" in issue for issue in issues)
    assert report["source_coverage"]["item_like_count"] == 3
    assert report["source_coverage"]["covered_item_like_count"] == 3
