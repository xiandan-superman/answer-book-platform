"""Exam adapter for independently checked Word units, never whole-set approval."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from .answer_coverage_audit import audit_answer_coverage
from .artifact_store import atomic_write_json, sha256_file
from .capabilities.academic_expressions import audit_academic_expressions
from .content_quality_audit import audit_content_quality
from .document_contracts import DOCUMENT_CONTRACT_VERSION
from .docx_audit import audit_docx_v4
from .docx_v4 import build_docx_from_fragments, expected_fragment_formula_count
from .figure_artifact_audit import audit_figure_artifacts
from .figure_size_audit import audit_docx_figure_sizes
from .output_checkpoints import content_sha256
from .pandoc_word import PANDOC_CONTRACT
from .unit_delivery import dependency_units, publish_manifest, read_manifest, store_unit, unit_artifact
from .v4_schema import validate_v4_answer_fragment


def preserve_exam_units(
    stage_dir: Path,
    *,
    structured_exam: dict[str, Any],
    fragments_json: Path,
    selection_data: dict[str, Any],
    checkpoint: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Called after shared input/content processing and before set assembly.

    Each unit is checked against its exact current draft, evidence, images and
    content; an academic/render defect in a sibling cannot invalidate it. Model
    review flags remain unresolved rather than being silently stripped.
    """
    root = stage_dir / "unit_delivery"
    source = json.loads(fragments_json.read_text(encoding="utf-8"))
    if str(source.get("provider") or "").lower() == "demo":
        raise ValueError("演示占位内容不能作为分题成果交付。")
    items = structured_exam.get("items") or []
    groups = dependency_units(items, id_field="question_id")
    drafts_path = stage_dir / "answer_drafts.json"
    drafts = json.loads(drafts_path.read_text(encoding="utf-8")) if drafts_path.exists() else {"drafts": []}
    expected = [{"object_id": str(item["question_id"]), "number": str(item.get("number") or index),
                 "section": str(item.get("section") or "")} for index, item in enumerate(items, start=1)]
    previous = read_manifest(root)
    reusable = {unit["revision"]: unit for unit in previous.get("units", []) if unit.get("status") == "deliverable"}
    units: list[dict[str, Any]] = []
    # Do not revoke a committed snapshot merely because a retry starts. The
    # next committed unit publishes the new scope; abort-before-work keeps the
    # previous, explicitly versioned download available.
    manifest = previous if groups else publish_manifest(root, expected=expected, units=units)
    for group in groups:
        checkpoint()
        ids = group["object_ids"]
        result: dict[str, Any] = {"object_ids": ids, "status": "not_deliverable", "issues": []}
        try:
            if not group["dependency_complete"]:
                raise ValueError("依赖题目缺失，题组不能单独交付。")
            scoped_exam = {**structured_exam, "items": group["items"]}
            rows = [row for row in source.get("fragments", []) if str(row.get("question_id") or "") in ids]
            originals = {str(item["question_id"]): item for item in group["items"]}
            for row in rows:
                original = originals[str(row["question_id"])]
                for field in ("number", "section"):
                    if original.get(field) is not None and str(row.get(field) or "") != str(original[field]):
                        raise ValueError("题号或分节与用户确认结构不一致，未纳入分题成果。")
                rendered_paths = {str(segment.get("path") or "")
                                  for block in row.get("blocks", []) for segment in block.get("segments", [])
                                  if segment.get("type") == "image_ref"}
                if any(not Path(str(path)).is_file() or str(path) not in rendered_paths
                       for path in original.get("image_refs", []) or []):
                    raise ValueError("原题图片缺失或未进入本题 Word 输入，未纳入分题成果。")
            scoped = {**source, "fragments": rows, "document_title": "分题解析成果（非完整试卷）"}
            scoped_drafts = {"drafts": [row for row in drafts.get("drafts", []) if str(row.get("question_id") or "") in ids]}
            selections = {**selection_data, "selections": [row for row in selection_data.get("selections", []) if str(row.get("question_id") or "") in ids]}
            assets = {}
            for row in rows:
                if row.get("_review_flags") or row.get("_review_candidate_issues"):
                    raise ValueError("本题仍有未解决审查意见，未纳入已通过的分题成果。")
                for block in row.get("blocks", []):
                    for segment in block.get("segments", []):
                        if segment.get("type") == "image_ref":
                            path = Path(str(segment.get("path") or ""))
                            if not path.is_absolute():
                                path = stage_dir / path
                                segment["path"] = str(path)
                            assets[str(path)] = sha256_file(path)
            revision = content_sha256({"exam": scoped_exam, "fragments": rows, "drafts": scoped_drafts,
                                       "selection": selections, "assets": assets,
                                       "contracts": [DOCUMENT_CONTRACT_VERSION, PANDOC_CONTRACT, "exam-units-v2"]})
            result["revision"] = revision
            if revision in reusable:
                unit_artifact(root, previous, reusable[revision]["sha256"])
                result = reusable[revision]
            else:
                require_evidence = str(selection_data.get("analysis_profile") or "") != "question_only"
                coverage = audit_answer_coverage(scoped_exam, scoped, require_evidence=require_evidence)
                quality = audit_content_quality(scoped_exam, scoped, scoped_drafts, selections)
                academic = audit_academic_expressions(scoped, structured_exam=scoped_exam, render_preflight=True)
                issues = [issue for row in rows for issue in validate_v4_answer_fragment(row)]
                issues.extend(coverage["issues"])
                issues.extend(quality["issues"])
                issues.extend(academic["issues"])
                if issues:
                    result["issues"] = issues
                else:
                    root.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix=".validate-", dir=root) as raw:
                        work = Path(raw)
                        atomic_write_json(work / "answer_fragments.json", scoped)
                        figures = audit_figure_artifacts(work)
                        if not figures["ok"]:
                            raise ValueError("本题图件缺失或不可读取。")
                        output = work / "unit.docx"
                        build_docx_from_fragments(work / "answer_fragments.json", output)
                        docx_issues = audit_docx_v4(output, min_formulas=sum(expected_fragment_formula_count(row) for row in rows))
                        docx_issues.extend(audit_docx_figure_sizes(output, structured_exam=scoped_exam)["issues"])
                        if docx_issues:
                            result["issues"] = docx_issues
                        else:
                            result = store_unit(root, output.read_bytes(), revision=revision, object_ids=ids,
                                                warnings=coverage["warnings"] + quality["warnings"] + academic["warnings"])
        except Exception as exc:
            # No model retry here: document/environment problems remain local
            # diagnostics. Previously committed units remain available.
            result["issues"] = [f"{type(exc).__name__}: {exc}"]
        units.append(result)
        manifest = publish_manifest(root, expected=expected, units=units)
    return manifest
