from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from .docx_v4 import build_docx_from_fragments
from .final_acceptance import build_final_acceptance_report
from .model_usage_report import MODEL_USAGE_REPORT_NAME


STAGE_REPORTS = [
    "final_acceptance_report.json",
    "acceptance_report.json",
    "pipeline_status.json",
    "knowledge_plans.json",
    "evidence_selection.json",
    "题目依据排查.csv",
    "answer_drafts.json",
    "answer_fragments.json",
    "exam_structure_audit.json",
    "retrieval_audit.json",
    "answer_coverage_audit.json",
    "content_quality_audit.json",
    "answer_review_notes.json",
    "question_review_docx.json",
    "user_allowed_audit_issues.json",
    "docx_audit.json",
    "render_audit.json",
    "question_review.csv",
]


def _build_candidate_fragments(stage_dir: Path, output_dir: Path) -> tuple[Path | None, int]:
    source = stage_dir / "answer_fragments.json"
    if not source.exists():
        return None, 0
    data = json.loads(source.read_text(encoding="utf-8"))
    candidate_count = 0
    fragments = []
    for fragment in data.get("fragments", []):
        candidate = fragment.get("_review_candidate_fragment") if isinstance(fragment, dict) else None
        if isinstance(candidate, dict):
            replacement = dict(candidate)
            warnings = [str(item) for item in replacement.get("warnings", []) if str(item).strip()]
            notice = "用户确认允许使用待复核前的模型解析候选版本。"
            if notice not in warnings:
                warnings.append(notice)
            replacement["warnings"] = warnings
            meta = dict(replacement.get("_meta") or {})
            meta["review_candidate_used_by_user"] = True
            replacement["_meta"] = meta
            fragments.append(replacement)
            candidate_count += 1
        else:
            fragments.append(fragment)
    if candidate_count == 0:
        return None, 0
    delivery_dir = output_dir / "delivery"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    candidate_json = delivery_dir / "answer_fragments.review_candidate.json"
    candidate_json.write_text(
        json.dumps({**data, "fragments": fragments, "review_candidate_count": candidate_count}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return candidate_json, candidate_count


def build_task_delivery_package(
    task_id: str,
    stage_dir: Path,
    output_dir: Path,
    allow_warnings: bool = True,
    allow_review_acknowledgement: bool = False,
    review_policy: str = "ask",
) -> dict[str, Any]:
    final = build_final_acceptance_report(stage_dir, output_dir, require_render=True)
    if final["status"] == "failed":
        return {"ok": False, "status": final["status"], "issues": final["issues"], "warnings": final["warnings"], "zip": None}
    review_ack = final.get("review_acknowledgement") or {}
    if review_ack.get("required") and review_policy == "ask" and not allow_review_acknowledgement:
        return {
            "ok": False,
            "status": "review_ack_required",
            "issues": [],
            "warnings": final["warnings"],
            "review_acknowledgement": review_ack,
            "review_policy": "ask",
            "zip": None,
        }
    if allow_review_acknowledgement and review_policy == "ask":
        review_policy = "use_candidate"
    if final["status"] == "passed_with_warnings" and not allow_warnings:
        return {"ok": False, "status": final["status"], "issues": ["warnings require acknowledgement"], "warnings": final["warnings"], "zip": None}

    delivery_dir = output_dir / "delivery"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    zip_path = delivery_dir / f"{task_id}_delivery.zip"
    if zip_path.exists():
        zip_path.unlink()

    added: list[str] = []
    answer_book_path = output_dir / "answer_book.docx"
    candidate_count = 0
    if review_ack.get("required") and review_policy == "use_candidate":
        candidate_json, candidate_count = _build_candidate_fragments(stage_dir, output_dir)
        if candidate_json:
            answer_book_path = delivery_dir / "answer_book.review_candidate.docx"
            build_docx_from_fragments(candidate_json, answer_book_path)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in [
            (answer_book_path, "answer_book.docx"),
            (output_dir / "question_review.docx", "question_review.docx"),
            (output_dir / "作图题全流程图片.docx", "作图题全流程图片.docx"),
            (output_dir / MODEL_USAGE_REPORT_NAME, MODEL_USAGE_REPORT_NAME),
        ]:
            if path.exists():
                zf.write(path, arcname)
                added.append(arcname)
        rendered_dir = output_dir / "word_rendered"
        include_rendered = review_policy != "use_candidate" or candidate_count == 0
        if include_rendered and rendered_dir.exists():
            pdf = rendered_dir / "answer_book.pdf"
            if pdf.exists():
                zf.write(pdf, "answer_book.pdf")
                added.append("answer_book.pdf")
            for png in sorted(rendered_dir.glob("page-*.png")):
                arcname = f"rendered_pages/{png.name}"
                zf.write(png, arcname)
                added.append(arcname)
        for name in STAGE_REPORTS:
            path = stage_dir / name
            if path.exists():
                arcname = f"reports/{name}"
                zf.write(path, arcname)
                added.append(arcname)
        manifest = {
            "task_id": task_id,
            "status": final["status"],
            "warning_count": final["warning_count"],
            "review_acknowledgement": {
                **review_ack,
                "user_allowed": bool(review_ack.get("required") and review_policy == "use_candidate"),
                "review_policy": review_policy,
                "review_candidate_count": candidate_count,
                "review_file_included": "question_review.docx" in added or "reports/question_review.csv" in added,
            },
            "files": added,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        added.append("manifest.json")

    return {
        "ok": True,
        "status": final["status"],
        "warning_count": final["warning_count"],
        "warnings": final["warnings"],
        "review_acknowledgement": review_ack,
        "review_policy": review_policy,
        "review_candidate_count": candidate_count,
        "zip": str(zip_path),
        "file_count": len(added),
        "files": added,
    }
