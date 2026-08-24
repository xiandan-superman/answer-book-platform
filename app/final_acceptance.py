from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .question_requirements import answer_figure_required, source_image_required

AUDIT_FILES = {
    "environment": "environment_check.json",
    "exam_structure": "exam_structure_audit.json",
    "retrieval": "retrieval_audit.json",
    "answer_coverage": "answer_coverage_audit.json",
    "content_quality": "content_quality_audit.json",
    "academic_expression": "academic_expression_audit.json",
    "docx": "docx_audit.json",
    "figure_size": "figure_size_audit.json",
    "render": "render_audit.json",
}

PENDING_REVIEW_ANSWERS = {"", "待复核", "待补充", "未完成", "未知"}
NON_DIRECT_SUPPORT_LABELS = {
    "general_principle_support": "通用原理证据",
    "transferable_support": "可迁移证据",
    "inverse_process_support": "反向过程证据",
}
def pipeline_failed_stage_findings(pipeline: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    if not isinstance(pipeline, dict):
        return [], []
    stages = [stage for stage in pipeline.get("stages", []) if isinstance(stage, dict)]
    issues: list[str] = []
    warnings: list[str] = []
    for stage in stages:
        if stage.get("status") != "failed":
            continue
        name = str(stage.get("stage") or "unknown")
        issues.append(f"pipeline_status.json contains failed stage: {name}")
    return issues, warnings


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def audit_ok(name: str, data: dict[str, Any] | None, require_render: bool) -> tuple[bool, list[str], list[str]]:
    if data is None:
        if name == "render" and not require_render:
            return True, [], []
        if name in {"figure_size", "academic_expression"}:
            return True, [], [f"{name}: audit file missing (legacy task)"]
        return False, [f"{name}: audit file missing"], []
    if name == "environment":
        formula = data.get("formula_conversion", {})
        if not formula.get("preferred_chain_ready"):
            return False, ["environment: preferred formula chain is not ready"], []
        return True, [], []
    ok = bool(data.get("ok", False))
    issues = [f"{name}: {x}" for x in data.get("issues", [])]
    warnings = [f"{name}: {x}" for x in data.get("warnings", [])]
    if not ok and not issues:
        issues = [f"{name}: audit did not pass"]
    return ok, issues, warnings


def diagnostic_advisories(stage_dir: Path) -> dict[str, Any]:
    fragments_data = read_json(stage_dir / "answer_fragments.json") or {}
    review_docx = read_json(stage_dir / "question_review_docx.json") or {}
    content_quality = read_json(stage_dir / "content_quality_audit.json") or {}
    answer_coverage = read_json(stage_dir / "answer_coverage_audit.json") or {}
    semantic_quality = read_json(stage_dir / "semantic_quality_advisories.json") or {}
    pending_questions: list[dict[str, Any]] = []
    informational_fragment_warning_count = 0
    for fragment in fragments_data.get("fragments", []) if isinstance(fragments_data, dict) else []:
        if not isinstance(fragment, dict):
            continue
        answer = str(fragment.get("answer", "")).strip()
        warnings = [str(item) for item in fragment.get("warnings", []) if str(item).strip()]
        flags = [item for item in fragment.get("_review_flags", []) if isinstance(item, dict)]
        # A model caveat is useful context for the reader, but it is not by
        # itself an unresolved answer. Hard audits and explicit review flags
        # decide whether the question must remain in the review queue.
        if warnings and answer not in PENDING_REVIEW_ANSWERS and not flags:
            informational_fragment_warning_count += len(warnings)
        if answer in PENDING_REVIEW_ANSWERS or flags:
            pending_questions.append(
                {
                    "question_id": str(fragment.get("question_id", "")).strip(),
                    "section": str(fragment.get("section", "")).strip(),
                    "number": str(fragment.get("number", "")).strip(),
                    "answer": answer,
                    "warning_count": len(warnings),
                    "review_flag_count": len(flags),
                }
            )
    review_question_count = int(review_docx.get("review_question_count", 0) or 0) if isinstance(review_docx, dict) else 0
    content_issue_count = int(content_quality.get("issue_count", 0) or 0) if isinstance(content_quality, dict) else 0
    coverage_warnings = answer_coverage.get("warnings", []) if isinstance(answer_coverage, dict) else []
    semantic_advisories = [
        item for item in semantic_quality.get("advisories", [])
        if isinstance(item, dict)
    ] if isinstance(semantic_quality, dict) else []
    semantic_advisory_count = int(semantic_quality.get("advisory_count", len(semantic_advisories)) or 0)
    unresolved_semantic_ids = {
        str(item).strip()
        for item in semantic_quality.get("unresolved_correctness_question_ids", [])
        if str(item).strip()
    } if isinstance(semantic_quality, dict) else set()
    actionable_semantic_advisory_count = sum(
        1
        for item in semantic_advisories
        if str(item.get("decision") or "").strip().lower() == "repair"
        or str(item.get("question_id") or "").strip() in unresolved_semantic_ids
    )
    review_service_advisory_count = int(semantic_quality.get("review_service_advisory_count", 0) or 0)
    advisory = bool(
        pending_questions
        or content_issue_count
        or coverage_warnings
        or actionable_semantic_advisory_count
        or review_service_advisory_count
    )
    return {
        "advisory": advisory,
        "pending_question_count": len(pending_questions),
        "review_question_count": review_question_count,
        "content_quality_issue_count": content_issue_count,
        "answer_coverage_warning_count": len(coverage_warnings),
        "semantic_model_advisory_count": semantic_advisory_count,
        "actionable_semantic_advisory_count": actionable_semantic_advisory_count,
        "informational_semantic_advisory_count": max(
            0, semantic_advisory_count - actionable_semantic_advisory_count
        ),
        "informational_fragment_warning_count": informational_fragment_warning_count,
        "review_service_advisory_count": review_service_advisory_count,
        "pending_questions": pending_questions[:50],
        "message": "诊断提示已随交付报告保留；正式答案未使用候选版或人工放行。" if advisory else "",
    }


def answer_fragment_blocking_findings(stage_dir: Path) -> list[str]:
    data = read_json(stage_dir / "answer_fragments.json") or {}
    issues: list[str] = []
    provider = str(data.get("provider") or "").strip().lower()
    if provider == "demo":
        issues.append("answer_fragments: 当前结果来自 demo 占位流程，未调用答案模型。")
    for fragment in data.get("fragments", []) if isinstance(data, dict) else []:
        if not isinstance(fragment, dict):
            continue
        qid = str(fragment.get("question_id") or "").strip() or "unknown"
        answer = str(fragment.get("answer") or "").strip()
        if answer in PENDING_REVIEW_ANSWERS:
            issues.append(f"answer_fragments: {qid} 答案仍为待复核/空答案。")
        flags = [item for item in fragment.get("_review_flags", []) if isinstance(item, dict)]
        for flag in flags:
            code = str(flag.get("code") or "").strip()
            message = str(flag.get("message") or "").strip()
            # A preserved review candidate is a usable, explicitly labelled
            # delivery tier. It must not be confused with generation failure.
            if code == "answer_generation_failed" or "configure provider API key" in message:
                issues.append(f"answer_fragments: {qid} 答案生成失败，{message or code}。")
                break
    return issues


def answer_fragment_delivery_summary(stage_dir: Path) -> dict[str, Any]:
    """Separate an unusable answer set from a useful but incomplete one."""
    data = read_json(stage_dir / "answer_fragments.json") or {}
    provider = str(data.get("provider") or "").strip().lower()
    fragments = [item for item in data.get("fragments", []) if isinstance(item, dict)]
    failed_question_ids: list[str] = []
    usable_question_ids: list[str] = []
    for fragment in fragments:
        qid = str(fragment.get("question_id") or "").strip() or "unknown"
        answer = str(fragment.get("answer") or "").strip()
        flags = [item for item in fragment.get("_review_flags", []) if isinstance(item, dict)]
        generation_failed = any(
            str(flag.get("code") or "").strip() == "answer_generation_failed"
            for flag in flags
        )
        if generation_failed or answer in PENDING_REVIEW_ANSWERS:
            failed_question_ids.append(qid)
        elif answer:
            usable_question_ids.append(qid)
    partial_candidate = bool(
        provider != "demo"
        and usable_question_ids
        and failed_question_ids
    )
    return {
        "fragment_count": len(fragments),
        "usable_count": len(usable_question_ids),
        "failed_count": len(failed_question_ids),
        "usable_question_ids": usable_question_ids,
        "failed_question_ids": failed_question_ids,
        "partial_candidate": partial_candidate,
    }


def _retry_stage_from_recovery(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith("_model_repair"):
        return text[: -len("_model_repair")]
    return text or "answer_generation"


def _retry_attempt_strategy(attempt: dict[str, Any]) -> str:
    strategy = str(attempt.get("strategy") or "").strip()
    model = str(attempt.get("model") or "").strip()
    max_tokens = attempt.get("max_tokens")
    thinking = attempt.get("thinking")
    compact = bool(attempt.get("compact_prompt"))
    parts = [strategy or "unknown"]
    if model:
        parts.append(f"model={model}")
    if max_tokens:
        parts.append(f"max_tokens={max_tokens}")
    if thinking:
        parts.append(f"thinking={thinking}")
    if compact:
        parts.append("compact_prompt=true")
    return "，".join(parts)


def _model_retry_row(question_id: str, stage: str, retry_report: dict[str, Any]) -> dict[str, Any] | None:
    attempts = [item for item in retry_report.get("attempts", []) if isinstance(item, dict)]
    if len(attempts) <= 1:
        return None
    errors = [str(item.get("error") or "").strip() for item in attempts if str(item.get("error") or "").strip()]
    final_attempt = attempts[-1] if attempts else {}
    return {
        "question_id": question_id,
        "stage": stage,
        "attempt_count": len(attempts),
        "strategies": [_retry_attempt_strategy(item) for item in attempts],
        "final_strategy": str(final_attempt.get("strategy") or "").strip(),
        "final_model": str(final_attempt.get("model") or "").strip(),
        "final_max_tokens": final_attempt.get("max_tokens"),
        "final_thinking": final_attempt.get("thinking"),
        "error_count": len(errors),
        "errors": errors[:10],
    }


def model_retry_summary(stage_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()

    for filename in ("knowledge_plans.json", "evidence_selection.json", "answer_fragments.json"):
        data = read_json(stage_dir / filename) or {}
        for item in data.get("model_token_feedback", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            row = _model_retry_row(
                str(item.get("question_id") or "").strip(),
                str(item.get("stage") or filename.replace(".json", "")).strip(),
                item,
            )
            if row:
                key = (row["question_id"], row["stage"], tuple(row["strategies"]))
                if key not in seen:
                    rows.append(row)
                    seen.add(key)

    fragments_data = read_json(stage_dir / "answer_fragments.json") or {}
    for fragment in fragments_data.get("fragments", []) if isinstance(fragments_data, dict) else []:
        if not isinstance(fragment, dict):
            continue
        meta = fragment.get("_meta") or {}
        retry_report = meta.get("llm_retry") if isinstance(meta, dict) else None
        if not isinstance(retry_report, dict):
            continue
        row = _model_retry_row(
            str(fragment.get("question_id") or "").strip(),
            _retry_stage_from_recovery(meta.get("recovered_by")),
            retry_report,
        )
        if row:
            key = (row["question_id"], row["stage"], tuple(row["strategies"]))
            if key not in seen:
                rows.append(row)
                seen.add(key)

    return {
        "applied": bool(rows),
        "retry_question_count": len({row["question_id"] for row in rows if row.get("question_id")}),
        "retry_event_count": len(rows),
        "items": rows,
        "message": "存在模型重试策略应用记录，已按题目和阶段列出策略链。" if rows else "",
    }


def non_direct_evidence_summary(stage_dir: Path) -> dict[str, Any]:
    data = read_json(stage_dir / "evidence_selection.json") or {}
    items: list[dict[str, Any]] = []
    for selection in data.get("selections", []) if isinstance(data, dict) else []:
        if not isinstance(selection, dict):
            continue
        qid = str(selection.get("question_id") or "").strip()
        for point in selection.get("knowledge_points", []):
            if not isinstance(point, dict):
                continue
            raw_selected_ids = point.get("selected_evidence_ids", [])
            if isinstance(raw_selected_ids, str):
                raw_selected_ids = [raw_selected_ids]
            selected_ids = [str(item).strip() for item in raw_selected_ids if str(item).strip()]
            if not selected_ids:
                continue
            support_map = point.get("evidence_support_types") if isinstance(point.get("evidence_support_types"), dict) else {}
            point_type = str(point.get("support_type") or "").strip()
            marked: list[dict[str, str]] = []
            for evidence_id in selected_ids:
                support_type = str(support_map.get(evidence_id) or point_type).strip()
                if support_type in NON_DIRECT_SUPPORT_LABELS:
                    marked.append(
                        {
                            "evidence_id": evidence_id,
                            "support_type": support_type,
                            "support_type_label": NON_DIRECT_SUPPORT_LABELS[support_type],
                        }
                    )
            if marked:
                items.append(
                    {
                        "question_id": qid,
                        "knowledge_point": str(point.get("knowledge_point") or "").strip(),
                        "selected_evidence": marked,
                    }
                )
    return {
        "applied": bool(items),
        "question_count": len({item["question_id"] for item in items if item.get("question_id")}),
        "item_count": len(items),
        "items": items[:100],
        "message": "存在通用原理、可迁移或反向过程类教材依据，已在题目依据排查表中标记。" if items else "",
    }


def _image_ref_figure_ids(segment: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    image_id = str(segment.get("image_id") or "").strip()
    if image_id:
        ids.add(image_id)
    image_path = str(segment.get("path") or "").strip()
    if image_path:
        ids.add(Path(image_path.replace("\\", "/")).stem)
    return ids


def referenced_figure_ids(stage_dir: Path) -> set[str]:
    data = read_json(stage_dir / "answer_fragments.json") or {}
    ids: set[str] = set()
    for fragment in data.get("fragments", []) if isinstance(data, dict) else []:
        if not isinstance(fragment, dict):
            continue
        for block in fragment.get("blocks", []):
            if not isinstance(block, dict):
                continue
            for segment in block.get("segments", []):
                if not isinstance(segment, dict) or segment.get("type") != "image_ref":
                    continue
                ids.update(_image_ref_figure_ids(segment))
    return ids


def figure_visual_qa_findings(stage_dir: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    data = read_json(stage_dir / "figure_visual_qa.json")
    if not isinstance(data, dict):
        return {"available": False, "failed_count": 0, "warning_count": 0, "items": []}, [], []
    if not data.get("enabled"):
        return {"available": True, "enabled": False, "failed_count": 0, "warning_count": 0, "items": []}, [], []
    issues: list[str] = []
    warnings: list[str] = []
    failed_items: list[dict[str, Any]] = []
    ignored_items: list[dict[str, Any]] = []
    final_figure_ids = referenced_figure_ids(stage_dir)
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        qa = item.get("qa") if isinstance(item.get("qa"), dict) else {}
        if qa.get("ok") is True:
            continue
        qid = str(item.get("question_id") or "").strip()
        figure_id = str(item.get("figure_id") or "").strip()
        summary = str(qa.get("summary") or qa.get("error") or "视觉 QA 未通过").strip()
        if final_figure_ids and figure_id and figure_id not in final_figure_ids:
            ignored_items.append({"question_id": qid, "figure_id": figure_id, "summary": summary})
            warnings.append(f"figure_visual_qa: ignored unreferenced failed figure {qid or 'unknown'} / {figure_id}")
            continue
        message = f"figure_visual_qa: {qid or 'unknown'} / {figure_id or 'unknown'} failed: {summary}"
        # A visual model's judgment is useful repair evidence but is not a
        # deterministic fact. In unattended mode it may warn, never hard-block.
        warnings.append(message)
        failed_items.append({"question_id": qid, "figure_id": figure_id, "summary": summary})
    if data.get("failed"):
        for item in data.get("failed", []):
            if isinstance(item, dict):
                warnings.append(f"figure_visual_qa: QA request failed for {item.get('question_id') or 'unknown'} / {item.get('figure_id') or 'unknown'}")
    for item in data.get("skipped", []) if isinstance(data.get("skipped"), list) else []:
        if not isinstance(item, dict) or str(item.get("reason") or "") != "figure image missing":
            continue
        issues.append(
            f"figure_visual_qa: {item.get('question_id') or 'unknown'} / "
            f"{item.get('figure_id') or 'unknown'} failed: figure image missing"
        )
    return {
        "available": True,
        "enabled": True,
        "failed_count": len(failed_items),
        "warning_count": len(warnings),
        "items": failed_items[:50],
        "referenced_figure_ids": sorted(final_figure_ids)[:100],
        "ignored_unreferenced_failed_count": len(ignored_items),
        "ignored_unreferenced_failed_items": ignored_items[:50],
    }, issues, warnings


def visual_qa_has_missing_image(stage_dir: Path) -> bool:
    data = read_json(stage_dir / "figure_visual_qa.json")
    if not isinstance(data, dict):
        return False
    return any(
        isinstance(item, dict) and str(item.get("reason") or "") == "figure image missing"
        for item in data.get("skipped", []) or []
    )


def figure_delivery_findings(stage_dir: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    """Audit source-question images and answer-created figures separately."""

    exam = read_json(stage_dir / "structured_exam.json") or {}
    fragments_data = read_json(stage_dir / "answer_fragments.json") or {}
    fragments = {
        str(fragment.get("question_id") or "").strip(): fragment
        for fragment in fragments_data.get("fragments", []) or []
        if isinstance(fragment, dict) and str(fragment.get("question_id") or "").strip()
    }
    issues: list[str] = []
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    for question in exam.get("items", []) or []:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        source_required = source_image_required(question)
        answer_required = answer_figure_required(question)
        if not source_required and not answer_required:
            continue
        source_paths = {str(Path(str(raw))) for raw in question.get("image_refs", []) or [] if str(raw).strip()}
        image_segments = [
            segment
            for block in fragments.get(qid, {}).get("blocks", []) or []
            if isinstance(block, dict)
            for segment in block.get("segments", []) or []
            if isinstance(segment, dict) and segment.get("type") == "image_ref"
        ]
        delivered_source = [
            segment for segment in image_segments
            if str(segment.get("role") or "") == "source_question_image"
            or str(Path(str(segment.get("path") or ""))) in source_paths
        ]
        delivered_answer = [segment for segment in image_segments if segment not in delivered_source]
        item_issues: list[str] = []
        if source_required and not source_paths:
            item_issues.append("题干引用图片，但抽取结果没有 image_refs")
        elif source_required and not delivered_source:
            item_issues.append("原题图片未进入答案片段/Word 输入")
        if answer_required and not delivered_answer:
            item_issues.append("题干要求作图，但答案中没有独立生成图")
        issues.extend(f"figure_delivery: {qid or 'unknown'} {message}" for message in item_issues)
        items.append({
            "question_id": qid,
            "source_required": source_required,
            "answer_required": answer_required,
            "source_delivered_count": len(delivered_source),
            "answer_delivered_count": len(delivered_answer),
            "issues": item_issues,
        })
    return {"ok": not issues, "question_count": len(items), "issue_count": len(issues), "items": items}, issues, warnings


def build_final_acceptance_report(stage_dir: Path, output_dir: Path, require_render: bool = True) -> dict[str, Any]:
    acceptance = read_json(stage_dir / "acceptance_report.json")
    pipeline = read_json(stage_dir / "pipeline_status.json")
    gates: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    formal_issues: list[str] = []
    warnings: list[str] = []
    for name, filename in AUDIT_FILES.items():
        data = read_json(stage_dir / filename)
        ok, gate_issues, gate_warnings = audit_ok(name, data, require_render)
        gates[name] = {
            "ok": ok,
            "issue_count": len(gate_issues),
            "warning_count": len(gate_warnings),
            "path": str(stage_dir / filename),
        }
        # An intact document with unresolved answer-content findings remains a
        # useful review candidate. Content findings prevent formal acceptance
        # but are not artifact-delivery failures.
        if name == "content_quality":
            formal_issues.extend(gate_issues)
        else:
            issues.extend(gate_issues)
        warnings.extend(gate_warnings)

    docx = output_dir / "answer_book.docx"
    pdf = output_dir / "word_rendered" / "answer_book.pdf"
    if not docx.exists():
        issues.append(f"output missing: {docx}")
    if require_render and not pdf.exists():
        issues.append(f"rendered PDF missing: {pdf}")

    if not acceptance or acceptance.get("status") not in {
        "passed",
        "passed_with_warnings",
        "completed_with_issues",
    }:
        issues.append("acceptance_report.json missing or not passed")
    pipeline_issues, pipeline_warnings = pipeline_failed_stage_findings(pipeline)
    answer_delivery_summary = answer_fragment_delivery_summary(stage_dir)
    answer_fragment_issues = answer_fragment_blocking_findings(stage_dir)
    if answer_fragment_issues and answer_delivery_summary.get("partial_candidate"):
        # Preserve useful completed answers for the user, but keep the whole
        # document outside the formal tier until every failed question is fixed.
        formal_issues.extend(answer_fragment_issues)
    else:
        issues.extend(answer_fragment_issues)
    figure_qa_summary, figure_qa_issues, figure_qa_warnings = figure_visual_qa_findings(stage_dir)
    figure_qa_accounted_for = bool(
        figure_qa_issues
        or figure_qa_summary.get("failed_count")
        or figure_qa_summary.get("ignored_unreferenced_failed_count")
    )
    if figure_qa_accounted_for and not visual_qa_has_missing_image(stage_dir):
        pipeline_issues = [issue for issue in pipeline_issues if issue != "pipeline_status.json contains failed stage: figures"]
    issues.extend(pipeline_issues)
    warnings.extend(pipeline_warnings)
    issues.extend(figure_qa_issues)
    warnings.extend(figure_qa_warnings)
    figure_delivery_summary, figure_delivery_issues, figure_delivery_warnings = figure_delivery_findings(stage_dir)
    issues.extend(figure_delivery_issues)
    warnings.extend(figure_delivery_warnings)

    advisories = diagnostic_advisories(stage_dir)
    delivery_ready = not issues
    has_referenced_visual_semantic_risk = bool(figure_qa_summary.get("failed_count"))
    requires_review = bool(
        formal_issues
        or has_referenced_visual_semantic_risk
        or advisories.get("advisory")
        or warnings
    )
    delivery_tier = (
        "blocked"
        if issues
        else ("review_candidate" if requires_review else "formal")
    )
    # The run status and delivery tier must describe the same user-facing
    # outcome. A readable candidate is useful, but it is not a completed
    # formal delivery and must remain visible in the review queue.
    if delivery_tier == "blocked":
        status = "failed"
    elif delivery_tier == "review_candidate":
        status = "completed_with_issues"
    else:
        status = "passed"
    formal_acceptance_passed = delivery_tier == "formal"
    retry_summary = model_retry_summary(stage_dir)
    non_direct_summary = non_direct_evidence_summary(stage_dir)
    shadow_quality = read_json(stage_dir / "quality_shadow_report.json") or {}
    report = {
        # ``ok`` remains the backwards-compatible machine-delivery gate.
        # Formal acceptance is deliberately stricter for referenced figures
        # that visual QA identifies as scientifically or semantically wrong.
        "ok": delivery_ready,
        "delivery_ready": delivery_ready,
        "formal_acceptance_passed": formal_acceptance_passed,
        "status": status,
        "delivery_tier": delivery_tier,
        "delivery_tier_label": {
            "formal": "正式解析版",
            "review_candidate": "可交付待复核候选版",
            "blocked": "阻断，不应作为正式结果交付",
        }[delivery_tier],
        "diagnostic_advisories": advisories,
        "model_retry_summary": retry_summary,
        "non_direct_evidence_summary": non_direct_summary,
        "quality_shadow_summary": {
            "available": bool(shadow_quality),
            "enforced": bool(shadow_quality.get("enforced", False)),
            "finding_count": int(shadow_quality.get("finding_count", 0) or 0),
            "would_block_count": int(shadow_quality.get("would_block_count", 0) or 0),
            "would_warn_count": int(shadow_quality.get("would_warn_count", 0) or 0),
            "path": str(stage_dir / "quality_shadow_report.json"),
        },
        "figure_visual_qa_summary": figure_qa_summary,
        "figure_delivery_summary": figure_delivery_summary,
        "answer_fragment_delivery_summary": answer_delivery_summary,
        "require_render": require_render,
        "gates": gates,
        "issue_count": len(issues) + len(formal_issues),
        "delivery_issue_count": len(issues),
        "formal_issue_count": len(formal_issues),
        "warning_count": len(warnings),
        "issues": [*issues, *formal_issues],
        "delivery_issues": issues,
        "formal_issues": formal_issues,
        "warnings": warnings,
        "outputs": {
            "docx": str(docx),
            "docx_exists": docx.exists(),
            "pdf": str(pdf),
            "pdf_exists": pdf.exists(),
        },
        "acceptance_report": acceptance,
    }
    (stage_dir / "final_acceptance_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
