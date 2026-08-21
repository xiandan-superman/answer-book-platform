from app.exercise_generation import _ensure_selected_source_coverage, scope_cover_summary


def test_model_plan_missing_selected_units_is_repaired_deterministically():
    selected = [{"source_question_id": f"source_{index:02d}"} for index in range(1, 4)]
    plan = [
        {"plan_item_id": "plan_item_01", "source_question_id": "source_01"},
        {"plan_item_id": "plan_item_02", "source_question_id": "source_01"},
        {"plan_item_id": "plan_item_03", "source_question_id": "source_02"},
    ]
    repaired = _ensure_selected_source_coverage(plan, selected)
    assert {item["source_question_id"] for item in repaired} == {"source_01", "source_02", "source_03"}
    cover = scope_cover_summary({"mode": "top_level", "questions": []}, selected, repaired)
    assert cover["complete"] is True
