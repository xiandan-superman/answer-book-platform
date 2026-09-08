from __future__ import annotations

from app.retrieval import EvidenceCandidate
from app.textbook_evidence_service import (
    build_concise_evidence_payload,
    publish_concise_evidence_artifacts,
    render_concise_evidence_markdown,
)


def _candidate(**updates) -> EvidenceCandidate:
    values = {
        "evidence_id": "ev_q1_01",
        "question_id": "q1",
        "textbook": "材料科学基础",
        "citation_textbook": "课本",
        "chapter_section": "二元相图",
        "source_file": "book.pdf",
        "pdf_page_idx": "160",
        "printed_page": "157",
        "score": 9.0,
        "evidence_text": "这段教材原文不得进入公开产物",
        "verified_page": True,
        "knowledge_point": "二元相图中的反应扩散与两相区判定",
    }
    values.update(updates)
    return EvidenceCandidate(**values)


def test_concise_payload_keeps_only_verified_pages_and_no_excerpt() -> None:
    structured = {"items": [{"question_id": "q1", "number": "1"}]}
    selection = {
        "selections": [
            {
                "question_id": "q1",
                "knowledge_points": [
                    {
                        "knowledge_point": "二元相图中的反应扩散与两相区判定",
                        "selected_evidence_ids": ["ev_q1_01", "ev_q1_02"],
                    }
                ],
            }
        ]
    }
    payload = build_concise_evidence_payload(
        structured,
        selection,
        [_candidate(), _candidate(evidence_id="ev_q1_02", printed_page="158", verified_page=False)],
    )
    serialized = str(payload)
    assert "157" in serialized
    assert "158" not in serialized
    assert "教材原文" not in serialized
    assert payload["unresolved"] == []


def test_unverified_only_point_remains_explicitly_unresolved() -> None:
    payload = build_concise_evidence_payload(
        {"items": [{"question_id": "q1", "number": "1"}]},
        {
            "selections": [
                {
                    "question_id": "q1",
                    "knowledge_points": [
                        {"knowledge_point": "扩散路径", "selected_evidence_ids": ["ev_q1_01"]}
                    ],
                }
            ]
        },
        [_candidate(verified_page=False)],
    )
    assert payload["questions"][0]["knowledge_points"] == []
    assert payload["unresolved"][0]["knowledge_point"] == "扩散路径"


def test_markdown_matches_evidence_only_contract() -> None:
    text = render_concise_evidence_markdown(
        "2009",
        {
            "questions": [
                {
                    "question_number": "1",
                    "knowledge_points": [
                        {
                            "knowledge_point": "扩散偶中相形成",
                            "citations": [
                                {"textbook": "课本", "printed_page": "157"},
                                {"textbook": "课本", "printed_page": "257"},
                            ],
                        }
                    ],
                }
            ]
        },
    )
    assert "1、教材依据：扩散偶中相形成：课本-p157、p257" in text
    assert "教材原文" not in text


def test_publish_concise_artifacts_writes_only_public_projection(tmp_path) -> None:
    result = publish_concise_evidence_artifacts(
        structured_exam={"items": [{"question_id": "q1", "number": "1"}]},
        selection_data={
            "selections": [
                {
                    "question_id": "q1",
                    "knowledge_points": [
                        {"knowledge_point": "扩散路径", "selected_evidence_ids": ["ev_q1_01"]}
                    ],
                }
            ]
        },
        candidates=[_candidate(knowledge_point="扩散路径")],
        stage_dir=tmp_path / "stages",
        output_dir=tmp_path / "output",
        label="2009",
    )
    assert result["status"] == "completed"
    markdown = (tmp_path / "output" / "2009真题教材依据.md").read_text(encoding="utf-8")
    payload = (tmp_path / "output" / "textbook_evidence.json").read_text(encoding="utf-8")
    assert "扩散路径：课本-p157" in markdown
    assert "教材原文" not in payload
