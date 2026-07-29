from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .question_types import is_choice_question


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_question_review(stage_dir: Path) -> dict[str, Any]:
    structured_exam = read_json(stage_dir / "structured_exam.json")
    fragments_data = read_json(stage_dir / "answer_fragments.json")
    coverage = read_json(stage_dir / "answer_coverage_audit.json")
    content_quality = read_json(stage_dir / "content_quality_audit.json")
    review_notes = read_json(stage_dir / "answer_review_notes.json")
    candidates = read_csv(stage_dir / "retrieval_candidates.csv")

    fragments_by_qid = {str(f.get("question_id", "")): f for f in fragments_data.get("fragments", [])}
    items_by_qid = {str(item.get("question_id", "")): item for item in structured_exam.get("items", [])}
    candidates_by_qid: dict[str, list[dict[str, str]]] = {}
    for row in candidates:
        candidates_by_qid.setdefault(str(row.get("question_id", "")), []).append(row)

    coverage_warnings = coverage.get("warnings", []) if isinstance(coverage, dict) else []
    coverage_issues = coverage.get("issues", []) if isinstance(coverage, dict) else []
    notes_by_qid: dict[str, list[str]] = {}
    for message in coverage_issues + coverage_warnings:
        text = str(message)
        qid = text.split(":", 1)[0]
        notes_by_qid.setdefault(qid, []).append(text)
    for raw in (content_quality.get("issues", []) if isinstance(content_quality, dict) else []):
        if not isinstance(raw, dict):
            continue
        qid = str(raw.get("question_id", "")).strip()
        if not qid:
            continue
        code = str(raw.get("code", "")).strip()
        item = items_by_qid.get(qid, {})
        if code.startswith("choice_") and not is_choice_question(item):
            continue
        message = str(raw.get("message", "")).strip()
        notes_by_qid.setdefault(qid, []).append(f"内容质量审查：{code} - {message}")
    for raw in (content_quality.get("warnings", []) if isinstance(content_quality, dict) else []):
        if not isinstance(raw, dict):
            continue
        qid = str(raw.get("question_id", "")).strip()
        if not qid:
            continue
        code = str(raw.get("code", "")).strip()
        item = items_by_qid.get(qid, {})
        if code.startswith("choice_") and not is_choice_question(item):
            continue
        message = str(raw.get("message", "")).strip()
        notes_by_qid.setdefault(qid, []).append(f"内容质量提示：{code} - {message}")
    review_notes_by_qid = {
        str(row.get("question_id", "")): row
        for row in review_notes.get("rows", [])
        if isinstance(row, dict)
    }

    rows: list[dict[str, Any]] = []
    for item in structured_exam.get("items", []):
        qid = str(item.get("question_id", ""))
        fragment = fragments_by_qid.get(qid)
        evidence_ids = set(str(x) for x in (fragment or {}).get("evidence_ids", []))
        evidence = candidates_by_qid.get(qid, [])
        cited_evidence = [row for row in evidence if str(row.get("evidence_id", "")) in evidence_ids]
        meta = (fragment or {}).get("_meta") or {}
        evidence_binding = meta.get("evidence_binding") if isinstance(meta, dict) else {}
        if not isinstance(evidence_binding, dict):
            evidence_binding = {}
        notes = list(notes_by_qid.get(qid, []))
        review_note = review_notes_by_qid.get(qid, {})
        note_warnings = review_note.get("warnings", []) if isinstance(review_note, dict) else []
        for warning in note_warnings:
            text = str(warning)
            if text not in notes:
                notes.append(text)
        note_binding = review_note.get("evidence_binding") if isinstance(review_note, dict) else {}
        if not evidence_binding and isinstance(note_binding, dict):
            evidence_binding = note_binding
        if evidence_binding.get("strategy") == "program_top_evidence":
            reason = str(evidence_binding.get("reason") or "程序按检索排序补充最相关教材证据。")
            notes.append(f"程序补证据：{reason}")
        rows.append(
            {
                "question_id": qid,
                "section": item.get("section", ""),
                "number": item.get("number", ""),
                "stem": item.get("stem", ""),
                "answer": (fragment or {}).get("answer", ""),
                "has_fragment": fragment is not None,
                "evidence_id_count": len(evidence_ids),
                "candidate_count": len(evidence),
                "cited_evidence": cited_evidence,
                "top_candidates": evidence[:5],
                "notes": notes,
                "evidence_binding": evidence_binding,
                "evidence_binding_reason": evidence_binding.get("reason", ""),
                "evidence_binding_strategy": evidence_binding.get("strategy", ""),
            }
        )
    return {
        "ok": bool(structured_exam.get("items")),
        "question_count": len(rows),
        "auto_evidence_count": sum(1 for row in rows if row.get("evidence_binding_strategy") == "program_top_evidence"),
        "review_note_count": int(review_notes.get("note_count", 0) or 0),
        "review_rows": rows,
        "coverage": coverage,
        "review_notes": review_notes,
    }


def write_question_review_csv(review: dict[str, Any], output_csv: Path) -> Path:
    fields = [
        "question_id",
        "section",
        "number",
        "answer",
        "has_fragment",
        "evidence_id_count",
        "candidate_count",
        "notes",
        "evidence_binding_strategy",
        "evidence_binding_reason",
        "top_evidence",
    ]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in review.get("review_rows", []):
            top = row.get("top_candidates", [])[:3]
            writer.writerow(
                {
                    "question_id": row.get("question_id", ""),
                    "section": row.get("section", ""),
                    "number": row.get("number", ""),
                    "answer": row.get("answer", ""),
                    "has_fragment": row.get("has_fragment", False),
                    "evidence_id_count": row.get("evidence_id_count", 0),
                    "candidate_count": row.get("candidate_count", 0),
                    "notes": " | ".join(str(x) for x in row.get("notes", [])),
                    "evidence_binding_strategy": row.get("evidence_binding_strategy", ""),
                    "evidence_binding_reason": row.get("evidence_binding_reason", ""),
                    "top_evidence": " | ".join(
                        f"{x.get('evidence_id','')} 《{x.get('citation_textbook') or x.get('textbook','')}》p{x.get('printed_page','')} score={x.get('score','')}"
                        for x in top
                    ),
                }
            )
    return output_csv
