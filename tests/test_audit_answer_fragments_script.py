from __future__ import annotations

import json

from scripts.audit_answer_fragments import audit


def test_answer_fragment_audit_reports_malformed_json_without_crashing(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    path.write_text("{broken", encoding="utf-8")

    issues = audit(path)

    assert len(issues) == 1
    assert "could not be read" in issues[0]


def test_answer_fragment_audit_rejects_duplicate_question_ids(tmp_path) -> None:
    path = tmp_path / "answer_fragments.json"
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "section": "一、简答题",
        "number": "1",
        "answer": "答案",
        "evidence_ids": [],
        "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "解析"}]}],
        "formulas": [],
        "warnings": [],
    }
    path.write_text(json.dumps({"fragments": [fragment, fragment]}), encoding="utf-8")

    issues = audit(path)

    assert any("duplicate question_id q1" in issue for issue in issues)
