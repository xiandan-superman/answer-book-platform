"""Evidence-based cloze contracts. Coordinates are Python Unicode code points.

Models select pedagogy and quote spans. This module only checks identity and
literal text; neither a quote match nor a valid span proves teaching quality.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

VERSION = "practice_requirements.v1"
MASK = "______"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


REQUIREMENTS_SCHEMA = {
    "wording": "strict_verbatim / minimal_edit / unconstrained; 不改变或不要过多改变属于minimal_edit，只有明确仅挖空且保持原文才strict_verbatim",
    "wording_quote": "用户原始要求中支持wording解释的连续原文；无要求则空",
    "max_stem_characters": "完整题干字符上限整数，无则0；Python Unicode codepoint计数",
    "length_quote": "支持字数上限的用户连续原文，无则空",
    "cross_source": "explicit / default；仅用户明确要求单题跨来源综合才explicit，整体综合选题不等于单题必须跨源",
    "cross_source_quote": "用户明确要求单题跨来源的连续原文，否则空",
    "conflicts": ["无法同时满足的具体用户要求及来源限制；无则空列表"],
}
CLOZE_SCHEMA = {
    "source_id": "S1等本题绑定的来源ID",
    "source_field": "source_content；只能使用下方原文，禁止用recognized_content、知识点摘要或约束定义冒充原文",
    "source_quote": "原文中连续且完整可独立作答的句子，逐字复制；含公式投影无法无损映射时报告冲突",
    "quote_occurrence": "该完整句子在source_content中第几次出现，1起",
    "blanks": [{"text": "句内要挖去的连续原词", "occurrence": "该原词在句内第几次出现，1起"}],
}


def compile_requirements(raw: Any, focus: str) -> dict[str, Any]:
    # With no separate user focus there is no evidence for wording, length, or
    # cross-source constraints.  Models sometimes copy an instruction embedded
    # in the source question into this contract.  Normalize that impossible
    # claim deterministically instead of making the user rerun the whole plan.
    if not focus.strip():
        raw = {
            "wording": "unconstrained",
            "wording_quote": "",
            "max_stem_characters": 0,
            "length_quote": "",
            "cross_source": "default",
            "cross_source_quote": "",
            "conflicts": [],
        }
    if not isinstance(raw, dict):
        raise ValueError("新规划缺少用户要求结构化合同，不能证明已保留要求。")
    result = copy.deepcopy(raw)
    if result.get("wording") not in {"strict_verbatim", "minimal_edit", "unconstrained"}:
        raise ValueError("用户要求wording必须明确区分严格原句、少量改写或无约束。")
    if result.get("cross_source") not in {"explicit", "default"}:
        raise ValueError("跨来源要求必须区分用户明确要求与平台默认。")
    for field, needed in (("wording_quote", result["wording"] != "unconstrained"),
                          ("length_quote", bool(result.get("max_stem_characters"))),
                          ("cross_source_quote", result["cross_source"] == "explicit")):
        quote = result.get(field) or ""
        if not isinstance(quote, str) or (needed and not quote) or (quote and quote not in focus):
            raise ValueError(f"用户要求引用不可核验：{field}")
    maximum = result.get("max_stem_characters", 0)
    if type(maximum) is not int or maximum < 0:
        raise ValueError("题干字符上限必须是非负整数。")
    result.update(schema_version=VERSION, focus_sha256=text_hash(focus), max_stem_characters=maximum,
                  interpretation_status="model_interpreted_not_semantically_proven")
    return result


def _occurrence(text: str, quote: str, occurrence: Any) -> int:
    if not quote or type(occurrence) is not int or occurrence < 1:
        raise ValueError("源句和空位必须指定非空原文及1起的出现序号。")
    start = -1
    for _ in range(occurrence):
        start = text.find(quote, start + 1)
        if start < 0:
            raise ValueError("源句或空位原词不在声明的原文位置。")
    return start


def compile_cloze(raw: Any, item: dict[str, Any], catalog: list[dict[str, Any]], aliases: dict[str, str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("严格原句填空缺少源句与空位映射。")
    source_id = aliases.get(str(raw.get("source_id")), str(raw.get("source_id") or ""))
    if source_id not in (item.get("source_refs") or []):
        raise ValueError("源句来源未绑定到本题。")
    source = next((s for s in catalog if s.get("source_question_id") == source_id), {})
    if raw.get("source_field") != "source_content" or not isinstance(source.get("source_content"), str):
        raise ValueError("原句必须引用可用source_content原文，摘要不能替代。")
    text = source["source_content"]
    quote = raw.get("source_quote")
    if not isinstance(quote, str):
        raise ValueError("缺少逐字来源句子。")
    # These are explicit serialization tokens, not teaching-semantic guesses.
    if any(token in quote for token in ("⟦MATHML:", "⟦LATEX:", "<math", "$", "\\(", "\\[")):
        raise ValueError("源句含公式序列化投影，当前无法证明原文到空位的无损对应。")
    offset = _occurrence(text, quote, raw.get("quote_occurrence", 1))
    blanks = raw.get("blanks")
    if not isinstance(blanks, list) or not blanks:
        raise ValueError("严格填空至少需要一个可核验空位。")
    spans = []
    for blank in blanks:
        if not isinstance(blank, dict) or not isinstance(blank.get("text"), str):
            raise ValueError("空位结构无效。")
        start = _occurrence(quote, blank["text"], blank.get("occurrence", 1))
        spans.append([start, start + len(blank["text"])])
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError("空位重叠或重复，不能确定性挖空。")
    # Persist coordinates and hashes, never duplicate the answer-bearing quote.
    return {"version": 1, "coordinate_system": "python_unicode_codepoint", "source_id": source_id,
            "source_field": "source_content", "source_sha256": text_hash(text),
            "quote_start": offset, "quote_end": offset + len(quote), "quote_sha256": text_hash(quote),
            "blank_spans": spans}


def expected_stem(mapping: dict[str, Any], catalog: list[dict[str, Any]]) -> str:
    if mapping.get("version") != 1 or mapping.get("coordinate_system") != "python_unicode_codepoint" or mapping.get("source_field") != "source_content":
        raise ValueError("未知来源坐标合同，不能证明原句对应。")
    source = next((s for s in catalog if s.get("source_question_id") == mapping.get("source_id")), {})
    text = source.get("source_content")
    if not isinstance(text, str) or text_hash(text) != mapping.get("source_sha256"):
        raise ValueError("来源正文缺失或已变化，空位映射失效。")
    start, end = mapping.get("quote_start"), mapping.get("quote_end")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError("来源句子坐标无效。")
    quote = text[start:end]
    if text_hash(quote) != mapping.get("quote_sha256"):
        raise ValueError("来源句子摘要不匹配。")
    spans = mapping.get("blank_spans")
    if not isinstance(spans, list) or not spans:
        raise ValueError("空位映射缺失。")
    cursor = 0
    parts = []
    for span in spans:
        if not isinstance(span, list) or len(span) != 2 or any(type(v) is not int for v in span):
            raise ValueError("空位坐标无效。")
        left, right = span
        if not cursor <= left < right <= len(quote):
            raise ValueError("空位越界、乱序或重叠。")
        parts.extend([quote[cursor:left], MASK])
        cursor = right
    parts.append(quote[cursor:])
    return "".join(parts)


def cloze_issue(exercise: dict[str, Any], planned: dict[str, Any], practice: dict[str, Any]) -> dict[str, Any] | None:
    contract = (practice.get("blueprint") or {}).get("requirements_contract") or {}
    strict = contract.get("wording") == "strict_verbatim" and (planned.get("question_type") or exercise.get("question_type")) == "填空题"
    mapping = planned.get("cloze_mapping")
    if not strict and not mapping:
        return None
    try:
        if not isinstance(mapping, dict):
            raise ValueError("严格原句填空缺少可核验空位映射。")
        if mapping.get("source_id") not in (planned.get("source_refs") or []):
            raise ValueError("来源绑定已改变，空位映射失效。")
        catalog = practice.get("selected_source_questions") or (practice.get("source_scope") or {}).get("questions") or []
        expected = expected_stem(mapping, catalog)
        if str(exercise.get("stem") or "") != expected:
            raise ValueError("题干与源句挖空结果不一致：未挖空文字或空位已改变。")
        maximum = contract.get("max_stem_characters") or 0
        if maximum and len(expected) > maximum:
            raise ValueError(f"完整题干含空线共{len(expected)}字符，超过{maximum}字符上限。")
        if any(exercise.get(field) for field in ("options", "tables", "figures", "formulas", "generated_images")):
            raise ValueError("严格原句题含源句之外的学生可见资源，无法证明仅做挖空且无答案泄漏。")
    except (ValueError, TypeError, KeyError) as exc:
        return {"code": "cloze_provenance_invalid", "message": str(exc), "blocking": True}
    return None


def practice_cloze_issues(practice: dict[str, Any]) -> list[str]:
    blueprint = practice.get("blueprint") or {}
    planned = {str(item.get("plan_item_id")): item for item in blueprint.get("exercise_plan") or [] if isinstance(item, dict)}
    issues = []
    for exercise in practice.get("exercises") or []:
        if not isinstance(exercise, dict) or exercise.get("generation_status") == "failed":
            continue
        item = planned.get(str(exercise.get("parent_plan_item_id") or exercise.get("plan_item_id"))) or {}
        issue = cloze_issue(exercise, item, practice)
        if issue:
            issues.append(f"第{exercise.get('number')}题原句填空核验失败：{issue['message']}")
    return issues
