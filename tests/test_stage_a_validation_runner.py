from scripts.run_stage_a_validation import historical_replays


def test_historical_replay_treats_contained_hard_errors_as_safety_passes():
    results = {item["id"]: item for item in historical_replays()}

    for case_id in ("V3-C-r2", "V4-C-r3"):
        assert results[case_id]["issue_codes"] == ["calculation_internal_inconsistency"]
        assert results[case_id]["stored_formal_acceptance_passed"] is False
        assert results[case_id]["hard_issue_contained"] is True
        assert results[case_id]["passed"] is True
