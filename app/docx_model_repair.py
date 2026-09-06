"""Word-specific localization, shared candidate/context/repair execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .audit_model_repair import _repair_prompt as audit_repair_prompt
from .audit_model_repair import repair_fragments_with_model_for_audit
from .formula_audit import formula_like_matches
from .retrieval import EvidenceCandidate
from .settings import ProviderConfig


def docx_model_repair_worker_count() -> int:
    try:
        return max(1, min(6, int(os.environ.get("DOCX_MODEL_REPAIR_MAX_WORKERS", "4"))))
    except ValueError:
        return 4


def _collect_docx_formula_findings(fragments: list[dict[str, Any]], docx_issues: list[str]) -> list[dict[str, Any]]:
    issue_text = "\n".join(docx_issues)
    findings = []
    for fragment in fragments:
        for block_index, block in enumerate(fragment.get("blocks", [])):
            if block.get("label") == "教材依据":
                continue
            for segment_index, segment in enumerate(block.get("segments", [])):
                if segment.get("type") != "text":
                    continue
                text = str(segment.get("text") or "")
                matches = formula_like_matches(text, include_chinese_paraphrase=True)
                if matches:
                    findings.append({
                        "question_id": str(fragment.get("question_id") or ""),
                        "code": "docx_formula_content", "message": text,
                        "block_index": block_index, "segment_index": segment_index,
                        "matches": matches,
                        "document_diagnostics": list(docx_issues),
                        "directly_reported": any(match in issue_text or text[:80] in issue_text for match in matches),
                    })
    return sorted(findings, key=lambda item: (not item["directly_reported"], item["question_id"]))


def _repair_prompt(question, evidence, fragment, findings, docx_issues, *, include_textbook_evidence=True):
    """Compatibility entry; there is no separate Word prompt implementation."""
    return audit_repair_prompt(
        audit_stage="docx", question=question, evidence=evidence, fragment=fragment,
        issues=[*findings, *({"message": issue} for issue in docx_issues)],
        include_textbook_evidence=include_textbook_evidence,
    )


def repair_fragments_with_model_for_docx(
    fragments_json: Path,
    structured_exam: dict[str, Any],
    candidates: list[EvidenceCandidate],
    *,
    selection_data: dict[str, Any] | None,
    provider: ProviderConfig,
    model: str,
    docx_issues: list[str],
    client: Any | None = None,
    backup_path: Path | None = None,
    max_repairs: int = 3,
    image_provider: ProviderConfig | None = None,
    image_model: str = "",
) -> dict[str, Any]:
    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    findings = _collect_docx_formula_findings(data.get("fragments", []), docx_issues)
    if not findings:
        return {"ok": False, "changed": False, "repaired_count": 0, "repaired_question_ids": [],
                "issues": ["未定位到可交给模型修复的公式化正文片段。"]}
    report = repair_fragments_with_model_for_audit(
        fragments_json, structured_exam, candidates, selection_data=selection_data,
        provider=provider, model=model, audit_stage="docx", audit_report={"issues": findings},
        client=client, backup_path=backup_path, max_repairs=max_repairs,
        image_provider=image_provider, image_model=image_model,
        worker_limit=docx_model_repair_worker_count(),
    )
    report["findings"] = findings
    return report
