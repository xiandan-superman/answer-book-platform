from __future__ import annotations

import copy
import json
import re
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
from .expression_promotion import (
    promote_answer_summary_mathematical_expressions,
    promote_split_partial_derivatives,
)
from .omml_input import strip_structured_math_metadata
from .v4_schema import validate_v4_answer_fragment

_GENERIC_SCHEMA_REVIEW_MESSAGE = "模型生成内容存在审查问题，已保留当前候选内容进入正式文件并在审查记录中标记。"
_UNRESOLVED_FORMULA_PLACEHOLDER_RE = re.compile(r"\{[fF]\d+(?:_[A-Za-z][A-Za-z0-9]*)?\}")


def _schema_candidate_issue(issue: Any) -> bool:
    text = str(issue or "")
    return any(
        marker in text
        for marker in (
            "schema type error at ",
            "missing top-level keys:",
            "schema_version must be ",
            "answer contains formula-like content",
            "blocks must be a list",
            "formulas must be a list",
            "evidence_ids must be a list",
            "formulas[",
            "blocks[",
            "duplicate formula_id:",
            "formula_ref points to missing formula_id:",
        )
    )


def _clear_resolved_schema_candidate(fragment: dict[str, Any], remaining_issues: list[str]) -> dict[str, Any]:
    candidate_issues = [str(item) for item in fragment.get("_review_candidate_issues", []) if str(item).strip()]
    if remaining_issues or not candidate_issues or not all(_schema_candidate_issue(item) for item in candidate_issues):
        return fragment
    flags = [item for item in fragment.get("_review_flags", []) if isinstance(item, dict)]
    removed_messages = {
        str(item.get("message") or "")
        for item in flags
        if str(item.get("code") or "") == "answer_generation_review_candidate"
    }
    fragment["_review_flags"] = [
        item for item in flags if str(item.get("code") or "") != "answer_generation_review_candidate"
    ]
    fragment["warnings"] = [
        str(item)
        for item in fragment.get("warnings", [])
        if str(item) not in removed_messages and str(item) != _GENERIC_SCHEMA_REVIEW_MESSAGE
    ]
    meta = dict(fragment.get("_meta") or {})
    meta["resolved_review_candidate_issues"] = candidate_issues
    if meta.get("recovered_by") == "review_candidate_preserved":
        meta["recovered_by"] = "deterministic_schema_repair"
    fragment["_meta"] = meta
    fragment.pop("_review_candidate_issues", None)
    return fragment


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
                        "display": False,
                        "source_note": "程序在 Word 生成前从普通文本中识别出的公式片段。",
                    }
                )
                # A token promoted from inside a sentence remains inline. Its
                # mathematical complexity must not change paragraph structure.
                repaired_segments.append({"type": "formula_ref", "formula_id": fid, "inline": True})
                cursor = end
            tail = text[cursor:]
            if tail:
                repaired_segments.append({"type": "text", "text": tail})
        if changed:
            block["segments"] = repaired_segments
    fragment["formulas"] = formulas
    return promote_answer_summary_mathematical_expressions(fragment)


def _strip_internal_formula_metadata(fragment: dict[str, Any]) -> dict[str, Any]:
    """Remove model-echoed Word input metadata from user-facing fields."""

    for key in ("answer", "answer_summary"):
        if key in fragment:
            fragment[key] = strip_structured_math_metadata(str(fragment.get(key) or ""))
    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict):
            continue
        repaired_segments: list[dict[str, Any]] = []
        inside_metadata = False
        for raw_segment in block.get("segments", []) or []:
            if not isinstance(raw_segment, dict):
                if not inside_metadata:
                    repaired_segments.append(raw_segment)
                continue
            if raw_segment.get("type") != "text":
                if not inside_metadata:
                    repaired_segments.append(raw_segment)
                continue
            remaining = str(raw_segment.get("text") or "")
            while remaining:
                if inside_metadata:
                    _metadata, separator, suffix = remaining.partition("⟧")
                    if not separator:
                        remaining = ""
                        break
                    inside_metadata = False
                    remaining = suffix
                    continue
                prefix, separator, suffix = remaining.partition("⟦MATHML:")
                if prefix:
                    clean_segment = dict(raw_segment)
                    clean_segment["text"] = strip_structured_math_metadata(prefix)
                    repaired_segments.append(clean_segment)
                if not separator:
                    remaining = ""
                    break
                inside_metadata = True
                remaining = suffix
        block["segments"] = repaired_segments
    return fragment


def _remove_unresolved_formula_placeholders(fragment: dict[str, Any]) -> dict[str, Any]:
    removed: list[str] = []

    def clean(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            removed.append(match.group(0))
            return ""

        return _UNRESOLVED_FORMULA_PLACEHOLDER_RE.sub(replace, str(value or ""))

    for key in ("answer", "answer_summary"):
        if key in fragment:
            fragment[key] = clean(str(fragment.get(key) or ""))
    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict):
            continue
        for segment in block.get("segments", []) or []:
            if isinstance(segment, dict) and segment.get("type") == "text":
                segment["text"] = clean(str(segment.get("text") or ""))
    if removed:
        unique = sorted(set(removed))
        message = "模型返回了不存在的公式引用，程序已移除占位符并保留周围答案文字；本题需复核公式完整性：" + "、".join(unique)
        warnings = [str(item) for item in fragment.get("warnings", []) or [] if str(item).strip()]
        if message not in warnings:
            warnings.append(message)
        fragment["warnings"] = warnings
        flags = [item for item in fragment.get("_review_flags", []) or [] if isinstance(item, dict)]
        if not any(str(item.get("code") or "") == "unresolved_formula_reference_removed" for item in flags):
            flags.append({"code": "unresolved_formula_reference_removed", "message": message})
        fragment["_review_flags"] = flags
    return fragment


def _drop_non_delivery_pending_formula_block(fragment: dict[str, Any]) -> dict[str, Any]:
    question_type = str(fragment.get("question_type") or "")
    section = str(fragment.get("section") or "")
    has_calculation_unit = question_type == "计算题" or any(
        isinstance(unit, dict) and str(unit.get("question_type") or "") == "计算题"
        for unit in fragment.get("answer_units", []) or []
    )
    is_term_explanation = question_type == "名词解释" or "名词解释" in section
    if not (has_calculation_unit or is_term_explanation):
        return fragment
    pending_blocks = [
        block
        for block in fragment.get("blocks", []) or []
        if isinstance(block, dict) and str(block.get("label") or "") == "待复核公式"
    ]
    if not pending_blocks:
        return fragment
    pending_ids = {
        str(segment.get("formula_id") or "")
        for block in pending_blocks
        for segment in block.get("segments", []) or []
        if isinstance(segment, dict) and segment.get("type") == "formula_ref"
    }
    fragment["blocks"] = [
        block
        for block in fragment.get("blocks", []) or []
        if not (isinstance(block, dict) and str(block.get("label") or "") == "待复核公式")
    ]
    fragment["warnings"] = [
        str(warning)
        for warning in fragment.get("warnings", []) or []
        if "未融入解析正文的公式" not in str(warning)
    ]
    if not fragment["warnings"]:
        fragment.pop("warnings", None)
    if is_term_explanation and pending_ids:
        remaining_refs = {
            str(segment.get("formula_id") or "")
            for block in fragment.get("blocks", []) or []
            if isinstance(block, dict)
            for segment in block.get("segments", []) or []
            if isinstance(segment, dict) and segment.get("type") == "formula_ref"
        }
        removable = pending_ids - remaining_refs
        fragment["formulas"] = [
            formula
            for formula in fragment.get("formulas", []) or []
            if not isinstance(formula, dict) or str(formula.get("formula_id") or "") not in removable
        ]
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
        repaired = _strip_internal_formula_metadata(copy.deepcopy(fragment))
        repaired = _repair_formula_placeholders(repaired)
        repaired = _remove_unresolved_formula_placeholders(repaired)
        repaired = _drop_non_delivery_pending_formula_block(repaired)
        repaired = demote_simple_symbol_formulas(repaired)
        repaired = promote_split_partial_derivatives(repaired)
        repaired = _repair_formula_text_segments(repaired)
        fragment_issues = validate_v4_answer_fragment(repaired)
        repaired = _clear_resolved_schema_candidate(repaired, fragment_issues)
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
    if not changed:
        return report

    if backup_path:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fragments_json, backup_path)
        report["backup"] = str(backup_path)
    data["fragments"] = repaired_fragments
    fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report["original_preserved"] = original != data
    return report
