from __future__ import annotations

import re
from typing import Any

from .capabilities.catalog import DEFAULT_CAPABILITY_REGISTRY


FORMULA_TEXT_RE = re.compile(
    r"("
    r"\\[A-Za-z]+|"
    r"[_^]|"
    r"[∂∆ΔΣΠ√≈≤≥≠→⇌∞]|"
    r"[A-Za-z]\s*=\s*[^。；，,\u4e00-\u9fff]{2,}|"
    r"\b(?:ln|log|exp|sin|cos|tan)\s*[\(\w]|"
    r"\b(?:RT|nRT|nF|zF|pV|Kp|Ksp|Ka|Kb)\b|"
    r"(?<![A-Za-z0-9.])(?:[A-Za-z]|\d+(?:\.\d+)?)\s*/\s*"
    r"(?:[A-Za-z]|\d+(?:\.\d+)?)(?![A-Za-z0-9.])"
    r")"
)

GENERIC_LABEL_CONTEXT_RE = re.compile(
    r"(横坐标|纵坐标|坐标轴|标出|标明|标签|图中|示意图|作图|绘制)",
    re.IGNORECASE,
)

GENERIC_LABEL_MATCH_RE = re.compile(
    r"^(?:"
    r"[A-Za-z]/(?:\d+|[A-Za-z])|"
    r"[A-Za-z]\([A-Za-z]\)"
    r")$"
)

CHINESE_FORMULA_PARAPHRASE_RE = re.compile(
    r"("
    r"(?:吉布斯自由能变|自由能变|熵变|焓变|内能变|附加压力|化学势)"
    r"[^。；\n]{0,24}"
    r"(?:小于零|大于零|等于零|为零|为正|为负|等于|正比于|反比于|乘积|商|差值|和值)"
    r"|"
    r"(?:等于|正比于|反比于)"
    r"[^。；\n]{0,30}"
    r"(?:减去|加上|乘以|除以|的乘积|的商|平方|开方)"
    r"|"
    r"(?:温度与熵变的乘积|压力与体积的乘积|表面张力与曲率半径|曲率半径成反比)"
    r")"
)


def is_allowed_domain_notation(text: str, match_text: str) -> bool:
    source = str(text or "")
    value = str(match_text or "").strip()
    if not value:
        return False
    compact = re.sub(r"\s+", "", value)
    capability_matches = DEFAULT_CAPABILITY_REGISTRY.match_expressions(
        value,
        source_format="text",
        context=source,
    )
    if any(
        match.expression_kind == "domain_notation"
        and re.sub(r"\s+", "", match.value) == compact
        for match in capability_matches
    ):
        return True
    if not GENERIC_LABEL_CONTEXT_RE.search(source):
        return False
    return bool(GENERIC_LABEL_MATCH_RE.fullmatch(compact))


def _allowed_domain_notation_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in DEFAULT_CAPABILITY_REGISTRY.match_expressions(
        str(text or ""),
        source_format="text",
        context=str(text or ""),
    ):
        if match.expression_kind == "domain_notation":
            spans.append((match.start, match.end))
    return spans


def _inside_allowed_span(match: re.Match[str], spans: list[tuple[int, int]]) -> bool:
    for start, end in spans:
        if start <= match.start() and match.end() <= end:
            return True
    return False


def _filter_allowed_domain_notation(text: str, matches: list[str]) -> list[str]:
    return [match for match in matches if not is_allowed_domain_notation(text, match)]


def formula_like_matches(
    text: str,
    include_chinese_paraphrase: bool = True,
    *,
    ignore_allowed_domain_notation: bool = True,
) -> list[str]:
    matches: list[str] = []
    allowed_spans = _allowed_domain_notation_spans(str(text)) if ignore_allowed_domain_notation else []
    regexes = [FORMULA_TEXT_RE]
    if include_chinese_paraphrase:
        regexes.append(CHINESE_FORMULA_PARAPHRASE_RE)
    for regex in regexes:
        for match in regex.finditer(str(text)):
            value = match.group(0).strip()
            if _inside_allowed_span(match, allowed_spans):
                continue
            if value and value not in matches:
                matches.append(value)
    if ignore_allowed_domain_notation:
        matches = _filter_allowed_domain_notation(str(text), matches)
    return matches


def symbolic_formula_like_matches(text: str, *, ignore_allowed_domain_notation: bool = True) -> list[str]:
    matches: list[str] = []
    allowed_spans = _allowed_domain_notation_spans(str(text)) if ignore_allowed_domain_notation else []
    for match in FORMULA_TEXT_RE.finditer(str(text)):
        value = match.group(0).strip()
        if _inside_allowed_span(match, allowed_spans):
            continue
        if value and value not in matches:
            matches.append(value)
    if ignore_allowed_domain_notation:
        matches = _filter_allowed_domain_notation(str(text), matches)
    return matches


SUBQUESTION_TITLE_RE = re.compile(
    r"^\s*第?\s*[\(（]?\s*\d+\s*[\)）]?\s*(?:小问|问)\s*[:：]\s*.+$"
)


def is_formula_allowed_label_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if SUBQUESTION_TITLE_RE.match(value):
        return True
    return False


def looks_like_formula(text: str) -> bool:
    return bool(formula_like_matches(text))


def looks_like_symbolic_formula(text: str) -> bool:
    return bool(symbolic_formula_like_matches(text))


def audit_text_segments_no_formula(
    value: Any,
    ignored_block_labels: set[str] | None = None,
    *,
    include_chinese_paraphrase: bool = False,
) -> list[str]:
    issues: list[str] = []
    ignored_block_labels = ignored_block_labels or set()

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            label = str(node.get("label", "")).strip()
            if label in ignored_block_labels and isinstance(node.get("segments"), list):
                return
            if node.get("type") == "text":
                text = str(node.get("text", ""))
                if is_formula_allowed_label_text(text):
                    matches = []
                else:
                    matches = formula_like_matches(text) if include_chinese_paraphrase else symbolic_formula_like_matches(text)
                if matches:
                    issues.append(f"{path}.text contains formula-like content; matched expression: {matches[0]}; text preview: {text[:120]}")
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for idx, child in enumerate(node):
                walk(child, f"{path}[{idx}]")

    walk(value, "")
    return issues


def assert_no_formula_leak(value: Any) -> None:
    issues = audit_text_segments_no_formula(value)
    if issues:
        joined = "\n".join(f"- {issue}" for issue in issues[:50])
        raise ValueError(f"Formula leak audit failed:\n{joined}")
