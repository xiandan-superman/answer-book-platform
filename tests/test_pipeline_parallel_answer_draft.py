from __future__ import annotations

from app.pipeline import _parallel_answer_draft_eligible


def _eligible(exam: dict, **updates) -> bool:
    options = {
        "textbook_evidence_only": False,
        "use_model": True,
        "reuse_fragments": False,
        "answer_key_available": True,
    }
    options.update(updates)
    return _parallel_answer_draft_eligible(exam, **options)


def test_plain_text_exam_can_overlap_answer_draft_and_evidence() -> None:
    assert _eligible({"items": [{"question_id": "q1", "stem": "解释扩散。"}]})


def test_parallel_draft_is_disabled_for_risky_or_reusable_paths() -> None:
    assert not _eligible({"items": [{"question_id": "q1", "image_refs": ["figure.png"]}]})
    assert not _eligible({"items": [{"question_id": "q1", "question_type": "作图题"}]})
    assert not _eligible({"items": [{"question_id": "q1"}]}, textbook_evidence_only=True)
    assert not _eligible({"items": [{"question_id": "q1"}]}, reuse_fragments=True)
    assert not _eligible({"items": [{"question_id": "q1"}]}, answer_key_available=False)
