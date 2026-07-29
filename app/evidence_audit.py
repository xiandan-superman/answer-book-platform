from __future__ import annotations

import csv
import json
from pathlib import Path


def audit_retrieval_candidates(structured_exam: dict, candidates_csv: Path, output_json: Path) -> list[str]:
    with candidates_csv.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    by_qid: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_qid.setdefault(row.get("question_id", ""), []).append(row)
    issues: list[str] = []
    warnings: list[str] = []
    for item in structured_exam.get("items", []):
        qid = str(item.get("question_id", ""))
        qrows = by_qid.get(qid, [])
        if not qrows:
            issues.append(f"{qid}: no retrieval candidates")
            continue
        if not any(str(r.get("verified_page", "")).lower() == "true" for r in qrows):
            warnings.append(f"{qid}: no candidate with verified/inferred printed page")
    report = {
        "ok": not issues,
        "candidate_count": len(rows),
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return issues

