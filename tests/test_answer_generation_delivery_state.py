from app.answer_generation import generation_completion_state


def test_failure_placeholder_does_not_count_as_usable_coverage() -> None:
    state = generation_completion_state(3, 3, issue_count=2, fallback_count=1)
    assert state == {
        "ok": False,
        "coverage_complete": True,
        "usable_coverage_complete": False,
        "review_required": True,
        "delivery_readiness": "review_candidate",
    }


def test_missing_fragment_still_blocks_pipeline_continuity() -> None:
    state = generation_completion_state(3, 2)
    assert state["ok"] is False
    assert state["coverage_complete"] is False
    assert state["usable_coverage_complete"] is False
