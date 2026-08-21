from app.pipeline_delivery import delivery_status_message


def test_partial_answer_candidate_message_reports_usable_and_failed_counts() -> None:
    message = delivery_status_message(
        {
            "delivery_tier": "review_candidate",
            "answer_fragment_delivery_summary": {
                "partial_candidate": True,
                "usable_count": 9,
                "failed_count": 1,
            },
        }
    )

    assert message == "已保留 9 道可用解析，1 道未完成；当前 Word 仅作为待复核候选版。"


def test_non_partial_review_candidate_keeps_existing_message() -> None:
    assert delivery_status_message({"delivery_tier": "review_candidate"}) == (
        "当前 Word 可阅读且可继续复核，但不应作为正式解析发布。"
    )
