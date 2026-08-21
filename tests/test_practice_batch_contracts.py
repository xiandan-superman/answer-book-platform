from __future__ import annotations

from app.practice_batch_contracts import complete_practice_slots, partition_practice_batch_rows


def test_partition_rejects_duplicate_out_of_range_and_nullable_rows() -> None:
    accepted, report = partition_practice_batch_rows(
        {
            "exercises": [
                {"batch_index": 2, "stem": "healthy"},
                {"batch_index": 1, "stem": "duplicate-a"},
                {"batch_index": 1, "stem": "duplicate-b"},
                {"batch_index": 9, "stem": "out-of-range"},
                {"batch_index": None, "stem": "missing-index"},
                None,
            ]
        },
        expected_count=3,
    )

    assert accepted == {2: {"batch_index": 2, "stem": "healthy"}}
    assert report["actual_indexes"] == [2, 1, 1, 9, ""]
    assert report["duplicate_indexes"] == [1]
    assert report["missing_indexes"] == [1, 3]
    assert report["invalid_item_count"] == 3


def test_complete_slots_deduplicates_and_restores_blueprint_order() -> None:
    plan = [
        {"plan_item_id": "p1"},
        {"plan_item_id": "p2"},
        {"plan_item_id": "p3"},
    ]

    def placeholder(item, index, error):
        return {
            "plan_item_id": item["plan_item_id"],
            "number": index,
            "generation_status": "failed",
            "generation_error": error,
        }

    completed = complete_practice_slots(
        [
            {"plan_item_id": "p3", "stem": "third"},
            {"plan_item_id": "p1", "stem": "first"},
            {"plan_item_id": "p1", "stem": "duplicate-must-not-replace"},
        ],
        plan,
        failed_placeholder=placeholder,
        failures={"p2": {"code": "missing", "message": "failed"}},
    )

    assert [item["plan_item_id"] for item in completed] == ["p1", "p2", "p3"]
    assert completed[0]["stem"] == "first"
    assert completed[1]["generation_error"]["code"] == "missing"
