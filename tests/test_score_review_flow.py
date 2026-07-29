from app.exam_structure_review import (
    apply_exam_structure_review_updates,
    build_exam_structure_review_request,
    validate_exam_structure_review_updates,
)
from app.prompts import build_answer_depth_profile
from app.question_scores import infer_suggested_score


def test_section_score_suggestions_cover_per_question_patterns():
    section = "六、 问答题(本题共32分， 第1、2小题各12分， 第3小题8分)"
    assert infer_suggested_score({"number": "1", "section_raw": section}) == 12
    assert infer_suggested_score({"number": "2", "section_raw": section}) == 12
    assert infer_suggested_score({"number": "3", "section_raw": section}) == 8
    assert infer_suggested_score({"number": "1", "section_raw": "五、计算题 (本题共15分)"}) == 15


def test_exam_structure_review_request_includes_score_confirmation_fields():
    request = build_exam_structure_review_request(
        "score_review_task",
        {
            "items": [
                {
                    "question_id": "q_score",
                    "major_number": "5",
                    "section": "五、计算题",
                    "section_raw": "五、计算题 (本题共15分)",
                    "number": "1",
                    "stem": "计算待测溶液 pH。",
                    "subquestions": [{"number": "1", "stem": "写出反应。", "score": 3}],
                }
            ]
        },
    )

    item = request["items"][0]
    assert item["score"] == "15"
    assert item["suggested_score"] == "15"
    assert item["score_reviewed"] is False
    assert item["subquestions"][0]["score"] == "3"
    assert item["subquestions"][0]["suggested_score"] == "3"


def test_confirmed_score_is_saved_and_used_for_depth():
    exam = {
        "items": [
            {
                "question_id": "q_confirmed_score",
                "major_number": "1",
                "section": "一、选择题",
                "section_raw": "一、选择题",
                "number": "1",
                "stem": "选择正确结论。(10分)",
            }
        ]
    }
    reviewed = apply_exam_structure_review_updates(
        exam,
        [{"question_id": "q_confirmed_score", "question_type": "选择题", "confirmed_score": "2"}],
    )
    question = reviewed["items"][0]
    assert question["score"] == 2
    assert question["confirmed_score"] == 2
    assert question["score_reviewed"] is True
    profile = build_answer_depth_profile(question)
    assert profile["score"] == 2
    assert profile["depth"] == "concise"


def test_review_update_requires_scores_before_confirming():
    issues = validate_exam_structure_review_updates(
        [
            {
                "question_id": "q_missing",
                "question_type": "简答题",
                "confirmed_score": "",
                "subquestions": [{"number": "1", "question_type": "计算题", "confirmed_score": ""}],
            }
        ]
    )

    assert "第1题 缺少确认分值" in issues
    assert "第1题小问1 缺少确认分值" in issues
