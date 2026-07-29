from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _is_non_review_warning(text: str) -> bool:
    value = str(text or "").strip()
    return value.startswith("自动切换模型 ") and "完成结构化生成" in value


def build_answer_review_notes(fragments_data: dict[str, Any], output_json: Path | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for fragment in fragments_data.get("fragments", []):
        meta = fragment.get("_meta") if isinstance(fragment.get("_meta"), dict) else {}
        evidence_binding = meta.get("evidence_binding") if isinstance(meta, dict) else {}
        if not isinstance(evidence_binding, dict):
            evidence_binding = {}
        warnings = [str(x) for x in fragment.get("warnings", []) if str(x).strip() and not _is_non_review_warning(str(x))]
        if not warnings and not evidence_binding:
            continue
        rows.append(
            {
                "question_id": str(fragment.get("question_id", "")),
                "section": fragment.get("section", ""),
                "number": fragment.get("number", ""),
                "warnings": warnings,
                "evidence_binding": evidence_binding,
                "review_required": bool(warnings or evidence_binding.get("strategy") == "program_top_evidence"),
            }
        )
    report = {
        "ok": True,
        "note_count": len(rows),
        "rows": rows,
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
