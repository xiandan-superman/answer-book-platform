from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

from .answer_generation import (
    _replace_formula_placeholders_in_text,
    _replace_formula_placeholders_in_value,
    _segments_from_inline_formula_text,
    demote_simple_symbol_formulas,
)
from .docx_v4 import _answer_summary_formula_candidates
from .v4_schema import validate_v4_answer_fragment


def _repair_formula_text_segments(fragment: dict[str, Any]) -> dict[str, Any]:
    formulas = list(fragment.get("formulas", []))
    existing_ids = {str(formula.get("formula_id")) for formula in formulas}
    created = 0

    def next_formula_id() -> str:
        nonlocal created
        qid = str(fragment.get("question_id") or "q").replace("-", "_")
        while True:
            created += 1
            fid = f"f_{qid}_docx_repair_{created:02d}"
            if fid not in existing_ids:
                existing_ids.add(fid)
                return fid

    for block in fragment.get("blocks", []):
        repaired_segments: list[dict[str, Any]] = []
        changed = False
        for segment in block.get("segments", []):
            if segment.get("type") != "text":
                repaired_segments.append(segment)
                continue
            text = str(segment.get("text", ""))
            candidates = _answer_summary_formula_candidates(text)
            if not candidates:
                repaired_segments.append(segment)
                continue
            changed = True
            cursor = 0
            for start, end, latex in candidates:
                plain = text[cursor:start]
                if plain:
                    repaired_segments.append({"type": "text", "text": plain})
                fid = next_formula_id()
                formulas.append(
                    {
                        "formula_id": fid,
                        "latex": latex,
                        "role": "relation",
                        "display": True,
                        "source_note": "程序在 Word 生成前从普通文本中识别出的公式片段。",
                    }
                )
                repaired_segments.append({"type": "formula_ref", "formula_id": fid})
                cursor = end
            tail = text[cursor:]
            if tail:
                repaired_segments.append({"type": "text", "text": tail})
        if changed:
            block["segments"] = repaired_segments
    fragment["formulas"] = formulas
    return fragment


def _repair_formula_placeholders(fragment: dict[str, Any]) -> dict[str, Any]:
    formulas = [formula for formula in fragment.get("formulas", []) if isinstance(formula, dict)]
    if not formulas:
        return fragment
    formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
    if not formula_ids:
        return fragment

    for key in ("answer", "answer_summary"):
        if key in fragment:
            fragment[key] = _replace_formula_placeholders_in_text(str(fragment.get(key) or ""), formulas)
    for key in ("warnings", "figure_specs", "_draft"):
        if key in fragment:
            fragment[key] = _replace_formula_placeholders_in_value(fragment.get(key), formulas)

    for block in fragment.get("blocks", []):
        if not isinstance(block, dict):
            continue
        repaired_segments: list[dict[str, Any]] = []
        for segment in block.get("segments", []):
            if not isinstance(segment, dict) or segment.get("type") != "text":
                repaired_segments.append(segment)
                continue
            text = str(segment.get("text", ""))
            segments, _used = _segments_from_inline_formula_text(text, formula_ids)
            repaired_segments.extend(segments or [segment])
        block["segments"] = repaired_segments
    return fragment


def repair_answer_fragments_for_docx(fragments_json: Path, backup_path: Path | None = None) -> dict[str, Any]:
    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    original = copy.deepcopy(data)
    issues: list[dict[str, Any]] = []
    repaired_fragments: list[dict[str, Any]] = []
    repaired_qids: list[str] = []

    for fragment in data.get("fragments", []):
        before = copy.deepcopy(fragment)
        repaired = _repair_formula_placeholders(copy.deepcopy(fragment))
        repaired = demote_simple_symbol_formulas(repaired)
        repaired = _repair_formula_text_segments(repaired)
        fragment_issues = validate_v4_answer_fragment(repaired)
        if fragment_issues:
            issues.append({"question_id": repaired.get("question_id"), "issues": fragment_issues})
        if repaired != before:
            repaired_qids.append(str(repaired.get("question_id", "")))
        repaired_fragments.append(repaired)

    changed = repaired_fragments != data.get("fragments", [])
    report = {
        "ok": not issues,
        "changed": changed,
        "repaired_count": len([qid for qid in repaired_qids if qid]),
        "repaired_question_ids": [qid for qid in repaired_qids if qid],
        "issue_count": len(issues),
        "issues": issues[:30],
    }
    if issues or not changed:
        return report

    if backup_path:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fragments_json, backup_path)
        report["backup"] = str(backup_path)
    data["fragments"] = repaired_fragments
    fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report["original_preserved"] = original != data
    return report
