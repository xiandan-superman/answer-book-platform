import json

from app.exam_audit import audit_exam_structure


def test_resolved_source_number_collisions_are_reported_without_failing(tmp_path) -> None:
    output = tmp_path / "audit.json"
    issues = audit_exam_structure(
        {
            "items": [
                {
                    "question_id": "qa_s01_01_01",
                    "question_id_base": "qa_s01_01_01",
                    "question_id_occurrence": 1,
                    "question_id_collision_count": 2,
                    "display_number": "1（同号第1题，共2题）",
                    "number": "1",
                    "stem": "第一套卷中的第一题。",
                },
                {
                    "question_id": "qa_s01_01_01__r02",
                    "question_id_base": "qa_s01_01_01",
                    "question_id_occurrence": 2,
                    "question_id_collision_count": 2,
                    "display_number": "1（同号第2题，共2题）",
                    "number": "1",
                    "stem": "第二套卷中的第一题。",
                },
            ]
        },
        output,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert issues == []
    assert report["ok"] is True
    assert report["question_id_disambiguation"]["applied"] is True
    assert report["question_id_disambiguation"]["item_count"] == 2


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


def test_empty_question_is_blocked_before_model_planning_but_image_only_question_is_valid(tmp_path) -> None:
    output = tmp_path / "audit.json"
    issues = audit_exam_structure(
        {
            "items": [
                {"question_id": "empty", "stem": "", "image_refs": []},
                {"question_id": "image_only", "stem": "", "image_refs": ["reaction.png"]},
            ]
        },
        output,
    )

    assert any(issue.startswith("empty: question has no stem") for issue in issues)
    assert not any(issue.startswith("image_only:") for issue in issues)


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


def test_confirmed_question_score_is_not_replaced_by_section_total(tmp_path):
    from app.exam_audit import audit_exam_structure
    from app.question_scores import infer_explicit_parent_score

    item = {"question_id": "q1", "number": "1", "major_number": "2",
            "section_item_count": 2, "section": "简答题",
            "extracted_section_raw": "二、简答题（共2题，每题10分，共20分）",
            "stem": "回答下列问题。", "score_reviewed": True, "confirmed_score": 10,
            "subquestions": [{"stem": "问题甲", "score": 5}, {"stem": "问题乙", "score": 5}]}
    assert infer_explicit_parent_score(item) == 10
    assert not audit_exam_structure({"items": [item]}, tmp_path / "audit.json")
    item["subquestions"][1]["score"] = 4
    assert any("subquestion total" in issue for issue in audit_exam_structure({"items": [item]}, tmp_path / "audit.json"))
    item.pop("confirmed_score")
    item.pop("score_reviewed")
    assert infer_explicit_parent_score(item) == 10
    item["extracted_section_raw"] = "简答题（共20分）"
    assert infer_explicit_parent_score(item) is None
