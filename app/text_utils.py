from __future__ import annotations

import re


CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def cn_to_int(text: str) -> int:
    text = str(text).strip()
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + CN_NUM.get(text[1:], 0)
    if text.endswith("十"):
        return CN_NUM.get(text[0], 0) * 10
    if "十" in text:
        a, b = text.split("十", 1)
        return CN_NUM.get(a, 1) * 10 + CN_NUM.get(b, 0)
    return CN_NUM.get(text, 0)


def tokenize_zh_en(text: str) -> list[str]:
    text = clean_text(text).lower()
    latin = re.findall(r"[a-z]+[a-z0-9_+-]*|\d+(?:\.\d+)?", text)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    grams: list[str] = []
    for token in cjk:
        if len(token) <= 4:
            grams.append(token)
        else:
            grams.extend(token[i : i + 2] for i in range(len(token) - 1))
            grams.extend(token[i : i + 3] for i in range(len(token) - 2))
    return latin + grams


_FORMULA_FORMAT_COMMAND_RE = re.compile(r"\\(?:mathbf|boldsymbol|mathrm|mathit|text|left|right|displaystyle|textstyle)\b")
_FORMULA_TAG_RE = re.compile(r"\\(?:tag|label)\s*\{[^}]*\}")
_ZONE_PRODUCT_RE = re.compile(r"(?<![a-z])([hkluvw]{2})(?![a-z])")


def normalize_formula(text: str) -> str:
    """Return a conservative canonical form suitable for formula retrieval.

    This is deliberately not a symbolic algebra system.  It removes presentation
    differences and normalizes the two-letter Miller/zone products only, so
    ``uh`` and ``hu`` compare equal without rewriting unrelated expressions.
    """
    value = str(text or "").lower()
    value = _FORMULA_TAG_RE.sub("", value)
    value = _FORMULA_FORMAT_COMMAND_RE.sub("", value)
    value = value.replace("\\cdot", "*").replace("·", "*").replace("×", "*")
    value = value.replace("−", "-").replace("–", "-").replace("＝", "=")
    value = value.replace("$$", "").replace("\\[", "").replace("\\]", "").replace("\\(", "").replace("\\)", "")
    # Knowledge plans often prefix an expression with a Chinese label, while
    # MinerU equation blocks contain the expression only.
    value = re.sub(r"^[\u4e00-\u9fff_a-z0-9 ]{2,}[：:]", "", value)
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\\[,!;:]", "", value)
    value = re.sub(r"\s+", "", value)

    def normalize_zone_product(match: re.Match[str]) -> str:
        return "".join(sorted(match.group(1)))

    return _ZONE_PRODUCT_RE.sub(normalize_zone_product, value)


def formulas_equivalent(left: str, right: str) -> bool:
    """Whether two formula strings are equal after conservative normalization."""
    normalized_left = normalize_formula(left)
    normalized_right = normalize_formula(right)
    return bool(normalized_left and normalized_right and normalized_left == normalized_right)
