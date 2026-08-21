from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .quality import FindingSeverity, QualityFinding

CodeResolver = Callable[[str], str]


def _safe_code(value: str, fallback: str) -> str:
    code = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").strip().lower()).strip("_.-")
    return code or fallback


def _qualified_code(source: str, code: str, fallback: str) -> str:
    prefix = _safe_code(source, "audit")
    normalized = _safe_code(code, fallback)
    return normalized if normalized.startswith(f"{prefix}.") else f"{prefix}.{normalized}"


def _entry_finding(
    entry: Any,
    *,
    source: str,
    severity: FindingSeverity,
    confidence: float,
    fallback_code: str,
    code_resolver: CodeResolver | None,
) -> QualityFinding | None:
    if isinstance(entry, Mapping):
        message = str(entry.get("message") or entry.get("summary") or entry.get("error") or "").strip()
        raw_code = str(entry.get("code") or "").strip()
        subject_id = str(entry.get("question_id") or entry.get("figure_id") or entry.get("subject_id") or "").strip()
        evidence = {str(key): value for key, value in entry.items() if key not in {"message", "severity"}}
    else:
        message = str(entry or "").strip()
        raw_code = ""
        subject_id = ""
        evidence = {"legacy_message": message}
    if not message:
        return None
    inferred_code = raw_code or (code_resolver(message) if code_resolver else fallback_code)
    return QualityFinding(
        code=_qualified_code(source, inferred_code, fallback_code),
        message=message,
        source=source,
        severity=severity,
        confidence=confidence,
        subject_id=subject_id,
        evidence=evidence,
    )


def findings_from_report(
    report: Mapping[str, Any] | None,
    *,
    source: str,
    issue_confidence: float = 0.99,
    warning_confidence: float = 0.75,
    issue_code: str = "audit_issue",
    warning_code: str = "audit_warning",
    code_resolver: CodeResolver | None = None,
) -> list[QualityFinding]:
    """Convert a legacy ``issues``/``warnings`` report without changing it."""

    if not isinstance(report, Mapping):
        return []
    findings: list[QualityFinding] = []
    for entry in report.get("issues", []) if isinstance(report.get("issues"), list) else []:
        finding = _entry_finding(
            entry,
            source=source,
            severity=FindingSeverity.ERROR,
            confidence=issue_confidence,
            fallback_code=issue_code,
            code_resolver=code_resolver,
        )
        if finding:
            findings.append(finding)
    for entry in report.get("warnings", []) if isinstance(report.get("warnings"), list) else []:
        finding = _entry_finding(
            entry,
            source=source,
            severity=FindingSeverity.WARNING,
            confidence=warning_confidence,
            fallback_code=warning_code,
            code_resolver=code_resolver,
        )
        if finding:
            findings.append(finding)
    return findings


def findings_from_figure_generation(report: Mapping[str, Any] | None) -> list[QualityFinding]:
    if not isinstance(report, Mapping):
        return []
    findings: list[QualityFinding] = []
    for item in report.get("items", []) if isinstance(report.get("items"), list) else []:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("question_id") or "").strip()
        figure_id = str(item.get("figure_id") or "").strip()
        shared_evidence = {
            "question_id": qid,
            "figure_id": figure_id,
            "diagram_type": item.get("diagram_type"),
            "schema_status": item.get("schema_status"),
            "generation_method": item.get("generation_method"),
        }
        for message in _strings(item.get("program_check_issues")):
            findings.append(
                QualityFinding(
                    code="figure_generation.program_check_failed",
                    message=message,
                    source="figure_generation",
                    severity=FindingSeverity.ERROR,
                    confidence=0.99,
                    subject_id=qid or figure_id,
                    evidence=shared_evidence,
                )
            )
        for message in _strings(item.get("risk_notes")):
            findings.append(
                QualityFinding(
                    code="figure_generation.manual_review_risk",
                    message=message,
                    source="figure_generation",
                    severity=FindingSeverity.WARNING,
                    confidence=0.75,
                    subject_id=qid or figure_id,
                    evidence=shared_evidence,
                )
            )
    return findings


def findings_from_visual_qa(report: Mapping[str, Any] | None) -> list[QualityFinding]:
    if not isinstance(report, Mapping) or not report.get("enabled"):
        return []
    findings: list[QualityFinding] = []
    for item in report.get("items", []) if isinstance(report.get("items"), list) else []:
        if not isinstance(item, Mapping):
            continue
        raw_qa = item.get("qa")
        qa: Mapping[str, Any] = raw_qa if isinstance(raw_qa, Mapping) else {}
        if qa.get("ok") is True:
            continue
        qid = str(item.get("question_id") or "").strip()
        figure_id = str(item.get("figure_id") or "").strip()
        message = str(qa.get("summary") or qa.get("error") or "图形视觉审查未通过").strip()
        findings.append(
            QualityFinding(
                code="figure_visual_qa.review_failed",
                message=message,
                source="figure_visual_qa",
                severity=FindingSeverity.WARNING,
                confidence=0.8,
                subject_id=qid or figure_id,
                evidence={"question_id": qid, "figure_id": figure_id, "qa": dict(qa)},
            )
        )
    for item in report.get("skipped", []) if isinstance(report.get("skipped"), list) else []:
        if not isinstance(item, Mapping) or str(item.get("reason") or "") != "figure image missing":
            continue
        qid = str(item.get("question_id") or "").strip()
        figure_id = str(item.get("figure_id") or "").strip()
        findings.append(
            QualityFinding(
                code="figure_visual_qa.image_missing",
                message="图形视觉审查时找不到待检查图片。",
                source="figure_visual_qa",
                severity=FindingSeverity.ERROR,
                confidence=1.0,
                subject_id=qid or figure_id,
                evidence={"question_id": qid, "figure_id": figure_id},
            )
        )
    return findings


def deduplicate_findings(findings: Iterable[QualityFinding]) -> list[QualityFinding]:
    unique: list[QualityFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (finding.code, finding.subject_id, finding.message)
        if key not in seen:
            unique.append(finding)
            seen.add(key)
    return unique


def legacy_issue_code(message: str) -> str:
    lowered = str(message or "").lower()
    rules = (
        ("unresolved formula placeholder", "unresolved_formula_placeholder"),
        ("raw latex", "raw_latex_marker"),
        ("omml formula count", "omml_formula_count_below_expected"),
        ("math object", "invalid_math_object"),
        ("no explicit math typography", "invalid_math_object"),
        ("empty delimiter slots", "invalid_math_object"),
        ("raw radical", "raw_radical_normal_text"),
        ("raw subscript", "raw_subscript_normal_text"),
        ("too small", "artifact_too_small"),
        ("too short", "artifact_too_small"),
        ("appears blank", "blank_page"),
        ("entirely white", "blank_page"),
        ("page count", "page_count_below_minimum"),
        ("does not exist", "artifact_missing"),
        ("was not generated", "artifact_missing"),
        ("could not be inspected", "inspection_failed"),
    )
    for token, code in rules:
        if token in lowered:
            return code
    return "audit_issue"


def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
