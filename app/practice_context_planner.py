from __future__ import annotations

import re
from typing import Any, Iterable

from .model_context_planner import (
    build_model_context_plan,
    estimate_text_tokens,
    model_stage_quality_limit,
)


IMAGE_ANCHOR_RE = re.compile(r"⟦IMAGE_REF:(\d+);[^⟧]*⟧")
IMAGE_EVIDENCE_RE = re.compile(r"^image:(\d+)$")
def _unique_strings(values: Iterable[Any], *, limit: int = 200) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def image_evidence_refs(text: str) -> list[str]:
    return _unique_strings(
        (f"image:{match.group(1)}" for match in IMAGE_ANCHOR_RE.finditer(str(text or ""))),
        limit=64,
    )


def image_numbers_from_evidence_refs(values: Iterable[Any], *, maximum: int | None = None) -> list[int]:
    numbers: list[int] = []
    for value in values:
        match = IMAGE_EVIDENCE_RE.fullmatch(str(value or "").strip())
        if not match:
            continue
        number = int(match.group(1))
        if number < 1 or (maximum is not None and number > maximum) or number in numbers:
            continue
        numbers.append(number)
    return numbers


def apply_source_evidence_contract(source: dict[str, Any]) -> dict[str, Any]:
    """Attach deterministic evidence metadata to one source unit in place."""

    content_refs = _unique_strings(source.get("content_refs") or [], limit=120)
    visual_refs = image_evidence_refs(
        str(source.get("source_content") or source.get("source_text") or source.get("stem_excerpt") or "")
    )
    source["content_refs"] = content_refs
    source["evidence_refs"] = _unique_strings([*content_refs, *visual_refs], limit=160)
    source["visual_evidence_refs"] = visual_refs
    source["visual_dependency"] = {
        "required": bool(visual_refs),
        "evidence_refs": visual_refs,
        "must_reach_stages": ["planning", "generation", "semantic_review"] if visual_refs else [],
        "replaceable_by_summary": False if visual_refs else True,
    }
    return source


def aggregate_source_evidence(
    source_refs: Iterable[Any],
    source_catalog: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {
        str(item.get("source_question_id") or "").strip(): item
        for item in source_catalog
        if isinstance(item, dict) and str(item.get("source_question_id") or "").strip()
    }
    content_refs: list[str] = []
    evidence_refs: list[str] = []
    visual_refs: list[str] = []
    for source_id in _unique_strings(source_refs, limit=20):
        source = by_id.get(source_id)
        if not source:
            continue
        apply_source_evidence_contract(source)
        content_refs.extend(source.get("content_refs") or [])
        evidence_refs.extend(source.get("evidence_refs") or [])
        visual_refs.extend(source.get("visual_evidence_refs") or [])
    return {
        "content_refs": _unique_strings(content_refs, limit=240),
        "required_evidence_refs": _unique_strings(evidence_refs, limit=320),
        "visual_evidence_refs": _unique_strings(visual_refs, limit=64),
    }


def build_context_plan(
    *,
    stage: str,
    provider_name: str,
    model_name: str,
    text: str,
    image_evidence_refs: Iterable[Any] = (),
    required_evidence_refs: Iterable[Any] = (),
    delivered_evidence_refs: Iterable[Any] = (),
    item_ids: Iterable[Any] = (),
    fixed_overhead_tokens: int = 0,
    messages: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    visual_refs = _unique_strings(image_evidence_refs, limit=64)
    required_refs = _unique_strings(required_evidence_refs, limit=320)
    delivered_refs = _unique_strings([*delivered_evidence_refs, *visual_refs], limit=320)
    required_visual_refs = [item for item in required_refs if IMAGE_EVIDENCE_RE.fullmatch(item)]
    delivered_visual_refs = [item for item in delivered_refs if IMAGE_EVIDENCE_RE.fullmatch(item)]
    required_content_refs = [item for item in required_refs if not IMAGE_EVIDENCE_RE.fullmatch(item)]
    delivered_content_refs = [item for item in delivered_refs if not IMAGE_EVIDENCE_RE.fullmatch(item)]
    message_list = list(messages) if messages is not None else [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                *[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"evidence://{evidence_ref}"},
                    }
                    for evidence_ref in visual_refs
                ],
            ],
        }
    ]
    generic = build_model_context_plan(
        stage=stage,
        provider_name=provider_name,
        model_name=model_name,
        messages=message_list,
        required_evidence_refs=required_refs,
        delivered_evidence_refs=delivered_refs,
        item_ids=item_ids,
        fixed_overhead_tokens=fixed_overhead_tokens,
    )
    generic["visual_evidence_refs"] = visual_refs
    generic["required_visual_evidence_refs"] = required_visual_refs
    generic["delivered_visual_evidence_refs"] = delivered_visual_refs
    generic["visual_evidence_complete"] = all(item in delivered_visual_refs for item in required_visual_refs)
    generic["required_content_evidence_refs"] = required_content_refs
    generic["delivered_content_evidence_refs"] = delivered_content_refs
    generic["content_evidence_complete"] = all(item in delivered_content_refs for item in required_content_refs)
    return generic
