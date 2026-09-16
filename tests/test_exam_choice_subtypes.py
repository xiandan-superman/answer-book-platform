from __future__ import annotations

import json

from app.answer_generation import build_answer_batch_prompt
from app.content_quality_audit import audit_content_quality
from app.exam_extract import section_kind
from app.exam_structure_review import (
    apply_exam_structure_review_updates,
    build_exam_structure_review_request,
    validate_exam_structure_review_updates,
)
from app.prompts import build_answer_draft_prompt
from app.question_types import (
    infer_question_type,
    is_choice_question,
    normalize_question_type,
    question_kind,
)


def test_choice_subtypes_are_normalized_inferred_and_kept_in_choice_family() -> None:
    assert normalize_question_type("单项选择题") == "单选题"
    assert normalize_question_type("多项选择") == "多选题"
    assert infer_question_type({"question_type": "选择题", "stem": "以下有两个或两个以上正确选项。"}) == "多选题"
    assert question_kind({"question_type": "单选题"}) == "choice"
    assert is_choice_question({"question_type": "多选题"}) is True


def test_exam_section_extract_recognizes_single_and_multiple_choice_titles() -> None:
    assert section_kind("一、单项选择题", ["只有一个正确答案"])[0] == "单选题"
    assert section_kind("二、多选题", ["选出所有正确项"])[0] == "多选题"


def test_structure_review_requires_legacy_choice_to_be_split() -> None:
    request = build_exam_structure_review_request(
        "choice-review",
        {"items": [{"question_id": "q1", "number": "1", "question_type": "选择题", "stem": "选择正确结论。"}]},
    )
    assert "单选题" in request["question_types"]
    assert "多选题" in request["question_types"]
    assert "选择题" not in request["question_types"]
    issues = validate_exam_structure_review_updates(
        [{"question_id": "q1", "question_type": "选择题", "confirmed_score": "2"}]
    )
    assert any("请确认为单选题或多选题" in issue for issue in issues)


def test_structure_review_persists_confirmed_choice_subtype() -> None:
    reviewed = apply_exam_structure_review_updates(
        {"items": [{"question_id": "q1", "major_number": "1", "number": "1", "question_type": "选择题", "stem": "选择正确项。"}]},
        [{"question_id": "q1", "question_type": "多选题", "confirmed_score": "4"}],
    )
    item = reviewed["items"][0]
    assert item["question_type"] == "多选题"
    assert item["confirmed_question_type"] == "多选题"
    assert item["section"] == "一、多选题"


def test_answer_prompt_contains_choice_cardinality_rules() -> None:
    single_messages = build_answer_draft_prompt(
        {"question_id": "q1", "question_type": "单选题", "stem": "选择正确项。"},
        [],
        include_textbook_evidence=False,
    )
    multi_messages = build_answer_draft_prompt(
        {"question_id": "q2", "question_type": "多选题", "stem": "选择所有正确项。"},
        [],
        include_textbook_evidence=False,
    )
    single_payload = json.loads(single_messages[-1]["content"])
    multi_payload = json.loads(multi_messages[-1]["content"])
    assert any("exactly one correct option" in rule for rule in single_payload["hard_rules"])
    assert any("at least two distinct" in rule for rule in multi_payload["hard_rules"])


def test_batch_prompt_keeps_rules_for_both_choice_subtypes() -> None:
    messages = build_answer_batch_prompt(
        [
            {
                "question": {"question_id": "q1", "question_type": "单选题", "stem": "选出唯一正确项。"},
                "evidence": [],
                "include_textbook_evidence": False,
            },
            {
                "question": {"question_id": "q2", "question_type": "多选题", "stem": "选出所有正确项。"},
                "evidence": [],
                "include_textbook_evidence": False,
            },
        ]
    )
    payload = json.loads(messages[-1]["content"])
    assert any("exactly one correct option" in rule for rule in payload["hard_rules"])
    assert any("at least two distinct" in rule for rule in payload["hard_rules"])


def _choice_quality_report(question_type: str, answer: str) -> dict:
    question = {"question_id": "q1", "number": "1", "question_type": question_type, "stem": "选择正确项。"}
    fragment = {
        "question_id": "q1",
        "answer": answer,
        "answer_summary": answer,
        "evidence_ids": [],
        "blocks": [
            {"label": "解析", "segments": [{"type": "text", "text": "逐项核对题干条件和各选项后得出结论。"}]},
            {"label": "选项分析", "segments": [{"type": "text", "text": "A、B、C、D 均已分析。"}]},
        ],
        "formulas": [],
    }
    draft = {
        "question_id": "q1",
        "answer": answer,
        "analysis": "逐项核对题干条件和各选项后得出结论。",
        "option_analysis": {"A": "分析", "B": "分析", "C": "分析", "D": "分析"},
    }
    return audit_content_quality({"items": [question]}, {"fragments": [fragment]}, {"drafts": [draft]})


def test_quality_gate_rejects_wrong_choice_answer_count() -> None:
    single_codes = {item["code"] for item in _choice_quality_report("单选题", "A、C")["issues"]}
    multi_codes = {item["code"] for item in _choice_quality_report("多选题", "A")["issues"]}
    assert "single_choice_answer_count_invalid" in single_codes
    assert "multiple_choice_answer_count_invalid" in multi_codes


def test_quality_gate_accepts_valid_choice_answer_count() -> None:
    single_codes = {item["code"] for item in _choice_quality_report("单选题", "B")["issues"]}
    multi_codes = {item["code"] for item in _choice_quality_report("多选题", "A、C")["issues"]}
    assert "single_choice_answer_count_invalid" not in single_codes
    assert "multiple_choice_answer_count_invalid" not in multi_codes
