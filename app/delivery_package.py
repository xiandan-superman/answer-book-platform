from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from .final_acceptance import build_final_acceptance_report
from .model_usage_report import MODEL_USAGE_REPORT_NAME, build_model_usage_report

STAGE_REPORTS = [
    "final_acceptance_report.json",
    "acceptance_report.json",
    "pipeline_status.json",
    "knowledge_plans.json",
    "evidence_selection.json",
    "题目依据排查.csv",
    "answer_drafts.json",
    "answer_fragments.json",
    "answer_checkpoint_reconciliation.json",
    "exam_structure_audit.json",
    "retrieval_audit.json",
    "answer_coverage_audit.json",
    "content_quality_audit.json",
    "academic_expression_audit.json",
    "selective_quality_review.json",
    "quality_shadow_report.json",
    "answer_review_notes.json",
    "question_review_docx.json",
    "docx_audit.json",
    "render_audit.json",
    "question_review.csv",
]


def _integrity_entry(path: Path, arcname: str) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": arcname,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _verify_delivery_zip(zip_path: Path) -> dict[str, Any]:
    issues: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if bad_member := archive.testzip():
                issues.append(f"交付包成员损坏：{bad_member}")
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json")) if "manifest.json" in names else {}
            answer_name = (
                "answer_book_review_candidate.docx"
                if isinstance(manifest, dict) and manifest.get("delivery_tier") == "review_candidate"
                else "answer_book.docx"
            )
            for required in (answer_name, "manifest.json"):
                if required not in names:
                    issues.append(f"交付包缺少必要文件：{required}")
            entries = manifest.get("file_integrity") if isinstance(manifest, dict) else []
            if not isinstance(entries, list) or not entries:
                issues.append("交付包清单缺少文件完整性记录。")
            for entry in entries or []:
                if not isinstance(entry, dict):
                    issues.append("交付包清单包含无效记录。")
                    continue
                name = str(entry.get("path") or "")
                if name not in names:
                    issues.append(f"交付包清单指向缺失文件：{name}")
                    continue
                content = archive.read(name)
                if len(content) != int(entry.get("size_bytes") or -1):
                    issues.append(f"交付包文件大小与清单不一致：{name}")
                if hashlib.sha256(content).hexdigest() != str(entry.get("sha256") or ""):
                    issues.append(f"交付包文件哈希与清单不一致：{name}")
    except Exception as exc:
        issues.append(f"交付包无法校验：{exc}")
    return {"ok": not issues, "issues": issues, "zip": str(zip_path)}


def build_task_delivery_package(
    task_id: str,
    stage_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    final = build_final_acceptance_report(stage_dir, output_dir, require_render=True)
    if final["status"] == "failed":
        return {"ok": False, "status": final["status"], "issues": final["issues"], "warnings": final["warnings"], "zip": None}
    build_model_usage_report(stage_dir, output_dir, task_id)
    advisories = final.get("diagnostic_advisories") or {}

    delivery_dir = output_dir / "delivery"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    zip_path = delivery_dir / f"{task_id}_delivery.zip"
    if zip_path.exists():
        zip_path.unlink()

    added: list[str] = []
    integrity: list[dict[str, Any]] = []
    review_candidate = final.get("delivery_tier") == "review_candidate"
    candidate_path = output_dir / "answer_book_review_candidate.docx"
    answer_book_path = candidate_path if review_candidate and candidate_path.exists() else output_dir / "answer_book.docx"
    answer_book_arcname = "answer_book_review_candidate.docx" if review_candidate else "answer_book.docx"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in [
            (answer_book_path, answer_book_arcname),
            (output_dir / "question_review.docx", "question_review.docx"),
            (output_dir / "作图题全流程图片.docx", "作图题全流程图片.docx"),
            (output_dir / MODEL_USAGE_REPORT_NAME, MODEL_USAGE_REPORT_NAME),
        ]:
            if path.exists():
                zf.write(path, arcname)
                added.append(arcname)
                integrity.append(_integrity_entry(path, arcname))
        rendered_dir = output_dir / "word_rendered"
        if rendered_dir.exists():
            pdf = rendered_dir / "answer_book.pdf"
            if pdf.exists():
                zf.write(pdf, "answer_book.pdf")
                added.append("answer_book.pdf")
                integrity.append(_integrity_entry(pdf, "answer_book.pdf"))
            for png in sorted(rendered_dir.glob("page-*.png")):
                arcname = f"rendered_pages/{png.name}"
                zf.write(png, arcname)
                added.append(arcname)
                integrity.append(_integrity_entry(png, arcname))
        for name in STAGE_REPORTS:
            path = stage_dir / name
            if path.exists():
                arcname = f"reports/{name}"
                zf.write(path, arcname)
                added.append(arcname)
                integrity.append(_integrity_entry(path, arcname))
        manifest = {
            "task_id": task_id,
            "status": final["status"],
            "delivery_tier": final.get("delivery_tier"),
            "formal_acceptance_passed": bool(final.get("formal_acceptance_passed")),
            "warning_count": final["warning_count"],
            "diagnostic_advisories": {
                **advisories,
                "governance_policy": "unattended",
                "review_file_included": "question_review.docx" in added or "reports/question_review.csv" in added,
            },
            "files": added,
            "file_integrity": integrity,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        added.append("manifest.json")

    verification = _verify_delivery_zip(zip_path)
    if not verification["ok"]:
        zip_path.unlink(missing_ok=True)
        return {
            "ok": False,
            "status": "delivery_package_invalid",
            "issues": verification["issues"],
            "warnings": final["warnings"],
            "zip": None,
        }

    return {
        "ok": True,
        "status": final["status"],
        "warning_count": final["warning_count"],
        "warnings": final["warnings"],
        "diagnostic_advisories": advisories,
        "governance_policy": "unattended",
        "zip": str(zip_path),
        "file_count": len(added),
        "files": added,
        "integrity": verification,
    }
