from __future__ import annotations

import base64
import csv
import json
import mimetypes
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .concurrency import run_limited_concurrent
from .evidence_trace_export import write_evidence_trace_csv
from .llm_client import LLMError, OpenAICompatibleClient
from .prompt_registry import prompt_contract
from .question_understanding import attach_question_visuals, needs_vision_model
from .retrieval import EvidenceCandidate, build_candidates, candidates_for_question, formula_match_score, planned_formulas_for_query
from .settings import ProviderConfig, provider_model_supports_vision
from .text_utils import clean_text

SCHEMA_VERSION = "answer_book.evidence_selection.v3"
# Evidence selection is classification over a bounded candidate set, not answer
# writing.  Large reasoning budgets previously consumed 11k-13k hidden tokens
# per question and dominated runtime without adding user-visible detail.
EVIDENCE_SELECTION_MAX_TOKENS = 8192
EVIDENCE_SELECTION_TIMEOUT_SECONDS = 90

SUPPORT_TYPE_LABELS = {
    "direct_support": "直接证据",
    "general_principle_support": "通用原理证据",
    "transferable_support": "可迁移证据",
    "inverse_process_support": "反向过程证据",
    "background_only": "背景材料",
    "keyword_only": "仅关键词相似",
}
ACCEPTABLE_SUPPORT_TYPES = {
    "direct_support",
    "general_principle_support",
    "transferable_support",
    "inverse_process_support",
}
NON_DIRECT_SUPPORT_TYPES = {
    "general_principle_support",
    "transferable_support",
    "inverse_process_support",
}


@dataclass
class EvidenceSelectionResult:
    ok: bool
    question_count: int
    selected_question_count: int
    selected_evidence_count: int
    expansion_question_count: int
    output_json: str
    confirmed_candidates_csv: str
    evidence_trace_csv: str
    max_workers: int = 1
    parallel_enabled: bool = False


def evidence_selection_worker_count() -> int:
    raw = os.environ.get("EVIDENCE_SELECTION_MAX_WORKERS") or os.environ.get("ANSWER_GENERATION_MAX_WORKERS", "10")
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 10


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(x) for x in value]
    else:
        values = []
    out: list[str] = []
    for item in values:
        text = clean_text(item)
        if text and text not in out:
            out.append(text)
    return out


def _candidate_dict(candidates: list[EvidenceCandidate]) -> dict[str, EvidenceCandidate]:
    return {candidate.evidence_id: candidate for candidate in candidates if candidate.evidence_id}


def _text_model_visual_status(raw: dict[str, Any]) -> dict[str, Any]:
    source_type = str(raw.get("source_type") or "").strip()
    has_asset = bool(str(raw.get("asset_path") or "").strip())
    if source_type not in {"figure_block", "table_block", "equation_block"} or not has_asset:
        return {"asset_available": has_asset, "text_model_can_read_visual": True, "visual_warning": ""}
    readable = bool(
        clean_text(raw.get("visual_summary") or "")
        or clean_text(raw.get("ocr_text") or "")
        or clean_text(raw.get("table_rows") or "")
        or clean_text(raw.get("table_html") or "")
        or clean_text(raw.get("caption") or "")
    )
    if readable:
        return {
            "asset_available": True,
            "text_model_can_read_visual": True,
            "visual_warning": "文本模型只能读取该图表的 caption/OCR/table_html/visual_summary，不能直接读取原图。",
        }
    return {
        "asset_available": True,
        "text_model_can_read_visual": False,
        "visual_warning": clean_text(raw.get("visual_unreadable_reason") or "该教材证据只有图片资源，缺少可供文本模型读取的视觉摘要/OCR/表格结构。"),
    }


def _candidate_dedupe_key(raw: dict[str, Any]) -> str:
    block_id = clean_text(raw.get("block_id") or "")
    if block_id:
        return f"block:{block_id}"
    source = clean_text(raw.get("source_file") or raw.get("textbook") or "")
    page = clean_text(raw.get("printed_page") or raw.get("pdf_page_idx") or "")
    text = re.sub(r"\s+", "", clean_text(raw.get("evidence_text") or raw.get("caption") or raw.get("ocr_text") or ""))
    return f"text:{source}:{page}:{text[:500]}"


def _compact_candidate_record(raw: dict[str, Any], *, include_visual_assets: bool) -> dict[str, Any]:
    status = _text_model_visual_status(raw)
    evidence_text = clean_text(raw.get("evidence_text") or raw.get("text") or "")
    record = {
        "evidence_id": raw.get("evidence_id"),
        "merged_evidence_ids": raw.get("merged_evidence_ids", []),
        "knowledge_point": raw.get("knowledge_point"),
        "merged_knowledge_points": raw.get("merged_knowledge_points", []),
        "citation_textbook": raw.get("citation_textbook") or raw.get("textbook"),
        "chapter_section": raw.get("chapter_section") or raw.get("chapter"),
        "printed_page": raw.get("printed_page") or raw.get("page_start"),
        "score": raw.get("score"),
        "source_type": raw.get("source_type"),
        "caption": clean_text(raw.get("caption") or "")[:300],
        "surrounding_text_preview": clean_text(raw.get("surrounding_text_preview") or "")[:260],
        "table_html": clean_text(raw.get("table_html") or "")[:600],
        "table_rows": clean_text(raw.get("table_rows") or "")[:900],
        "visual_summary": clean_text(raw.get("visual_summary") or "")[:600],
        "visual_status": raw.get("visual_status"),
        "ocr_text": clean_text(raw.get("ocr_text") or "")[:500],
        "asset_available": status.get("asset_available"),
        "text_model_can_read_visual": status.get("text_model_can_read_visual"),
        "visual_warning": clean_text(status.get("visual_warning") or "")[:260],
        "evidence_text": evidence_text[:700],
        "_full_evidence_text_chars": len(evidence_text),
    }
    record["asset_path"] = raw.get("asset_path") if include_visual_assets else ""
    return record


def _candidate_payload(candidates: list[EvidenceCandidate], *, include_visual_assets: bool, max_unique: int = 12) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        raw = asdict(candidate)
        key = _candidate_dedupe_key(raw)
        existing = by_key.get(key)
        if existing is None:
            raw["merged_evidence_ids"] = []
            raw["merged_knowledge_points"] = []
            by_key[key] = raw
            continue
        existing.setdefault("merged_evidence_ids", [])
        if raw.get("evidence_id") and raw.get("evidence_id") not in existing["merged_evidence_ids"]:
            existing["merged_evidence_ids"].append(raw.get("evidence_id"))
        existing.setdefault("merged_knowledge_points", [])
        kp = clean_text(raw.get("knowledge_point") or "")
        if kp and kp not in existing["merged_knowledge_points"]:
            existing["merged_knowledge_points"].append(kp)
        try:
            if float(raw.get("score") or 0) > float(existing.get("score") or 0):
                keep_ids = list(existing.get("merged_evidence_ids", []) or [])
                old_id = existing.get("evidence_id")
                if old_id and old_id not in keep_ids:
                    keep_ids.append(old_id)
                keep_points = existing.get("merged_knowledge_points", [])
                raw["merged_evidence_ids"] = keep_ids
                raw["merged_knowledge_points"] = keep_points
                by_key[key] = raw
        except (TypeError, ValueError):
            pass
    for raw in by_key.values():
        payload.append(_compact_candidate_record(raw, include_visual_assets=include_visual_assets))
        if len(payload) >= max_unique:
            break
    return payload


def _support_type(value: Any, default: str = "direct_support") -> str:
    text = str(value or "").strip()
    return text if text in SUPPORT_TYPE_LABELS else default


def _support_type_label(value: Any) -> str:
    return SUPPORT_TYPE_LABELS.get(_support_type(value), SUPPORT_TYPE_LABELS["direct_support"])


def _dominant_support_type(selected: list[str], support_map: dict[str, str]) -> str:
    for support_type in ("direct_support", "general_principle_support", "transferable_support", "inverse_process_support"):
        if any(support_map.get(evidence_id) == support_type for evidence_id in selected):
            return support_type
    return "direct_support" if selected else "background_only"


def _non_direct_evidence_ids(selected: list[str], support_map: dict[str, str]) -> list[str]:
    return [evidence_id for evidence_id in selected if support_map.get(evidence_id) in NON_DIRECT_SUPPORT_TYPES]


def selected_evidence_ids(selection: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for point in selection.get("knowledge_points", []):
        if not isinstance(point, dict):
            continue
        for evidence_id in _strings(point.get("selected_evidence_ids")):
            if evidence_id not in out:
                out.append(evidence_id)
    return out


def filter_candidates_by_selection(candidates: list[EvidenceCandidate], selections_by_qid: dict[str, dict[str, Any]]) -> list[EvidenceCandidate]:
    by_id = _candidate_dict(candidates)
    out: list[EvidenceCandidate] = []
    seen: set[str] = set()
    for selection in selections_by_qid.values():
        for evidence_id in selected_evidence_ids(selection):
            if evidence_id in by_id and evidence_id not in seen:
                out.append(by_id[evidence_id])
                seen.add(evidence_id)
    return out


def load_confirmed_candidates(path: Path) -> list[EvidenceCandidate]:
    """Load the durable post-selection evidence snapshot for checkpoint reuse.

    Expanded-retrieval evidence IDs do not exist in the initial candidate CSV.
    Rebuilding only the initial candidates during a retry therefore changed the
    answer-generation evidence surface even though the confirmed selection was
    being reused.  The confirmed CSV is the transaction output of selection and
    is the authoritative downstream checkpoint.
    """

    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    candidates: list[EvidenceCandidate] = []
    for row in rows:
        values = dict(row)
        try:
            values["score"] = float(values.get("score") or 0)
        except (TypeError, ValueError):
            values["score"] = 0.0
        values["verified_page"] = str(values.get("verified_page") or "").strip().lower() == "true"
        candidates.append(EvidenceCandidate(**{field: values.get(field, "") for field in EvidenceCandidate.__dataclass_fields__}))
    return candidates


def _format_pages(pages: list[str]) -> str:
    clean_pages = list(dict.fromkeys(str(page).strip() for page in pages if str(page).strip()))
    if not clean_pages:
        return ""
    try:
        nums = [int(page) for page in clean_pages]
    except ValueError:
        return "、p".join(clean_pages)
    if len(nums) > 1 and nums == list(range(nums[0], nums[-1] + 1)):
        return f"{nums[0]}-p{nums[-1]}"
    return "、p".join(str(num) for num in nums)


def _citation_textbook_label(candidate: EvidenceCandidate) -> str:
    name = str(candidate.citation_textbook or candidate.textbook or "教材").strip()
    name = name.replace("_", "").replace(" ", "")
    name = re.sub(r"\.json$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\d+$", "", name)
    name = re.sub(r"第\d+版[上下]?$", "", name)
    if not name:
        name = "教材"
    if not name.endswith("教材"):
        name = f"{name}教材"
    return name


def _citation_locations(candidates: list[EvidenceCandidate]) -> str:
    pages: list[str] = []
    for candidate in candidates:
        page = str(candidate.printed_page or candidate.pdf_page_idx or "").strip()
        if page and page not in pages:
            pages.append(page)
    page_text = _format_pages(pages)
    return f"课本-p{page_text}" if page_text else "课本"


def citation_groups_from_selection(selection: dict[str, Any], candidates: list[EvidenceCandidate]) -> list[dict[str, Any]]:
    by_id = _candidate_dict(candidates)
    groups: list[dict[str, Any]] = []
    for point in selection.get("knowledge_points", []):
        if not isinstance(point, dict):
            continue
        knowledge_point = str(point.get("knowledge_point") or "考查点").strip()
        ids = [evidence_id for evidence_id in _strings(point.get("selected_evidence_ids")) if evidence_id in by_id]
        if not ids:
            groups.append(
                {
                    "knowledge_point": knowledge_point,
                    "selected_evidence_ids": [],
                    "pages": [],
                    "citation": f"{knowledge_point}：未确认到可用教材依据",
                    "reason": str(point.get("no_suitable_evidence_reason") or point.get("reason") or "").strip(),
                    "support_type": "background_only",
                    "support_type_label": SUPPORT_TYPE_LABELS["background_only"],
                    "evidence_support_types": {},
                    "non_direct_evidence": False,
                    "non_direct_evidence_ids": [],
                }
            )
            continue
        selected = [by_id[evidence_id] for evidence_id in ids]
        pages = list(dict.fromkeys(str(candidate.printed_page or candidate.pdf_page_idx or "").strip() for candidate in selected if str(candidate.printed_page or candidate.pdf_page_idx or "").strip()))
        location = _citation_locations(selected) or "未标页码"
        raw_support_map = point.get("evidence_support_types") if isinstance(point.get("evidence_support_types"), dict) else {}
        support_map = {
            evidence_id: _support_type(raw_support_map.get(evidence_id) or point.get("support_type"))
            for evidence_id in ids
        }
        support_type = _dominant_support_type(ids, support_map)
        non_direct_ids = _non_direct_evidence_ids(ids, support_map)
        groups.append(
            {
                "knowledge_point": knowledge_point,
                "selected_evidence_ids": ids,
                "pages": pages,
                "citation": f"{knowledge_point}：{location}",
                "reason": str(point.get("reason") or "").strip(),
                "support_type": support_type,
                "support_type_label": _support_type_label(support_type),
                "evidence_support_types": support_map,
                "non_direct_evidence": bool(non_direct_ids),
                "non_direct_evidence_ids": non_direct_ids,
            }
        )
    return groups


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _candidate_visual_parts(candidates: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        source_type = str(raw.get("source_type") or "").strip()
        if source_type not in {"figure_block", "table_block"}:
            continue
        path_text = str(raw.get("asset_path") or "").strip()
        if not path_text or path_text in seen:
            continue
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            continue
        seen.add(path_text)
        label = {
            "evidence_id": raw.get("evidence_id"),
            "source_type": source_type,
            "caption": raw.get("caption"),
            "printed_page": raw.get("printed_page"),
        }
        parts.append({"type": "text", "text": "教材候选图表：" + json.dumps(label, ensure_ascii=False)})
        parts.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
        if len(seen) >= limit:
            break
    return parts


def _selection_prompt(
    question: dict[str, Any],
    knowledge_plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    expanded: bool = False,
    include_visual_assets: bool = False,
) -> list[dict[str, Any]]:
    payload = {
        "task": "confirm_textbook_evidence_for_question",
        "expanded_candidate_pool": expanded,
        "hard_rules": [
            "Only return one valid JSON object.",
            "Do not output Markdown fences or any text outside JSON.",
            "Do not answer the question.",
            "Confirm evidence by each knowledge point, not by the whole question only.",
            "A question may have multiple knowledge points; every knowledge point must appear in output.",
            "Select all evidence_ids needed for the knowledge point, including cross-page evidence.",
            "If question.question_understanding is present, use it as the normalized visual/table surface of the question.",
            "For image/table questions, judge candidate evidence against question_understanding OCR, labels, axes, legends, table_rows, visual_notes, and answer_relevant_observations.",
            "Do not reject a candidate only because the key tested information appears in question_understanding rather than in the original stem text.",
            "Candidate source_type may be text_block, figure_block, table_block, or equation_block.",
            "For figure_block candidates, use caption, OCR text, surrounding_text_preview, and attached image if available.",
            "For table_block candidates, use table_html/text, caption, surrounding_text_preview, and attached table image if available.",
            "If no image is attached and candidate.text_model_can_read_visual is false, do not select that candidate as direct_support based on the unseen image; select it only if caption/surrounding text independently supports the knowledge point, otherwise reject it or set needs_expansion.",
            "candidate.asset_available only means the program has the image file; a text-only model cannot read it unless visual_summary, ocr_text, table_rows, table_html, or caption is present.",
            "Do not rely on generated summaries for tables; use the table content itself.",
            "Reject candidates that are only keyword-similar but do not support the tested content.",
            "Evidence is acceptable when its content can support solving the question; it does not need to exactly match the question keywords.",
            "General principles, transferable mechanisms, and inverse/reverse process descriptions are acceptable when they can support the tested content.",
            "Do not reject evidence only because it is broader than the question, uses a different material/example, or describes the reverse process.",
            "Reject background-only or keyword-only candidates that cannot support reasoning or solution.",
            "Classify selected evidence with support_type: direct_support, general_principle_support, transferable_support, or inverse_process_support.",
            "Use background_only or keyword_only only for non-acceptable candidates, not as selected evidence.",
            "If at least one acceptable evidence exists for a knowledge point, select it and set needs_expansion false.",
            "Set needs_expansion true only when no acceptable candidate supports that knowledge point.",
        ],
        "support_type_guide": {
            "direct_support": "教材内容直接覆盖题目考查对象、过程、公式或判断依据。",
            "general_principle_support": "教材给出通用原理或共性机制，可用于解决题目中的具体对象。",
            "transferable_support": "教材给出相近材料、相近体系或同类机制，可迁移到题目情境。",
            "inverse_process_support": "教材描述 A 到 B，题目考查 B 到 A，或相反方向，但机理可反向使用。",
            "background_only": "仅提供背景介绍，不能支撑解题。",
            "keyword_only": "只有关键词相似，实质内容不能支撑解题。",
        },
        "output_schema_example": {
            "question_id": question.get("question_id", ""),
            "knowledge_points": [
                {
                    "knowledge_point": "考查点名称",
                    "selected_evidence_ids": ["ev_xxx"],
                    "rejected_evidence_ids": ["ev_yyy"],
                    "support_type": "general_principle_support",
                    "evidence_support_types": {"ev_xxx": "general_principle_support"},
                    "non_direct_evidence_ids": ["ev_xxx"],
                    "reason": "为什么这些证据支撑该考查点",
                    "no_suitable_evidence_reason": "",
                    "needs_expansion": False,
                }
            ],
        },
        "question": question,
        "knowledge_plan": knowledge_plan,
        "candidate_evidence": candidates,
    }
    user_content: Any
    visual_parts = _candidate_visual_parts(candidates) if include_visual_assets else []
    if visual_parts:
        user_content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}, *visual_parts]
    else:
        user_content = json.dumps(payload, ensure_ascii=False)
    return [
        {"role": "system", "content": "你是真题解析平台的教材证据确认器。你只判断候选教材依据是否支撑考查点，并输出一个合法 JSON object。不要输出 Markdown 或 JSON 之外的文字。"},
        {"role": "user", "content": user_content},
    ]


def _message_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("text") or "")
    return ""


def _compact_selection_prompt_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "user":
            compacted.append(message)
            continue
        try:
            payload = json.loads(_message_text_content(message.get("content")) or "{}")
        except json.JSONDecodeError:
            compacted.append(message)
            continue
        compact_candidates = []
        for raw in payload.get("candidate_evidence", []):
            if not isinstance(raw, dict):
                continue
            evidence_text = clean_text(raw.get("evidence_text") or raw.get("text") or "")
            compact_candidates.append(
                {
                    "evidence_id": raw.get("evidence_id"),
                    "knowledge_point": raw.get("knowledge_point"),
                    "citation_textbook": raw.get("citation_textbook") or raw.get("textbook"),
                    "chapter_section": raw.get("chapter_section") or raw.get("chapter"),
                    "printed_page": raw.get("printed_page") or raw.get("page_start"),
                    "score": raw.get("score"),
                    "source_type": raw.get("source_type"),
                    "caption": raw.get("caption"),
                    "surrounding_text_preview": clean_text(raw.get("surrounding_text_preview") or "")[:220],
                    "table_html": clean_text(raw.get("table_html") or "")[:420],
                    "visual_summary": clean_text(raw.get("visual_summary") or "")[:420],
                    "visual_status": raw.get("visual_status"),
                    "text_model_can_read_visual": raw.get("text_model_can_read_visual"),
                    "visual_warning": clean_text(raw.get("visual_warning") or "")[:220],
                    "asset_available": raw.get("asset_available"),
                    "evidence_text": evidence_text[:420],
                }
            )
        payload["candidate_evidence"] = compact_candidates
        payload["compact_prompt"] = True
        compacted.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
    return compacted


def _normalize_selection(question: dict[str, Any], plan: dict[str, Any], data: dict[str, Any], candidates: list[EvidenceCandidate]) -> dict[str, Any]:
    available_ids = {candidate.evidence_id for candidate in candidates}
    qid = str(question.get("question_id", "")).strip()
    points = data.get("knowledge_points")
    if not isinstance(points, list) or not points:
        points = [{"knowledge_point": point} for point in _strings(plan.get("knowledge_points"))]
    normalized_points: list[dict[str, Any]] = []
    for raw in points:
        if not isinstance(raw, dict):
            continue
        point_support_type = _support_type(raw.get("support_type"))
        raw_support_map = raw.get("evidence_support_types") if isinstance(raw.get("evidence_support_types"), dict) else {}
        raw_selected = [evidence_id for evidence_id in _strings(raw.get("selected_evidence_ids")) if evidence_id in available_ids]
        selected: list[str] = []
        rejected_from_support: list[str] = []
        support_map: dict[str, str] = {}
        for evidence_id in raw_selected:
            support_type = _support_type(raw_support_map.get(evidence_id) or point_support_type)
            if support_type in ACCEPTABLE_SUPPORT_TYPES:
                selected.append(evidence_id)
                support_map[evidence_id] = support_type
            else:
                rejected_from_support.append(evidence_id)
        rejected = [
            evidence_id
            for evidence_id in [*_strings(raw.get("rejected_evidence_ids")), *rejected_from_support]
            if evidence_id in available_ids and evidence_id not in selected
        ]
        rejected = list(dict.fromkeys(rejected))
        dominant_support = _dominant_support_type(selected, support_map)
        non_direct_ids = _non_direct_evidence_ids(selected, support_map)
        normalized_points.append(
            {
                "knowledge_point": clean_text(raw.get("knowledge_point") or "考查点"),
                "selected_evidence_ids": selected,
                "rejected_evidence_ids": rejected,
                "support_type": dominant_support,
                "support_type_label": _support_type_label(dominant_support),
                "evidence_support_types": support_map,
                "non_direct_evidence_ids": non_direct_ids,
                "non_direct_evidence": bool(non_direct_ids),
                "reason": clean_text(raw.get("reason") or ""),
                "no_suitable_evidence_reason": clean_text(raw.get("no_suitable_evidence_reason") or ""),
                "needs_expansion": not selected and bool(candidates),
                # Lack of a suitable textbook passage is a delivery annotation,
                # not a user-review gate. The answer and final trace keep this
                # explicit so it cannot be mistaken for direct evidence.
                "evidence_status": (
                    "unavailable" if not selected else "non_direct" if non_direct_ids else "confirmed"
                ),
            }
        )
    return {
        "question_id": qid,
        "knowledge_points": normalized_points,
        "citation_groups": citation_groups_from_selection({"knowledge_points": normalized_points}, candidates),
    }


def _program_selection(question: dict[str, Any], plan: dict[str, Any], candidates: list[EvidenceCandidate], reason: str = "") -> dict[str, Any]:
    """Preserve ranked candidates without treating an unconfirmed fallback as proof."""

    points = _strings(plan.get("knowledge_points")) or ["考查点"]
    candidate_evidence_ids = [str(candidate.evidence_id or "").strip() for candidate in candidates if str(candidate.evidence_id or "").strip()]
    confirmation_incomplete = bool(candidate_evidence_ids)
    confirmation_reason = clean_text(reason) or (
        "未完成教材证据确认；已保留检索候选顺序供复核，不能将候选直接作为正式教材依据。"
        if confirmation_incomplete
        else "未检索到候选教材依据。"
    )
    normalized_points: list[dict[str, Any]] = []
    for point in points:
        normalized_points.append(
            {
                "knowledge_point": point,
                "selected_evidence_ids": [],
                "rejected_evidence_ids": [],
                # Candidates remain reviewable in the original retrieval
                # order, but an exception or skipped model confirmation must
                # never silently create a direct/confirmed textbook citation.
                "candidate_evidence_ids": list(candidate_evidence_ids),
                "support_type": "background_only",
                "support_type_label": SUPPORT_TYPE_LABELS["background_only"],
                "evidence_support_types": {},
                "non_direct_evidence_ids": [],
                "non_direct_evidence": False,
                "reason": confirmation_reason,
                "no_suitable_evidence_reason": confirmation_reason,
                "needs_expansion": not candidate_evidence_ids,
                "evidence_status": "unavailable",
                "confirmation_status": "unconfirmed" if confirmation_incomplete else "unavailable",
            }
        )
    return {
        "question_id": str(question.get("question_id", "")),
        "knowledge_points": normalized_points,
        "citation_groups": citation_groups_from_selection({"knowledge_points": normalized_points}, candidates),
    }


def selection_needs_expansion(selection: dict[str, Any]) -> bool:
    for point in selection.get("knowledge_points", []):
        if isinstance(point, dict) and point.get("confirmation_status") == "unconfirmed":
            continue
        if isinstance(point, dict) and (point.get("needs_expansion") or not point.get("selected_evidence_ids")):
            return True
    return False


def unresolved_knowledge_points(selection: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for point in selection.get("knowledge_points", []):
        if not isinstance(point, dict):
            continue
        if point.get("confirmation_status") == "unconfirmed":
            continue
        knowledge_point = clean_text(point.get("knowledge_point") or "")
        if knowledge_point and (point.get("needs_expansion") or not point.get("selected_evidence_ids")):
            out.append(knowledge_point)
    return out


def _plan_for_points(plan: dict[str, Any], points: list[str]) -> dict[str, Any]:
    scoped = dict(plan)
    scoped["knowledge_points"] = points
    return scoped


def _formula_candidates_for_point(plan: dict[str, Any], point: str, candidates: list[EvidenceCandidate]) -> list[EvidenceCandidate]:
    """Return only exact/equivalent formula evidence for a targeted recheck."""
    query = " ".join([point, *[str(x) for x in plan.get("search_queries", []) or []]])
    planned_formulas = planned_formulas_for_query(query, plan)
    matches: list[EvidenceCandidate] = []
    for candidate in candidates:
        if str(candidate.source_type).lower() != "equation_block":
            continue
        if any(
            formula_match_score(formula, candidate.evidence_text, context=query) > 0
            for formula in planned_formulas
        ):
            matches.append(candidate)
    matches.sort(key=lambda candidate: (-candidate.score, candidate.evidence_id))
    return matches[:3]


def _apply_formula_evidence_guard(selection: dict[str, Any], plan: dict[str, Any], candidates: list[EvidenceCandidate]) -> tuple[dict[str, Any], list[str]]:
    """Use an exact algebraic match only as a final deterministic safety net."""
    guarded = dict(selection)
    guarded_points: list[dict[str, Any]] = []
    repaired_points: list[str] = []
    for raw in selection.get("knowledge_points", []):
        point = dict(raw) if isinstance(raw, dict) else raw
        if (
            not isinstance(point, dict)
            or point.get("selected_evidence_ids")
            or point.get("confirmation_status") == "unconfirmed"
        ):
            guarded_points.append(point)
            continue
        name = clean_text(point.get("knowledge_point") or "")
        matches = _formula_candidates_for_point(plan, name, candidates)
        if not matches:
            guarded_points.append(point)
            continue
        selected = [candidate.evidence_id for candidate in matches]
        point.update(
            {
                "selected_evidence_ids": selected,
                "rejected_evidence_ids": [evidence_id for evidence_id in point.get("rejected_evidence_ids", []) if evidence_id not in selected],
                "support_type": "direct_support",
                "support_type_label": SUPPORT_TYPE_LABELS["direct_support"],
                "evidence_support_types": {evidence_id: "direct_support" for evidence_id in selected},
                "non_direct_evidence_ids": [],
                "non_direct_evidence": False,
                "reason": "程序复核：教材公式与知识计划公式经规范化后完全等价，作为直接教材证据保留。",
                "no_suitable_evidence_reason": "",
                "needs_expansion": False,
            }
        )
        guarded_points.append(point)
        repaired_points.append(name)
    guarded["knowledge_points"] = guarded_points
    return guarded, repaired_points


def _merge_selection(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    patch_points = {
        clean_text(point.get("knowledge_point") or ""): point
        for point in patch.get("knowledge_points", [])
        if isinstance(point, dict) and clean_text(point.get("knowledge_point") or "")
    }
    merged_points: list[dict[str, Any]] = []
    seen: set[str] = set()
    for point in base.get("knowledge_points", []):
        if not isinstance(point, dict):
            continue
        name = clean_text(point.get("knowledge_point") or "")
        merged_points.append(patch_points.get(name, point))
        seen.add(name)
    for name, point in patch_points.items():
        if name not in seen:
            merged_points.append(point)
    merged = dict(base)
    merged["knowledge_points"] = merged_points
    merged["_meta"] = {
        **(base.get("_meta") or {}),
        "expansion_patch_meta": patch.get("_meta") or {},
    }
    return merged


def _selection_trace_copy(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": selection.get("question_id", ""),
        "knowledge_points": [
            {
                "knowledge_point": point.get("knowledge_point", ""),
                "selected_evidence_ids": list(point.get("selected_evidence_ids", []) or []),
                "rejected_evidence_ids": list(point.get("rejected_evidence_ids", []) or []),
                "candidate_evidence_ids": list(point.get("candidate_evidence_ids", []) or []),
                "support_type": point.get("support_type", ""),
                "support_type_label": point.get("support_type_label", ""),
                "evidence_support_types": dict(point.get("evidence_support_types", {}) or {}),
                "non_direct_evidence_ids": list(point.get("non_direct_evidence_ids", []) or []),
                "non_direct_evidence": bool(point.get("non_direct_evidence")),
                "reason": point.get("reason", ""),
                "no_suitable_evidence_reason": point.get("no_suitable_evidence_reason", ""),
                "needs_expansion": bool(point.get("needs_expansion")),
                "evidence_status": point.get("evidence_status", ""),
                "confirmation_status": point.get("confirmation_status", ""),
            }
            for point in selection.get("knowledge_points", [])
            if isinstance(point, dict)
        ],
        "citation_groups": selection.get("citation_groups", []),
        "_meta": selection.get("_meta", {}),
    }


def _select_one(
    client: OpenAICompatibleClient | None,
    provider: ProviderConfig,
    model: str,
    question: dict[str, Any],
    plan: dict[str, Any],
    candidates: list[EvidenceCandidate],
    expanded: bool = False,
) -> dict[str, Any]:
    if client is None:
        selection = _program_selection(
            question,
            plan,
            candidates,
            "未调用模型，教材证据确认未完成；候选仅供复核，未作为正式教材依据。",
        )
        selection["_meta"] = {
            "provider": provider.name,
            "model": model,
            "fallback": True,
            "confirmation_status": "unconfirmed",
            "model_confirmation_attempted": False,
            "expanded_candidate_pool": expanded,
        }
        return selection
    try:
        fallback_model = next((item for item in provider.model_options if item != model), None)
        include_visual_assets = provider_model_supports_vision(provider, model)
        candidate_payload = _candidate_payload(candidates, include_visual_assets=include_visual_assets)
        messages = _selection_prompt(
            question,
            plan,
            candidate_payload,
            expanded=expanded,
            include_visual_assets=include_visual_assets,
        )
        if include_visual_assets and needs_vision_model(question):
            messages = attach_question_visuals(messages, question)
        with prompt_contract("exam.evidence_selection"):
            data = client.chat_json_object(
                messages,
                model=model,
                max_tokens=EVIDENCE_SELECTION_MAX_TOKENS,
                fallback_model=fallback_model,
                compact_messages=_compact_selection_prompt_messages,
                thinking="disabled",
                timeout=EVIDENCE_SELECTION_TIMEOUT_SECONDS,
                task_stage="evidence_selection",
                item_ids=[str(question.get("question_id") or question.get("number") or "")],
                enforce_context_budget=True,
            )
        selection = _normalize_selection(question, plan, data, candidates)
        selection["_meta"] = {
            "provider": provider.name,
            "model": model,
            "expanded_candidate_pool": expanded,
            "multimodal_evidence_confirmation": include_visual_assets,
            "llm_retry": getattr(client, "last_json_retry_report", {}),
        }
        return selection
    except (LLMError, Exception) as exc:
        selection = _program_selection(
            question,
            plan,
            candidates,
            f"模型教材引用确认失败；候选仅供复核，未作为正式教材依据：{exc}",
        )
        selection["_meta"] = {
            "provider": provider.name,
            "model": model,
            "fallback": True,
            "confirmation_status": "unconfirmed",
            "model_confirmation_attempted": True,
            "expanded_candidate_pool": expanded,
            "multimodal_evidence_confirmation": provider_model_supports_vision(provider, model),
            "llm_retry": getattr(client, "last_json_retry_report", {}),
        }
        return selection


def _write_progress(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_candidates_csv(path: Path, candidates: list[EvidenceCandidate]) -> None:
    fields = list(asdict(candidates[0]).keys()) if candidates else [
        "evidence_id",
        "question_id",
        "textbook",
        "citation_textbook",
        "chapter_section",
        "source_file",
        "pdf_page_idx",
        "printed_page",
        "score",
        "evidence_text",
        "verified_page",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([asdict(candidate) for candidate in candidates])


def confirm_evidence_selection(
    structured_exam: dict[str, Any],
    knowledge_plans: dict[str, dict[str, Any]],
    initial_candidates: list[EvidenceCandidate],
    provider: ProviderConfig,
    model: str,
    output_json: Path,
    blocks_csv: Path,
    page_map_csv: Path,
    progress_json: Path | None = None,
    use_model: bool = True,
    visual_provider: ProviderConfig | None = None,
    visual_model: str = "",
) -> tuple[EvidenceSelectionResult, list[EvidenceCandidate]]:
    questions = list(structured_exam.get("items", []))
    selections_by_qid: dict[str, dict[str, Any]] = {}
    expanded_question_ids: list[str] = []
    expanded_points_by_qid: dict[str, list[str]] = {}
    all_candidates_by_id = _candidate_dict(initial_candidates)
    max_workers = evidence_selection_worker_count()
    parallel_enabled = max_workers > 1 and len(questions) > 1

    def progress_payload(
        *,
        status: str,
        mode: str,
        completed: int,
        current_question_id: str = "",
        expansion_total: int = 0,
        expansion_completed: int = 0,
    ) -> dict[str, Any]:
        return {
            "stage": "evidence_selection",
            "status": status,
            "mode": mode,
            "total": len(questions),
            "completed": completed,
            "current_question_id": current_question_id,
            "expansion_total": expansion_total,
            "expansion_completed": expansion_completed,
            "max_workers": max_workers,
            "parallel_enabled": parallel_enabled,
        }

    _write_progress(
        progress_json,
        progress_payload(status="running", mode="selection", completed=0),
    )
    completed_selections = 0

    def runtime_for_question(question: dict[str, Any]) -> tuple[ProviderConfig, str]:
        if (
            needs_vision_model(question)
            and not provider_model_supports_vision(provider, model)
            and visual_provider is not None
            and provider_model_supports_vision(visual_provider, visual_model)
        ):
            return visual_provider, visual_model
        return provider, model

    def select_question(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        _, question = item
        qid = str(question.get("question_id", "")).strip()
        plan = knowledge_plans.get(qid, {"question_id": qid, "knowledge_points": ["考查点"]})
        q_candidates = candidates_for_question(initial_candidates, qid, limit=8)
        active_provider, active_model = runtime_for_question(question)
        client = OpenAICompatibleClient(active_provider) if use_model and active_provider.api_key else None
        selection = _select_one(client, active_provider, active_model, question, plan, [EvidenceCandidate(**row) for row in q_candidates], expanded=False)
        selection["_trace"] = {
            "first_selection": _selection_trace_copy(selection),
            "initial_candidate_count": sum(1 for candidate in initial_candidates if candidate.question_id == qid),
            "initial_candidate_ids": [candidate.evidence_id for candidate in initial_candidates if candidate.question_id == qid],
        }
        return {"qid": qid, "selection": selection, "unresolved_points": unresolved_knowledge_points(selection)}

    def on_selection_complete(_index: int, _item: tuple[int, dict[str, Any]], item_result: dict[str, Any]) -> None:
        nonlocal completed_selections
        completed_selections += 1
        _write_progress(
            progress_json,
            progress_payload(
                status="running",
                mode="selection",
                completed=completed_selections,
                current_question_id=str(item_result.get("qid") or ""),
            ),
        )

    selection_results = run_limited_concurrent(
        list(enumerate(questions, start=1)),
        select_question,
        max_workers=max_workers,
        on_complete=on_selection_complete,
    )

    for item_result in selection_results:
        qid = str(item_result.get("qid") or "")
        selection = item_result["selection"]
        selections_by_qid[qid] = selection
        unresolved_points = list(item_result.get("unresolved_points") or [])
        if unresolved_points:
            expanded_question_ids.append(qid)
            expanded_points_by_qid[qid] = unresolved_points

    if expanded_points_by_qid:
        expanded_csv = output_json.parent / "retrieval_candidates.expanded.csv"
        expanded_candidates = build_candidates(
            structured_exam,
            blocks_csv,
            page_map_csv,
            expanded_csv,
            top_k=15,
            knowledge_plans=knowledge_plans,
            target_points_by_qid=expanded_points_by_qid,
            id_offset_by_qid={
                qid: sum(1 for candidate in initial_candidates if candidate.question_id == qid)
                for qid in expanded_points_by_qid
            },
        )
        all_candidates_by_id.update(_candidate_dict(expanded_candidates))
        expansion_total = sum(len(points) for points in expanded_points_by_qid.values())
        expansion_completed = 0

        def select_expanded_question(item: tuple[int, str]) -> dict[str, Any]:
            _, qid = item
            question = next((item for item in questions if str(item.get("question_id", "")).strip() == qid), {})
            plan = _plan_for_points(knowledge_plans.get(qid, {"question_id": qid, "knowledge_points": ["考查点"]}), expanded_points_by_qid.get(qid, []))
            q_candidates = candidates_for_question(expanded_candidates, qid, limit=15)
            active_provider, active_model = runtime_for_question(question)
            client = OpenAICompatibleClient(active_provider) if use_model and active_provider.api_key else None
            selection = _select_one(client, active_provider, active_model, question, plan, [EvidenceCandidate(**row) for row in q_candidates], expanded=True)
            return {"qid": qid, "selection": selection}

        def on_expansion_complete(_index: int, _item: tuple[int, str], item_result: dict[str, Any]) -> None:
            nonlocal expansion_completed
            qid = str(item_result.get("qid") or "")
            expansion_completed += len(expanded_points_by_qid.get(qid, []))
            _write_progress(
                progress_json,
                progress_payload(
                    status="running",
                    mode="expansion",
                    completed=len(questions),
                    current_question_id=qid,
                    expansion_total=expansion_total,
                    expansion_completed=expansion_completed,
                ),
            )

        expansion_results = run_limited_concurrent(
            list(enumerate(expanded_question_ids, start=1)),
            select_expanded_question,
            max_workers=max_workers,
            on_complete=on_expansion_complete,
        )

        for item_result in expansion_results:
            qid = str(item_result.get("qid") or "")
            selection = item_result["selection"]
            base_selection = selections_by_qid.get(qid, {"question_id": qid, "knowledge_points": []})
            merged_selection = _merge_selection(base_selection, selection)
            first_trace = (base_selection.get("_trace") or {}).get("first_selection") if isinstance(base_selection.get("_trace"), dict) else None
            merged_selection["_trace"] = {
                **(base_selection.get("_trace") or {}),
                "first_selection": first_trace or _selection_trace_copy(base_selection),
                "expansion_selection": _selection_trace_copy(selection),
                "expanded_candidate_count": sum(1 for candidate in expanded_candidates if candidate.question_id == qid),
                "expanded_candidate_ids": [candidate.evidence_id for candidate in expanded_candidates if candidate.question_id == qid],
            }
            selections_by_qid[qid] = merged_selection

    # A formula can be present in the textbook but rejected if the model only
    # sees an isolated equation. Recheck that single knowledge point with only
    # algebraically equivalent formula candidates, never the whole task.
    for question in questions:
        qid = str(question.get("question_id") or "").strip()
        current = selections_by_qid.get(qid)
        plan = knowledge_plans.get(qid)
        if not current or not plan:
            continue
        unresolved = unresolved_knowledge_points(current)
        formula_points = [
            point
            for point in unresolved
            if _formula_candidates_for_point(plan, point, list(all_candidates_by_id.values()))
        ]
        if formula_points:
            scoped_plan = _plan_for_points(plan, formula_points)
            recheck_candidates: list[EvidenceCandidate] = []
            for point in formula_points:
                for candidate in _formula_candidates_for_point(plan, point, list(all_candidates_by_id.values())):
                    if candidate.evidence_id not in {item.evidence_id for item in recheck_candidates}:
                        recheck_candidates.append(candidate)
            concise_question = {
                key: question.get(key)
                for key in ("question_id", "section", "stem", "question_type", "requirements")
                if question.get(key) not in (None, "", [])
            }
            active_provider, active_model = runtime_for_question(question)
            client = OpenAICompatibleClient(active_provider) if use_model and active_provider.api_key else None
            recheck = _select_one(client, active_provider, active_model, concise_question, scoped_plan, recheck_candidates, expanded=True)
            current = _merge_selection(current, recheck)
            current.setdefault("_trace", {})["formula_only_recheck"] = _selection_trace_copy(recheck)

        guarded, repaired_points = _apply_formula_evidence_guard(current, plan, list(all_candidates_by_id.values()))
        if repaired_points:
            guarded.setdefault("_trace", {})["formula_equivalence_guard"] = {
                "applied_points": repaired_points,
                "reason": "模型复核后仍未选择与知识计划公式完全等价的教材公式。",
            }
        selections_by_qid[qid] = guarded

    all_candidates = list(all_candidates_by_id.values())
    for selection in selections_by_qid.values():
        selection["citation_groups"] = citation_groups_from_selection(selection, all_candidates)
    confirmed_candidates = filter_candidates_by_selection(all_candidates, selections_by_qid)
    confirmed_csv = output_json.parent / "confirmed_evidence_candidates.csv"
    _write_candidates_csv(confirmed_csv, confirmed_candidates)
    model_token_feedback = [
        {
            "question_id": qid,
            "stage": "evidence_selection",
            **meta.get("llm_retry", {}),
        }
        for qid, selection in selections_by_qid.items()
        if isinstance((meta := selection.get("_meta") or {}), dict) and meta.get("llm_retry")
    ]
    output = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "status": "passed",
        "provider": provider.name,
        "model": model,
        "selections": list(selections_by_qid.values()),
        "expanded_question_ids": expanded_question_ids,
        "expanded_points_by_qid": expanded_points_by_qid,
        "unresolved_evidence": [
            {
                "question_id": str(selection.get("question_id") or ""),
                "knowledge_points": [
                    str(point.get("knowledge_point") or "")
                    for point in selection.get("knowledge_points", [])
                    if isinstance(point, dict) and point.get("evidence_status") == "unavailable"
                ],
            }
            for selection in selections_by_qid.values()
            if any(isinstance(point, dict) and point.get("evidence_status") == "unavailable" for point in selection.get("knowledge_points", []))
        ],
        "confirmed_evidence_count": len(confirmed_candidates),
        "model_token_feedback": model_token_feedback,
        "concurrency": {
            "max_workers": max_workers,
            "parallel_enabled": parallel_enabled,
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_trace_csv = write_evidence_trace_csv(output_json.parent)
    _write_progress(
        progress_json,
        progress_payload(
            status="completed",
            mode="completed",
            completed=len(questions),
            expansion_total=sum(len(points) for points in expanded_points_by_qid.values()),
            expansion_completed=sum(len(points) for points in expanded_points_by_qid.values()),
        ),
    )
    result = EvidenceSelectionResult(
        ok=True,
        question_count=len(questions),
        selected_question_count=sum(1 for selection in selections_by_qid.values() if selected_evidence_ids(selection)),
        selected_evidence_count=len(confirmed_candidates),
        expansion_question_count=len(expanded_question_ids),
        output_json=str(output_json),
        confirmed_candidates_csv=str(confirmed_csv),
        evidence_trace_csv=str(evidence_trace_csv),
        max_workers=max_workers,
        parallel_enabled=parallel_enabled,
    )
    return result, confirmed_candidates
