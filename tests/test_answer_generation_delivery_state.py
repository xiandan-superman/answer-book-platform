from app.answer_generation import generation_completion_state


def test_complete_candidate_with_issues_continues_to_quality_governance() -> None:
    state = generation_completion_state(3, 3, issue_count=2, fallback_count=1)
    assert state == {
        "ok": True,
        "coverage_complete": True,
        "review_required": True,
        "delivery_readiness": "review_candidate",
    }


def test_missing_fragment_still_blocks_pipeline_continuity() -> None:
    state = generation_completion_state(3, 2)
    assert state["ok"] is False
    assert state["coverage_complete"] is False
