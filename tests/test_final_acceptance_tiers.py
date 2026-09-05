from __future__ import annotations

import json

from app.final_acceptance import AUDIT_FILES, answer_fragment_blocking_findings, build_final_acceptance_report


def _write_passing_acceptance_inputs(stage, output) -> None:
    stage.mkdir()
    output.mkdir()
    for name, filename in AUDIT_FILES.items():
        if name == "render":
            continue
        report = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            report["formula_conversion"] = {"preferred_chain_ready": True}
        (stage / filename).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    (output / "answer_book.docx").write_bytes(b"candidate")


def test_review_candidate_is_not_treated_as_generation_failure(tmp_path) -> None:
    (tmp_path / "answer_fragments.json").write_text(
        json.dumps(
            {
                "provider": "configured",
                "fragments": [
                    {
                        "question_id": "q1",
                        "answer": "已保留的候选答案",
                        "_review_flags": [
                            {
                                "code": "answer_generation_review_candidate",
                                "message": "机器一致性校验未全部通过，需复核。",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert answer_fragment_blocking_findings(tmp_path) == []


def test_unresolved_content_produces_review_candidate_not_artifact_failure(tmp_path) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    for name, filename in AUDIT_FILES.items():
        if name == "render":
            continue
        report = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            report["formula_conversion"] = {"preferred_chain_ready": True}
        if name == "content_quality":
            report = {
                "ok": False,
                "issue_count": 1,
                "issues": [{"question_id": "q1", "code": "numeric_mismatch", "message": "计算结果不一致"}],
                "warnings": [],
            }
        (stage / filename).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    (stage / "answer_fragments.json").write_text(
        json.dumps({"provider": "configured", "fragments": [{"question_id": "q1", "answer": "候选结果"}]}),
        encoding="utf-8",
    )
    (stage / "structured_exam.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (output / "answer_book.docx").write_bytes(b"review-candidate")

    report = build_final_acceptance_report(stage, output, require_render=False)

    assert report["delivery_ready"] is True
    assert report["formal_acceptance_passed"] is False
    assert report["delivery_tier"] == "review_candidate"
    assert report["status"] == "completed_with_issues"
    assert report["delivery_issue_count"] == 0
    assert report["formal_issue_count"] == 1


def test_one_failed_answer_keeps_other_answers_as_review_candidate(tmp_path) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    _write_passing_acceptance_inputs(stage, output)
    (stage / "structured_exam.json").write_text(
        json.dumps({"items": [{"question_id": "q1"}, {"question_id": "q2"}]}),
        encoding="utf-8",
    )
    (stage / "answer_fragments.json").write_text(
        json.dumps(
            {
                "provider": "configured",
                "fragments": [
                    {"question_id": "q1", "answer": "可用答案"},
                    {
                        "question_id": "q2",
                        "answer": "待复核",
                        "_review_flags": [{"code": "answer_generation_failed", "message": "模型超时"}],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_final_acceptance_report(stage, output, require_render=False)

    assert report["delivery_ready"] is True
    assert report["formal_acceptance_passed"] is False
    assert report["delivery_tier"] == "review_candidate"
    assert report["status"] == "completed_with_issues"
    assert report["answer_fragment_delivery_summary"]["usable_count"] == 1
    assert report["answer_fragment_delivery_summary"]["failed_question_ids"] == ["q2"]


def test_all_failed_answers_remain_blocked(tmp_path) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    _write_passing_acceptance_inputs(stage, output)
    (stage / "structured_exam.json").write_text(
        json.dumps({"items": [{"question_id": "q1"}]}),
        encoding="utf-8",
    )
    (stage / "answer_fragments.json").write_text(
        json.dumps(
            {
                "provider": "configured",
                "fragments": [
                    {
                        "question_id": "q1",
                        "answer": "待复核",
                        "_review_flags": [{"code": "answer_generation_failed", "message": "模型超时"}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_final_acceptance_report(stage, output, require_render=False)

    assert report["delivery_ready"] is False
    assert report["delivery_tier"] == "blocked"
    assert report["status"] == "failed"


def test_completed_with_issues_acceptance_record_remains_a_valid_review_candidate(tmp_path) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    for name, filename in AUDIT_FILES.items():
        if name == "render":
            continue
        report = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            report["formula_conversion"] = {"preferred_chain_ready": True}
        (stage / filename).write_text(json.dumps(report), encoding="utf-8")
    (stage / "acceptance_report.json").write_text(
        json.dumps({"status": "completed_with_issues", "execution_status": "passed"}),
        encoding="utf-8",
    )
    (stage / "answer_fragments.json").write_text(
        json.dumps({"provider": "configured", "fragments": []}), encoding="utf-8"
    )
    (stage / "structured_exam.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (output / "answer_book.docx").write_bytes(b"review-candidate")

    report = build_final_acceptance_report(stage, output, require_render=False)

    assert not any("acceptance_report.json missing or not passed" in issue for issue in report["issues"])


def test_unresolved_model_science_review_downgrades_formal_tier(tmp_path) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    for name, filename in AUDIT_FILES.items():
        if name == "render":
            continue
        report = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            report["formula_conversion"] = {"preferred_chain_ready": True}
        (stage / filename).write_text(json.dumps(report), encoding="utf-8")
    (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    (stage / "answer_fragments.json").write_text(
        json.dumps({"provider": "configured", "fragments": [{"question_id": "q1", "answer": "模型答案"}]}),
        encoding="utf-8",
    )
    (stage / "semantic_quality_advisories.json").write_text(
        json.dumps(
            {
                "ok": True,
                "delivery_blocked": False,
                "advisory_count": 1,
                "advisories": [
                    {
                        "question_id": "q1",
                        "decision": "repair",
                        "confidence": 0.0,
                        "reason": "reviewer_numeric_patch_failed_machine_validation",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (stage / "structured_exam.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (output / "answer_book.docx").write_bytes(b"review-candidate")

    report = build_final_acceptance_report(stage, output, require_render=False)

    assert report["delivery_ready"] is True
    assert report["formal_acceptance_passed"] is False
    assert report["delivery_tier"] == "review_candidate"
    assert report["diagnostic_advisories"]["semantic_model_advisory_count"] == 1
    assert report["diagnostic_advisories"]["actionable_semantic_advisory_count"] == 1
    assert report["status"] == "completed_with_issues"


def test_warn_only_model_caveats_do_not_downgrade_passing_hard_gates(tmp_path) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    _write_passing_acceptance_inputs(stage, output)
    (stage / "structured_exam.json").write_text(json.dumps({"items": [{"question_id": "q1"}]}), encoding="utf-8")
    (stage / "answer_fragments.json").write_text(
        json.dumps(
            {
                "provider": "configured",
                "fragments": [
                    {"question_id": "q1", "answer": "完整答案", "warnings": ["题干条件采用通常约定。"]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (stage / "semantic_quality_advisories.json").write_text(
        json.dumps(
            {
                "ok": True,
                "delivery_blocked": False,
                "advisory_count": 1,
                "advisories": [
                    {"question_id": "q1", "decision": "warn", "reason": "保留证据边界说明"}
                ],
                "unresolved_correctness_question_ids": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (stage / "question_review_docx.json").write_text(
        json.dumps({"ok": True, "review_question_count": 1}), encoding="utf-8"
    )

    report = build_final_acceptance_report(stage, output, require_render=False)

    assert report["formal_acceptance_passed"] is True
    assert report["delivery_tier"] == "formal"
    assert report["diagnostic_advisories"]["semantic_model_advisory_count"] == 1
    assert report["diagnostic_advisories"]["actionable_semantic_advisory_count"] == 0
    assert report["diagnostic_advisories"]["informational_fragment_warning_count"] == 1
    assert report["diagnostic_advisories"]["review_question_count"] == 1
