from __future__ import annotations

import re
from typing import Any

from .text_utils import cn_to_int

SCORE_KEYS = ("confirmed_score", "score", "points", "point", "分值")


def parse_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        score = float(value)
        return score if score >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    score = float(match.group(0))
    return score if score >= 0 else None


def normalize_score(value: Any) -> int | float | None:
    score = parse_score(value)
    if score is None:
        return None
    return int(score) if score.is_integer() else score


def format_score(value: Any) -> str:
    score = normalize_score(value)
    return "" if score is None else str(score)


def confirmed_score_from_question(question: dict[str, Any]) -> float | None:
    if question.get("score_reviewed"):
        return parse_score(question.get("confirmed_score"))
    for key in SCORE_KEYS:
        score = parse_score(question.get(key))
        if score is not None:
            return score
    return None


def _score_text(question: dict[str, Any], keys: tuple[str, ...]) -> str:
    return " ".join(str(question.get(key) or "") for key in keys)


def _question_number_tokens(question: dict[str, Any]) -> list[str]:
    raw = str(question.get("number") or "").strip()
    tokens: list[str] = []
    if raw:
        tokens.append(raw)
    if raw.isdigit():
        digits = "一二三四五六七八九"
        value = int(raw)
        tokens.append(str(value))
        if 1 <= value <= 9:
            tokens.append(digits[value - 1])
        elif value == 10:
            tokens.append("十")
    else:
        numeric = cn_to_int(raw)
        if numeric is not None:
            tokens.append(str(numeric))
    return list(dict.fromkeys(token for token in tokens if token))


def _split_question_number_list(text: str) -> list[str]:
    text = text.replace("和", "、").replace("及", "、").replace(",", "、").replace("，", "、")
    values: list[str] = []
    for part in [chunk.strip() for chunk in text.split("、") if chunk.strip()]:
        if "-" in part or "至" in part or "到" in part:
            bounds = re.split(r"[-至到]", part, maxsplit=1)
            if len(bounds) == 2:
                start = cn_to_int(bounds[0]) if not bounds[0].isdigit() else int(bounds[0])
                end = cn_to_int(bounds[1]) if not bounds[1].isdigit() else int(bounds[1])
                if start is not None and end is not None and start <= end <= start + 30:
                    values.extend(str(i) for i in range(start, end + 1))
                    continue
        values.append(part)
        parsed = cn_to_int(part)
        if parsed is not None:
            values.append(str(parsed))
    return list(dict.fromkeys(values))


def _per_question_score_from_text(text: str, question: dict[str, Any]) -> float | None:
    tokens = set(_question_number_tokens(question))
    if not tokens:
        return None
    for match in re.finditer(r"第\s*([一二三四五六七八九十\d、,，和及\-至到]+)\s*(?:小题|题)\s*(?:各)?\s*(\d+(?:\.\d+)?)\s*分", text):
        numbers = set(_split_question_number_list(match.group(1)))
        if tokens & numbers:
            return float(match.group(2))
    return None


def infer_suggested_score(question: dict[str, Any]) -> float | None:
    for key in SCORE_KEYS:
        score = parse_score(question.get(key))
        if score is not None:
            return score
    section_text = _score_text(question, ("section", "section_raw", "extracted_section", "extracted_section_raw", "raw_title"))
    per_question = _per_question_score_from_text(section_text, question)
    if per_question is not None:
        return per_question
    # A grouped/unnumbered parent represents the whole major section. Its stem
    # commonly contains several child scores such as 2, 8 and 4 points. The
    # explicit section total must win; otherwise the first child score is
    # incorrectly confirmed as the parent score.
    section_total = re.search(r"(?:本题)?\s*(?:满分|共)\s*(\d+(?:\.\d+)?)\s*分", section_text)
    bare_parenthesized_total = re.search(
        r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]",
        section_text,
    )
    section_item_count = int(question.get("section_item_count") or 0)
    represents_whole_section = bool(question.get("subquestions")) and (
        section_item_count == 1
        if section_item_count
        else str(question.get("number") or "").strip() == str(question.get("major_number") or "").strip()
    )
    if section_total and represents_whole_section:
        return float(section_total.group(1))
    if bare_parenthesized_total and represents_whole_section:
        return float(bare_parenthesized_total.group(1))
    section_match = re.search(r"每小题\s*(\d+(?:\.\d+)?)\s*分", section_text)
    if section_match:
        return float(section_match.group(1))
    text = _score_text(question, ("stem", "title", "raw_title"))
    for pattern in (
        r"每小题\s*(\d+(?:\.\d+)?)\s*分",
        r"[（(]\s*(?:本题)?\s*(?:满分|共)?\s*(\d+(?:\.\d+)?)\s*分\s*[）)]",
        r"(?:本题)?\s*(?:满分|共)\s*(\d+(?:\.\d+)?)\s*分",
        r"(\d+(?:\.\d+)?)\s*分",
    ):
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    if section_total:
        return float(section_total.group(1))
    return None
