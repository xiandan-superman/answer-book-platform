from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .pipeline import output_dir, stage_dir
from .task_store import load_task, task_dir

STAGE_LABELS = {
    "environment": "运行环境检查",
    "extract_exam": "真题结构抽取",
    "exam_structure_review": "真题结构、题型与分值确认",
    "question_understanding": "题面图表理解",
    "figure_schema_planning": "专业作图 Schema 规划",
    "textbook_index": "教材索引加载",
    "knowledge_planning": "考查内容判断",
    "retrieval": "教材候选依据检索",
    "evidence_selection": "教材依据二次确认",
    "answer_generation": "结构化解析生成",
    "answer_coverage": "答案覆盖检查",
    "content_quality": "内容质量审查",
    "figures": "配图生成",
    "docx": "Word 文档生成",
    "question_review": "存疑审查文档生成",
    "render": "PDF/PNG 渲染复核",
    "delivery_short_circuit": "答案不可用，跳过文档渲染",
    "final_acceptance": "最终验收",
    "acceptance": "交付验收",
    "pipeline": "生产流程",
    "uploading": "上传任务到混合云",
    "hybrid_upload": "上传任务到混合云",
    "cloud_queue": "等待混合云执行",
    "cloud_pipeline": "混合云解析流程",
    "completed": "已完成",
}

STAGE_FILES = {
    "environment": ["environment_check.json"],
    "extract_exam": ["structured_exam.json", "exam_structure_audit.json"],
    "exam_structure_review": ["structured_exam.json", "exam_structure_review_request.json", "exam_structure_review_response.json"],
    "question_understanding": ["question_understanding.json"],
    "figure_schema_planning": ["figure_schema_plan.json", "structured_exam.json"],
    "textbook_index": ["textbook_index_status.json", "textbook_blocks.csv", "textbook_page_map.csv"],
    "knowledge_planning": ["knowledge_plans.json", "knowledge_planning_progress.json"],
    "retrieval": ["retrieval_candidates.csv", "retrieval_audit.json"],
    "evidence_selection": ["evidence_selection.json", "confirmed_evidence_candidates.csv", "evidence_selection_progress.json"],
    "answer_generation": [
        "answer_fragments.json",
        "answer_generation_progress.json",
        "answer_drafts.json",
        "answer_checkpoint_reconciliation.json",
    ],
    "answer_coverage": ["answer_coverage_audit.json", "answer_review_notes.json"],
    "content_quality": ["content_quality_audit.json", "question_review_docx.json", "figure_generation_audit.json", "figure_visual_qa.json"],
    "docx": ["docx_audit.json"],
    "render": ["render_audit.json"],
    "delivery_short_circuit": ["document_delivery_skip.json"],
    "final_acceptance": ["final_acceptance_report.json", "acceptance_report.json"],
}

ERROR_LABELS = {
    "Content quality audit failed": "内容质量审查未通过",
    "Answer coverage audit failed": "答案覆盖检查未通过",
    "Answer generation failed v4 validation": "结构化解析生成未通过格式校验",
    "Evidence selection failed": "教材依据二次确认失败",
    "Retrieval audit failed": "教材候选依据检索审查未通过",
    "Knowledge planning failed": "考查内容判断失败",
    "Exam structure audit failed": "真题结构抽取审查未通过",
    "DOCX v4 audit failed": "Word 文档审查未通过",
    "Rendered page audit failed": "PDF/PNG 渲染复核未通过",
    "Final acceptance audit failed": "最终验收未通过",
}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def _read_events(path: Path, limit: int = 80) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = {"time": "", "event": "unparsed_log_line", "payload": {"line": line[:500]}}
        if isinstance(row, dict):
            rows.append(row)
    return rows[-limit:]


def _stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage or "未知阶段")


def _error_label(error: str) -> str:
    normalized = str(error or "").strip()
    if (
        ("latin-1" in normalized and "encode" in normalized)
        or "上传请求头编码失败" in normalized
    ):
        return "上传任务标识编码失败；请更新客户端与混合云服务端后重试。"
    return ERROR_LABELS.get(normalized, normalized)


def _compact_issue(raw: Any, *, default_stage: str, severity: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        qid = str(raw.get("question_id") or raw.get("question") or "").strip()
        code = str(raw.get("code") or raw.get("type") or "").strip()
        message = str(raw.get("message") or raw.get("error") or raw.get("issue") or raw).strip()
        raw_severity = str(raw.get("severity") or severity).strip()
    else:
        text = str(raw).strip()
        figure_match = re.match(r"figure_visual_qa:\s*([^\s/]+)", text)
        numbered_match = re.search(r"第\s*([^\s题]+)\s*题", text)
        qid = figure_match.group(1) if figure_match else (numbered_match.group(1) if numbered_match else "")
        code = "figure_visual_qa" if figure_match else ("pipeline_failed_stage" if text.startswith("pipeline_status.json") else "")
        message = text
        raw_severity = severity
    return {
        "stage": default_stage,
        "stage_label": _stage_label(default_stage),
        "severity": raw_severity or severity,
        "question_id": qid,
        "code": code,
        "message": message[:800],
    }


def _collect_detail_issues(stage: str, detail: Any) -> list[dict[str, Any]]:
    if not isinstance(detail, dict):
        return []
    out: list[dict[str, Any]] = []
    for raw in detail.get("issues") or []:
        out.append(_compact_issue(raw, default_stage=stage, severity="issue"))
    for raw in detail.get("warnings") or []:
        out.append(_compact_issue(raw, default_stage=stage, severity="warning"))
    return out


def _collect_file_issues(sdir: Path, stage: str) -> list[dict[str, Any]]:
    names = {
        "exam_structure_audit.json": "extract_exam",
        "retrieval_audit.json": "retrieval",
        "answer_coverage_audit.json": "answer_coverage",
        "content_quality_audit.json": "content_quality",
        "answer_checkpoint_reconciliation.json": "answer_generation",
        "docx_audit.json": "docx",
        "render_audit.json": "render",
        "final_acceptance_report.json": "final_acceptance",
    }
    out: list[dict[str, Any]] = []
    for filename, default_stage in names.items():
        if stage and default_stage != stage:
            continue
        data = _read_json(sdir / filename)
        if not isinstance(data, dict):
            continue
        if filename == "answer_checkpoint_reconciliation.json":
            for raw in data.get("inconsistencies") or []:
                out.append(_compact_issue(raw, default_stage=default_stage, severity="warning"))
            for category, code in (
                ("missing_question_ids", "checkpoint_fragment_missing"),
                ("invalid_question_ids", "checkpoint_fragment_invalid"),
                ("duplicate_question_ids", "checkpoint_fragment_duplicate"),
                ("foreign_question_ids", "checkpoint_fragment_foreign"),
            ):
                for qid in data.get(category) or []:
                    out.append(
                        _compact_issue(
                            {
                                "question_id": qid,
                                "code": code,
                                "message": f"断点对账：{qid} 属于 {category}，恢复时不会直接复用。",
                                "severity": "warning",
                            },
                            default_stage=default_stage,
                            severity="warning",
                        )
                    )
            continue
        out.extend(_collect_detail_issues(default_stage, data))
    return out


def _dedupe_issues(issues: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for issue in issues:
        key = (issue.get("stage"), issue.get("severity"), issue.get("question_id"), issue.get("code"), issue.get("message"))
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
        if len(out) >= limit:
            break
    return out


def _question_summary(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_qid: dict[str, dict[str, Any]] = {}
    for issue in issues:
        qid = str(issue.get("question_id") or "").strip()
        if not qid:
            continue
        row = by_qid.setdefault(qid, {"question_id": qid, "issue_count": 0, "warning_count": 0, "messages": []})
        if issue.get("severity") == "warning":
            row["warning_count"] += 1
        else:
            row["issue_count"] += 1
        if len(row["messages"]) < 4:
            row["messages"].append(issue.get("message", ""))
    return sorted(by_qid.values(), key=lambda x: (-(x["issue_count"] + x["warning_count"]), x["question_id"]))[:30]


def _recommendations(stage: str, error: str, issues: list[dict[str, Any]]) -> list[str]:
    recs: list[str] = []
    if stage in {"uploading", "hybrid_upload"}:
        if "任务标识编码失败" in error or "请求头编码失败" in error:
            recs.append("这是上传协议编码问题；请同步更新客户端和混合云服务端，然后从检查点重跑。")
            recs.append("无需修改中文任务名，也无需重新上传或重新建立教材索引。")
        else:
            recs.append("检查混合云地址、访问令牌和网络连通性，然后从检查点重跑。")
    elif stage == "extract_exam":
        recs.append("检查真题 DOCX 是否包含异常标题、分栏、题号缺失或扫描图片题。")
        recs.append("打开 structured_exam.json 和 exam_structure_audit.json，确认题目切分数量与原卷一致。")
    elif stage == "textbook_index":
        recs.append("检查教材文件是否可读取，尤其是 PDF 页码、扫描版文本层和教材目录页。")
        recs.append("查看 textbook_index_status.json 中的 page_map_issues，并必要时做页码校准。")
    elif stage in {"knowledge_planning", "evidence_selection", "answer_generation"}:
        recs.append("优先检查模型 API Key、模型返回是否被截断，以及对应 progress/json 文件中的失败题号。")
        recs.append("如果问题集中在少数题，先抽取这些题的候选依据和模型输出做单题复核。")
    elif stage == "retrieval":
        recs.append("检查教材索引是否覆盖相关章节，并查看 retrieval_audit.json 的候选依据缺失原因。")
    elif stage == "content_quality":
        recs.append("优先处理 issue 级别题目；warning 可作为审查提示，不一定阻断生成。")
        recs.append("对 missing_confirmed_evidence 类问题，核对 evidence_selection.json 与 answer_fragments.json 的 evidence_ids 是否一致。")
    elif stage == "docx":
        recs.append("检查 Word 生成审计中的公式对象、图片关系和段落结构，再从文档阶段重跑。")
    elif stage == "render":
        recs.append("检查 PDF/PNG 渲染审计与本机文档工具链，确认页面完整且没有空白或截断。")
    elif stage == "final_acceptance":
        messages = "\n".join(str(item.get("message") or "") for item in issues)
        figure_failure = bool(
            "figure_visual_qa" in messages
            or "figure_size" in messages
            or "inline figure images" in messages
            or "failed stage: figures" in messages
            or "图件" in messages
        )
        if figure_failure:
            recs.append("先按题号检查缺失图件和专业视觉审查失败项，再从配图阶段重跑；不要直接导出当前文档。")
        if ("docx" in messages.lower() or "Word" in messages or "公式" in messages) and not figure_failure:
            recs.append("检查 Word 生成审计、公式对象和图片关系，修复后重新执行文档与最终验收。")
        if "render" in messages.lower() or "PDF" in messages or "PNG" in messages:
            recs.append("检查 PDF/PNG 渲染复核结果与本机文档工具链，修复后重新执行渲染与最终验收。")
        if not recs:
            recs.append("按最终验收报告中的阻断项回到对应阶段修复，全部通过后再重新验收。")
    elif "API key" in error or "Provider request" in error:
        recs.append("检查模型服务商、API Key、网络连通性和模型名称。")
    if issues and not recs:
        recs.append("先按下方题号列表逐题排查 issue 级别问题，再处理 warning。")
    if not recs:
        recs.append("当前没有发现明确失败项；可查看最近日志确认任务是否仍在运行或等待下一阶段。")
    return recs[:5]


def _related_files(sdir: Path, odir: Path, stage: str) -> list[dict[str, Any]]:
    files = [
        "pipeline_status.json",
        "pipeline_error.json",
        "events.jsonl",
        "hybrid_preprocess.json",
        "hybrid_preprocess_error.json",
        "hybrid_local_environment.json",
        "hybrid_client_events.jsonl",
        "hybrid_handoff.json",
        "cloud_pipeline_status.json",
        "hybrid_import_receipt.json",
        "hybrid_cloud_worker.json",
        "hybrid_cloud_failure.json",
        "hybrid_client_error.json",
        "hybrid_local_delivery_error.json",
        "academic_expression_audit.local_delivery.json",
        "hybrid_cloud_preflight.json",
    ]
    files.extend(STAGE_FILES.get(stage, []))
    out: list[dict[str, Any]] = []
    seen = set()
    for name in files:
        candidates = [sdir / name]
        if name == "events.jsonl":
            candidates = [sdir.parent / name]
        for path in candidates:
            if path in seen or not path.exists():
                continue
            seen.add(path)
            out.append({"name": path.name, "path": str(path), "kind": "stage" if path.is_relative_to(sdir) else "task"})
    if odir.exists():
        for path in sorted(odir.glob("*")):
            if path.is_file() and path.suffix.lower() in {".docx", ".pdf", ".zip", ".csv"}:
                out.append({"name": path.name, "path": str(path), "kind": "output"})
    return out[:20]


def build_task_diagnostics(task_id: str) -> dict[str, Any]:
    record = load_task(task_id)
    tdir = task_dir(task_id)
    sdir = stage_dir(task_id)
    odir = output_dir(task_id)
    pipeline = _read_json(sdir / "pipeline_status.json") or {"stages": []}
    pipeline_error = _read_json(sdir / "pipeline_error.json") or {}
    events = _read_events(tdir / "events.jsonl")
    stages = pipeline.get("stages") if isinstance(pipeline, dict) else []
    stages = stages if isinstance(stages, list) else []
    failed_stages = [x for x in stages if isinstance(x, dict) and x.get("status") == "failed"]
    actionable_failed_stages = [x for x in failed_stages if x.get("stage") != "pipeline"]
    review_stages = [x for x in stages if isinstance(x, dict) and x.get("status") == "review_required"]
    last_stage = stages[-1] if stages and isinstance(stages[-1], dict) else {}
    primary = actionable_failed_stages[-1] if actionable_failed_stages else (failed_stages[-1] if failed_stages else (review_stages[-1] if review_stages else last_stage))
    stage = str(primary.get("stage") or record.current_stage or "")
    error = _error_label(str(record.error or pipeline_error.get("error") or primary.get("detail", {}).get("error") or ""))
    issues = []
    if isinstance(primary, dict):
        issues.extend(_collect_detail_issues(stage, primary.get("detail")))
    issues.extend(_collect_file_issues(sdir, stage))
    if error and not any(item.get("severity") != "warning" for item in issues):
        issues.append(
            _compact_issue(
                {
                    "code": "task_runtime_failure",
                    "message": error,
                    "severity": "issue",
                },
                default_stage=stage,
                severity="issue",
            )
        )
    issues = _dedupe_issues(issues)
    issue_count = sum(1 for x in issues if x.get("severity") != "warning")
    warning_count = sum(1 for x in issues if x.get("severity") == "warning")
    needs_attention = bool(
        record.status in {"failed", "cancelled"}
        or error
        or issue_count
        or warning_count
        or actionable_failed_stages
        or review_stages
    )
    return {
        "task_id": task_id,
        "task": record.__dict__,
        "status": record.status,
        "current_stage": record.current_stage,
        "primary_stage": stage,
        "primary_stage_label": _stage_label(stage),
        "needs_attention": needs_attention,
        "error": error,
        "summary": {
            "title": "任务需要排查" if needs_attention else "任务日志摘要",
            "stage": _stage_label(stage),
            "issue_count": issue_count,
            "warning_count": warning_count,
            "event_count": len(events),
            "completed_stage_count": sum(1 for x in stages if isinstance(x, dict) and x.get("status") in {"passed", "review_required"}),
        },
        "issues": issues,
        "question_summary": _question_summary(issues),
        "recommendations": _recommendations(stage, error, issues),
        "related_files": _related_files(sdir, odir, stage),
        "recent_events": events[-12:],
        "pipeline_error": pipeline_error,
    }
