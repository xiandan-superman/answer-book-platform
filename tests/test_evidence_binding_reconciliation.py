from __future__ import annotations

from app.answer_generation import reconcile_confirmed_evidence_binding


def test_reconcile_confirmed_evidence_replaces_stale_ids_and_citation_block() -> None:
    fragment = {
        "question_id": "q1",
        "evidence_ids": ["ev_old", "ev_rejected"],
        "blocks": [
            {"label": "教材依据", "segments": [{"type": "text", "text": "旧引用：课本-p1"}]},
            {"label": "解析", "segments": [{"type": "text", "text": "正文"}]},
        ],
    }
    evidence = [
        {"question_id": "q1", "evidence_id": "ev_new_1", "printed_page": "8"},
        {"question_id": "q1", "evidence_id": "ev_new_2", "printed_page": "9"},
    ]
    selection = {
        "question_id": "q1",
        "knowledge_points": [
            {"knowledge_point": "考查点甲", "selected_evidence_ids": ["ev_new_1"]},
            {"knowledge_point": "考查点乙", "selected_evidence_ids": ["ev_new_1", "ev_new_2"]},
        ],
    }

    changed = reconcile_confirmed_evidence_binding(fragment, evidence, selection)

    assert changed is True
    assert fragment["evidence_ids"] == ["ev_new_1", "ev_new_2"]
    evidence_block = next(block for block in fragment["blocks"] if block["label"] == "教材依据")
    citation = "".join(segment.get("text", "") for segment in evidence_block["segments"])
    assert "考查点甲：课本-p8" in citation
    assert "考查点乙：课本-p8-p9" in citation
    assert fragment["_meta"]["evidence_binding"]["bound_evidence_ids"] == ["ev_new_1", "ev_new_2"]


def test_reconcile_confirmed_evidence_removes_ids_when_selection_has_no_support() -> None:
    fragment = {
        "question_id": "q1",
        "evidence_ids": ["ev_rejected"],
        "blocks": [{"label": "教材依据", "segments": [{"type": "text", "text": "旧引用"}]}],
    }
    selection = {
        "question_id": "q1",
        "knowledge_points": [
            {
                "knowledge_point": "考查点",
                "selected_evidence_ids": [],
                "no_suitable_evidence_reason": "教材无直接表述。",
            }
        ],
    }

    reconcile_confirmed_evidence_binding(fragment, [], selection)

    assert fragment["evidence_ids"] == []
    evidence_block = next(block for block in fragment["blocks"] if block["label"] == "教材依据")
    assert evidence_block["segments"][0]["highlight"] == "unconfirmed_evidence"
