from __future__ import annotations

import json
from pathlib import Path
from typing import Any


AUDIT_FILES = {
    "environment": "environment_check.json",
    "exam_structure": "exam_structure_audit.json",
    "retrieval": "retrieval_audit.json",
    "answer_coverage": "answer_coverage_audit.json",
    "content_quality": "content_quality_audit.json",
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
    placeholder_applied = any(stage.get("stage") == "docx_placeholder" and stage.get("status") == "applied" for stage in stages)
    issues: list[str] = []
    warnings: list[str] = []
    for stage in stages:
        if stage.get("status") != "failed":
            continue
        name = str(stage.get("stage") or "unknown")
        if name.startswith("content_quality") or name == "docx":
            warnings.append(f"pipeline_status: {name} failed but this audit stage is non-blocking")
            continue
        if name == "docx_user_allowed_candidate" and placeholder_applied:
            warnings.append("pipeline_status: docx_user_allowed_candidate failed but docx_placeholder succeeded")
            continue
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
        if name == "figure_size":
            return True, [], ["figure_size: audit file missing (legacy task)"]
        return False, [f"{name}: audit file missing"], []
    if name == "environment":
        formula = data.get("formula_conversion", {})
        if not formula.get("preferred_chain_ready"):
            return False, ["environment: preferred formula chain is not ready"], []
        return True, [], []
    ok = bool(data.get("ok", False))
    issues = [f"{name}: {x}" for x in data.get("issues", [])]
    warnings = [f"{name}: {x}" for x in data.get("warnings", [])]
    if name in {"content_quality", "docx"} and not ok:
        review_warnings = [f"{name}: auto allowed after repair attempts: {x}" for x in data.get("issues", [])]
        review_warnings.extend(warnings)
        if not review_warnings:
            review_warnings = [f"{name}: auto allowed after repair attempts"]
        return True, [], review_warnings
    if not ok and not issues:
        issues = [f"{name}: audit did not pass"]
    return ok, issues, warnings


def review_acknowledgement(stage_dir: Path) -> dict[str, Any]:
    fragments_data = read_json(stage_dir / "answer_fragments.json") or {}
    review_docx = read_json(stage_dir / "question_review_docx.json") or {}
    content_quality = read_json(stage_dir / "content_quality_audit.json") or {}
    answer_coverage = read_json(stage_dir / "answer_coverage_audit.json") or {}
    pending_questions: list[dict[str, Any]] = []
    for fragment in fragments_data.get("fragments", []) if isinstance(fragments_data, dict) else []:
        if not isinstance(fragment, dict):
            continue
        answer = str(fragment.get("answer", "")).strip()
        warnings = [str(item) for item in fragment.get("warnings", []) if str(item).strip()]
        flags = [item for item in fragment.get("_review_flags", []) if isinstance(item, dict)]
        if answer in PENDING_REVIEW_ANSWERS or warnings or flags:
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
    required = bool(pending_questions or review_question_count or content_issue_count or coverage_warnings)
    return {
        "required": required,
        "pending_question_count": len(pending_questions),
        "review_question_count": review_question_count,
        "content_quality_issue_count": content_issue_count,
        "answer_coverage_warning_count": len(coverage_warnings),
        "pending_questions": pending_questions[:50],
        "message": "存在待复核或质量审查项，需用户评估后选择导出候选解析版或待复核占位版。" if required else "",
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
            if code == "answer_generation_failed" or "configure provider API key" in message:
                issues.append(f"answer_fragments: {qid} 答案生成失败，{message or code}。")
                break
    return issues


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
        issues.append(message)
        failed_items.append({"question_id": qid, "figure_id": figure_id, "summary": summary})
    if data.get("failed"):
        for item in data.get("failed", []):
            if isinstance(item, dict):
                warnings.append(f"figure_visual_qa: QA request failed for {item.get('question_id') or 'unknown'} / {item.get('figure_id') or 'unknown'}")
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


def build_final_acceptance_report(stage_dir: Path, output_dir: Path, require_render: bool = True) -> dict[str, Any]:
    acceptance = read_json(stage_dir / "acceptance_report.json")
    pipeline = read_json(stage_dir / "pipeline_status.json")
    gates: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
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
        issues.extend(gate_issues)
        warnings.extend(gate_warnings)

    docx = output_dir / "answer_book.docx"
    pdf = output_dir / "word_rendered" / "answer_book.pdf"
    if not docx.exists():
        issues.append(f"output missing: {docx}")
    if require_render and not pdf.exists():
        issues.append(f"rendered PDF missing: {pdf}")

    if not acceptance or acceptance.get("status") != "passed":
        issues.append("acceptance_report.json missing or not passed")
    pipeline_issues, pipeline_warnings = pipeline_failed_stage_findings(pipeline)
    issues.extend(answer_fragment_blocking_findings(stage_dir))
    figure_qa_summary, figure_qa_issues, figure_qa_warnings = figure_visual_qa_findings(stage_dir)
    figure_qa_accounted_for = figure_qa_issues or bool(figure_qa_summary.get("ignored_unreferenced_failed_count"))
    if figure_qa_accounted_for and not visual_qa_has_missing_image(stage_dir):
        pipeline_issues = [issue for issue in pipeline_issues if issue != "pipeline_status.json contains failed stage: figures"]
    issues.extend(pipeline_issues)
    warnings.extend(pipeline_warnings)
    issues.extend(figure_qa_issues)
    warnings.extend(figure_qa_warnings)

    status = "failed" if issues else ("passed_with_warnings" if warnings else "passed")
    review_ack = review_acknowledgement(stage_dir)
    retry_summary = model_retry_summary(stage_dir)
    non_direct_summary = non_direct_evidence_summary(stage_dir)
    report = {
        "ok": not issues,
        "status": status,
        "review_acknowledgement": review_ack,
        "model_retry_summary": retry_summary,
        "non_direct_evidence_summary": non_direct_summary,
        "figure_visual_qa_summary": figure_qa_summary,
        "require_render": require_render,
        "gates": gates,
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
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
