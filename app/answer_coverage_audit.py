from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def audit_answer_coverage(structured_exam: dict[str, Any], fragments_data: dict[str, Any], output_json: Path | None = None) -> dict[str, Any]:
    items = structured_exam.get("items", [])
    fragments = fragments_data.get("fragments", [])
    expected_ids = [str(item.get("question_id", "")).strip() for item in items if str(item.get("question_id", "")).strip()]
    fragment_ids = [str(fragment.get("question_id", "")).strip() for fragment in fragments if str(fragment.get("question_id", "")).strip()]
    expected_set = set(expected_ids)
    fragment_set = set(fragment_ids)
    counter = Counter(fragment_ids)

    issues: list[str] = []
    warnings: list[str] = []

    missing = [qid for qid in expected_ids if qid not in fragment_set]
    unknown = [qid for qid in fragment_ids if qid not in expected_set]
    duplicates = [qid for qid, count in counter.items() if count > 1]
    if missing:
        issues.append("missing answer fragments: " + ", ".join(missing[:50]))
    if unknown:
        issues.append("unknown answer fragments: " + ", ".join(unknown[:50]))
    if duplicates:
        issues.append("duplicate answer fragments: " + ", ".join(duplicates[:50]))

    item_by_id = {str(item.get("question_id", "")).strip(): item for item in items}
    for fragment in fragments:
        qid = str(fragment.get("question_id", "")).strip()
        if not qid or qid not in item_by_id:
            continue
        item = item_by_id[qid]
        if str(fragment.get("section", "")).strip() != str(item.get("section", "")).strip():
            warnings.append(f"{qid}: section mismatch: exam={item.get('section', '')} fragment={fragment.get('section', '')}")
        if str(fragment.get("number", "")).strip() != str(item.get("number", "")).strip():
            warnings.append(f"{qid}: number mismatch: exam={item.get('number', '')} fragment={fragment.get('number', '')}")
        if not str(fragment.get("answer", "")).strip():
            issues.append(f"{qid}: answer is empty")
        if str(fragment.get("answer", "")).strip() in {"待复核", "待补充", "未完成"}:
            warnings.append(f"{qid}: answer is pending review")
        if not fragment.get("evidence_ids"):
            warnings.append(f"{qid}: evidence_ids is empty")

    report = {
        "ok": not issues,
        "question_count": len(expected_ids),
        "fragment_count": len(fragment_ids),
        "covered_count": len(expected_set & fragment_set),
        "missing_count": len(missing),
        "unknown_count": len(unknown),
        "duplicate_count": len(duplicates),
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
