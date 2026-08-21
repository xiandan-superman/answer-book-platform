from __future__ import annotations

import re
from typing import Any


DECLARED_OCR_CORRECTION_RE = re.compile(
    r"题目中(?P<source>.+?)应为(?P<target>.+?)的\s*OCR\s*错误",
    flags=re.IGNORECASE,
)


def _declared_correction_candidates(fragment: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for warning in fragment.get("warnings", []) or []:
        match = DECLARED_OCR_CORRECTION_RE.search(str(warning or ""))
        if not match:
            continue
        source = match.group("source").strip()
        target = match.group("target").strip()
        source_tuple = re.search(r"[（(]([^（）()]+)[）)]", source)
        target_tuple = re.search(r"[（(]([^（）()]+)[）)]", target)
        if source_tuple and target_tuple:
            source = source_tuple.group(1).strip()
            target = target_tuple.group(1).strip()
        else:
            source = re.sub(r"^(?:坐标|字符|公式|化学式)", "", source).strip("（）() ：:")
            target = target.strip("（）() ：:")
        if source and target and source != target:
            candidate = {"source": source, "target": target}
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _resolve_parent(root: Any, path: list[Any]) -> tuple[Any, Any] | None:
    if not path:
        return None
    current = root
    for component in path[:-1]:
        try:
            current = current[int(component)] if isinstance(current, list) else current[str(component)]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return current, path[-1]


def apply_declared_ocr_corrections(fragment: dict[str, Any]) -> list[dict[str, str]]:
    """Apply only corrections carrying verified source location and span.

    A warning written by a model is evidence of uncertainty, not evidence that
    every matching token in the answer is wrong.  Warning-derived candidates
    are therefore recorded for quality review.  Mutation requires a structured
    ``_meta.verified_ocr_corrections`` entry containing ``path``, ``start`` and
    ``end`` that points to the exact source-backed occurrence.
    """

    candidates = _declared_correction_candidates(fragment)
    if not candidates:
        return []
    meta = dict(fragment.get("_meta") or {})
    meta["declared_ocr_corrections_pending"] = candidates

    verified = meta.get("verified_ocr_corrections")
    verified_entries = verified if isinstance(verified, list) else []
    applied: list[dict[str, str]] = []
    for entry in verified_entries:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        target = str(entry.get("target") or "")
        path = entry.get("path")
        if not source or not target or not isinstance(path, list):
            continue
        if {"source": source, "target": target} not in candidates:
            continue
        resolved = _resolve_parent(fragment, path)
        if resolved is None:
            continue
        parent, key = resolved
        try:
            value = parent[int(key)] if isinstance(parent, list) else parent[str(key)]
            start = int(entry.get("start"))
            end = int(entry.get("end"))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not isinstance(value, str) or start < 0 or end < start or value[start:end] != source:
            continue
        rewritten = value[:start] + target + value[end:]
        if isinstance(parent, list):
            parent[int(key)] = rewritten
        else:
            parent[str(key)] = rewritten
        applied_item = {"source": source, "target": target}
        if applied_item not in applied:
            applied.append(applied_item)

    if applied:
        meta["declared_ocr_corrections_applied"] = applied
        pending = [candidate for candidate in candidates if candidate not in applied]
        if pending:
            meta["declared_ocr_corrections_pending"] = pending
        else:
            meta.pop("declared_ocr_corrections_pending", None)
    fragment["_meta"] = meta
    return applied
