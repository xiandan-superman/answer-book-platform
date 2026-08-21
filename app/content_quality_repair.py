from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .user_facing_text import strip_internal_repair_provenance

XRD_UNSUPPORTED_SPACING_CLAUSE_RE = re.compile(
    r"峰间距[^，。；;\n]{0,36}(?:逐渐|必然|单调)[^，。；;\n]{0,12}"
    r"(?:增大|减小|变大|变小)(?:，?但)?"
)


def _question_issue_codes(audit_report: dict[str, Any]) -> dict[str, set[str]]:
    codes: dict[str, set[str]] = {}
    for item in [*audit_report.get("issues", []), *audit_report.get("warnings", [])]:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        code = str(item.get("code") or "").strip()
        if qid and code:
            codes.setdefault(qid, set()).add(code)
    return codes


def _has_block(fragment: dict[str, Any], label: str) -> bool:
    return any(
        isinstance(block, dict)
        and str(block.get("label") or "").strip() == label
        and any(
            isinstance(segment, dict) and str(segment.get("text") or segment.get("formula_id") or "").strip()
            for segment in block.get("segments", [])
        )
        for block in fragment.get("blocks", [])
    )


def _calculation_mistake_note(fragment: dict[str, Any]) -> str:
    draft = fragment.get("_draft") if isinstance(fragment.get("_draft"), dict) else {}
    contract = draft.get("calculation_contract") if isinstance(draft.get("calculation_contract"), dict) else {}
    if contract.get("partitions") or contract.get("transitions"):
        return "使用杠杆定律或组成比例计算时，必须统一取值温度和计算基准；各组成分数应在同一基准下求和并校验为100%。"
    return "代入前应统一单位和计算基准，保留必要的有效数字，并将最终结果回代题意检查。"


def _analysis_segments_from_draft(draft: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Recover model-authored analysis without inventing new content."""

    if not isinstance(draft, dict):
        return []
    raw_segments = draft.get("analysis_segments")
    if isinstance(raw_segments, list):
        segments = [dict(item) for item in raw_segments if isinstance(item, dict)]
        if any(str(item.get("text") or item.get("formula_id") or "").strip() for item in segments):
            return segments
    analysis = str(draft.get("analysis") or "").strip()
    return [{"type": "text", "text": analysis}] if analysis else []


def _clean_provenance_in_user_node(value: Any) -> Any:
    if isinstance(value, str):
        return strip_internal_repair_provenance(value)
    if isinstance(value, list):
        return [_clean_provenance_in_user_node(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned = dict(value)
    for key in ("answer", "answer_summary", "analysis", "text", "result_text"):
        if key in cleaned and isinstance(cleaned.get(key), str):
            cleaned[key] = strip_internal_repair_provenance(cleaned[key])
    for key in ("analysis_segments", "steps", "answer_units", "mistake_notes"):
        if key in cleaned:
            cleaned[key] = _clean_provenance_in_user_node(cleaned[key])
    return cleaned


def _remove_unsupported_xrd_spacing_claims(value: Any) -> tuple[Any, bool]:
    """Remove only the unsupported trend clause while preserving surrounding math."""

    if isinstance(value, str):
        cleaned = XRD_UNSUPPORTED_SPACING_CLAUSE_RE.sub("", value)
        return cleaned, cleaned != value
    if isinstance(value, list):
        changed = False
        cleaned_items: list[Any] = []
        for item in value:
            cleaned, item_changed = _remove_unsupported_xrd_spacing_claims(item)
            cleaned_items.append(cleaned)
            changed = changed or item_changed
        return cleaned_items, changed
    if not isinstance(value, dict):
        return value, False
    changed = False
    cleaned_mapping: dict[str, Any] = {}
    for key, item in value.items():
        cleaned, item_changed = _remove_unsupported_xrd_spacing_claims(item)
        cleaned_mapping[key] = cleaned
        changed = changed or item_changed
    return cleaned_mapping, changed


def repair_content_quality_locally(
    fragments_json: Path,
    audit_report: dict[str, Any],
    backup_path: Path | None = None,
) -> dict[str, Any]:
    """Apply semantics-preserving repairs derivable from validated content."""

    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    issue_codes = _question_issue_codes(audit_report)
    repaired_question_ids: list[str] = []
    for fragment in data.get("fragments", []):
        if not isinstance(fragment, dict):
            continue
        qid = str(fragment.get("question_id") or "").strip()
        q_codes = issue_codes.get(qid, set())
        draft = fragment.get("_draft") if isinstance(fragment.get("_draft"), dict) else None
        changed = False
        if "internal_repair_provenance_leak" in q_codes:
            for key in ("answer", "answer_summary", "answer_units"):
                if key in fragment:
                    fragment[key] = _clean_provenance_in_user_node(fragment[key])
            for block in fragment.get("blocks", []) or []:
                if not isinstance(block, dict) or str(block.get("label") or "").strip() == "教材依据":
                    continue
                cleaned_segments: list[dict[str, Any]] = []
                for segment in block.get("segments", []) or []:
                    if not isinstance(segment, dict):
                        continue
                    if "text" not in segment:
                        cleaned_segments.append(segment)
                        continue
                    cleaned = strip_internal_repair_provenance(segment.get("text"))
                    if cleaned:
                        cleaned_segments.append({**segment, "text": cleaned})
                block["segments"] = cleaned_segments
                if not cleaned_segments and str(block.get("label") or "").strip() == "易错点及注意事项":
                    note = _calculation_mistake_note(fragment)
                    block["segments"] = [{"type": "text", "text": note}]
                changed = True
            if draft is not None:
                cleaned_draft = _clean_provenance_in_user_node(draft)
                draft.clear()
                draft.update(cleaned_draft)
                cleaned_notes = [str(value).strip() for value in draft.get("mistake_notes", []) or [] if str(value).strip()]
                draft["mistake_notes"] = cleaned_notes or [_calculation_mistake_note(fragment)]
                changed = True
        if "calculation_missing_mistake_notes" in q_codes and not _has_block(fragment, "易错点及注意事项"):
            note = _calculation_mistake_note(fragment)
            fragment.setdefault("blocks", []).append(
                {"label": "易错点及注意事项", "segments": [{"type": "text", "text": note}]}
            )
            if draft is not None and not draft.get("mistake_notes"):
                draft["mistake_notes"] = [note]
            changed = True
        if "missing_analysis" in q_codes and not _has_block(fragment, "解析"):
            analysis_segments = _analysis_segments_from_draft(draft)
            if analysis_segments:
                blocks = fragment.setdefault("blocks", [])
                insert_at = next(
                    (
                        index
                        for index, block in enumerate(blocks)
                        if isinstance(block, dict) and str(block.get("label") or "").strip() == "解题步骤"
                    ),
                    len(blocks),
                )
                blocks.insert(insert_at, {"label": "解析", "segments": analysis_segments})
                changed = True
        if "xrd_unsupported_peak_spacing_trend" in q_codes:
            for key in ("answer", "answer_summary", "answer_units", "blocks", "_draft"):
                if key not in fragment:
                    continue
                cleaned, item_changed = _remove_unsupported_xrd_spacing_claims(fragment[key])
                fragment[key] = cleaned
                changed = changed or item_changed
        if changed:
            repaired_question_ids.append(qid)

    report = {
        "ok": True,
        "changed": bool(repaired_question_ids),
        "repaired_count": len(repaired_question_ids),
        "repaired_question_ids": repaired_question_ids,
        "strategy": "deterministic_content_quality_repair",
    }
    if not repaired_question_ids:
        return report
    if backup_path:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fragments_json, backup_path)
        report["backup"] = str(backup_path)
    fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
