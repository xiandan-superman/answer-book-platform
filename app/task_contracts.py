from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class WorkflowType(str, Enum):
    EXAM_ANALYSIS = "exam_analysis"
    PRACTICE_BY_QUESTION = "practice_by_question"
    PRACTICE_BY_KNOWLEDGE = "practice_by_knowledge"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    COMPLETED = "completed"


class QualityStatus(str, Enum):
    UNKNOWN = "unknown"
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class TaskCapabilities:
    view_detail: bool = True
    view_progress: bool = False
    view_result: bool = False
    view_quality: bool = False
    view_files: bool = False
    start: bool = False
    pause: bool = False
    resume: bool = False
    cancel: bool = False
    retry: bool = False
    reopen_review: bool = False
    reuse: bool = False
    download: bool = False
    delete: bool = False


@dataclass(frozen=True)
class ErrorPresentation:
    kind: str
    title: str
    message: str
    retry_hint: str
    support_id: str = ""


PRACTICE_COMPLETION_ISSUES_SCHEMA = "answer_book.practice_completion_issues.v1"
_PRACTICE_ISSUE_PRESENTATION = {
    "configuration_blocked": {
        "priority": 400,
        "label": "需要检查 API 配置",
        "action": "check_configuration",
        "action_label": "检查 API 配置",
        "class_name": "blocked",
        "icon": "fas fa-key",
    },
    "generation_incomplete": {
        "priority": 300,
        "label": "存在未完成题目",
        "action": "continue_incomplete",
        "action_label": "继续未完成项",
        "class_name": "warning",
        "icon": "fas fa-triangle-exclamation",
    },
    "review_required": {
        "priority": 200,
        "label": "题目已生成 · 待复核",
        "action": "review_result",
        "action_label": "查看并处理复核",
        "class_name": "warning",
        "icon": "fas fa-triangle-exclamation",
    },
    "warning_only": {
        "priority": 100,
        "label": "已完成 · 有提示",
        "action": "view_warnings",
        "action_label": "查看提示",
        "class_name": "warning",
        "icon": "fas fa-circle-info",
    },
}


def _nonnegative_int(*values: Any) -> int | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return None


def _practice_issue_reasons(values: Any, *, fallback: str = "", limit: int = 20) -> list[str]:
    rows = values if isinstance(values, list) else []
    reasons: list[str] = []
    for value in rows:
        if isinstance(value, dict):
            text = str(value.get("message") or value.get("summary") or value.get("code") or "").strip()
        else:
            text = str(value or "").strip()
        text = " ".join(text.split())[:300]
        if text and text not in reasons:
            reasons.append(text)
        if len(reasons) >= limit:
            break
    if not reasons and fallback:
        reasons.append(fallback)
    return reasons


def practice_completion_issue_contract(data: dict[str, Any] | None) -> dict[str, Any]:
    """Build the single public truth for terminal practice-result semantics.

    Multiple issue types may coexist. The first item is always the primary
    presentation according to the explicit priority table above.
    """
    data = data if isinstance(data, dict) else {}
    existing_contract = data.get("completion_issues") if isinstance(data.get("completion_issues"), dict) else {}
    existing_issues = [item for item in existing_contract.get("issues") or [] if isinstance(item, dict)]
    generation = data.get("generation") if isinstance(data.get("generation"), dict) else {}
    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    exercises = data.get("exercises") if isinstance(data.get("exercises"), list) else []
    blueprint = data.get("blueprint") if isinstance(data.get("blueprint"), dict) else {}
    exercise_plan = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []

    failed_slots = [
        item for item in exercises
        if isinstance(item, dict) and (
            item.get("generation_status") == "failed"
            or isinstance(item.get("generation_error"), dict)
        )
    ]
    total_count = _nonnegative_int(
        data.get("total_count"), generation.get("total_count"), quality.get("total_count"),
        len(exercise_plan) if exercise_plan else None,
        len(exercises) if exercises else None,
    ) or 0
    failed_count = _nonnegative_int(
        data.get("failed_count"), generation.get("failed_count"), quality.get("failed_count"),
        len(failed_slots) if failed_slots else None,
    ) or 0
    generated_count_value = _nonnegative_int(
        data.get("generated_count"), generation.get("generated_count"), quality.get("generated_count"),
    )
    generated_count = generated_count_value if generated_count_value is not None else max(0, len(exercises) - len(failed_slots))
    unfinished_value = _nonnegative_int(data.get("unfinished_count"), generation.get("unfinished_count"), quality.get("unfinished_count"))
    inferred_unfinished = max(failed_count, total_count - generated_count) if total_count else failed_count
    unfinished_count = max(failed_count, unfinished_value if unfinished_value is not None else inferred_unfinished)

    batch_errors = [item for item in generation.get("batch_errors") or [] if isinstance(item, dict)]
    configuration_blocked = bool(
        data.get("configuration_blocked") is True
        or data.get("requires_configuration") is True
        or generation.get("configuration_blocked") is True
        or generation.get("status") == "configuration_blocked"
        or any(item.get("requires_configuration") is True for item in batch_errors)
        or any(
            isinstance(item, dict)
            and isinstance(item.get("generation_error"), dict)
            and item["generation_error"].get("requires_configuration") is True
            for item in exercises
        )
    )

    review_reasons = _practice_issue_reasons(quality.get("blocking_issues"))
    if not review_reasons:
        review_reasons = _practice_issue_reasons(next(
            (item.get("reasons") for item in existing_issues if item.get("code") == "review_required"),
            [],
        ))
    audit_count = 0
    for item in exercises:
        if not isinstance(item, dict):
            continue
        error = item.get("generation_error") if isinstance(item.get("generation_error"), dict) else {}
        if item.get("audit_status") == "audit_failed" or error.get("code") == "blueprint_audit_failed":
            audit_count += 1
    audit_count = max(audit_count, sum(item.get("code") == "blueprint_audit_failed" for item in batch_errors))
    if audit_count:
        review_reasons.append(f"{audit_count} 题蓝图需要复核")
    semantic = data.get("semantic_review") if isinstance(data.get("semantic_review"), dict) else {}
    semantic_status = str(semantic.get("status") or "").lower()
    semantic_risks = [
        risk
        for item in semantic.get("items") or [] if isinstance(item, dict)
        for risk in item.get("risks") or [] if isinstance(risk, dict)
        and str(risk.get("severity") or "").lower() in {"medium", "high"}
    ]
    if semantic and semantic_status not in {"passed", "warning"}:
        review_reasons.append("语义审查未完成，需人工复核")
    if semantic_risks:
        review_reasons.extend(_practice_issue_reasons(semantic_risks, fallback="语义审查发现需复核风险"))
    if str(quality.get("release_level") or "") == "review_candidate" and not review_reasons:
        review_reasons.append("当前成果需复核后使用")
    review_reasons = list(dict.fromkeys(review_reasons))[:20]
    warning_reasons = _practice_issue_reasons(quality.get("warnings"))
    if not warning_reasons:
        warning_reasons = _practice_issue_reasons(next(
            (item.get("reasons") for item in existing_issues if item.get("code") == "warning_only"),
            [],
        ))

    issues: list[dict[str, Any]] = []

    def add_issue(code: str, reasons: list[str], count: int = 0) -> None:
        presentation = _PRACTICE_ISSUE_PRESENTATION[code]
        issues.append({"code": code, "count": max(0, int(count)), "reasons": reasons, **presentation})

    if configuration_blocked:
        add_issue("configuration_blocked", ["模型服务配置不可用，修正配置后可继续未完成项"], unfinished_count)
    # A legacy partial flag without a trustworthy positive count is not enough
    # to claim that questions are unfinished. This enforces the public count invariant.
    if unfinished_count > 0 or failed_count > 0:
        add_issue("generation_incomplete", [f"{max(unfinished_count, failed_count)} 题尚未完成"], max(unfinished_count, failed_count))
    if review_reasons:
        add_issue("review_required", review_reasons, len(review_reasons))
    if warning_reasons and not review_reasons:
        add_issue("warning_only", warning_reasons, len(warning_reasons))
    elif not issues and (
        generation.get("partial_success") is True
        or generation.get("status") == "partial_success"
        or str(quality.get("status") or "").lower() in {"warning", "warn"}
    ):
        add_issue("warning_only", warning_reasons or ["旧记录含质量提示，请查看结果"], max(1, len(warning_reasons)))

    issues.sort(key=lambda item: int(item.get("priority") or 0), reverse=True)
    primary = dict(issues[0]) if issues else {
        "code": "completed",
        "priority": 0,
        "label": "已完成",
        "action": "view_result",
        "action_label": "查看结果",
        "class_name": "passed",
        "icon": "fas fa-check",
        "count": 0,
        "reasons": [],
    }
    return {
        "schema_version": PRACTICE_COMPLETION_ISSUES_SCHEMA,
        "issues": issues,
        "primary": primary,
        "primary_code": primary["code"],
        "display_label": primary["label"],
        "action": primary["action"],
        "action_label": primary["action_label"],
        "generated_count": generated_count,
        "total_count": total_count,
        "unfinished_count": unfinished_count,
        "failed_count": failed_count,
    }


def public_support_id(value: str = "", *, task_id: str = "") -> str:
    existing = str(value or "").strip()[:80]
    if existing:
        return existing
    stable_task_id = str(task_id or "").strip()
    if not stable_task_id:
        return ""
    digest = hashlib.sha256(stable_task_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"PJ-{digest}"


def quality_presentation(
    workflow: WorkflowType,
    quality: QualityStatus,
    *,
    status: RunStatus | None = None,
    final_acceptance: dict[str, Any] | None = None,
    completion_issues: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    if workflow != WorkflowType.EXAM_ANALYSIS and status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_ISSUES}:
        primary = completion_issues.get("primary") if isinstance(completion_issues, dict) else {}
        if isinstance(primary, dict) and primary.get("label"):
            return {
                "label": str(primary["label"]),
                "class_name": str(primary.get("class_name") or "warning"),
                "icon": str(primary.get("icon") or "fas fa-triangle-exclamation"),
            }
    if quality == QualityStatus.UNKNOWN:
        return None
    if workflow != WorkflowType.EXAM_ANALYSIS and status == RunStatus.FAILED:
        return {
            "label": "生成未完成",
            "class_name": "blocked",
            "icon": "fas fa-triangle-exclamation",
        }
    if quality == QualityStatus.BLOCKED and workflow != WorkflowType.EXAM_ANALYSIS:
        return {"label": "部分题目生成失败", "class_name": "warning", "icon": "fas fa-triangle-exclamation"}
    if quality == QualityStatus.BLOCKED:
        return {"label": "质量阻断", "class_name": "blocked", "icon": "fas fa-octagon-xmark"}
    if workflow == WorkflowType.EXAM_ANALYSIS:
        formally_accepted = False
        review_candidate = False
        if isinstance(final_acceptance, dict):
            review_candidate = final_acceptance.get("delivery_tier") == "review_candidate"
            if "formal_acceptance_passed" in final_acceptance:
                formally_accepted = bool(final_acceptance.get("formal_acceptance_passed"))
            elif final_acceptance.get("status"):
                formally_accepted = str(final_acceptance.get("status")) in {"passed", "passed_with_warnings"}
            else:
                formally_accepted = final_acceptance.get("ok") is True
        if quality == QualityStatus.PASSED and formally_accepted:
            return {"label": "最终验收通过", "class_name": "passed", "icon": "fas fa-shield-check"}
        if quality == QualityStatus.WARNING and formally_accepted:
            return {"label": "正式交付通过 · 含诊断提示", "class_name": "passed", "icon": "fas fa-shield-check"}
        if review_candidate:
            return {"label": "可交付待复核", "class_name": "warning", "icon": "fas fa-triangle-exclamation"}
        if quality == QualityStatus.WARNING:
            return {"label": "质量检查含提示", "class_name": "warning", "icon": "fas fa-triangle-exclamation"}
        return {"label": "质量检查通过", "class_name": "passed", "icon": "fas fa-shield-check"}
    return {"label": "已完成", "class_name": "passed", "icon": "fas fa-check"}


def workflow_for_kind(task_kind: str) -> WorkflowType:
    if task_kind == "knowledge":
        return WorkflowType.PRACTICE_BY_KNOWLEDGE
    if task_kind == "practice":
        return WorkflowType.PRACTICE_BY_QUESTION
    return WorkflowType.EXAM_ANALYSIS


def quality_from_summary(summary: dict[str, Any] | None) -> QualityStatus:
    values = [item for item in (summary or {}).values() if isinstance(item, dict)]
    if not values:
        return QualityStatus.UNKNOWN
    if any(item.get("ok") is False or int(item.get("issue_count") or 0) > 0 for item in values):
        return QualityStatus.BLOCKED
    if any(int(item.get("warning_count") or 0) > 0 for item in values):
        return QualityStatus.WARNING
    return QualityStatus.PASSED


def quality_from_practice(data: dict[str, Any] | None) -> QualityStatus:
    data = data if isinstance(data, dict) else {}
    generation = data.get("generation") if isinstance(data.get("generation"), dict) else {}
    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    generation_status = str(generation.get("status") or "")
    quality_status = str(quality.get("status") or "").lower()
    if generation_status == "configuration_blocked" or generation.get("configuration_blocked") is True:
        return QualityStatus.BLOCKED
    if generation_status == "partial_success" or generation.get("partial_success"):
        return QualityStatus.WARNING
    if quality_status in {"failed", "blocked", "error"}:
        return QualityStatus.BLOCKED
    if quality_status in {"warning", "warn", "needs_review"} or quality.get("warnings"):
        return QualityStatus.WARNING
    if quality_status in {"passed", "ok", "completed"}:
        return QualityStatus.PASSED
    return QualityStatus.UNKNOWN


def present_error(error: str, *, stage: str = "", support_id: str = "") -> ErrorPresentation | None:
    text = str(error or "").strip()
    lowered = text.lower()
    if not text:
        return None
    public_support_id = str(support_id or "").strip()[:80]

    def public(kind: str, title: str, message: str, retry_hint: str) -> ErrorPresentation:
        return ErrorPresentation(kind, title, message, retry_hint, public_support_id)

    if "用户拒绝" in text or "user reject" in lowered:
        return public("review_rejected", "等待修正后重新确认", "本次结构确认已被拒绝，任务没有进入后续生成。", "修正题目结构后，从结构确认阶段继续。")
    if "524" in lowered or "timeout" in lowered or "timed out" in lowered or "超时" in text:
        return public("provider_timeout", "模型服务响应超时", "模型服务在规定时间内没有返回完整结果。", "可从当前安全检查点重试；重试前应确认将复用哪些蓝图和已生成题目。")
    if re.search(r"\b401\b", lowered) or any(marker in lowered for marker in ("unauthorized", "authentication failed", "invalid api key", "invalid_api_key")):
        return public(
            "provider_authentication",
            "模型服务认证失败",
            "API Key 可能无效、已过期，或模型服务未通过认证。",
            "请打开 API 配置，检查并重新测试对应平台的 Key；验证成功后再重试。",
        )
    if re.search(r"\b403\b", lowered) or any(marker in lowered for marker in ("permission denied", "forbidden", "access denied")):
        return public(
            "provider_permission",
            "模型服务权限不足",
            "当前账号或 API Key 可能没有所选模型、Endpoint 或区域的访问权限。",
            "请检查账号和模型权限，或在 API 配置中改用已获授权的模型后再重试。",
        )
    if re.search(r"\b404\b", lowered) or any(marker in lowered for marker in ("invalidendpointormodel", "model or endpoint", "endpoint not found", "model not found")):
        return public(
            "provider_target_not_found",
            "模型服务配置不匹配",
            "所选模型名称、Endpoint 或可用区域可能不匹配，模型服务未找到可用目标。",
            "请打开 API 配置，核对模型名称、Endpoint 和可用区域；连接验证成功后再重试。",
        )
    provider_markers = (
        "provider", "api key", "apikey", "endpoint", "model service", "llmerror",
        "model returned", "model content", "image response", "streaming response", "dashscope",
        "模型服务", "模型连接", "模型调用", "供应商",
    )
    configuration_markers = (
        "configuration", "configured", "credential", "endpoint", "api key", "apikey",
        "配置", "凭据", "认证", "权限",
    )
    if any(marker in lowered for marker in provider_markers) and any(marker in lowered for marker in configuration_markers):
        return public(
            "provider_configuration",
            "模型服务配置不可用",
            "当前模型服务配置不可用，具体原因尚不能确定。",
            "请打开 API 配置，检查平台、Key、模型和 Endpoint，连接验证成功后再重试。",
        )
    if ("strategy" in lowered and "plan" in lowered) or "策略" in text and "一致" in text:
        return public("strategy_mismatch", "出题策略与蓝图不一致", "任务请求的出题策略和已确认蓝图不匹配。", "重新校验蓝图策略后再生成，不应直接沿用冲突参数。")
    if "json" in lowered:
        return public("invalid_model_output", "模型返回格式无效", "模型返回内容无法通过结构化格式校验。", "可重试当前模型步骤；已确认的范围和蓝图不需要重新生成。")
    if "重启" in text or "interrupt" in lowered or "interpreter shutdown" in lowered or "cannot schedule new futures" in lowered:
        return public("interrupted", "任务因服务重启中断", "服务停止时该任务仍在运行，当前引擎无法自动续接。", "从已保存的检查点重新运行，并保留原任务记录用于对照。")
    if stage == "final_acceptance" or "final acceptance" in lowered or "最终验收" in text:
        return public("final_acceptance_failed", "最终验收未通过", "交付产物存在阻断问题，当前结果不能作为正式交付。", "查看具体阻断项，修复对应阶段后重新验收。")
    if stage == "figures" or "figure" in lowered or "图件" in text:
        return public("figure_failed", "图件生成或审查未通过", "至少一个题目图件未正确生成或未通过专业规则审查。", "定位具体题号和图件，从图件阶段修复。")
    if stage in {"docx", "render"} or "docx audit" in lowered:
        return public("document_failed", "文档生成或格式校验未通过", "解析内容已保留，但 Word 生成、格式或渲染检查存在阻断问题。", "查看文档诊断后从已保存的内容重新生成，无需重做已通过的解析。")
    if any(marker in lowered for marker in provider_markers):
        return public(
            "provider_error",
            "模型服务暂时不可用",
            "模型服务返回异常，本次任务没有完整完成。",
            "请先检查 API 配置和模型服务状态；确认配置可用后，再从当前检查点重试。",
        )
    return public("workflow_failed", "任务执行未完成", text, "查看失败阶段和诊断记录后，从匹配的检查点重试。")


def exam_run_status(row: dict[str, Any]) -> RunStatus:
    status = str(row.get("status") or "created")
    stage = str(row.get("current_stage") or "")
    error = str(row.get("error") or "")
    if row.get("exam_structure_review_pending") or row.get("review_decision_pending"):
        return RunStatus.NEEDS_INPUT
    if status == "failed" and ("用户拒绝" in error or stage == "exam_structure_review" and "拒绝" in error):
        return RunStatus.NEEDS_INPUT
    if status in {item.value for item in RunStatus}:
        return RunStatus(status)
    if status in {"created", "pending"}:
        return RunStatus.QUEUED
    return RunStatus.QUEUED


def practice_run_status(engine_status: str, *, operation: str = "", quality: QualityStatus = QualityStatus.UNKNOWN) -> RunStatus:
    if engine_status == "completed" and operation in {"analyze", "plan"}:
        return RunStatus.NEEDS_INPUT
    if engine_status == "completed" and quality in {QualityStatus.WARNING, QualityStatus.BLOCKED}:
        return RunStatus.COMPLETED_WITH_ISSUES
    if engine_status in {item.value for item in RunStatus}:
        return RunStatus(engine_status)
    return RunStatus.QUEUED


def capabilities_for(
    workflow: WorkflowType,
    status: RunStatus,
    *,
    stage: str = "",
    operation: str = "",
    quality: QualityStatus = QualityStatus.UNKNOWN,
    error_kind: str = "",
) -> TaskCapabilities:
    is_exam = workflow == WorkflowType.EXAM_ANALYSIS
    if status == RunStatus.RUNNING:
        return TaskCapabilities(view_progress=True, pause=is_exam, cancel=True)
    if status == RunStatus.PAUSED:
        return TaskCapabilities(view_progress=True, resume=is_exam, cancel=True)
    if status == RunStatus.QUEUED:
        return TaskCapabilities(view_progress=True, start=is_exam, cancel=True, delete=True)
    if status == RunStatus.NEEDS_INPUT:
        return TaskCapabilities(
            view_progress=is_exam,
            view_result=not is_exam and operation in {"analyze", "plan"},
            view_quality=True,
            reopen_review=is_exam and (stage == "exam_structure_review" or error_kind == "review_rejected"),
            delete=True,
        )
    if status == RunStatus.FAILED:
        return TaskCapabilities(view_progress=is_exam, view_quality=True, view_files=is_exam, retry=True, delete=True)
    if status == RunStatus.CANCELLED:
        return TaskCapabilities(view_progress=is_exam, retry=True, delete=True)
    if status == RunStatus.COMPLETED_WITH_ISSUES:
        return TaskCapabilities(
            view_result=True,
            view_quality=True,
            view_files=is_exam,
            reuse=not is_exam,
            download=is_exam,
            delete=True,
        )
    return TaskCapabilities(
        view_result=True,
        view_quality=True,
        view_files=is_exam,
        reuse=not is_exam,
        download=quality != QualityStatus.BLOCKED,
        delete=True,
    )


def enrich_contract(
    row: dict[str, Any],
    *,
    workflow: WorkflowType,
    status: RunStatus,
    quality: QualityStatus,
    stage: str,
    operation: str = "",
    error: str = "",
    support_id: str = "",
    final_acceptance: dict[str, Any] | None = None,
    completion_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    presentation = None if status == RunStatus.CANCELLED else present_error(error, stage=stage, support_id=support_id)
    capabilities = capabilities_for(
        workflow,
        status,
        stage=stage,
        operation=operation,
        quality=quality,
        error_kind=presentation.kind if presentation else "",
    )
    completion_issues = (
        practice_completion_issue_contract(completion_source if isinstance(completion_source, dict) else row)
        if workflow != WorkflowType.EXAM_ANALYSIS and status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_ISSUES}
        else None
    )
    return {
        **row,
        "error": presentation.message if presentation else str(row.get("error") or ""),
        "record_type": "run",
        "workflow_type": workflow.value,
        "engine_status": row.get("status") or "",
        "status": status.value,
        "quality_status": quality.value,
        "quality_presentation": quality_presentation(
            workflow,
            quality,
            status=status,
            final_acceptance=final_acceptance,
            completion_issues=completion_issues,
        ),
        "completion_issues": completion_issues,
        "capabilities": asdict(capabilities),
        "error_presentation": asdict(presentation) if presentation else None,
        "schema_version": 1,
    }
