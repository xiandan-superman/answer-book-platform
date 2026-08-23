from app.pipeline_delivery import delivery_status_message, finalize_primary_docx_filename


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


def test_review_candidate_keeps_only_explicit_candidate_filename(tmp_path) -> None:
    formal_name = tmp_path / "answer_book.docx"
    formal_name.write_bytes(b"candidate")
    report = {
        "delivery_tier": "review_candidate",
        "formal_acceptance_passed": False,
        "outputs": {"docx": str(formal_name), "docx_exists": True},
    }

    primary, candidate = finalize_primary_docx_filename(tmp_path, formal_name, report)

    assert primary.name == "answer_book_review_candidate.docx"
    assert candidate == str(primary)
    assert primary.read_bytes() == b"candidate"
    assert not formal_name.exists()
    assert report["outputs"]["docx"] == str(primary)


def test_blocked_delivery_also_leaves_no_formal_filename(tmp_path) -> None:
    formal_name = tmp_path / "answer_book.docx"
    formal_name.write_bytes(b"blocked-candidate")
    report = {
        "delivery_tier": "blocked",
        "formal_acceptance_passed": False,
        "outputs": {"docx": str(formal_name), "docx_exists": True},
    }

    primary, candidate = finalize_primary_docx_filename(tmp_path, formal_name, report)

    assert primary.name == "answer_book_review_candidate.docx"
    assert candidate == str(primary)
    assert not formal_name.exists()


def test_formal_delivery_removes_stale_candidate_name(tmp_path) -> None:
    formal_name = tmp_path / "answer_book.docx"
    candidate_name = tmp_path / "answer_book_review_candidate.docx"
    formal_name.write_bytes(b"formal")
    candidate_name.write_bytes(b"stale")
    report = {"formal_acceptance_passed": True, "outputs": {}}

    primary, candidate = finalize_primary_docx_filename(tmp_path, formal_name, report)

    assert primary == formal_name
    assert candidate == ""
    assert formal_name.read_bytes() == b"formal"
    assert not candidate_name.exists()
