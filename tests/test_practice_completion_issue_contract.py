from __future__ import annotations

from app.task_contracts import PRACTICE_COMPLETION_ISSUES_SCHEMA, practice_completion_issue_contract


def exercise(*, failed: bool = False, configuration: bool = False, audit: bool = False) -> dict:
    row: dict = {"stem": "测试题"}
    if failed:
        row["generation_status"] = "failed"
        row["generation_error"] = {
            "code": "blueprint_audit_failed" if audit else "provider_generation_missing",
            "requires_configuration": configuration,
        }
    if audit:
        row["audit_status"] = "audit_failed"
    return row


def test_complete_three_of_three_with_findings_is_review_not_incomplete() -> None:
    contract = practice_completion_issue_contract({
        "exercises": [exercise(), exercise(), exercise()],
        "generation": {"total_count": 3, "generated_count": 3, "failed_count": 0},
        "quality": {
            "generated_count": 3,
            "failed_count": 0,
            "blocking_issues": ["题干约束需复核", "单位表达需复核"],
        },
    })

    assert contract["schema_version"] == PRACTICE_COMPLETION_ISSUES_SCHEMA
    assert contract["primary_code"] == "review_required"
    assert contract["display_label"] == "题目已生成 · 待复核"
    assert contract["unfinished_count"] == 0
    assert [item["code"] for item in contract["issues"]] == ["review_required"]
    assert len(contract["primary"]["reasons"]) == 2
    assert "未完成" not in str(contract)


def test_configuration_partial_result_preserves_both_reasons_and_priority() -> None:
    contract = practice_completion_issue_contract({
        "exercises": [exercise(), exercise(failed=True, configuration=True)],
        "generation": {"total_count": 2, "generated_count": 1, "failed_count": 1, "configuration_blocked": True},
        "quality": {"generated_count": 1, "failed_count": 1},
    })

    assert [item["code"] for item in contract["issues"]] == ["configuration_blocked", "generation_incomplete"]
    assert contract["primary_code"] == "configuration_blocked"
    assert contract["action"] == "check_configuration"
    assert contract["unfinished_count"] == 1


def test_incomplete_and_review_reasons_coexist_without_losing_healthy_results() -> None:
    contract = practice_completion_issue_contract({
        "exercises": [exercise(), exercise(failed=True, audit=True), exercise()],
        "generation": {"total_count": 3, "generated_count": 2, "failed_count": 1},
        "quality": {"generated_count": 2, "failed_count": 1, "blocking_issues": ["已生成题中有一项需复核"]},
    })

    assert [item["code"] for item in contract["issues"]] == ["generation_incomplete", "review_required"]
    assert contract["generated_count"] == 2
    assert contract["total_count"] == 3
    assert contract["primary"]["action"] == "continue_incomplete"
    assert len(contract["issues"][1]["reasons"]) == 2


def test_warning_only_and_clean_completion_have_distinct_actions() -> None:
    warning = practice_completion_issue_contract({
        "exercises": [exercise()],
        "generation": {"total_count": 1, "generated_count": 1},
        "quality": {"generated_count": 1, "warnings": ["措辞可进一步优化"]},
    })
    clean = practice_completion_issue_contract({
        "exercises": [exercise()],
        "generation": {"total_count": 1, "generated_count": 1},
        "quality": {"generated_count": 1},
    })

    assert (warning["primary_code"], warning["display_label"], warning["action"]) == (
        "warning_only", "已完成 · 有提示", "view_warnings",
    )
    assert (clean["primary_code"], clean["display_label"], clean["action"]) == (
        "completed", "已完成", "view_result",
    )


def test_legacy_partial_record_without_positive_missing_count_never_claims_unfinished_questions() -> None:
    contract = practice_completion_issue_contract({
        "generation": {"status": "partial_success", "partial_success": True},
        "quality": {"status": "warning"},
    })

    assert contract["primary_code"] == "warning_only"
    assert contract["unfinished_count"] == 0
    assert all(item["code"] != "generation_incomplete" for item in contract["issues"])
    assert "未完成题目" not in contract["display_label"]


def test_legacy_structured_findings_are_conservatively_preserved_in_read_models() -> None:
    contract = practice_completion_issue_contract({
        "total_count": 3,
        "generated_count": 3,
        "unfinished_count": 0,
        "completion_issues": {
            "schema_version": PRACTICE_COMPLETION_ISSUES_SCHEMA,
            "issues": [{"code": "review_required", "reasons": ["旧记录的复核结论"]}],
        },
    })

    assert contract["primary_code"] == "review_required"
    assert contract["primary"]["reasons"] == ["旧记录的复核结论"]
    assert contract["unfinished_count"] == 0
