from __future__ import annotations

from typing import Any

from .formula_audit import audit_text_segments_no_formula, looks_like_formula


ALLOWED_SEGMENT_TYPES = {"text", "formula_ref", "image_ref"}
REQUIRED_TOP_KEYS = {"schema_version", "question_id", "answer", "blocks", "formulas", "evidence_ids"}
REQUIRED_FORMULA_KEYS = {"formula_id", "latex", "role", "display"}


def validate_v4_answer_fragment(data: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    missing = REQUIRED_TOP_KEYS - set(data)
    if missing:
        issues.append(f"missing top-level keys: {sorted(missing)}")
    if data.get("schema_version") != "answer_book.answer_fragment.v4":
        issues.append("schema_version must be answer_book.answer_fragment.v4")
    if looks_like_formula(str(data.get("answer", ""))):
        issues.append("answer contains formula-like content; use formulas + formula_ref instead")
    if not isinstance(data.get("blocks"), list):
        issues.append("blocks must be a list")
    if not isinstance(data.get("formulas"), list):
        issues.append("formulas must be a list")
    if not isinstance(data.get("evidence_ids"), list):
        issues.append("evidence_ids must be a list")

    formula_ids: set[str] = set()
    for idx, formula in enumerate(data.get("formulas") or []):
        if not isinstance(formula, dict):
            issues.append(f"formulas[{idx}] must be object")
            continue
        missing_formula = REQUIRED_FORMULA_KEYS - set(formula)
        if missing_formula:
            issues.append(f"formulas[{idx}] missing keys: {sorted(missing_formula)}")
        fid = str(formula.get("formula_id", "")).strip()
        if not fid:
            issues.append(f"formulas[{idx}].formula_id is empty")
        if fid in formula_ids:
            issues.append(f"duplicate formula_id: {fid}")
        formula_ids.add(fid)
        if "\\" not in str(formula.get("latex", "")) and len(str(formula.get("latex", ""))) < 3:
            issues.append(f"formulas[{idx}].latex looks empty")

    referenced_formula_ids: set[str] = set()
    for bidx, block in enumerate(data.get("blocks") or []):
        if not isinstance(block, dict):
            issues.append(f"blocks[{bidx}] must be object")
            continue
        segments = block.get("segments")
        if not isinstance(segments, list):
            issues.append(f"blocks[{bidx}].segments must be list")
            continue
        for sidx, segment in enumerate(segments):
            if not isinstance(segment, dict):
                issues.append(f"blocks[{bidx}].segments[{sidx}] must be object")
                continue
            stype = segment.get("type")
            if stype not in ALLOWED_SEGMENT_TYPES:
                issues.append(f"blocks[{bidx}].segments[{sidx}].type invalid: {stype}")
            if stype == "formula_ref":
                fid = str(segment.get("formula_id", "")).strip()
                if not fid:
                    issues.append(f"blocks[{bidx}].segments[{sidx}] formula_ref missing formula_id")
                referenced_formula_ids.add(fid)
            if stype == "text" and "formula_id" in segment:
                issues.append(f"blocks[{bidx}].segments[{sidx}] text segment must not include formula_id")

    for fid in sorted(referenced_formula_ids - formula_ids):
        issues.append(f"formula_ref points to missing formula_id: {fid}")

    for issue in audit_text_segments_no_formula(data, ignored_block_labels={"教材依据"}):
        issues.append(issue)

    return issues


def assert_valid_v4_answer_fragment(data: dict[str, Any]) -> None:
    issues = validate_v4_answer_fragment(data)
    if issues:
        raise ValueError("v4 answer fragment validation failed:\n" + "\n".join(f"- {x}" for x in issues[:80]))
