from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .audit_review_gate import enforce_unattended_audit_report
from .capabilities.shadow_quality import build_shadow_quality_report
from .document_contracts import DOCUMENT_CONTRACT_VERSION
from .figure_size_audit import audit_docx_figure_sizes
from .final_acceptance import build_final_acceptance_report
from .model_usage_report import build_model_usage_report
from .question_review_docx import (
    build_figure_review_docx,
    build_question_review_docx,
    collect_question_figure_review_items,
    collect_question_review_items,
)
from .render_audit import audit_docx_pdf_consistency, audit_rendered_pages_report
from .render_fonts import project_font_diagnostics
from .render_word import export_docx_to_pdf, render_pdf_to_png
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
    mark: Callable[[str, str, Any], None],
    write_json: Callable[[Path, Any], None],
    build_docx_with_repair: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Build and audit either a formal deliverable or a labelled review candidate."""

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

    if render_with_word:
        checkpoint(task_id)
        update_task(task_id, current_stage="render")
        mark("render", "started", {"message": "开始生成 PDF/PNG 并进行渲染复核。"})
        rendered = output_dir / "word_rendered"
        pdf = rendered / "answer_book.pdf"
        export_docx_to_pdf(docx_path, pdf)
        pngs = render_pdf_to_png(pdf, rendered)
        rendered_page_audit = audit_rendered_pages_report(rendered, min_pages=1)
        render_issues = list(rendered_page_audit["issues"])
        delivery_consistency = audit_docx_pdf_consistency(docx_path, pdf)
        render_issues.extend(delivery_consistency["issues"])
        write_json(
            stage_dir / "render_audit.json",
            {
                "ok": not render_issues,
                "issues": render_issues,
                "rendered_page_audit": rendered_page_audit,
                "delivery_consistency": delivery_consistency,
                "project_fonts": project_font_diagnostics(),
            },
        )
        build_shadow_quality_report(stage_dir)
        if render_issues:
            mark("render", "failed", {"issues": render_issues[:30]})
            raise RuntimeError("Rendered page audit failed")
        mark("render", "passed", {"pdf": str(pdf), "png_count": len(pngs)})

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
    formal_docx_path.unlink(missing_ok=True)
    docx_path.replace(formal_docx_path)
    docx_path = formal_docx_path
    final_report = build_final_acceptance_report(
        stage_dir,
        output_dir,
        require_render=render_with_word,
    )
    if not final_report.get("delivery_ready", final_report.get("ok", False)):
        primary_docx, review_candidate_docx = finalize_primary_docx_filename(
            output_dir,
            docx_path,
            final_report,
        )
        write_json(stage_dir / "final_acceptance_report.json", final_report)
        mark("final_acceptance", "failed", {"issues": final_report["issues"][:30]})
        raise RuntimeError("Final acceptance audit failed")
    primary_docx, review_candidate_docx = finalize_primary_docx_filename(
        output_dir,
        docx_path,
        final_report,
    )
    if review_candidate_docx:
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
