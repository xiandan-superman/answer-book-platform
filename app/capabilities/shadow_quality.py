from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .audit_adapters import (
    deduplicate_findings,
    findings_from_figure_generation,
    findings_from_report,
    legacy_issue_code,
)
from .quality import PolicyAction, QualityFinding, QualityPolicy
from .quality_governance import build_unattended_policy, governance_for, unattended_status

SHADOW_REPORT_VERSION = "answer_book.quality_shadow.v2"

AUDIT_SOURCES: tuple[tuple[str, str, float, float], ...] = (
    ("academic_expression", "academic_expression_audit.json", 1.0, 0.75),
    ("selective_quality", "selective_quality_review.json", 0.8, 0.75),
    ("content_quality", "content_quality_audit.json", 0.99, 0.75),
    ("docx", "docx_audit.json", 0.99, 0.85),
    ("figure_size", "figure_size_audit.json", 0.99, 0.75),
)

DEFAULT_SHADOW_POLICY = build_unattended_policy()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def collect_stage_findings(stage_dir: Path) -> tuple[list[QualityFinding], list[str]]:
    findings: list[QualityFinding] = []
    available_sources: list[str] = []
    for source, filename, issue_confidence, warning_confidence in AUDIT_SOURCES:
        report = _read_json(stage_dir / filename)
        if report is None:
            continue
        available_sources.append(source)
        findings.extend(
            findings_from_report(
                report,
                source=source,
                issue_confidence=issue_confidence,
                warning_confidence=warning_confidence,
                code_resolver=legacy_issue_code,
            )
        )
    figure_generation = _read_json(stage_dir / "figure_generation_audit.json")
    if figure_generation is not None:
        available_sources.append("figure_generation")
        findings.extend(findings_from_figure_generation(figure_generation))
    return deduplicate_findings(findings), available_sources


def evaluate_shadow_quality(
    stage_dir: Path,
    *,
    policy: QualityPolicy = DEFAULT_SHADOW_POLICY,
) -> dict[str, Any]:
    findings, available_sources = collect_stage_findings(stage_dir)
    evaluated = policy.evaluate(findings)
    for item in evaluated:
        governance = governance_for(str(item.get("code") or ""))
        status, reasons = unattended_status(governance)
        item["governance"] = {
            **governance.to_dict(),
            "unattended_status": status,
            "status_reasons": reasons,
        }
    action_counts = Counter(str(item["action"]) for item in evaluated)
    ceiling_counts = Counter(str(item["governance"]["action_ceiling"]) for item in evaluated)
    source_counts = Counter(str(item["source"]) for item in evaluated)
    code_counts = Counter(str(item["code"]) for item in evaluated)
    return {
        "schema_version": SHADOW_REPORT_VERSION,
        "mode": "shadow",
        "enforced": False,
        "governance_mode": "unattended",
        "human_review_required": False,
        "available_sources": available_sources,
        "finding_count": len(evaluated),
        "would_block_count": action_counts[PolicyAction.BLOCK.value],
        "would_warn_count": action_counts[PolicyAction.WARN.value],
        "ignored_count": action_counts[PolicyAction.IGNORE.value],
        "source_counts": dict(sorted(source_counts.items())),
        "code_counts": dict(sorted(code_counts.items())),
        "action_ceiling_counts": dict(sorted(ceiling_counts.items())),
        "findings": evaluated,
        "policy": {
            "blocking_codes": sorted(policy.blocking_codes),
            "warning_codes": sorted(policy.warning_codes),
            "minimum_block_confidence": policy.minimum_block_confidence,
        },
    }


def build_shadow_quality_report(
    stage_dir: Path,
    output_json: Path | None = None,
    *,
    policy: QualityPolicy = DEFAULT_SHADOW_POLICY,
) -> dict[str, Any]:
    report = evaluate_shadow_quality(stage_dir, policy=policy)
    target = output_json or stage_dir / "quality_shadow_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
