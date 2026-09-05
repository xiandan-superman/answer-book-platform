from app.exam_audit import audit_exam_structure
from app.exam_structure_review import (
    apply_exam_structure_review_updates,
    build_exam_structure_review_request,
    submit_exam_structure_review,
    validate_exam_structure_review_updates,
    wait_for_exam_structure_review,
)
from app.prompts import build_answer_depth_profile
from app.question_scores import infer_suggested_score


def test_section_score_suggestions_cover_per_question_patterns():
    section = "六、 问答题(本题共32分， 第1、2小题各12分， 第3小题8分)"
    assert infer_suggested_score({"number": "1", "section_raw": section}) == 12
    assert infer_suggested_score({"number": "2", "section_raw": section}) == 12
    assert infer_suggested_score({"number": "3", "section_raw": section}) == 8
    assert infer_suggested_score({"number": "1", "section_raw": "五、计算题 (本题共15分)"}) == 15


def test_grouped_major_question_uses_section_total_before_first_child_score():
    question = {
        "number": "9",
        "major_number": "9",
        "section_raw": "九、相图分析题（本题共14分）",
        "stem": "(1)说明转变。(2分)\n(2)计算组成。(8分)\n(3)分析组织。(4分)",
        "subquestions": [
            {"number": "1", "stem": "说明转变。(2分)"},
            {"number": "2", "stem": "计算组成。(8分)"},
            {"number": "3", "stem": "分析组织。(4分)"},
        ],
    }

    assert infer_suggested_score(question) == 14


def test_single_parent_uses_bare_parenthesized_section_total():
    question = {
        "number": "1",
        "major_number": "2",
        "section_item_count": 1,
        "section_raw": "二、简答题（20分）",
        "stem": "(1)说明结构。（10分）\n(2)计算距离。（10分）",
        "subquestions": [
            {"number": "1", "stem": "说明结构。（10分）"},
            {"number": "2", "stem": "计算距离。（10分）"},
        ],
    }

    assert infer_suggested_score(question) == 20


def test_bare_section_total_is_not_assigned_to_each_of_multiple_items():
    question = {
        "number": "1",
        "major_number": "1",
        "section_item_count": 2,
        "section_raw": "一、简答题（20分）",
        "stem": "说明概念。（10分）",
        "subquestions": [{"number": "1", "stem": "说明概念。（10分）", "synthetic_parent": True}],
    }

    assert infer_suggested_score(question) == 10


def test_exam_structure_audit_blocks_parent_child_score_mismatch(tmp_path):
    issues = audit_exam_structure(
        {
            "items": [{
                "question_id": "q_score_mismatch",
                "number": "9",
                "major_number": "9",
                "section": "九、计算题",
                "section_raw": "九、计算题（本题共14分）",
                "stem": "(1)第一问。(2分)\n(2)第二问。(4分)",
                "subquestions": [
                    {"number": "1", "stem": "第一问。(2分)"},
                    {"number": "2", "stem": "第二问。(4分)"},
                ],
            }],
        },
        tmp_path / "audit.json",
    )

    assert any("does not equal subquestion total" in issue for issue in issues)


def test_exam_structure_audit_does_not_treat_first_child_score_as_parent_total(tmp_path):
    issues = audit_exam_structure(
        {
            "items": [{
                "question_id": "q_children_only_scores",
                "number": "6",
                "major_number": "6",
                "section": "六、计算题",
                "section_raw": "六、计算题",
                "stem": "(1)说明条件。(3分)\n(2)完成计算。(6分)\n(3)分析结果。(6分)",
                "subquestions": [
                    {"number": "1", "stem": "说明条件。(3分)"},
                    {"number": "2", "stem": "完成计算。(6分)"},
                    {"number": "3", "stem": "分析结果。(6分)"},
                ],
            }],
        },
        tmp_path / "audit.json",
    )

    assert not any("does not equal subquestion total" in issue for issue in issues)


def test_structure_review_does_not_suggest_first_child_score_for_parent():
    request = build_exam_structure_review_request(
        "child_scores_only",
        {
            "items": [{
                "question_id": "q6",
                "number": "6",
                "section": "四、计算与综合分析题",
                "stem": (
                    "固态扩散，回答下列问题：\n"
                    "(1) 说明机制。(3分)\n"
                    "(2) 分析因素。(6分)\n"
                    "(3) 判断方向。(6分)"
                ),
                "subquestions": [
                    {"number": "1", "stem": "说明机制。(3分)"},
                    {"number": "2", "stem": "分析因素。(6分)"},
                    {"number": "3", "stem": "判断方向。(6分)"},
                ],
            }],
        },
    )

    parent = request["items"][0]
    assert parent["suggested_score"] == ""
    assert [row["suggested_score"] for row in parent["subquestions"]] == ["3", "6", "6"]


def test_exam_structure_audit_recovers_legacy_auto_confirmed_first_child_score(tmp_path):
    issues = audit_exam_structure(
        {
            "items": [{
                "question_id": "legacy_q6",
                "number": "6",
                "section": "四、计算题",
                "stem": "(1)说明条件。(3分)\n(2)完成计算。(6分)\n(3)分析结果。(6分)",
                "score": 3,
                "confirmed_score": 3,
                "score_reviewed": True,
                "subquestions": [
                    {"number": "1", "stem": "说明条件。(3分)", "score": 3},
                    {"number": "2", "stem": "完成计算。(6分)", "score": 6},
                    {"number": "3", "stem": "分析结果。(6分)", "score": 6},
                ],
            }],
        },
        tmp_path / "audit.json",
    )

    assert not any("does not equal subquestion total" in issue for issue in issues)


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


def test_structure_review_waits_for_user_and_persists_manual_confirmation(tmp_path, monkeypatch):
    from app import exam_structure_review, task_store
    from app.task_store import TaskRecord

    tasks = tmp_path / "tasks"
    tasks.mkdir()
    monkeypatch.setattr(task_store, "TASKS_DIR", tasks)
    task_store.task_dir("manual-review").mkdir()
    task_store.save_task(TaskRecord(
        task_id="manual-review",
        exam_path="exam.docx",
        textbooks_dir="textbooks",
        provider="test",
        model="test",
        status="running",
        created_at="2026-09-05 10:00:00",
        updated_at="2026-09-05 10:00:00",
    ))
    exam = {
        "items": [
            {
                "question_id": "q_auto",
                "major_number": "1",
                "section": "一、简答题",
                "section_raw": "一、简答题（每小题5分）",
                "number": "1",
                "stem": "说明基本原理。",
            }
        ]
    }
    output = tmp_path / "structured_exam.json"

    def confirm_after_wait(_seconds):
        request = exam_structure_review._read_json(
            exam_structure_review.exam_structure_request_path("manual-review")
        )
        updates = request["items"]
        updates[0]["confirmed_score"] = updates[0]["suggested_score"] or "5"
        submit_exam_structure_review("manual-review", updates)

    monkeypatch.setattr(exam_structure_review.time, "sleep", confirm_after_wait)
    reviewed = wait_for_exam_structure_review("manual-review", exam, tmp_path, output)

    assert reviewed["exam_structure_review_mode"] == "manual"
    assert reviewed["human_review_required"] is False
    assert reviewed["items"][0]["confirmed_question_type"] == "简答题"
    assert reviewed["items"][0]["confirmed_score"] == 5
    request = exam_structure_review._read_json(exam_structure_review.exam_structure_request_path("manual-review"))
    response = exam_structure_review._read_json(exam_structure_review.exam_structure_response_path("manual-review"))
    assert request["status"] == "confirmed"
    assert request["mode"] == "manual"
    assert response["decision"] == "confirm"
    assert task_store.load_task("manual-review").status == "running"
