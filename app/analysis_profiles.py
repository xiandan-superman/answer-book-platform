from __future__ import annotations

EVIDENCE_BACKED_ANALYSIS = "evidence_backed"
QUESTION_ONLY_ANALYSIS = "question_only"
_SUPPORTED_ANALYSIS_PROFILES = {
    EVIDENCE_BACKED_ANALYSIS,
    QUESTION_ONLY_ANALYSIS,
}


def normalize_analysis_profile(value: object) -> str:
    profile = str(value or EVIDENCE_BACKED_ANALYSIS).strip().lower()
    if profile not in _SUPPORTED_ANALYSIS_PROFILES:
        raise ValueError(f"unsupported analysis_profile: {profile}")
    return profile


def analysis_uses_textbook_evidence(value: object) -> bool:
    return normalize_analysis_profile(value) == EVIDENCE_BACKED_ANALYSIS
