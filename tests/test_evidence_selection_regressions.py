from __future__ import annotations

from app.evidence_selection import _candidate_payload, _merge_selection
from app.retrieval import EvidenceCandidate


def _candidate(evidence_id: str, point: str, score: float) -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id=evidence_id,
        question_id="q1",
        textbook="book",
        citation_textbook="book",
        chapter_section="chapter",
        source_file="book.pdf",
        pdf_page_idx=evidence_id,
        printed_page=evidence_id,
        score=score,
        evidence_text=f"{point}-{evidence_id}",
        verified_page=True,
        knowledge_point=point,
    )


def test_candidate_payload_round_robins_across_knowledge_points() -> None:
    candidates = [
        *[_candidate(f"a{i}", "A", 100 - i) for i in range(11)],
        *[_candidate(f"b{i}", "B", 80 - i) for i in range(3)],
        *[_candidate(f"c{i}", "C", 70 - i) for i in range(3)],
    ]

    payload = _candidate_payload(candidates, include_visual_assets=False, max_unique=12)

    points = [row["knowledge_point"] for row in payload]
    assert len(payload) == 12
    assert points[:3] == ["A", "B", "C"]
    assert {"A", "B", "C"}.issubset(points)


def test_empty_expansion_patch_cannot_delete_confirmed_evidence() -> None:
    base = {
        "question_id": "q1",
        "knowledge_points": [
            {
                "knowledge_point": "PET结构与命名",
                "selected_evidence_ids": ["ev12"],
                "evidence_status": "confirmed",
            }
        ],
    }
    patch = {
        "question_id": "q1",
        "knowledge_points": [
            {
                "knowledge_point": "PET结构与命名",
                "selected_evidence_ids": [],
                "evidence_status": "unavailable",
                "needs_expansion": True,
            }
        ],
    }

    merged = _merge_selection(base, patch)

    assert merged["knowledge_points"][0]["selected_evidence_ids"] == ["ev12"]
    assert merged["knowledge_points"][0]["evidence_status"] == "confirmed"
    assert merged["_meta"]["retained_confirmed_points"] == ["PET结构与命名"]
