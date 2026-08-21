from __future__ import annotations

import re


# These phrases describe the generation/review workflow rather than the
# subject matter.  They belong in diagnostic JSON and reviewer documents, not
# in the student-facing answer book.
INTERNAL_REPAIR_PROVENANCE_RE = re.compile(
    r"(?:原答案|原稿|初始答案|模型答案|模型输出|本次修复|回修|"
    r"修复后|修正后|已修正|自动修复|审查发现|门禁发现)"
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?;；])")


def contains_internal_repair_provenance(text: object) -> bool:
    return bool(INTERNAL_REPAIR_PROVENANCE_RE.search(str(text or "")))


def strip_internal_repair_provenance(text: object) -> str:
    """Remove complete workflow-provenance sentences from user-facing prose.

    Removing whole sentences is intentionally conservative.  Rewriting a
    clause could change disciplinary meaning; any surviving subject-matter
    sentence remains verbatim and an empty result is handled by the existing
    calculation-note fallback.
    """

    source = str(text or "").strip()
    if not source:
        return ""
    parts = [part for part in _SENTENCE_BOUNDARY_RE.split(source) if part]
    kept = [part.strip() for part in parts if not contains_internal_repair_provenance(part)]
    return "".join(kept).strip()
