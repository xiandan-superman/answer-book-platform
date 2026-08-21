from __future__ import annotations

import re
from typing import Any


_CIRCLED_NUMBERS = {
    1: "①",
    2: "②",
    3: "③",
    4: "④",
    5: "⑤",
    6: "⑥",
    7: "⑦",
    8: "⑧",
    9: "⑨",
    10: "⑩",
    11: "⑪",
    12: "⑫",
    13: "⑬",
    14: "⑭",
    15: "⑮",
    16: "⑯",
    17: "⑰",
    18: "⑱",
    19: "⑲",
    20: "⑳",
}


def _number(value: Any) -> str:
    return str(value or "").strip().strip("第小问题（）()：:、.． ")


def _title_text(value: Any) -> str:
    return str(value or "").strip(" \t\r\n：:；;。")


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _heading_key(value: Any) -> str:
    return re.sub(r"[：:；;。？！?!]+$", "", _compact(value))


def _normalize_body_segment(segment: dict[str, Any]) -> dict[str, Any]:
    """Join a soft OCR/model line break before a parenthesized value.

    Structural answer-unit headings have already been consumed by
    ``_split_segments_by_unit``.  A remaining break such as ``共晶点\n（60）``
    therefore denotes prose, not a new subquestion numbered 60.
    """

    if segment.get("type") != "text":
        return segment
    text = str(segment.get("text") or "")
    normalized = re.sub(
        r"(?<=[\u4e00-\u9fffA-Za-z0-9])\r?\n[ \t]*(?=[（(]\s*\d+\s*[）)])",
        "",
        text,
    )
    if normalized == text:
        return segment
    return {**segment, "text": normalized}


def _requirement_label(number: str) -> str:
    suffix = str(number or "").rsplit(".", 1)[-1]
    try:
        numeric = int(suffix)
    except ValueError:
        return suffix
    return _CIRCLED_NUMBERS.get(numeric, f"({numeric})")


def is_synthetic_requirement_parent(question: dict[str, Any], subquestion: dict[str, Any]) -> bool:
    """Return whether a requirement container is not a source-visible level.

    New extraction results carry an explicit flag.  The conservative legacy
    check supports existing tasks created before that flag: inferred parents
    stored identical ``raw`` and ``stem`` text, whereas an explicit source
    marker such as ``(1)`` remains present in ``raw``.
    """

    if bool(subquestion.get("synthetic_parent")):
        return True
    subquestions = [row for row in question.get("subquestions", []) or [] if isinstance(row, dict)]
    requirements = [row for row in subquestion.get("requirements", []) or [] if isinstance(row, dict)]
    if len(subquestions) != 1 or len(requirements) < 2:
        return False
    raw = _compact(subquestion.get("raw"))
    stem = _compact(subquestion.get("stem"))
    if not raw or raw != stem:
        return False
    return not bool(re.match(r"^[（(]\s*[^)）]+\s*[）)]", str(subquestion.get("raw") or "").strip()))


def question_unit_rows(fragment: dict[str, Any]) -> list[dict[str, str]]:
    """Return ordered leaf answer units with stable display headings."""

    rows: list[dict[str, str]] = []
    for index, subquestion in enumerate(fragment.get("subquestions", []) or [], start=1):
        if not isinstance(subquestion, dict):
            continue
        parent_number = _number(subquestion.get("number") or index)
        parent_stem = _title_text(subquestion.get("stem") or subquestion.get("raw"))
        requirements = [item for item in subquestion.get("requirements", []) or [] if isinstance(item, dict)]
        if requirements:
            flatten = is_synthetic_requirement_parent(fragment, subquestion)
            for requirement_index, requirement in enumerate(requirements, start=1):
                number = _number(requirement.get("number") or f"{parent_number}.{requirement_index}")
                stem = _title_text(requirement.get("stem") or requirement.get("raw"))
                if flatten:
                    rows.append(
                        {
                            "number": number,
                            "level": "subquestion",
                            "parent_number": "",
                            "parent_heading": "",
                            "heading": f"({requirement_index}){stem}",
                            "source_heading": f"{_requirement_label(number)}、{stem}" if stem else f"{_requirement_label(number)}、",
                            "source_parent_heading": f"({parent_number}){parent_stem}",
                        }
                    )
                    continue
                rows.append(
                    {
                        "number": number,
                        "level": "requirement",
                        "parent_number": parent_number,
                        "parent_heading": f"({parent_number}){parent_stem}",
                        "heading": f"{_requirement_label(number)}、{stem}" if stem else f"{_requirement_label(number)}、",
                    }
                )
            continue
        rows.append(
            {
                "number": parent_number,
                "level": "subquestion",
                "parent_number": "",
                "parent_heading": "",
                "heading": f"({parent_number}){parent_stem}",
            }
        )
    return rows


def _block_segments(fragment: dict[str, Any], label: str) -> list[dict[str, Any]]:
    for block in fragment.get("blocks", []) or []:
        if isinstance(block, dict) and str(block.get("label") or "").strip() == label:
            return [segment for segment in block.get("segments", []) or [] if isinstance(segment, dict)]
    return []


def _split_segments_by_unit(
    segments: list[dict[str, Any]],
    rows: list[dict[str, str]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    leaf_titles = {_heading_key(row["heading"]): row["number"] for row in rows}
    leaf_titles.update({_heading_key(row["source_heading"]): row["number"] for row in rows if row.get("source_heading")})
    parent_titles = {_heading_key(row["parent_heading"]) for row in rows if row.get("parent_heading")}
    parent_titles.update({_heading_key(row["source_parent_heading"]) for row in rows if row.get("source_parent_heading")})
    by_unit: dict[str, list[dict[str, Any]]] = {}
    unassigned: list[dict[str, Any]] = []
    current = ""
    for segment in segments:
        if segment.get("type") == "text":
            compact = _heading_key(segment.get("text"))
            if compact in parent_titles:
                current = ""
                continue
            unit_number = leaf_titles.get(compact)
            if unit_number:
                current = unit_number
                by_unit.setdefault(current, [])
                continue
        if current:
            by_unit.setdefault(current, []).append(_normalize_body_segment(segment))
        else:
            unassigned.append(segment)
    return by_unit, unassigned


def _figure_segments_by_unit(
    fragment: dict[str, Any],
    segments: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    specs = [spec for spec in fragment.get("figure_specs", []) or [] if isinstance(spec, dict)]
    spec_units = [str(spec.get("answer_unit_number") or "").strip() for spec in specs]
    by_unit: dict[str, list[dict[str, Any]]] = {}
    unassigned: list[dict[str, Any]] = []
    current_unit = ""
    image_index = 0
    for segment in segments:
        explicit = str(segment.get("answer_unit_number") or "").strip()
        if segment.get("type") == "image_ref":
            current_unit = explicit or (spec_units[image_index] if image_index < len(spec_units) else "")
            image_index += 1
        elif explicit:
            current_unit = explicit
        if current_unit:
            by_unit.setdefault(current_unit, []).append(segment)
        else:
            unassigned.append(segment)
    return by_unit, unassigned


def plan_ordered_answer_units(fragment: dict[str, Any]) -> dict[str, Any]:
    """Project question-level blocks into original answer-unit order.

    The projection is read-only: formula refs, images, captions and prose are
    reused verbatim. If legacy headings cannot be mapped safely, ``ok`` is
    false and the DOCX renderer must keep the established block layout.
    """

    rows = question_unit_rows(fragment)
    declared_units = {
        str(unit.get("number") or "").strip()
        for unit in fragment.get("answer_units", []) or []
        if isinstance(unit, dict) and str(unit.get("number") or "").strip()
    }
    if len(rows) < 2 or not declared_units:
        return {"ok": False, "units": [], "unassigned": {}}

    analysis, analysis_unassigned = _split_segments_by_unit(_block_segments(fragment, "解析"), rows)
    steps, steps_unassigned = _split_segments_by_unit(_block_segments(fragment, "解题步骤"), rows)
    figures, figure_unassigned = _figure_segments_by_unit(fragment, _block_segments(fragment, "图示"))
    units: list[dict[str, Any]] = []
    for row in rows:
        number = row["number"]
        if number not in declared_units:
            continue
        if not (analysis.get(number) or steps.get(number) or figures.get(number)):
            continue
        units.append(
            {
                **row,
                "analysis_segments": analysis.get(number, []),
                "step_segments": steps.get(number, []),
                "figure_segments": figures.get(number, []),
            }
        )

    mapped = {unit["number"] for unit in units}
    required_payload_units = set(analysis) | set(steps) | set(figures)
    ok = (
        bool(units)
        and required_payload_units.issubset(mapped)
        and not analysis_unassigned
        and not steps_unassigned
        and not figure_unassigned
    )
    return {
        "ok": ok,
        "units": units,
        "unassigned": {
            "analysis": analysis_unassigned,
            "steps": steps_unassigned,
            "figures": figure_unassigned,
        },
    }
