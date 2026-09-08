from __future__ import annotations

from typing import Any

EVIDENCE_BACKED_ANALYSIS = "evidence_backed"
QUESTION_ONLY_ANALYSIS = "question_only"
TEXTBOOK_EVIDENCE_ONLY_ANALYSIS = "textbook_evidence_only"
_SUPPORTED_ANALYSIS_PROFILES = {
    EVIDENCE_BACKED_ANALYSIS,
    QUESTION_ONLY_ANALYSIS,
    TEXTBOOK_EVIDENCE_ONLY_ANALYSIS,
}

QUESTION_ONLY_EXCLUDED_ARTIFACT_NAMES = frozenset(
    {
        "knowledge_plans.json",
        "knowledge_planning_progress.json",
        "evidence_selection.json",
        "evidence_selection_progress.json",
        "confirmed_evidence_candidates.csv",
        "retrieval_candidates.csv",
        "retrieval_candidates.expanded.csv",
        "retrieval_candidates.summary.json",
        "retrieval_audit.json",
        "textbook_blocks.csv",
        "textbook_page_map.csv",
        "textbook_package_audit.json",
        "textbook_index_status.json",
        "题目依据排查.csv",
    }
)

_TEXTBOOK_EVIDENCE_BLOCK_LABELS = frozenset(
    {"教材依据", "教材引用", "参考教材", "引用依据", "证据依据"}
)


def normalize_analysis_profile(value: object) -> str:
    profile = str(value or EVIDENCE_BACKED_ANALYSIS).strip().lower()
    if profile not in _SUPPORTED_ANALYSIS_PROFILES:
        raise ValueError(f"unsupported analysis_profile: {profile}")
    return profile


def analysis_uses_textbook_evidence(value: object) -> bool:
    return normalize_analysis_profile(value) in {
        EVIDENCE_BACKED_ANALYSIS,
        TEXTBOOK_EVIDENCE_ONLY_ANALYSIS,
    }


def analysis_generates_answers(value: object) -> bool:
    return normalize_analysis_profile(value) != TEXTBOOK_EVIDENCE_ONLY_ANALYSIS


def is_textbook_evidence_only(value: object) -> bool:
    return normalize_analysis_profile(value) == TEXTBOOK_EVIDENCE_ONLY_ANALYSIS


def is_question_only_excluded_artifact(name: object) -> bool:
    return str(name or "").strip() in QUESTION_ONLY_EXCLUDED_ARTIFACT_NAMES


def sanitize_question_only_fragments(data: dict[str, Any]) -> dict[str, Any]:
    """Remove textbook-only fields from a question-only answer payload."""

    if not isinstance(data, dict) or analysis_uses_textbook_evidence(data.get("analysis_profile")):
        return data
    data["analysis_profile"] = QUESTION_ONLY_ANALYSIS
    data["document_title"] = "题目解析"
    for fragment in data.get("fragments", []) or []:
        if not isinstance(fragment, dict):
            continue
        fragment["evidence_ids"] = []
        fragment["blocks"] = [
            block
            for block in fragment.get("blocks", []) or []
            if not (
                isinstance(block, dict)
                and str(block.get("label") or "").strip() in _TEXTBOOK_EVIDENCE_BLOCK_LABELS
            )
        ]
        meta = fragment.get("_meta")
        if isinstance(meta, dict):
            meta.pop("evidence_binding", None)
        fragment["warnings"] = [
            warning
            for warning in fragment.get("warnings", []) or []
            if not any(label in str(warning) for label in _TEXTBOOK_EVIDENCE_BLOCK_LABELS)
        ]
    data["recovery_events"] = [
        event
        for event in data.get("recovery_events", []) or []
        if not (
            isinstance(event, dict)
            and "evidence" in str(event.get("strategy") or "").lower()
        )
    ]
    return data
