from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.task_contracts import (
    QualityStatus,
    RunStatus,
    WorkflowType,
    capabilities_for,
    present_error,
)
from app.task_read_model import build_exam_run, build_practice_runs


def test_exam_review_rejection_is_action_required_not_technical_failure() -> None:
    run = build_exam_run(
        {
            "task_id": "exam-review-rejected",
            "status": "failed",
            "current_stage": "exam_structure_review",
            "error": "用户拒绝真题结构确认",
        }
    )

    assert run["status"] == "needs_input"
    assert run["engine_status"] == "failed"
    assert run["error_presentation"]["kind"] == "review_rejected"
    assert run["capabilities"]["reopen_review"] is True
    assert run["capabilities"]["view_result"] is False


def test_final_acceptance_failure_blocks_result_delivery() -> None:
    run = build_exam_run(
        {
            "task_id": "exam-final-acceptance-failed",
            "status": "failed",
            "current_stage": "final_acceptance",
            "error": "Final acceptance audit failed",
        },
        {"final_acceptance": {"ok": False, "issue_count": 4, "warning_count": 0}},
    )

    assert run["status"] == "failed"
    assert run["quality_status"] == "blocked"
    assert run["error_presentation"]["kind"] == "final_acceptance_failed"
    assert run["capabilities"]["view_result"] is False
    assert run["capabilities"]["download"] is False
    assert run["capabilities"]["retry"] is True


def test_exam_final_acceptance_pass_uses_delivery_specific_quality_label() -> None:
    run = build_exam_run(
        {
            "task_id": "exam-final-acceptance-passed",
            "status": "completed",
            "current_stage": "completed",
        },
        {"final_acceptance": {"ok": True, "issue_count": 0, "warning_count": 0}},
    )

    assert run["quality_presentation"]["label"] == "最终验收通过"


def test_running_exam_never_claims_quality_has_passed_from_partial_audits() -> None:
    run = build_exam_run(
        {"task_id": "exam-running", "status": "running", "current_stage": "question_understanding"},
        {"exam_structure": {"ok": True, "issue_count": 0, "warning_count": 0}},
    )

    assert run["status"] == "running"
    assert run["quality_status"] == "unknown"
    assert run["quality_presentation"] is None


def test_exam_and_practice_warning_labels_preserve_workflow_semantics() -> None:
    exam = build_exam_run(
        {"task_id": "exam-warning", "status": "completed", "current_stage": "completed"},
        {"final_acceptance": {"ok": True, "issue_count": 0, "warning_count": 1}},
    )
    practice = build_practice_runs(
        [],
        [{
            "history_id": "practice-warning",
            "task_kind": "practice",
            "quality": {"status": "warning"},
            "generation": {"status": "completed"},
        }],
    )[0]

    assert exam["status"] == "completed"
    assert exam["quality_presentation"]["label"] == "正式交付通过 · 含诊断提示"
    assert exam["capabilities"]["download"] is True
    assert practice["status"] == "completed_with_issues"
    assert practice["quality_presentation"]["label"] == "已完成 · 有提示"
    assert practice["quality_presentation"]["class_name"] == "warning"


def test_exam_visual_semantic_risk_is_downloadable_but_not_formally_accepted() -> None:
    run = build_exam_run(
        {"task_id": "exam-visual-risk", "status": "completed", "current_stage": "completed"},
        {
            "final_acceptance": {
                "ok": True,
                "delivery_ready": True,
                "formal_acceptance_passed": False,
                "delivery_tier": "review_candidate",
                "status": "completed_with_issues",
                "issue_count": 0,
                "warning_count": 1,
            }
        },
    )

    assert run["status"] == "completed_with_issues"
    assert run["quality_presentation"]["label"] == "可交付待复核"
    assert run["capabilities"]["download"] is True


def test_legacy_completed_exam_with_review_candidate_tier_stays_in_review_queue() -> None:
    run = build_exam_run(
        {"task_id": "exam-review-candidate", "status": "completed", "current_stage": "completed"},
        {
            "final_acceptance": {
                "ok": True,
                "delivery_ready": True,
                "formal_acceptance_passed": False,
                "delivery_tier": "review_candidate",
                "status": "passed",
                "issue_count": 0,
                "warning_count": 0,
            }
        },
    )

    assert run["status"] == "completed_with_issues"
    assert run["quality_status"] == "warning"
    assert run["quality_presentation"]["label"] == "可交付待复核"
    assert run["capabilities"]["download"] is True


def test_task_quality_summary_preserves_final_acceptance_semantics() -> None:
    from app.server import _task_quality_summary

    with tempfile.TemporaryDirectory() as raw_tmp:
        stage = Path(raw_tmp)
        (stage / "final_acceptance_report.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "delivery_ready": True,
                    "formal_acceptance_passed": False,
                    "delivery_tier": "review_candidate",
                    "status": "completed_with_issues",
                    "issues": [],
                    "warnings": ["图片科学性错误"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with patch("app.server.stage_dir", return_value=stage):
            summary = _task_quality_summary("task")

    final = summary["final_acceptance"]
    assert final["status"] == "completed_with_issues"
    assert final["delivery_tier"] == "review_candidate"
    assert final["delivery_ready"] is True
    assert final["formal_acceptance_passed"] is False
    assert final["warnings"] == ["图片科学性错误"]


def test_practice_pass_keeps_structural_not_subject_quality_claim() -> None:
    run = build_practice_runs(
        [],
        [{
            "history_id": "practice-passed",
            "task_kind": "knowledge",
            "quality": {"status": "passed"},
            "generation": {"status": "completed"},
        }],
    )[0]

    assert run["quality_presentation"]["label"] == "已完成"


def test_control_capabilities_remain_different_between_workflows() -> None:
    exam = capabilities_for(WorkflowType.EXAM_ANALYSIS, RunStatus.RUNNING)
    practice = capabilities_for(WorkflowType.PRACTICE_BY_QUESTION, RunStatus.RUNNING)

    assert exam.pause is True
    assert exam.cancel is True
    assert practice.pause is False
    assert practice.cancel is True


def test_only_exam_queued_run_can_be_started_or_cancelled() -> None:
    exam = capabilities_for(WorkflowType.EXAM_ANALYSIS, RunStatus.QUEUED)
    practice = capabilities_for(WorkflowType.PRACTICE_BY_KNOWLEDGE, RunStatus.QUEUED)

    assert exam.start is True
    assert exam.cancel is True
    assert practice.start is False
    assert practice.cancel is True


def test_low_level_failures_are_presented_as_actionable_user_messages() -> None:
    timeout = present_error("Provider request failed: The read operation timed out")
    interrupted = present_error("cannot schedule new futures after interpreter shutdown")
    document = present_error("DOCX audit failed after bounded repairs", stage="docx")

    assert timeout and timeout.kind == "provider_timeout" and "模型服务" in timeout.message
    assert interrupted and interrupted.kind == "interrupted" and "服务停止" in interrupted.message
    assert document and document.kind == "document_failed" and "Word" in document.message


def test_provider_configuration_failures_are_specific_but_do_not_expose_raw_responses() -> None:
    cases = [
        (
            'Provider HTTP 401: {"error":{"code":"Unauthorized","message":"invalid api key","request_id":"req-401"}}',
            "provider_authentication",
            "可能无效、已过期",
        ),
        (
            'Provider HTTP 403: {"error":{"code":"Forbidden","message":"access denied","request_id":"req-403"}}',
            "provider_permission",
            "权限",
        ),
        (
            'Provider HTTP 404: {"error":{"code":"InvalidEndpointOrModel.NotFound","message":"model not found","request_id":"req-404"}}',
            "provider_target_not_found",
            "Endpoint",
        ),
    ]

    for raw, kind, expected in cases:
        presentation = present_error(raw, support_id="PJ-SAFE123456")
        assert presentation is not None
        assert presentation.kind == kind
        assert expected in presentation.message
        assert presentation.support_id == "PJ-SAFE123456"
        public = f"{presentation.title} {presentation.message} {presentation.retry_hint}"
        assert "request_id" not in public
        assert "req-" not in public
        assert "InvalidEndpointOrModel" not in public


def test_unknown_provider_failure_and_timeout_have_sanitized_public_copy() -> None:
    unknown = present_error(
        'Provider exploded: {"internal_code":"opaque-77","request_id":"req-secret"}',
        support_id="PJ-UNKNOWN01",
    )
    timeout = present_error(
        "Provider request failed: The read operation timed out; request_id=req-timeout",
        support_id="PJ-TIMEOUT01",
    )
    image_response = present_error(
        "Unexpected image response shape: {'request_id': 'req-image', 'internal_code': 'opaque-image'}",
        support_id="PJ-IMAGE001",
    )

    assert unknown and unknown.kind == "provider_error"
    assert "opaque-77" not in unknown.message
    assert "req-secret" not in unknown.message
    assert unknown.support_id == "PJ-UNKNOWN01"
    assert timeout and timeout.kind == "provider_timeout"
    assert "req-timeout" not in timeout.message
    assert timeout.support_id == "PJ-TIMEOUT01"
    assert image_response and image_response.kind == "provider_error"
    assert "req-image" not in image_response.message
    assert "opaque-image" not in image_response.message


def test_practice_job_api_includes_public_error_presentation() -> None:
    from app.server import _practice_job_api_payload

    payload = _practice_job_api_payload({
        "job_id": "job-1",
        "status": "failed",
        "current_stage": "analyze",
        "error": "Provider request failed: The read operation timed out",
        "support_id": "PJ-API000001",
        "warning_reason": "Provider request failed: The read operation timed out; request_id=req-hidden",
        "suggested_action": "raw retry hint",
        "diagnostic_context": {"traceback": "raw provider traceback"},
        "failure_context": {"provider_response": "raw provider response"},
    })

    assert payload["error"]
    assert payload["error_presentation"]["kind"] == "provider_timeout"
    assert payload["error_presentation"]["message"] == "模型服务在规定时间内没有返回完整结果。"
    assert payload["error_presentation"]["support_id"] == "PJ-API000001"
    assert payload["error"] == "模型服务在规定时间内没有返回完整结果。"
    assert "diagnostic_context" not in payload
    assert "failure_context" not in payload
    assert "req-hidden" not in str(payload)
    assert payload["warning_reason"] == payload["error_presentation"]["message"]
    assert payload["suggested_action"] == payload["error_presentation"]["retry_hint"]


def test_provider_timeout_has_scenario_specific_recovery_copy() -> None:
    presentation = present_error("Provider HTTP 524: error code: 524", stage="generating")

    assert presentation is not None
    assert presentation.kind == "provider_timeout"
    assert "检查点" in presentation.retry_hint
    assert "蓝图" in presentation.retry_hint


def test_practice_batch_is_one_run_with_multiple_steps() -> None:
    jobs = [
        {
            "job_id": "generation_analyze",
            "practice_batch_id": "batch-one",
            "task_kind": "practice",
            "operation": "analyze",
            "status": "completed",
            "current_stage": "completed",
            "created_at": "2026-08-01T10:00:00+08:00",
            "updated_at": "2026-08-01T10:01:00+08:00",
        },
        {
            "job_id": "generation_plan",
            "practice_batch_id": "batch-one",
            "task_kind": "practice",
            "operation": "plan",
            "status": "failed",
            "current_stage": "planning",
            "created_at": "2026-08-01T10:02:00+08:00",
            "updated_at": "2026-08-01T10:03:00+08:00",
            "error": "Provider HTTP 524: error code: 524",
        },
    ]

    runs = build_practice_runs(jobs, [])

    assert len(runs) == 1
    assert runs[0]["task_id"] == "generation_plan"
    assert runs[0]["status"] == "failed"
    assert [step["operation"] for step in runs[0]["steps"]] == ["analyze", "plan"]


def test_practice_task_center_contract_never_exposes_raw_provider_errors() -> None:
    raw = 'Provider HTTP 404: {"code":"InvalidEndpointOrModel.NotFound","request_id":"req-private"}'
    runs = build_practice_runs(
        [{
            "job_id": "generation_provider_failure",
            "practice_batch_id": "batch-provider-failure",
            "task_kind": "knowledge",
            "operation": "analyze",
            "status": "failed",
            "current_stage": "failed",
            "created_at": "2026-08-23T10:00:00+08:00",
            "updated_at": "2026-08-23T10:01:00+08:00",
            "support_id": "PJ-TASKCENTER",
            "error": raw,
        }],
        [],
    )

    assert len(runs) == 1
    run = runs[0]
    assert run["error_presentation"]["kind"] == "provider_target_not_found"
    assert run["error_presentation"]["support_id"] == "PJ-TASKCENTER"
    assert "InvalidEndpointOrModel" not in run["error"]
    assert "req-private" not in str(run)
    assert run["steps"][0]["error"] == run["error"]


def test_legacy_completed_background_steps_do_not_pollute_task_center() -> None:
    jobs = [
        {
            "job_id": "generation_old_analysis",
            "practice_batch_id": "",
            "task_kind": "practice",
            "operation": "analyze",
            "status": "completed",
            "current_stage": "completed",
        },
        {
            "job_id": "generation_old_failure",
            "practice_batch_id": "",
            "task_kind": "knowledge",
            "operation": "generate_from_plan",
            "status": "failed",
            "current_stage": "generating",
            "error": "模型 JSON 无效",
        },
    ]

    runs = build_practice_runs(jobs, [])

    assert [run["task_id"] for run in runs] == ["generation_old_failure"]


def test_partial_practice_history_remains_distinct_from_review() -> None:
    histories = [
        {
            "history_id": "practice_partial",
            "task_kind": "knowledge",
            "title": "知识点练习",
            "created_at": "2026-08-01T10:00:00+08:00",
            "updated_at": "2026-08-01T10:05:00+08:00",
            "question_count": 10,
            "generation": {"status": "partial_success", "partial_success": True},
            "quality": {"status": "warning"},
            "request": {"practice_batch_id": "batch-partial"},
        }
    ]

    run = build_practice_runs([], histories)[0]

    assert run["status"] == "completed_with_issues"
    assert run["quality_status"] == QualityStatus.WARNING.value
    assert run["quality_presentation"]["label"] == "已完成 · 有提示"
    assert run["capabilities"]["view_result"] is True
    assert run["capabilities"]["download"] is False
