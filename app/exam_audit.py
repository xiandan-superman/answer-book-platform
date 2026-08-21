from __future__ import annotations

import json
import re
from pathlib import Path

from .question_scores import infer_suggested_score

ITEM_RE = re.compile(r"^\s*\d{1,3}[、.．]\s*")
SECTION_RE = re.compile(r"^\s*[一二三四五六七八九十]+、")
EMBEDDED_SECTION_TITLE_RE = re.compile(
    r"(?:^|\n)\s*(?:选择题|判断题|正误题|填空题|名词解释题|名词解释|名解题|简答题|问答题|计算题|回答下列问题)\s*[（(].*(?:本题|每小题|共|分)"
)
EMBEDDED_SUBJECT_TITLE_RE = re.compile(r"(?:^|\n)\s*[“\"]?[^。\n]{2,20}[”\"]?\s*部分\s*$")
PER_ITEM_TOTAL_SCORE_PATTERNS = (
    re.compile(r"(?P<each>\d+(?:\.\d+)?)\s*分\s*/\s*小题[^\d]{0,16}共\s*(?P<total>\d+(?:\.\d+)?)\s*分"),
    re.compile(r"每小题\s*(?P<each>\d+(?:\.\d+)?)\s*分[^\d]{0,16}共\s*(?P<total>\d+(?:\.\d+)?)\s*分"),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _norm_source_item(text: str) -> str:
    return _norm(ITEM_RE.sub("", text))


def _is_item_like_source(text: str) -> bool:
    clean = str(text or "").strip()
    if not ITEM_RE.match(clean):
        return False
    return len(_norm_source_item(clean)) >= 4


def audit_exam_structure(structured_exam: dict, output_json: Path) -> list[str]:
    items = structured_exam.get("items", [])
    issues: list[str] = []
    warnings: list[str] = []
    if not items:
        issues.append("no questions extracted")
    qids = [str(x.get("question_id", "")) for x in items]
    if len(qids) != len(set(qids)):
        issues.append("duplicate question_id found")
    for item in items:
        qid = str(item.get("question_id", ""))
        stem = str(item.get("stem", "")).strip()
        if len(stem) < 4:
            warnings.append(f"{qid}: very short stem")
        section = str(item.get("section", ""))
        if "选择题" in section and item.get("image_refs"):
            warnings.append(f"{qid}: choice question has image hint; review if this is false positive")
        if "回答下列问题" in stem and not any(label in section for label in ("问答题", "简答题")):
            warnings.append(f"{qid}: stem says 回答下列问题 but section is {section}")
        if EMBEDDED_SECTION_TITLE_RE.search(stem):
            issues.append(f"{qid}: stem contains section title; exam split likely failed")
        if EMBEDDED_SUBJECT_TITLE_RE.search(stem):
            issues.append(f"{qid}: stem contains subject partition title; exam split likely failed")
        subquestions = [row for row in (item.get("subquestions") or []) if isinstance(row, dict)]
        if subquestions:
            parent_score = infer_suggested_score(item)
            child_scores = [infer_suggested_score(row) for row in subquestions]
            if parent_score is not None and all(score is not None for score in child_scores):
                child_total = sum(float(score) for score in child_scores if score is not None)
                if abs(float(parent_score) - child_total) > 1e-6:
                    issues.append(
                        f"{qid}: parent score {parent_score:g} does not equal subquestion total {child_total:g}"
                    )
    section_items: dict[str, list[dict]] = {}
    for item in items:
        raw_title = str(item.get("extracted_section_raw") or item.get("section_raw") or item.get("section") or "")
        section_items.setdefault(raw_title, []).append(item)
    for raw_title, rows in section_items.items():
        score_match = next((pattern.search(raw_title) for pattern in PER_ITEM_TOTAL_SCORE_PATTERNS if pattern.search(raw_title)), None)
        if score_match is None:
            continue
        each = float(score_match.group("each"))
        total = float(score_match.group("total"))
        expected = round(total / each) if each > 0 else 0
        if expected > 0 and abs(expected * each - total) < 1e-6 and len(rows) != expected:
            issues.append(
                f"section item count mismatch: {raw_title} implies {expected} items from {each:g}×{expected}={total:g}, extracted {len(rows)}"
            )
    source_paragraphs = [str(x) for x in structured_exam.get("source_paragraphs", []) if str(x).strip()]
    source_coverage = {
        "source_paragraph_count": len(source_paragraphs),
        "item_like_count": 0,
        "covered_item_like_count": 0,
        "missing_item_like_count": 0,
        "missing_item_like": [],
    }
    if source_paragraphs and items:
        normalized_stems = [_norm(str(item.get("stem", ""))) for item in items]
        missing_item_like: list[str] = []
        for para in source_paragraphs:
            if SECTION_RE.match(para):
                continue
            if not _is_item_like_source(para):
                continue
            source_coverage["item_like_count"] += 1
            normalized = _norm_source_item(para)
            if any(normalized and normalized in stem for stem in normalized_stems):
                source_coverage["covered_item_like_count"] += 1
            else:
                missing_item_like.append(para)
        source_coverage["missing_item_like"] = missing_item_like[:30]
        source_coverage["missing_item_like_count"] = len(missing_item_like)
        for para in missing_item_like[:10]:
            issues.append(f"source paragraph not covered by extracted questions: {para[:80]}")
    report = {
        "ok": not issues,
        "question_count": len(items),
        "source_coverage": source_coverage,
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return issues
