from __future__ import annotations

from typing import Any

from .text_utils import clean_text


QUESTION_TYPES = ("选择题", "判断题", "填空题", "名词解释", "简答题", "计算题", "作图题")

_TYPE_ALIASES = {
    "选择": "选择题",
    "判断": "判断题",
    "正误题": "判断题",
    "正误": "判断题",
    "填空": "填空题",
    "名词解释题": "名词解释",
    "名词解释": "名词解释",
    "名解题": "名词解释",
    "名解": "名词解释",
    "简答": "简答题",
    "问答题": "简答题",
    "问答": "简答题",
    "解答题": "简答题",
    "解答": "简答题",
    "综合题": "简答题",
    "综合": "简答题",
    "证明题": "简答题",
    "证明": "简答题",
    "推导题": "简答题",
    "推导": "简答题",
    "选做题": "简答题",
    "选做": "简答题",
    "计算": "计算题",
    "画图题": "作图题",
    "图示题": "作图题",
    "作图": "作图题",
    "画图": "作图题",
    "绘图": "作图题",
}

_KIND_BY_TYPE = {
    "选择题": "choice",
    "判断题": "judge",
    "填空题": "fill",
    "名词解释": "term_explanation",
    "简答题": "short_answer",
    "计算题": "calculation",
    "作图题": "graphic",
}


def normalize_question_type(value: Any) -> str:
    text = clean_text(str(value or ""))
    if text in QUESTION_TYPES:
        return text
    return _TYPE_ALIASES.get(text, "")


def _type_from_text(text: str) -> str:
    if any(keyword in text for keyword in ("选择题", "选择")):
        return "选择题"
    if any(keyword in text for keyword in ("判断题", "正误题", "判断", "正误")):
        return "判断题"
    if any(keyword in text for keyword in ("填空题", "填空")):
        return "填空题"
    if any(keyword in text for keyword in ("名词解释题", "名词解释", "名解题", "名解")):
        return "名词解释"
    if any(keyword in text for keyword in ("计算题", "计算")):
        return "计算题"
    if any(keyword in text for keyword in ("作图题", "画图题", "图示题", "作图", "画出", "绘制", "绘图", "示意图")):
        return "作图题"
    if any(keyword in text for keyword in ("简答题", "问答题", "解答题", "综合题", "证明题", "推导题", "选做题")):
        return "简答题"
    return ""


def infer_question_type(item: dict[str, Any]) -> str:
    for key in ("confirmed_question_type", "question_type"):
        explicit = normalize_question_type(item.get(key))
        if explicit:
            return explicit
    section_text = " ".join(str(item.get(key) or "") for key in ("section", "section_raw"))
    from_section = _type_from_text(section_text)
    if from_section:
        return from_section
    qid = str(item.get("question_id") or "")
    if qid.startswith("choice_"):
        return "选择题"
    if qid.startswith("judge_"):
        return "判断题"
    if qid.startswith("fill_"):
        return "填空题"
    if qid.startswith(("term_", "definition_")):
        return "名词解释"
    if qid.startswith("calc_"):
        return "计算题"
    if qid.startswith(("figure_", "diagram_", "graphic_")):
        return "作图题"
    from_stem = _type_from_text(str(item.get("stem") or ""))
    return from_stem or "简答题"


def explicit_question_type(item: dict[str, Any]) -> str:
    for key in ("confirmed_question_type", "question_type"):
        explicit = normalize_question_type(item.get(key))
        if explicit:
            return explicit
    return ""


def question_kind(item: dict[str, Any]) -> str:
    sub_kinds = {
        _KIND_BY_TYPE.get(explicit_question_type(part), "short_answer")
        for part in iter_question_parts(item)
        if isinstance(part, dict) and explicit_question_type(part)
    }
    if len(sub_kinds) > 1:
        return "mixed"
    if len(sub_kinds) == 1:
        return next(iter(sub_kinds))
    return _KIND_BY_TYPE.get(explicit_question_type(item) or infer_question_type(item), "short_answer")


def question_has_type(item: dict[str, Any], question_type: str) -> bool:
    normalized = normalize_question_type(question_type)
    if not normalized:
        return False
    if explicit_question_type(item) == normalized:
        return True
    return any(isinstance(part, dict) and explicit_question_type(part) == normalized for part in iter_question_parts(item))


def iter_question_parts(item: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []

    def walk(parent: dict[str, Any]) -> None:
        for key in ("subquestions", "requirements"):
            for raw in parent.get(key, []) or []:
                if not isinstance(raw, dict):
                    continue
                parts.append(raw)
                walk(raw)

    walk(item)
    return parts


def iter_leaf_question_parts(item: dict[str, Any]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []

    def walk(parent: dict[str, Any]) -> None:
        children = [
            raw
            for key in ("subquestions", "requirements")
            for raw in (parent.get(key, []) or [])
            if isinstance(raw, dict)
        ]
        if not children:
            return
        for child in children:
            grand_children = [
                raw
                for key in ("subquestions", "requirements")
                for raw in (child.get(key, []) or [])
                if isinstance(raw, dict)
            ]
            if grand_children:
                walk(child)
            else:
                leaves.append(child)

    walk(item)
    return leaves


def is_choice_question(item: dict[str, Any]) -> bool:
    return question_has_type(item, "选择题")


def is_calculation_question(item: dict[str, Any]) -> bool:
    return question_has_type(item, "计算题")


def has_calculation_answer_unit(item: dict[str, Any]) -> bool:
    leaves = iter_leaf_question_parts(item)
    if leaves:
        return any(explicit_question_type(part) == "计算题" for part in leaves)
    return (explicit_question_type(item) or infer_question_type(item)) == "计算题"


def is_short_answer_question(item: dict[str, Any]) -> bool:
    return (explicit_question_type(item) or infer_question_type(item)) == "简答题"


def is_term_explanation_question(item: dict[str, Any]) -> bool:
    return (explicit_question_type(item) or infer_question_type(item)) == "名词解释"
