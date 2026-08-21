from __future__ import annotations

from app.answer_generation import evidence_selection_citation_segments
from app.evidence_selection import _program_selection


def test_missing_textbook_evidence_is_auto_labeled_without_review_gate() -> None:
    selection = _program_selection(
        {"question_id": "q1"},
        {"knowledge_points": ["相律"]},
        [],
    )

    point = selection["knowledge_points"][0]
    assert point["evidence_status"] == "unavailable"
    assert point["selected_evidence_ids"] == []
    assert point["no_suitable_evidence_reason"] == "未检索到候选教材依据。"

    segments = evidence_selection_citation_segments([], selection)
    assert segments[0]["highlight"] == "unconfirmed_evidence"
    assert "未确认到可用教材依据" in segments[0]["text"]
