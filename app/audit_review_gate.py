from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .capabilities.audit_adapters import findings_from_report, legacy_issue_code
from .capabilities.quality import PolicyAction
from .capabilities.quality_governance import build_unattended_policy, governance_for
from .task_store import append_event, task_dir, update_task


class AuditRejectedByUser(RuntimeError):
    pass


def review_request_path(task_id: str) -> Path:
    return task_dir(task_id) / "review_decision_request.json"


def review_response_path(task_id: str) -> Path:
    return task_dir(task_id) / "review_decision_response.json"


def user_allowed_audit_path(stage_dir: Path) -> Path:
    return stage_dir / "user_allowed_audit_issues.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _qid_from_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("question_id") or "").strip()
    text = str(item)
    return text.split(":", 1)[0].strip() if ":" in text else ""


def _normalize_items(stage: str, report: dict[str, Any], issue_kind: str = "issues") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in report.get(issue_kind, []) if isinstance(report, dict) else []:
        if isinstance(raw, dict):
            message = str(raw.get("message") or raw.get("code") or raw)
            qid = str(raw.get("question_id") or "").strip()
            code = str(raw.get("code") or "").strip()
            severity = str(raw.get("severity") or ("issue" if issue_kind == "issues" else "warning"))
        else:
            message = str(raw)
            qid = _qid_from_item(raw)
            code = ""
            severity = "issue" if issue_kind == "issues" else "warning"
        out.append(
            {
                "stage": stage,
                "question_id": qid,
                "code": code,
                "severity": severity,
                "message": message,
                "display": _display_hint(stage, message),
            }
        )
    return out


def _display_hint(stage: str, message: str) -> str:
    if stage == "answer_coverage":
        return "该题在最终解析中可能缺失、重复、题号不匹配，或答案仍是待复核占位。"
    if stage == "content_quality":
        return "该题会进入最终 Word 的对应题目解析区，同时在存疑题目审查文档中列出原因。"
    if stage == "docx":
        return "该问题发生在 Word 生成或 DOCX 审计环节；若允许通过，将保留审查记录并尝试使用当前可生成版本继续。"
    return "该问题会写入最终审查报告，供交付前复核。"


def get_pending_review_decision(task_id: str) -> dict[str, Any]:
    request = _read_json(review_request_path(task_id))
    if request.get("status") != "waiting":
        return {"ok": True, "pending": False}
    response = _read_json(review_response_path(task_id))
    if response.get("request_id") == request.get("request_id") and response.get("decision") in {"allow", "reject"}:
        return {"ok": True, "pending": False, "response": response}
    return {"ok": True, "pending": True, "request": request}


def submit_review_decision(task_id: str, decision: str, note: str = "") -> dict[str, Any]:
    decision = str(decision or "").strip()
    if decision not in {"allow", "reject"}:
        raise ValueError("decision must be allow or reject")
    request = _read_json(review_request_path(task_id))
    request_id = str(request.get("request_id") or "")
    response = {
        "request_id": request_id,
        "decision": decision,
        "note": str(note or "").strip(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    review_response_path(task_id).write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(task_id, f"review_decision_{decision}", response)
    return {"ok": True, "response": response}


def _append_allowed(stage_dir: Path, request: dict[str, Any], response: dict[str, Any]) -> None:
    path = user_allowed_audit_path(stage_dir)
    data = _read_json(path)
    entries = list(data.get("entries", [])) if isinstance(data.get("entries"), list) else []
    entries.append(
        {
            "request_id": request.get("request_id"),
            "stage": request.get("stage"),
            "title": request.get("title"),
            "items": request.get("items", []),
            "decision": response.get("decision"),
            "user_note": response.get("note", ""),
            "decided_at": response.get("updated_at"),
        }
    )
    path.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_allowed_to_audit_report(stage_dir: Path, stage: str, report: dict[str, Any], request: dict[str, Any], response: dict[str, Any], output_json: Path | None = None) -> dict[str, Any]:
    allowed_items = request.get("items", [])
    warnings = list(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else []
    warnings.extend(
        {
            "stage": stage,
            "question_id": item.get("question_id", ""),
            "code": item.get("code", "user_allowed_audit_issue"),
            "severity": "warning",
            "message": f"用户允许通过审查问题：{item.get('message', '')}",
            "user_allowed": True,
        }
        for item in allowed_items
    )
    updated = dict(report)
    updated["ok"] = True
    updated["issues"] = []
    updated["issue_count"] = 0
    updated["warnings"] = warnings
    updated["warning_count"] = len(warnings)
    updated["user_allowed_issues"] = allowed_items
    updated["user_allowed"] = True
    _append_allowed(stage_dir, request, response)
    if output_json:
        output_json.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated


def auto_allow_audit_report(
    stage_dir: Path,
    stage: str,
    report: dict[str, Any],
    *,
    title: str,
    output_json: Path | None = None,
    note: str = "系统按规则默认允许通过；问题保留在最终审查报告中。",
) -> dict[str, Any]:
    issues = _normalize_items(stage, report, "issues")
    if not issues:
        return report
    request = {
        "request_id": f"{stage}_auto_{int(time.time())}",
        "stage": stage,
        "title": title,
        "items": issues,
        "status": "auto_allowed",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    response = {
        "request_id": request["request_id"],
        "decision": "allow",
        "note": note,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    updated = apply_allowed_to_audit_report(stage_dir, stage, report, request, response, None)
    warnings = [item for item in report.get("warnings", []) if item]
    warnings.extend(
        {
            "stage": stage,
            "question_id": item.get("question_id", ""),
            "code": item.get("code", "auto_allowed_audit_issue"),
            "severity": "warning",
            "message": f"系统按规则默认允许通过审查问题：{item.get('message', '')}",
            "auto_allowed": True,
        }
        for item in issues
    )
    updated["warnings"] = warnings
    updated["warning_count"] = len(warnings)
    updated["auto_allowed_issues"] = issues
    updated["auto_allowed"] = True
    if output_json:
        output_json.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated


def enforce_unattended_audit_report(
    report: dict[str, Any],
    *,
    source: str,
    output_json: Path | None = None,
) -> dict[str, Any]:
    """Apply evidence-aware policy without waiting for or simulating a person."""

    policy = build_unattended_policy()
    blocking: list[Any] = []
    advisory: list[Any] = list(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else []
    decisions: list[dict[str, Any]] = []
    raw_issues = report.get("issues", []) if isinstance(report.get("issues"), list) else []
    for raw in raw_issues:
        findings = findings_from_report(
            {"issues": [raw]},
            source=source,
            code_resolver=legacy_issue_code,
        )
        if not findings:
            continue
        finding = findings[0]
        governance = governance_for(finding.code)
        action = policy.action_for(finding)
        decision = {
            **finding.to_dict(),
            "action": action.value,
            "governance": governance.to_dict(),
        }
        decisions.append(decision)
        if action is PolicyAction.BLOCK:
            blocking.append(raw)
        else:
            raw_value = dict(raw) if isinstance(raw, dict) else {"message": str(raw)}
            advisory.append(
                {
                    **raw_value,
                    "original_severity": "issue",
                    "severity": "warning",
                    "unattended_action": action.value,
                    "governance_code": finding.code,
                }
            )
    updated = {
        **report,
        "ok": not blocking,
        "issues": blocking,
        "issue_count": len(blocking),
        "warnings": advisory,
        "warning_count": len(advisory),
        "governance_mode": "unattended",
        "human_review_required": False,
        "governance_decisions": decisions,
        "blocked_count": len(blocking),
        "advisory_issue_count": len(raw_issues) - len(blocking),
    }
    if output_json:
        output_json.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated


def wait_for_user_review_decision(
    task_id: str,
    stage: str,
    report: dict[str, Any],
    stage_dir: Path,
    *,
    title: str,
    output_json: Path | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    issues = _normalize_items(stage, report, "issues")
    if not issues:
        return report
    request_id = f"{stage}_{int(time.time())}"
    request = {
        "status": "waiting",
        "request_id": request_id,
        "stage": stage,
        "title": title,
        "message": "模型回修和本地修复后仍存在审查问题，需要用户决定是否允许继续。",
        "items": issues,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    review_request_path(task_id).write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(task_id, "review_decision_waiting", request)
    update_task(task_id, status="paused", current_stage="review_decision", error=title)
    start = time.time()
    while True:
        response = _read_json(review_response_path(task_id))
        if str(response.get("request_id") or "") == request_id:
            if response.get("decision") == "allow":
                request["status"] = "allowed"
                review_request_path(task_id).write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
                update_task(task_id, status="running", current_stage=stage, error="")
                return apply_allowed_to_audit_report(stage_dir, stage, report, request, response, output_json)
            if response.get("decision") == "reject":
                request["status"] = "rejected"
                review_request_path(task_id).write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
                update_task(task_id, status="failed", current_stage=stage, error="用户拒绝审查问题通过")
                raise AuditRejectedByUser("用户拒绝审查问题通过")
        if timeout_seconds is not None and time.time() - start > timeout_seconds:
            raise TimeoutError("等待用户审查决策超时")
        time.sleep(1)
