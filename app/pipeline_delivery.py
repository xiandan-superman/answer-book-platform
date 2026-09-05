from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from .audit_review_gate import enforce_unattended_audit_report
from .capabilities.shadow_quality import build_shadow_quality_report
from .document_contracts import DOCUMENT_CONTRACT_VERSION
from .figure_size_audit import audit_docx_figure_sizes
from .final_acceptance import (
    answer_fragment_blocking_findings,
    answer_fragment_delivery_summary,
    build_final_acceptance_report,
    read_json,
)
from .image_artifacts import mark_final_adopted_assets
from .model_usage_report import build_model_usage_report
from .question_review_docx import (
    build_figure_review_docx,
    build_question_review_docx,
    collect_question_figure_review_items,
    collect_question_review_items,
)
from .task_control import checkpoint
from .task_store import update_task


def delivery_status_message(final_report: dict[str, Any]) -> str:
    summary = final_report.get("answer_fragment_delivery_summary") or {}
    if summary.get("partial_candidate"):
        return (
            f"已保留 {int(summary.get('usable_count') or 0)} 道可用解析，"
            f"{int(summary.get('failed_count') or 0)} 道未完成；"
            "当前 Word 仅作为待复核候选版。"
        )
    if final_report.get("delivery_tier") == "review_candidate":
        return "当前 Word 可阅读且可继续复核，但不应作为正式解析发布。"
    return "当前产物已通过正式验收。"


def _artifact_integrity(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": str(path),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def finalize_primary_docx_filename(
    output_dir: Path,
    docx_path: Path,
    final_report: dict[str, Any],
) -> tuple[Path, str]:
    """Make the surviving filename match the final publication tier."""

    candidate_path = output_dir / "answer_book_review_candidate.docx"
    outputs = final_report.setdefault("outputs", {})
    if final_report.get("formal_acceptance_passed") is False:
        candidate_path.unlink(missing_ok=True)
        docx_path.replace(candidate_path)
        outputs["docx"] = str(candidate_path)
        outputs["docx_exists"] = candidate_path.exists()
        return candidate_path, str(candidate_path)
    candidate_path.unlink(missing_ok=True)
    return docx_path, ""


def _existing_document_failure_files(stage_dir: Path) -> list[str]:
    """Keep a document/renderer investigation on its original code path."""

    failed: list[str] = []
    for name in ("docx_audit.json",):
        report = read_json(stage_dir / name)
        if isinstance(report, dict) and report.get("ok") is False and not report.get("skipped"):
            failed.append(name)
    return failed


def heavy_document_delivery_skip_decision(
    stage_dir: Path,
    *,
    preserve_document_diagnostics: bool = False,
) -> dict[str, Any]:
    """Decide whether a known all-unusable answer set warrants no document work.

    This deliberately asks for positive answer-stage evidence.  A missing or
    ambiguous answer payload is not enough to bypass Word/PDF diagnostics.
    """

    summary = answer_fragment_delivery_summary(stage_dir)
    blocked_findings = answer_fragment_blocking_findings(stage_dir)
    existing_document_failures = _existing_document_failure_files(stage_dir)
    result = {
        "schema_version": "answer_book.document_delivery_skip.v1",
        "status": "not_applicable",
        "skip_heavy_delivery": False,
        "answer_fragment_delivery_summary": summary,
        "blocked_answer_findings": blocked_findings[:30],
        "existing_document_failure_files": existing_document_failures,
        "preserve_document_diagnostics": preserve_document_diagnostics,
        "reason": "",
    }
    if preserve_document_diagnostics:
        result["reason"] = "explicit_document_diagnostics_requested"
        return result
    if existing_document_failures:
        result["reason"] = "existing_document_or_render_failure_requires_investigation"
        return result
    if int(summary.get("usable_count") or 0) > 0:
        result["reason"] = "usable_answer_exists"
        return result
    fragment_count = int(summary.get("fragment_count") or 0)
    failed_count = int(summary.get("failed_count") or 0)
    if fragment_count <= 0 or failed_count != fragment_count:
        result["reason"] = "answer_unavailability_not_proven"
        return result
    if not blocked_findings:
        result["reason"] = "answer_stage_or_configuration_failure_not_proven"
        return result
    result.update(
        {
            "status": "skipped",
            "skip_heavy_delivery": True,
            "reason": "all_answer_fragments_unusable_after_answer_stage_or_configuration_failure",
            "preserved": [
                "answer_fragments.json",
                "content_quality_audit.json",
                "final_acceptance_report.json",
                "pipeline checkpoints and retry controls",
            ],
        }
    )
    return result


def _finish_unusable_answer_delivery(
    *,
    task_id: str,
    stage_dir: Path,
    output_dir: Path,
    render_with_word: bool,
    content_quality: dict[str, Any],
    skip_decision: dict[str, Any],
    mark: Callable[[str, str, Any], None],
    write_json: Callable[[Path, Any], None],
) -> dict[str, Any]:
    """Write durable diagnostics before failing an intentionally undeliverable run."""

    checkpoint(task_id)
    update_task(task_id, current_stage="delivery_short_circuit")
    write_json(stage_dir / "document_delivery_skip.json", skip_decision)
    mark("delivery_short_circuit", "skipped", skip_decision)

    report = {
        "task_id": task_id,
        "status": "completed_with_issues",
        "execution_status": "skipped",
        "docx": "",
        "pipeline_status": str(stage_dir / "pipeline_status.json"),
        "rendered": False,
        "render_requested": render_with_word,
        "document_delivery_skipped": True,
        "document_delivery_skip_reason": skip_decision["reason"],
        "content_quality_review_required": not content_quality.get("ok", False),
    }
    checkpoint(task_id)
    update_task(task_id, current_stage="acceptance")
    mark("acceptance", "started", {"message": "答案不可用，保留诊断并跳过文档交付。"})
    write_json(stage_dir / "acceptance_report.json", report)
    mark("acceptance", "completed_with_issues", report)

    checkpoint(task_id)
    update_task(task_id, current_stage="model_usage_report")
    mark("model_usage_report", "started", {"message": "开始生成模型调用汇总文档。"})
    model_usage_report = build_model_usage_report(stage_dir, output_dir, task_id)
    mark("model_usage_report", "passed", {"report": str(model_usage_report)})

    checkpoint(task_id)
    update_task(task_id, current_stage="final_acceptance")
    mark("final_acceptance", "started", {"require_render": False, "document_delivery_skipped": True})
    final_report = build_final_acceptance_report(stage_dir, output_dir, require_render=False)
    write_json(stage_dir / "final_acceptance_report.json", final_report)
    mark(
        "final_acceptance",
        "failed",
        {
            "issues": final_report["issues"][:30],
            "document_delivery_skipped": True,
            "reason": skip_decision["reason"],
        },
    )
    raise RuntimeError("Final acceptance audit failed")


def complete_pipeline_delivery(
    *,
    task_id: str,
    fragments_json: Path,
    stage_dir: Path,
    output_dir: Path,
    structured_exam: dict[str, Any],
    candidates: list[Any],
    selection_data: dict[str, Any],
    provider: Any,
    model: str,
    use_model: bool,
    render_with_word: bool,
    content_quality: dict[str, Any],
    preserve_document_diagnostics: bool = False,
    mark: Callable[[str, str, Any], None],
    write_json: Callable[[Path, Any], None],
    build_docx_with_repair: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Build and audit either a formal deliverable or a labelled review candidate."""

    # Legacy callers may still request rendering; exam delivery is Word-only.
    render_with_word = False
    skip_decision = heavy_document_delivery_skip_decision(
        stage_dir,
        preserve_document_diagnostics=preserve_document_diagnostics,
    )
    if skip_decision["skip_heavy_delivery"]:
        return _finish_unusable_answer_delivery(
            task_id=task_id,
            stage_dir=stage_dir,
            output_dir=output_dir,
            render_with_word=render_with_word,
            content_quality=content_quality,
            skip_decision=skip_decision,
            mark=mark,
            write_json=write_json,
        )
    # A recovery run may follow a previous all-unusable attempt.  Keep the
    # previous record from suppressing document gates once this run is again
    # allowed to build a candidate or formal document.
    stale_skip_record = stage_dir / "document_delivery_skip.json"
    if stale_skip_record.exists():
        write_json(stale_skip_record, skip_decision)

    checkpoint(task_id)
    update_task(task_id, current_stage="docx")
    # Construction and intermediate audits use a neutral internal name.  A
    # run that fails before final acceptance must never leave a formal-looking
    # answer_book.docx behind.
    docx_path = stage_dir / "working_answer_book.docx"
    mark("docx", "started", {"docx": str(docx_path), "message": "开始生成并审查最终 Word 文档。"})
    docx_result = build_docx_with_repair(
        task_id,
        fragments_json,
        docx_path,
        stage_dir,
        mark,
        structured_exam=structured_exam,
        candidates=candidates,
        selection_data=selection_data,
        provider=provider,
        model=model,
        use_model=use_model,
    )
    if docx_result.get("content_changed"):
        refreshed_content_quality = docx_result.get("content_quality")
        if isinstance(refreshed_content_quality, dict) and refreshed_content_quality:
            content_quality = refreshed_content_quality
        mark(
            "content_quality_after_docx_repair",
            "passed" if content_quality.get("ok") else "review_candidate",
            {
                "revalidated": True,
                "issue_count": len(content_quality.get("issues", [])),
                "warning_count": len(content_quality.get("warnings", [])),
                "candidate_sha256": content_quality.get("candidate_sha256", ""),
            },
        )
    issues = docx_result["issues"]
    docx_audit_report = {
        "schema_version": "answer_book.docx_audit.v2",
        "document_contract_version": DOCUMENT_CONTRACT_VERSION,
        "ok": not issues,
        "issues": issues,
        "warnings": [],
    }
    write_json(stage_dir / "docx_audit.json", docx_audit_report)
    if issues:
        docx_audit_report = enforce_unattended_audit_report(
            docx_audit_report,
            source="docx",
            output_json=stage_dir / "docx_audit.json",
        )
        issues = docx_audit_report.get("issues", [])
    build_shadow_quality_report(stage_dir)
    if issues:
        mark(
            "docx_unattended_gate",
            "failed",
            {
                "human_review_required": False,
                "blocked_count": docx_audit_report.get("blocked_count", len(issues)),
                "issues": issues[:30],
            },
        )
        raise RuntimeError("DOCX audit failed after bounded repairs")
    if not docx_path.exists():
        mark(
            "docx",
            "failed",
            {
                "issues": ["answer_book.docx was not generated"],
                "repair": docx_result.get("repair", {}),
            },
        )
        raise RuntimeError("answer_book.docx was not generated")
    figure_size_audit = audit_docx_figure_sizes(
        docx_path,
        structured_exam=structured_exam,
    )
    write_json(stage_dir / "figure_size_audit.json", figure_size_audit)
    build_shadow_quality_report(stage_dir)
    mark(
        "figure_size_audit",
        "passed" if figure_size_audit["ok"] else "failed",
        {
            "figure_count": len(figure_size_audit.get("figures", [])),
            "applicable": figure_size_audit.get("applicable", True),
            "required_question_ids": figure_size_audit.get("required_question_ids", []),
            "issues": figure_size_audit.get("issues", [])[:20],
            "warnings": figure_size_audit.get("warnings", [])[:20],
        },
    )
    if not figure_size_audit["ok"]:
        raise RuntimeError("Embedded figure size audit failed")
    mark("docx", "passed", {"docx": str(docx_path), "repair": docx_result.get("repair", {})})

    checkpoint(task_id)
    update_task(task_id, current_stage="question_review")
    mark("question_review", "started", {"message": "开始生成存疑审查文档。"})
    review_items = collect_question_review_items(stage_dir)
    review_docx = build_question_review_docx(
        stage_dir,
        output_dir,
        render_snapshots=render_with_word,
    )
    figure_review_items = collect_question_figure_review_items(stage_dir)
    figure_review_docx = build_figure_review_docx(stage_dir, output_dir)
    write_json(
        stage_dir / "question_review_docx.json",
        {
            "ok": review_docx.exists(),
            "review_question_count": len(review_items),
            "docx": str(review_docx),
            "figure_review_question_count": len(figure_review_items),
            "figure_review_docx": str(figure_review_docx),
        },
    )
    write_json(
        stage_dir / "figure_review_docx.json",
        {
            "ok": figure_review_docx.exists(),
            "review_question_count": len(figure_review_items),
            "docx": str(figure_review_docx),
        },
    )
    mark(
        "question_review",
        "passed",
        {
            "review_question_count": len(review_items),
            "docx": str(review_docx),
            "figure_review_question_count": len(figure_review_items),
            "figure_review_docx": str(figure_review_docx),
        },
    )

    shadow_quality = build_shadow_quality_report(stage_dir)
    mark(
        "quality_shadow",
        "passed",
        {
            "mode": shadow_quality["mode"],
            "enforced": shadow_quality["enforced"],
            "finding_count": shadow_quality["finding_count"],
            "would_block_count": shadow_quality["would_block_count"],
            "would_warn_count": shadow_quality["would_warn_count"],
            "report": str(stage_dir / "quality_shadow_report.json"),
        },
    )
    report = {
        "task_id": task_id,
        "status": "passed",
        "docx": str(docx_path),
        "pipeline_status": str(stage_dir / "pipeline_status.json"),
        "rendered": render_with_word,
        "content_quality_review_required": not content_quality.get("ok", False),
        "quality_shadow": {
            "enforced": False,
            "finding_count": shadow_quality["finding_count"],
            "would_block_count": shadow_quality["would_block_count"],
            "would_warn_count": shadow_quality["would_warn_count"],
        },
    }
    checkpoint(task_id)
    update_task(task_id, current_stage="acceptance")
    mark("acceptance", "started", {"message": "开始整理验收结果。"})
    write_json(stage_dir / "acceptance_report.json", report)
    mark("acceptance", "passed", report)
    checkpoint(task_id)
    update_task(task_id, current_stage="model_usage_report")
    mark("model_usage_report", "started", {"message": "开始生成模型调用汇总文档。"})
    model_usage_report = build_model_usage_report(stage_dir, output_dir, task_id)
    mark("model_usage_report", "passed", {"report": str(model_usage_report)})
    checkpoint(task_id)
    update_task(task_id, current_stage="final_acceptance")
    mark("final_acceptance", "started", {"require_render": render_with_word})
    formal_docx_path = output_dir / "answer_book.docx"
    final_report = build_final_acceptance_report(
        stage_dir,
        output_dir,
        require_render=render_with_word,
        candidate_docx=docx_path,
    )
    if not final_report.get("delivery_ready", final_report.get("ok", False)):
        write_json(stage_dir / "final_acceptance_report.json", final_report)
        mark("final_acceptance", "failed", {"issues": final_report["issues"][:30]})
        raise RuntimeError("Final acceptance audit failed")
    review_candidate_path = output_dir / "answer_book_review_candidate.docx"
    publication_target = formal_docx_path if final_report.get("formal_acceptance_passed") else review_candidate_path
    candidate_integrity = _artifact_integrity(docx_path)
    publication_manifest_path = stage_dir / "publication_manifest.json"
    publication_manifest = {
        "schema_version": "answer_book.publication.v1",
        "state": "prepared",
        "delivery_tier": str(final_report.get("delivery_tier") or "blocked"),
        "source": candidate_integrity,
        "target": str(publication_target),
    }
    # Persist the exact approved candidate before the atomic name promotion.
    # A crash can therefore be distinguished from an unvalidated output on
    # the next inspection instead of silently trusting a formal-looking file.
    write_json(publication_manifest_path, publication_manifest)
    if final_report.get("formal_acceptance_passed"):
        primary_docx = formal_docx_path
        review_candidate_docx = ""
        os.replace(docx_path, primary_docx)
        review_candidate_path.unlink(missing_ok=True)
    else:
        primary_docx = review_candidate_path
        review_candidate_docx = str(review_candidate_path)
        os.replace(docx_path, primary_docx)
        formal_docx_path.unlink(missing_ok=True)
    docx_path = primary_docx
    artifact_integrity = {"docx": _artifact_integrity(primary_docx)}
    if artifact_integrity["docx"]["sha256"] != candidate_integrity["sha256"]:
        raise RuntimeError("候选 Word 原子发布后完整性校验失败")
    publication_manifest.update(
        {
            "state": "committed",
            "committed": artifact_integrity["docx"],
        }
    )
    write_json(publication_manifest_path, publication_manifest)
    final_report.setdefault("outputs", {}).update(
        {
            "docx": str(primary_docx),
            "docx_exists": primary_docx.exists(),
            "artifact_integrity": artifact_integrity,
        }
    )
    try:
        final_artifact_adoption = mark_final_adopted_assets(read_json(fragments_json) or {})
    except Exception:
        final_artifact_adoption = {
            "final_adopted_count": 0,
            "unresolved_selected_asset_count": 0,
            "report_unavailable": True,
        }
    write_json(stage_dir / "final_acceptance_report.json", final_report)
    answer_delivery_summary = final_report.get("answer_fragment_delivery_summary") or {}
    delivery_status = {
        "schema_version": "answer_book.delivery_status.v1",
        "delivery_ready": bool(final_report.get("delivery_ready")),
        "formal_acceptance_passed": bool(final_report.get("formal_acceptance_passed")),
        "delivery_tier": str(final_report.get("delivery_tier") or "blocked"),
        "delivery_tier_label": str(final_report.get("delivery_tier_label") or ""),
        "primary_docx": str(primary_docx),
        "review_candidate_docx": review_candidate_docx,
        "review_docx": str(output_dir / "question_review.docx"),
        "answer_fragment_delivery_summary": answer_delivery_summary,
        "artifact_adoption": final_artifact_adoption,
        "artifact_integrity": artifact_integrity,
        "message": delivery_status_message(final_report),
    }
    write_json(output_dir / "delivery_status.json", delivery_status)
    report["delivery_tier"] = delivery_status["delivery_tier"]
    report["formal_acceptance_passed"] = delivery_status["formal_acceptance_passed"]
    # Do not expose successful document construction as successful content
    # acceptance. Keep both outcomes explicit for CLI and API callers.
    report["execution_status"] = "passed"
    report["status"] = str(final_report.get("status") or "failed")
    report["delivery_ready"] = bool(final_report.get("delivery_ready"))
    report["review_candidate_docx"] = review_candidate_docx
    report["docx"] = str(primary_docx)
    write_json(stage_dir / "acceptance_report.json", report)
    # Refresh the user-facing usage report after the final tier is known. The
    # earlier copy is useful during failure handling, but must not remain stale
    # in a completed delivery.
    build_model_usage_report(stage_dir, output_dir, task_id)
    completed_with_issues = final_report.get("status") == "completed_with_issues"
    update_task(
        task_id,
        status="completed_with_issues" if completed_with_issues else "completed",
        current_stage="completed",
        error="",
    )
    mark(
        "final_acceptance",
        "completed_with_issues" if completed_with_issues else "passed",
        {
            "formal_acceptance_passed": final_report.get("formal_acceptance_passed", False),
            "delivery_ready": final_report.get("delivery_ready", False),
            "warning_count": final_report["warning_count"],
            "report": str(stage_dir / "final_acceptance_report.json"),
        },
    )
    return report
