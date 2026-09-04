from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .calculation_consistency import (
    calculation_contract_issues,
    calculation_draft_consistency_issues,
    reconcile_calculation_reference_structure,
)
from .capabilities.catalog import capability_policy_contributions
from .concurrency import run_limited_concurrent
from .document_presentation import is_synthetic_requirement_parent
from .drawing_code import question_drawing_mode
from .expression_promotion import promote_inline_mathematical_expressions, promote_inline_reactions
from .formula_audit import looks_like_formula
from .image_artifacts import ImageArtifactStore
from .image_orchestration import (
    DEFAULT_EDUCATIONAL_IMAGE_STYLE_RULE,
    ensure_generation_image_label_language_requirement,
)
from .llm_client import LLMError, OpenAICompatibleClient, StructuredOutputError
from .model_output_contracts import AnswerDraftBatchOutput, AnswerDraftOutput
from .model_tool_loop import ImageGenerationTool, ModelToolLoop, tool_loop_supported
from .omml_input import strip_structured_math_metadata
from .prompt_registry import prompt_contract
from .prompts import build_answer_depth_profile, build_answer_draft_prompt
from .provider_errors import classify_provider_error
from .question_requirements import answer_figure_required
from .question_types import (
    infer_question_type,
    is_calculation_question,
    is_term_explanation_question,
    iter_leaf_question_parts,
    question_has_type,
    question_kind,
)
from .question_understanding import attach_question_visuals, is_drawing_question, needs_vision_model
from .retrieval import EvidenceCandidate, candidates_for_question
from .runtime_monitor import model_call_context
from .settings import (
    DEFAULT_MODEL_MAX_TOKENS,
    STRUCTURED_ANSWER_MAX_TOKENS,
    ProviderConfig,
    provider_model_supports_vision,
    provider_supports_image_generation,
)
from .text_utils import cn_to_int
from .user_facing_text import strip_internal_repair_provenance
from .v4_schema import validate_v4_answer_fragment

ANSWER_GENERATION_TIMEOUT_SECONDS = 180
ANSWER_GENERATION_COMPLEX_TIMEOUT_SECONDS = 300
ANSWER_GENERATION_REASONING_TIMEOUT_SECONDS = 600
ANSWER_GENERATION_QUESTION_BUDGET_SECONDS = 360
ANSWER_SOURCE_CONTRACT_VERSION = "answer_book.answer_source_contract.v1"


def _clean_question_stem(question: dict[str, Any]) -> str:
    """Return visible question text without internal Word formula metadata."""

    return strip_structured_math_metadata(str(question.get("stem") or ""))


def _clean_question_source_markup(value: Any) -> Any:
    """Remove extraction-only math metadata before it can enter durable fragments."""

    if isinstance(value, str):
        return strip_structured_math_metadata(value)
    if isinstance(value, list):
        return [_clean_question_source_markup(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_question_source_markup(item) for key, item in value.items()}
    return value


def question_answer_source_fingerprint(question: dict[str, Any]) -> str:
    payload = json.dumps(question, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def answer_source_contract(structured_exam: dict[str, Any]) -> dict[str, Any]:
    questions = [item for item in structured_exam.get("items", []) or [] if isinstance(item, dict)]
    question_fingerprints = {
        str(question.get("question_id") or "").strip(): question_answer_source_fingerprint(question)
        for question in questions
        if str(question.get("question_id") or "").strip()
    }
    ordered = [
        {"question_id": str(question.get("question_id") or "").strip(), "fingerprint": question_answer_source_fingerprint(question)}
        for question in questions
    ]
    fingerprint = hashlib.sha256(
        json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "version": ANSWER_SOURCE_CONTRACT_VERSION,
        "fingerprint": fingerprint,
        "question_fingerprints": question_fingerprints,
    }


@dataclass
class GenerationResult:
    ok: bool
    question_count: int
    fragment_count: int
    issue_count: int
    output_json: str
    recovered_count: int = 0
    fallback_count: int = 0
    max_workers: int = 1
    parallel_enabled: bool = False
    reused_fragment_count: int = 0
    review_required: bool = False


def generation_completion_state(
    question_count: int,
    fragment_count: int,
    *,
    issue_count: int = 0,
    fallback_count: int = 0,
) -> dict[str, Any]:
    """Separate pipeline continuity from formal-delivery readiness."""

    coverage_complete = fragment_count == question_count
    review_required = bool(issue_count or fallback_count)
    return {
        "ok": coverage_complete,
        "coverage_complete": coverage_complete,
        "review_required": review_required,
        "delivery_readiness": "review_candidate" if review_required else "formal_candidate",
    }


def answer_generation_worker_count() -> int:
    raw = os.environ.get("ANSWER_GENERATION_MAX_WORKERS", "10")
    try:
        return max(1, min(12, int(raw)))
    except ValueError:
        return 10


def answer_generation_batch_enabled() -> bool:
    return str(os.environ.get("ANSWER_GENERATION_BATCH_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def answer_generation_batch_size() -> int:
    raw = os.environ.get("ANSWER_GENERATION_BATCH_SIZE", "3")
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return 3


def answer_generation_batch_token_budget() -> int:
    raw = os.environ.get("ANSWER_GENERATION_BATCH_TOKEN_BUDGET", "8000")
    try:
        return max(2000, int(raw))
    except ValueError:
        return 8000


def answer_generation_evidence_target_count() -> int:
    raw = os.environ.get("ANSWER_GENERATION_EVIDENCE_TARGET", "10")
    try:
        return max(4, min(16, int(raw)))
    except ValueError:
        return 10


def _bounded_env_int(name: str, default: int, *, minimum: int = 30, maximum: int = 1800) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return max(minimum, min(maximum, int(raw)))
    except ValueError:
        return default


def answer_generation_thinking_mode(provider: ProviderConfig) -> str:
    """Honor explicit Thinking choices and keep structured JSON safe on auto.

    The web task records the user's explicit choice in ``provider.thinking_mode``.
    Explicit effort levels remain a user-controlled latency/cost trade-off.
    Some reasoning providers use their entire output allowance on hidden
    reasoning under ``auto`` and never emit JSON, so automatic mode uses the
    configurable safe default instead of repeatedly exhausting the request.
    """

    mode = str(getattr(provider, "thinking_mode", "auto") or "auto").strip().lower()
    if mode == "auto":
        configured = str(os.environ.get("ANSWER_GENERATION_AUTO_THINKING_MODE", "disabled") or "disabled").strip().lower()
        return configured if configured in {"auto", "enabled", "disabled", "low", "medium", "high", "xhigh"} else "disabled"
    if mode in {"enabled", "disabled", "low", "medium", "high", "xhigh"}:
        return mode
    return "disabled"


def answer_generation_attempt_thinking_mode(
    provider: ProviderConfig,
    question: dict[str, Any] | None,
    attempt: int,
) -> str:
    """Keep every retry on the user-selected reasoning contract."""

    _ = question, attempt
    return answer_generation_thinking_mode(provider)


def answer_generation_timeout_seconds(
    question: dict[str, Any] | None = None,
    *,
    thinking_mode: str = "auto",
) -> int:
    """Return a configurable deadline sized for question and reasoning cost."""

    complex_question = bool(
        question
        and (
            question_has_type(question, "计算题")
            or question_has_type(question, "作图题")
            or is_drawing_question(question)
        )
    )
    if thinking_mode in {"enabled", "medium", "high", "xhigh"}:
        return _bounded_env_int(
            "ANSWER_GENERATION_REASONING_TIMEOUT_SECONDS",
            ANSWER_GENERATION_REASONING_TIMEOUT_SECONDS,
        )
    if complex_question:
        return _bounded_env_int(
            "ANSWER_GENERATION_COMPLEX_TIMEOUT_SECONDS",
            ANSWER_GENERATION_COMPLEX_TIMEOUT_SECONDS,
        )
    return _bounded_env_int("ANSWER_GENERATION_TIMEOUT_SECONDS", ANSWER_GENERATION_TIMEOUT_SECONDS)


def answer_generation_question_budget_seconds() -> int:
    """Bound all model switches and repair attempts for one question."""

    return _bounded_env_int(
        "ANSWER_GENERATION_QUESTION_BUDGET_SECONDS",
        ANSWER_GENERATION_QUESTION_BUDGET_SECONDS,
        minimum=60,
        maximum=900,
    )


def answer_generation_max_model_candidates() -> int:
    return _bounded_env_int(
        "ANSWER_GENERATION_MAX_MODEL_CANDIDATES",
        2,
        minimum=1,
        maximum=3,
    )


def structured_answer_max_tokens(provider: ProviderConfig, question: dict[str, Any] | None = None) -> int:
    """Bound answer output by the number of independently required units.

    A very large static ceiling made providers spend several minutes streaming
    or timing out even for short exams.  The v4 fragment schema is compact: a
    base budget plus a bounded allowance per leaf unit gives multipart problems
    enough room without turning every request into a 49k-token generation.
    """

    if question is None:
        return STRUCTURED_ANSWER_MAX_TOKENS
    leaf_count = max(1, len(iter_leaf_question_parts(question)))
    adaptive = 6144 + min(leaf_count, 10) * 1536
    return min(STRUCTURED_ANSWER_MAX_TOKENS, max(8192, adaptive))


def _rough_token_estimate(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # Conservative enough for Chinese-heavy prompts without requiring tokenizer deps.
    return max(1, (len(text) + 1) // 2)


def _format_elapsed(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _answer_batch_kind(question: dict[str, Any]) -> str:
    if question_has_type(question, "计算题") or question_has_type(question, "作图题"):
        return "complex"
    kind = question_kind(question)
    if kind == "choice":
        return "choice"
    if kind == "fill":
        return "fill"
    if kind == "judge":
        return "judge"
    return "general"


def _is_microbatch_candidate(question: dict[str, Any]) -> bool:
    if question.get("image_refs") or question.get("page_visual_refs") or needs_vision_model(question) or is_drawing_question(question):
        return False
    return _answer_batch_kind(question) in {"choice", "fill", "judge"}


def _answer_model_candidates_for_question(provider: ProviderConfig, requested_model: str, question: dict[str, Any]) -> list[str]:
    def unique(*groups: Any) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for group in groups for item in group if str(item).strip()))

    if needs_vision_model(question):
        understanding = question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
        if understanding.get("direct_multimodal"):
            # Direct delivery has no separate OCR/vision transcript to fall
            # back to. Every candidate must therefore be able to see the
            # original image itself.
            return [
                candidate
                for candidate in unique([requested_model], provider.model_options)
                if provider_model_supports_vision(provider, candidate)
            ]
        if understanding.get("vision_used"):
            return unique([requested_model], provider.model_options)
        vision_model = str(getattr(provider, "vision_model", "") or "").strip()
        if not getattr(provider, "supports_vision", False) or not vision_model:
            return []
        # The vision model sees the source artifact first; the user's selected
        # answer model remains a visible fallback and is never silently lost.
        return unique([vision_model, requested_model], provider.model_options)
    return unique([requested_model], provider.model_options)


def _equivalent_tool_loop_model_candidates(
    client: OpenAICompatibleClient,
    provider: ProviderConfig,
    candidates: list[str],
) -> list[str]:
    """Keep only model retries that preserve the native image-tool contract."""

    return [
        candidate
        for candidate in candidates
        if tool_loop_supported(client, provider, candidate)
    ]


def _batch_group_key(question: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(question.get("subject_index") or ""),
        str(question.get("major_number") or ""),
        str(question.get("section") or question.get("section_raw") or ""),
    )


def fallback_fragment(question: dict[str, Any], evidence: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    qid = str(question.get("question_id", ""))
    evidence_ids = [str(e.get("evidence_id")) for e in evidence[:1] if e.get("evidence_id")]
    return {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": qid,
        "section": question.get("section", ""),
        "number": question.get("number", ""),
        "display_number": question.get("display_number", question.get("number", "")),
        "question_id_base": question.get("question_id_base", ""),
        "question_id_occurrence": question.get("question_id_occurrence", 0),
        "question_id_collision_count": question.get("question_id_collision_count", 0),
        "answer": "待复核",
        "evidence_ids": evidence_ids,
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {
                        "type": "text",
                        "text": "本题尚未完成模型结构化判断，需要人工复核后生成正式解析。",
                    }
                ],
            }
        ],
        "formulas": [],
        "warnings": [f"{reason}；已进入存疑题目审查文档。"],
        "_review_flags": [
            {
                "code": "answer_generation_failed",
                "message": reason,
            }
        ],
    }


def _citation_textbook_label(row: dict[str, Any]) -> str:
    name = str(row.get("citation_textbook") or row.get("textbook") or "教材").strip()
    name = name.replace("_", "").replace(" ", "")
    name = re.sub(r"\.json$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\d+$", "", name)
    name = re.sub(r"第\d+版[上下]?$", "", name)
    if not name:
        name = "教材"
    if not name.endswith("教材"):
        name = f"{name}教材"
    return name


def _evidence_page(row: dict[str, Any]) -> str:
    return str(row.get("printed_page") or row.get("pdf_page_idx") or "").strip()


def evidence_citation_text(evidence: list[dict[str, Any]], evidence_ids: list[str]) -> str:
    by_id = {str(e.get("evidence_id")): e for e in evidence}
    rows: list[dict[str, Any]] = []
    for eid in evidence_ids:
        row = by_id.get(str(eid))
        if row:
            rows.append(row)
    citation = _citation_locations(rows)
    return citation if citation else "未检索到可验证教材依据"


def _format_selection_pages(pages: list[str]) -> str:
    pages = list(dict.fromkeys(str(page).strip() for page in pages if str(page).strip()))
    if not pages:
        return ""
    try:
        nums = [int(page) for page in pages]
    except ValueError:
        return "、p".join(pages)
    if len(nums) > 1 and nums == list(range(nums[0], nums[-1] + 1)):
        return f"{nums[0]}-p{nums[-1]}"
    return "、p".join(str(num) for num in nums)


def _citation_locations(rows: list[dict[str, Any]]) -> str:
    pages: list[str] = []
    for row in rows:
        page = _evidence_page(row)
        if page and page not in pages:
            pages.append(page)
    page_text = _format_selection_pages(pages)
    return f"课本-p{page_text}" if page_text else "课本"


def evidence_selection_citation_text(evidence: list[dict[str, Any]], evidence_selection: dict[str, Any] | None) -> str:
    return "".join(str(segment.get("text", "")) for segment in evidence_selection_citation_segments(evidence, evidence_selection))


def evidence_selection_citation_segments(evidence: list[dict[str, Any]], evidence_selection: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not evidence_selection:
        return []
    by_id = {str(row.get("evidence_id")): row for row in evidence}
    parts: list[dict[str, Any]] = []
    for point in evidence_selection.get("knowledge_points", []):
        if not isinstance(point, dict):
            continue
        name = str(point.get("knowledge_point") or "考查点").strip()
        ids = [str(x).strip() for x in point.get("selected_evidence_ids", []) if str(x).strip()]
        rows = [by_id[evidence_id] for evidence_id in ids if evidence_id in by_id]
        if not rows:
            reason = str(point.get("no_suitable_evidence_reason") or "").strip()
            parts.append(
                {
                    "type": "text",
                    "text": f"{name}：未确认到可用教材依据{f'（{reason}）' if reason else ''}",
                    "highlight": "unconfirmed_evidence",
                }
            )
            continue
        parts.append({"type": "text", "text": f"{name}：{_citation_locations(rows)}"})
    if not any(part.get("highlight") == "unconfirmed_evidence" for part in parts):
        joined = "；".join(str(part.get("text", "")) for part in parts if str(part.get("text", "")))
        return [{"type": "text", "text": joined}] if joined else []
    segments: list[dict[str, Any]] = []
    for part in parts:
        if segments:
            segments.append({"type": "text", "text": "；"})
        segments.append(part)
    return segments


def attach_program_evidence_block(fragment: dict[str, Any], evidence: list[dict[str, Any]], evidence_selection: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence_ids = [str(x) for x in fragment.get("evidence_ids", [])]
    citation_segments = evidence_selection_citation_segments(evidence, evidence_selection)
    if not citation_segments:
        citation_segments = [{"type": "text", "text": evidence_citation_text(evidence, evidence_ids)}]
    block = {
        "label": "教材依据",
        "segments": citation_segments,
    }
    blocks = list(fragment.get("blocks", []))
    blocks = [b for b in blocks if str(b.get("label", "")) != "教材依据"]
    blocks.insert(0, block)
    fragment["blocks"] = blocks
    return fragment


def selected_evidence_ids(evidence_selection: dict[str, Any] | None) -> list[str]:
    """Return the authoritative per-question evidence IDs in stable order."""

    selected: list[str] = []
    if not isinstance(evidence_selection, dict):
        return selected
    for point in evidence_selection.get("knowledge_points", []) or []:
        if not isinstance(point, dict):
            continue
        for raw in point.get("selected_evidence_ids", []) or []:
            evidence_id = str(raw or "").strip()
            if evidence_id and evidence_id not in selected:
                selected.append(evidence_id)
    return selected


def reconcile_confirmed_evidence_binding(
    fragment: dict[str, Any],
    evidence: list[dict[str, Any]],
    evidence_selection: dict[str, Any] | None,
) -> bool:
    """Synchronize a fragment with the latest confirmed evidence selection.

    Reused answer checkpoints and bounded model repairs may retain evidence IDs
    from an earlier retrieval run.  The selection result is authoritative for
    both IDs and the student-facing citation block; keeping only one of those
    in sync creates a deterministic late-stage gate failure.
    """

    if not isinstance(evidence_selection, dict):
        return False
    before = json.dumps(
        {
            "evidence_ids": fragment.get("evidence_ids", []),
            "evidence_block": [
                block
                for block in fragment.get("blocks", []) or []
                if isinstance(block, dict) and str(block.get("label") or "") == "教材依据"
            ],
            "binding": (fragment.get("_meta") or {}).get("evidence_binding")
            if isinstance(fragment.get("_meta"), dict)
            else None,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    confirmed_ids = selected_evidence_ids(evidence_selection)
    fragment["evidence_ids"] = confirmed_ids
    attach_program_evidence_block(fragment, evidence, evidence_selection)
    meta = dict(fragment.get("_meta") or {})
    meta["evidence_binding"] = {
        "strategy": "confirmed_selection_reconciliation",
        "reason": "按本次教材依据确认结果同步引用 ID 与正式教材依据块。",
        "bound_evidence_ids": confirmed_ids,
    }
    fragment["_meta"] = meta
    after = json.dumps(
        {
            "evidence_ids": fragment.get("evidence_ids", []),
            "evidence_block": [
                block
                for block in fragment.get("blocks", []) or []
                if isinstance(block, dict) and str(block.get("label") or "") == "教材依据"
            ],
            "binding": meta.get("evidence_binding"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return before != after


def include_confirmed_evidence_ids(fragment: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_ids = [str(x).strip() for x in fragment.get("evidence_ids", []) if str(x).strip()]
    for row in evidence:
        evidence_id = str(row.get("evidence_id") or "").strip()
        if evidence_id and evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)
    fragment["evidence_ids"] = evidence_ids
    return fragment


def _normalize_model_text_spacing(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"第\s*\n+\s*([（(]\s*[一二三四五六七八九十0-9]+\s*[）)]\s*问?)", r"第\1", value)
    value = re.sub(r"第\s*\n+\s*([一二三四五六七八九十0-9]+\s*(?:小问|步|问))", r"第\1", value)
    return value


def _text_segments(text: str) -> list[dict[str, str]]:
    cleaned = _normalize_model_text_spacing(str(text or "")).strip()
    return [{"type": "text", "text": cleaned}] if cleaned else []


PROGRAM_CITATION_LINE_RE = re.compile(r"^\s*(?:教材依据|参考教材|引用依据|证据依据)\s*[:：].*", re.MULTILINE)
PROGRAM_CITATION_INLINE_RE = re.compile(r"\s*(?:教材依据|参考教材|引用依据|证据依据)\s*[:：][^\n。]*?(?:\n|$)")


def _strip_program_citation_text(text: str) -> str:
    value = str(text or "")
    value = PROGRAM_CITATION_LINE_RE.sub("", value)
    value = PROGRAM_CITATION_INLINE_RE.sub("", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _question_ask_parts(stem: str) -> list[str]:
    text = re.sub(r"[（(]\s*\d+\s*分\s*[）)]", "", str(stem or ""))
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    normalized = re.sub(r"([。！？?；;])\s*([（(]\s*\d+\s*[）)])", r"\1 \2", text)
    parts = [part.strip(" ：:，,。！？?；;") for part in re.split(r"[？?；;]|\s*[（(]\s*\d+\s*[）)]\s*", normalized) if part.strip()]
    cleaned: list[str] = []
    for part in parts:
        item = re.sub(r"^(试)?(简述|说明|分析|比较|解释|计算|画出|绘制|写出|求|讨论)", "", part).strip(" ：:，,。！？?；;")
        if item:
            cleaned.append(item)
    return cleaned


def _insert_newline_before(text: str, start: int) -> str:
    if start <= 0:
        return text
    prefix = text[:start].rstrip()
    suffix = text[start:].lstrip()
    if prefix.endswith("\n"):
        return text
    return f"{prefix}\n{suffix}"


NUMBERED_PART_RE = re.compile(r"\s*([（(]\s*(?:[2-9]\d*|[二三四五六七八九十]+)\s*[）)])")
FIRST_LEVEL_SUBQUESTION_RE = re.compile(
    r"^\s*(?:[（(]\s*(?:[1-9]\d*|[一二三四五六七八九十]+)\s*[）)]|第\s*(?:[1-9]\d*|[一二三四五六七八九十]+)\s*(?:小问|问))"
)


def _insert_newlines_before_numbered_parts(text: str) -> str:
    value = str(text or "")

    def replace(match: re.Match[str]) -> str:
        prefix = value[: match.start()].rstrip()
        if prefix.endswith("第"):
            return match.group(1)
        return "\n" + match.group(1)

    return NUMBERED_PART_RE.sub(replace, value)


def _normalize_multipart_text_layout(text: str, stem: str) -> str:
    value = _normalize_model_text_spacing(str(text or "")).strip()
    if not value:
        return value
    if NUMBERED_PART_RE.search(value):
        return _insert_newlines_before_numbered_parts(value).strip()
    if "\n" in value:
        return value
    parts = _question_ask_parts(stem)
    if len(parts) < 2:
        return value
    for part in parts[1:]:
        candidates = [part]
        for suffix in ("防治措施", "驱动力", "形成原因", "产生原因"):
            if suffix in part:
                candidates.append(suffix)
        for candidate in candidates:
            candidate = candidate.strip()
            if len(candidate) < 4:
                continue
            idx = value.find(candidate)
            if idx > 0:
                return _insert_newline_before(value, idx)
        if "条件" in part:
            match = re.search(r"(?<=[。；;])\s*([^。\n；;]{0,24}条件(?:包括|为|是))", value)
            if match and match.start(1) > 0:
                return _insert_newline_before(value, match.start(1))
        if "驱动力" in part:
            match = re.search(r"(?<=[。；;])\s*([^。\n；;]{0,24}驱动力(?:是|为|来源于|来自))", value)
            if match and match.start(1) > 0:
                return _insert_newline_before(value, match.start(1))
        if "原因" in part:
            match = re.search(r"(?<=[。；;])\s*([^。\n；;]{0,24}原因(?:是|为|包括|在于))", value)
            if match and match.start(1) > 0:
                return _insert_newline_before(value, match.start(1))
    return value


def _normalize_formula_latex(text: str) -> str:
    from .expression_normalization import normalize_expression_latex

    latex = str(text or "").strip()
    previous = None
    while previous != latex:
        previous = latex
        latex = re.sub(r"\\\\(?=[A-Za-z])", r"\\", latex)
        latex = re.sub(r"\\\\(?=\s*\\[A-Za-z])", r"\\", latex)
    return normalize_expression_latex(latex)


def _draft_formulas(draft: dict[str, Any], qid: str) -> list[dict[str, Any]]:
    formulas: list[dict[str, Any]] = []
    for index, raw in enumerate(draft.get("formulas") or [], start=1):
        if not isinstance(raw, dict):
            continue
        latex = _normalize_formula_latex(raw.get("latex", ""))
        if not latex:
            continue
        formulas.append(
            {
                "formula_id": f"f_{qid}_{index:02d}",
                "latex": latex,
                "role": str(raw.get("role") or "relation"),
                "display": bool(raw.get("display", True)),
                "source_note": str(raw.get("meaning") or raw.get("source_note") or "模型解析草稿公式"),
            }
        )
    return formulas


def _draft_figure_specs(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect structured figures from the draft and every answer unit.

    A composite question owns the final answer fragment, but each drawing leaf
    owns its own figure.  Keeping only the draft-level array silently discarded
    valid figures produced inside ``answer_units`` and forced the runtime into a
    lower-quality image-model fallback.  The unit number is retained as stable
    routing metadata; it is not a discipline-specific assumption.
    """

    collected: list[dict[str, Any]] = []

    def append_specs(value: Any, unit_number: str = "") -> None:
        raw_specs = value
        if isinstance(raw_specs, dict):
            raw_specs = [raw_specs]
        if not isinstance(raw_specs, list):
            return
        for raw in raw_specs:
            if not isinstance(raw, dict):
                continue
            spec = dict(raw)
            if unit_number:
                spec.setdefault("answer_unit_number", unit_number)
            collected.append(spec)

    append_specs(draft.get("figure_specs") or draft.get("diagram_specs") or [])
    for unit in draft.get("answer_units") or []:
        if not isinstance(unit, dict):
            continue
        append_specs(
            unit.get("figure_specs") or unit.get("diagram_specs") or [],
            _normalize_subquestion_number(unit.get("number")),
        )
    # One confirmed drawing leaf owns one final figure.  Models often mirror
    # the same spec at question level and inside its answer unit; keeping both
    # doubled rendering, visual QA, and Word output.  Select the richer version
    # per unit/kind and retain genuinely separate figures.
    deduplicated: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}

    def richness(spec: dict[str, Any]) -> tuple[int, int]:
        structural = sum(
            len(spec.get(key) or []) if isinstance(spec.get(key), list) else int(bool(spec.get(key)))
            for key in ("required_labels", "features", "annotations", "points", "regions")
        )
        return structural, len(json.dumps(spec, ensure_ascii=False, sort_keys=True))

    exact_seen: set[str] = set()
    for spec in collected:
        unit_number = _normalize_subquestion_number(spec.get("answer_unit_number"))
        kind = str(spec.get("kind") or "").strip()
        if unit_number and kind:
            key = (unit_number, kind)
            if key in positions:
                index = positions[key]
                if richness(spec) > richness(deduplicated[index]):
                    deduplicated[index] = spec
                continue
            positions[key] = len(deduplicated)
            deduplicated.append(spec)
            continue
        signature = json.dumps(
            {key: value for key, value in spec.items() if key not in {"figure_id", "source"}},
            ensure_ascii=False,
            sort_keys=True,
        )
        if signature in exact_seen:
            continue
        exact_seen.add(signature)
        deduplicated.append(spec)
    return deduplicated


def _draft_drawing_code_specs(draft: dict[str, Any], qid: str) -> list[dict[str, Any]]:
    raw_specs: list[tuple[Any, str]] = [
        (draft.get("drawing_code_specs") or draft.get("drawing_codes") or draft.get("drawing_code") or [], "")
    ]
    for unit in draft.get("answer_units") or []:
        if not isinstance(unit, dict):
            continue
        raw_specs.append(
            (
                unit.get("drawing_code_specs") or unit.get("drawing_codes") or unit.get("drawing_code") or [],
                _normalize_subquestion_number(unit.get("number")),
            )
        )
    specs: list[dict[str, Any]] = []
    for value, unit_number in raw_specs:
        if isinstance(value, str):
            value = [{"code": value}]
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            continue
        for raw in value:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("code") or "").strip()
            if not code:
                continue
            spec = dict(raw)
            if unit_number:
                spec.setdefault("answer_unit_number", unit_number)
            index = len(specs) + 1
            spec.setdefault("question_id", qid)
            spec.setdefault("figure_id", f"{qid}_code_fig_{index:02d}")
            spec.setdefault("kind", "model_drawing_code")
            spec.setdefault("caption", "题目图示")
            specs.append(spec)
    return specs


def _append_formula_refs(segments: list[dict[str, Any]], formulas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not formulas:
        return segments
    formula_refs = [{"type": "formula_ref", "formula_id": formula["formula_id"]} for formula in formulas]
    if not segments:
        return formula_refs
    out: list[dict[str, Any]] = []
    inserted = False
    for segment in segments:
        out.append(segment)
        if not inserted and segment.get("type") == "text":
            out.extend(formula_refs)
            inserted = True
    if not inserted:
        out.extend(formula_refs)
    return out


_LIST_ITEM_TERMINATOR_RE = re.compile(r"[。；;]+$")


def _list_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    parts: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("explanation") or item.get("step") or "").strip()
        else:
            text = str(item).strip()
        if text:
            parts.append(text)
    return parts


def _join_sentence_list(value: Any, separator: str = "；") -> str:
    """Join model list items without producing ``。；`` at item boundaries.

    A plain string is already authored prose and keeps its original punctuation.
    Only program-combined list entries have their terminal Chinese sentence
    punctuation normalized.
    """
    if isinstance(value, str):
        return value.strip()
    items = _list_items(value)
    if not items:
        return ""
    append_stop = bool(_LIST_ITEM_TERMINATOR_RE.search(items[-1]))
    cleaned = [_LIST_ITEM_TERMINATOR_RE.sub("", item).strip() for item in items]
    cleaned = [item for item in cleaned if item]
    if not cleaned:
        return ""
    text = separator.join(cleaned)
    return f"{text}。" if append_stop else text


def _option_analysis_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for key in sorted(value):
        text = str(value.get(key) or "").strip()
        if text:
            parts.append(f"{key}：{text}")
    return _join_sentence_list(parts)


def _inline_formula_segments_from_text(text: str, formulas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
    return _segments_from_inline_formula_text(text, formula_ids)


def _list_text(value: Any) -> str:
    return _join_sentence_list(value)


def _warning_items(value: Any, formulas: list[dict[str, Any]]) -> list[str]:
    """Keep warnings as independent items instead of join-and-split text."""
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.split("；")]
    else:
        raw_items = _list_items(value)
    return [
        _replace_formula_placeholders_in_text(item, formulas)
        for item in raw_items
        if item
    ]


def _parse_formula_indices(value: Any) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    parsed: list[int] = []
    for raw in values:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if index > 0 and index not in parsed:
            parsed.append(index)
    return parsed


FORMULA_INDEX_KEYS = {"formula_indices", "relation_formula_indices", "substitution_formula_indices", "result_formula_indices"}


def _shift_positive_formula_indices(value: Any, offset: int) -> Any:
    if offset <= 0:
        return value
    if isinstance(value, list):
        return [_shift_positive_formula_indices(item, offset) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key in FORMULA_INDEX_KEYS:
            raw_values = item if isinstance(item, list) else [item]
            shifted: list[Any] = []
            for raw in raw_values:
                try:
                    index = int(raw)
                    shifted.append(index + offset if index > 0 else index)
                except (TypeError, ValueError):
                    shifted.append(raw)
            out[key] = shifted if isinstance(item, list) else (shifted[0] if shifted else item)
        else:
            out[key] = _shift_positive_formula_indices(item, offset)
    return out


def normalize_nested_calculation_payload(draft: dict[str, Any]) -> dict[str, Any]:
    """Hoist unit-local formulas/contracts into the canonical draft ledger.

    Models sometimes follow the visual nesting of ``answer_units`` and place a
    calculation unit's formulas and contract inside that unit.  The renderer and
    auditors intentionally have one question-level formula table, so normalize
    the equivalent shape once at the schema boundary and remap local indices.
    """

    if not isinstance(draft, dict):
        return draft
    normalized = copy.deepcopy(draft)
    global_formulas = [item for item in normalized.get("formulas", []) or [] if isinstance(item, dict)]
    contract = normalized.get("calculation_contract")
    merged_contract = copy.deepcopy(contract) if isinstance(contract, dict) else {}
    for key in ("requested_outputs", "result_quantities", "intermediate_quantities", "partitions", "transitions"):
        if not isinstance(merged_contract.get(key), list):
            merged_contract[key] = []

    units = normalized.get("answer_units") if isinstance(normalized.get("answer_units"), list) else []
    for index, raw_unit in enumerate(units):
        if not isinstance(raw_unit, dict):
            continue
        unit = copy.deepcopy(raw_unit)
        local_formulas = [item for item in unit.pop("formulas", []) or [] if isinstance(item, dict)]
        offset = len(global_formulas)
        if local_formulas:
            unit = _shift_positive_formula_indices(unit, offset)
            global_formulas.extend(local_formulas)
        local_contract = unit.pop("calculation_contract", None)
        if isinstance(local_contract, dict):
            local_contract = copy.deepcopy(local_contract)
            for quantity in local_contract.get("result_quantities", []) or []:
                if not isinstance(quantity, dict):
                    continue
                try:
                    formula_index = int(quantity.get("formula_index"))
                except (TypeError, ValueError):
                    continue
                if formula_index > 0:
                    quantity["formula_index"] = formula_index + offset
            for key in ("requested_outputs", "result_quantities", "intermediate_quantities", "partitions", "transitions"):
                for item in local_contract.get(key, []) or []:
                    if isinstance(item, dict) and item not in merged_contract[key]:
                        merged_contract[key].append(item)
        units[index] = unit

    normalized["answer_units"] = units
    normalized["formulas"] = global_formulas
    normalized["calculation_contract"] = merged_contract
    return normalized


def _collect_formula_reference_indices(value: Any, *, key: str = "") -> list[int]:
    indices: list[int] = []
    if isinstance(value, str):
        indices.extend(int(match.group(1)) for match in FORMULA_PLACEHOLDER_RE.finditer(value))
    elif isinstance(value, list):
        for item in value:
            indices.extend(_collect_formula_reference_indices(item, key=key))
    elif isinstance(value, dict):
        for child_key, item in value.items():
            child_key = str(child_key)
            if child_key in FORMULA_INDEX_KEYS:
                raw_values = item if isinstance(item, list) else [item]
                for raw in raw_values:
                    try:
                        indices.append(int(raw))
                    except (TypeError, ValueError):
                        continue
            else:
                indices.extend(_collect_formula_reference_indices(item, key=child_key))
    return indices


def _should_normalize_zero_based_formula_refs(draft: dict[str, Any]) -> bool:
    formula_count = len(draft.get("formulas") or [])
    if formula_count <= 0:
        return False
    indices = _collect_formula_reference_indices(draft)
    if 0 not in indices:
        return False
    return max(indices) <= formula_count - 1


def _shift_formula_refs_to_one_based(value: Any, *, key: str = "") -> Any:
    if isinstance(value, str):
        return FORMULA_PLACEHOLDER_RE.sub(lambda match: f"{{f{int(match.group(1)) + 1}}}", value)
    if isinstance(value, list):
        return [_shift_formula_refs_to_one_based(item, key=key) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for child_key, item in value.items():
            child_key = str(child_key)
            if child_key in FORMULA_INDEX_KEYS:
                raw_values = item if isinstance(item, list) else [item]
                shifted: list[Any] = []
                for raw in raw_values:
                    try:
                        shifted.append(int(raw) + 1)
                    except (TypeError, ValueError):
                        shifted.append(raw)
                out[child_key] = shifted if isinstance(item, list) else (shifted[0] if shifted else item)
            else:
                out[child_key] = _shift_formula_refs_to_one_based(item, key=child_key)
        return out
    return value


def normalize_formula_reference_base(draft: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(draft, dict) or not _should_normalize_zero_based_formula_refs(draft):
        return draft, False
    normalized = _shift_formula_refs_to_one_based(copy.deepcopy(draft))
    return normalized if isinstance(normalized, dict) else draft, True


SUBQUESTION_HEADING_RE = re.compile(
    r"^第\s*[（(]?\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]?\s*(?:小问|问)\s*[:：、.．]?\s*(.*)$"
)
PAREN_SUBQUESTION_HEADING_RE = re.compile(
    r"^[（(]\s*([一二三四五六七八九十0-9]{1,3})\s*[）)]\s*(.+)$"
)


def _normalize_subquestion_number(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.strip("第小问题（）()：:、.． ")
    if raw.isdigit():
        return str(int(raw))
    number = cn_to_int(raw)
    return str(number) if number > 0 else raw


def _extract_subquestion_heading(text: str) -> tuple[str, str] | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    match = SUBQUESTION_HEADING_RE.match(stripped)
    if not match:
        match = PAREN_SUBQUESTION_HEADING_RE.match(stripped)
    if not match:
        return None
    return _normalize_subquestion_number(match.group(1)), str(match.group(2) or "").strip()


def _strip_subquestion_heading(text: str) -> str:
    parsed = _extract_subquestion_heading(text)
    return parsed[1] if parsed else str(text or "").strip()


def _step_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"text": value.strip(), "formula_indices": []}] if value.strip() else []
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("explanation") or item.get("step") or "").strip()
            row = {
                "text": text,
                "formula_indices": _parse_formula_indices(item.get("formula_indices") or item.get("formulas")),
                "relation_formula_indices": _parse_formula_indices(item.get("relation_formula_indices")),
                "substitution_formula_indices": _parse_formula_indices(item.get("substitution_formula_indices")),
                "result_formula_indices": _parse_formula_indices(item.get("result_formula_indices")),
                "result_text": str(item.get("result_text") or "").strip(),
                "subquestion_number": _normalize_subquestion_number(item.get("subquestion_number")),
            }
            if item.get("_subquestion_heading"):
                row["_subquestion_heading"] = True
            if item.get("_subquestion_conflict"):
                row["_subquestion_conflict"] = True
            if text or row["formula_indices"] or row["relation_formula_indices"] or row["substitution_formula_indices"] or row["result_formula_indices"] or row["result_text"]:
                out.append(row)
        else:
            text = str(item).strip()
            if text:
                out.append({"text": text, "formula_indices": []})
    return out


def _calculation_subquestion_count(question: dict[str, Any]) -> int:
    stem = _clean_question_stem(question)
    score_marks = re.findall(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", stem)
    if len(score_marks) >= 2:
        return len(score_marks)
    numbered_parts = re.findall(r"(?:^|[\n；;。])\s*[（(]\s*[一二三四五六七八九十0-9]+\s*[）)]", stem)
    if len(numbered_parts) >= 2:
        return len(numbered_parts)
    return 0


def _has_subquestion_heading(text: str) -> bool:
    return _extract_subquestion_heading(text) is not None


def _has_step_heading(text: str) -> bool:
    stripped = text.strip()
    return bool(
        _has_subquestion_heading(stripped)
        or re.match(r"^第[一二三四五六七八九十0-9]+步", stripped)
        or re.match(r"^[0-9]+[.、]", stripped)
    )


def _strip_step_heading(text: str) -> str:
    stripped = text.strip()
    return re.sub(r"^(?:第[一二三四五六七八九十0-9]+步|[0-9]+[.、])[:：\s]*", "", stripped).strip()


def _question_subquestion_rows(question: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(question.get("subquestions") or [], start=1):
        if not isinstance(raw, dict):
            continue
        number = _normalize_subquestion_number(raw.get("number") or index)
        if not number:
            continue
        requirements = [req for req in raw.get("requirements", []) or [] if isinstance(req, dict)]
        if requirements:
            flatten = is_synthetic_requirement_parent(question, raw)
            for req_index, req in enumerate(requirements, start=1):
                req_number = _normalize_subquestion_number(req.get("number") or f"{number}.{req_index}")
                if not req_number:
                    continue
                rows.append(
                    {
                        "number": req_number,
                        "stem": strip_structured_math_metadata(str(req.get("stem") or "")).strip(),
                        "marker": str(req.get("marker") or req_number).strip(),
                        "question_type": infer_question_type(req),
                        "level": "subquestion" if flatten else "requirement",
                        "parent_number": "" if flatten else number,
                        "parent_stem": strip_structured_math_metadata(str(raw.get("stem") or "")).strip(),
                        "requirement_index": str(req_index),
                        "display_number": str(req_index) if flatten else "",
                        "synthetic_flattened": flatten,
                    }
                )
            continue
        rows.append(
            {
                "number": number,
                "stem": strip_structured_math_metadata(str(raw.get("stem") or "")).strip(),
                "marker": str(raw.get("marker") or "").strip(),
                "question_type": infer_question_type(raw),
                "level": "subquestion",
            }
        )
    return rows


def _unit_steps_have_payload(steps: list[dict[str, Any]]) -> bool:
    return any(
        str(step.get("text") or "").strip()
        or step.get("formula_indices")
        or step.get("relation_formula_indices")
        or step.get("substitution_formula_indices")
        or step.get("result_formula_indices")
        or str(step.get("result_text") or "").strip()
        for step in steps
    )


PENDING_ANSWER_VALUES = {"", "待复核", "待补充", "未完成", "未知", "见解析"}


def _is_effective_answer_text(answer: Any) -> bool:
    text = str(answer or "").strip()
    return text not in PENDING_ANSWER_VALUES and not looks_like_formula(text)


def _answer_unit_has_payload(unit: dict[str, Any]) -> bool:
    answer = str(unit.get("answer") or "").strip()
    if _is_effective_answer_text(answer):
        return True
    if any(
        str(item.get("text") or "").strip() or item.get("formula_indices")
        for item in _analysis_segment_items(unit.get("analysis_segments"))
    ):
        return True
    return _unit_steps_have_payload(_step_items(unit.get("steps")))


def _normalized_answer_units(draft: dict[str, Any], question: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one answer payload per confirmed answer unit.

    New drafts carry answer_units explicitly. The legacy branch intentionally has
    only narrow, deterministic recovery rules so old drafts remain readable without
    guessing which of several unrelated subquestions a paragraph belongs to.
    """
    rows = _question_subquestion_rows(question)
    raw_units = draft.get("answer_units")
    if len(rows) < 2:
        if isinstance(raw_units, list) and raw_units:
            formulas = _draft_formulas(draft, str(question.get("question_id") or ""))
            units: list[dict[str, Any]] = []
            for raw in raw_units:
                if not isinstance(raw, dict):
                    continue
                unit = {
                    "number": _normalize_subquestion_number(raw.get("number")) or str(question.get("number") or "1"),
                    "question_type": str(raw.get("question_type") or question.get("question_type") or "简答题").strip() or "简答题",
                    "answer": _replace_formula_placeholders_in_text(str(raw.get("answer") or ""), formulas),
                    "analysis_segments": _analysis_segment_items(raw.get("analysis_segments")),
                    "steps": _step_items(raw.get("steps")),
                    "figure_specs": _draft_figure_specs(raw),
                    "drawing_code_specs": _draft_drawing_code_specs(raw, str(question.get("question_id") or "")),
                }
                if _answer_unit_has_payload(unit):
                    units.append(unit)
            if len(units) == 1:
                return units
        return []

    by_number: dict[str, dict[str, Any]] = {
        row["number"]: {
            "number": row["number"],
            "question_type": row.get("question_type") or "简答题",
            "answer": "",
            "analysis_segments": [],
            "steps": [],
            "figure_specs": [],
            "drawing_code_specs": [],
        }
        for row in rows
    }
    if isinstance(raw_units, list) and raw_units:
        for raw in raw_units:
            if not isinstance(raw, dict):
                continue
            number = _normalize_subquestion_number(raw.get("number"))
            if number not in by_number:
                continue
            unit = by_number[number]
            # The reviewed question structure is authoritative. A model may
            # not relabel a confirmed calculation leaf as short-answer (or the
            # reverse), because rendering and coverage rules depend on it.
            unit["question_type"] = unit["question_type"]
            unit["answer"] = _replace_formula_placeholders_in_text(str(raw.get("answer") or ""), _draft_formulas(draft, str(question.get("question_id") or "")))
            unit["analysis_segments"] = _analysis_segment_items(raw.get("analysis_segments"))
            unit["steps"] = _step_items(raw.get("steps"))
            unit["figure_specs"] = _draft_figure_specs(raw)
            unit["drawing_code_specs"] = _draft_drawing_code_specs(raw, str(question.get("question_id") or ""))
        return [by_number[row["number"]] for row in rows]

    # Compatibility for drafts created before answer_units existed. This is enough
    # for the observed "one short-answer + one calculation" shape, but deliberately
    # does not invent assignments for multiple same-type units.
    noncalculation = [row for row in rows if row.get("question_type") != "计算题"]
    calculation = [row for row in rows if row.get("question_type") == "计算题"]
    legacy_analysis = _analysis_segment_items(draft.get("analysis_segments"))
    if len(noncalculation) == 1 and legacy_analysis:
        by_number[noncalculation[0]["number"]]["analysis_segments"] = legacy_analysis

    root_analysis = _normalize_multipart_text_layout(str(draft.get("analysis") or ""), _clean_question_stem(question))
    if root_analysis and len(calculation) == 1:
        by_number[calculation[0]["number"]]["analysis_segments"].append(
            {"text": root_analysis, "formula_indices": []}
        )

    steps = _step_items(draft.get("steps"))
    if steps:
        calculation_numbers = {row["number"] for row in calculation}
        unassigned: list[dict[str, Any]] = []
        for step in steps:
            number = _normalize_subquestion_number(step.get("subquestion_number"))
            if number in calculation_numbers:
                by_number[number]["steps"].append(step)
            else:
                unassigned.append(step)
        if len(calculation) == 1:
            by_number[calculation[0]["number"]]["steps"].extend(unassigned)

    return [by_number[row["number"]] for row in rows]


def _formula_ids_in_segments(segments: list[dict[str, Any]]) -> set[str]:
    return {
        str(segment.get("formula_id") or "")
        for segment in segments
        if isinstance(segment, dict) and segment.get("type") == "formula_ref" and str(segment.get("formula_id") or "")
    }


def _answer_unit_blocks(
    answer_units: list[dict[str, Any]],
    rows: list[dict[str, str]],
    formulas: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Render unit payloads into the existing question-level Word block model."""
    formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
    rows_by_number = {row["number"]: row for row in rows}
    analysis_out: list[dict[str, Any]] = []
    steps_out: list[dict[str, Any]] = []
    last_analysis_parent = ""
    last_steps_parent = ""

    for unit in answer_units:
        number = str(unit.get("number") or "")
        row = rows_by_number.get(number)
        if row is None:
            continue
        unit_analysis: list[dict[str, Any]] = []
        answer_text = str(unit.get("answer") or "").strip()
        if _is_effective_answer_text(answer_text):
            unit_analysis.append({"type": "text", "text": f"答案：{answer_text}\n"})
        for item in _analysis_segment_items(unit.get("analysis_segments")):
            text = _normalize_multipart_text_layout(str(item.get("text") or ""), row.get("stem") or "")
            if text:
                segments, _ = _segments_from_inline_formula_text(text, formula_ids)
                unit_analysis.extend(segments)
            # formula_indices remains a compatibility declaration. New answer_units
            # should use placeholders, but preserving it keeps existing drafts usable.
            declared = _formula_ids_for_indices(item.get("formula_indices") or [], formula_ids)
            referenced = _formula_ids_in_segments(unit_analysis)
            for formula_id in declared:
                if formula_id not in referenced:
                    unit_analysis.append({"type": "formula_ref", "formula_id": formula_id})
        if unit_analysis:
            last_analysis_parent = _append_structured_heading_segments(analysis_out, row, last_analysis_parent)
            analysis_out.extend(unit_analysis)
            if not (analysis_out and analysis_out[-1].get("type") == "text" and str(analysis_out[-1].get("text") or "").endswith("\n")):
                analysis_out.append({"type": "text", "text": "\n"})

        unit_steps = _step_items(unit.get("steps"))
        if unit_steps:
            for step in unit_steps:
                step["subquestion_number"] = number
            last_steps_parent = _append_structured_heading_segments(steps_out, row, last_steps_parent)
            steps_out.extend(_calculation_step_segments(unit_steps, formulas))

    used = _formula_ids_in_segments(analysis_out) | _formula_ids_in_segments(steps_out)
    return analysis_out, steps_out, used


def _answer_from_answer_units(answer_units: list[dict[str, Any]], rows: list[dict[str, str]] | None = None) -> str:
    parts: list[tuple[str, str]] = []
    for unit in answer_units:
        if not isinstance(unit, dict):
            continue
        answer = str(unit.get("answer") or "").strip()
        if not answer or answer in PENDING_ANSWER_VALUES:
            continue
        number = str(unit.get("number") or "").strip()
        parts.append((number, answer))
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][1]
    rows_by_number = {str(row.get("number") or ""): row for row in rows or []}
    if rows_by_number and any(rows_by_number.get(number, {}).get("level") == "requirement" or "." in number for number, _answer in parts):
        grouped: list[str] = []
        current_parent = ""
        current_items: list[str] = []
        parent_titles: dict[str, str] = {}
        for number, answer in parts:
            row = rows_by_number.get(number, {})
            if row.get("level") == "requirement" or "." in number:
                parent_number = str(row.get("parent_number") or number.rsplit(".", 1)[0]).strip()
                parent_titles[parent_number] = _parent_subquestion_title(row)
                if current_parent and parent_number != current_parent:
                    title = parent_titles.get(current_parent) or _subquestion_label(current_parent)
                    grouped.append(f"{title}：" + "；".join(current_items))
                    current_items = []
                current_parent = parent_number
                current_items.append(f"{_requirement_label(number, row.get('requirement_index', ''))}、{answer}")
            else:
                if current_parent:
                    title = parent_titles.get(current_parent) or _subquestion_label(current_parent)
                    grouped.append(f"{title}：" + "；".join(current_items))
                    current_parent = ""
                    current_items = []
                grouped.append(f"{_subquestion_label(number)}{answer}")
        if current_parent:
            title = parent_titles.get(current_parent) or _subquestion_label(current_parent)
            grouped.append(f"{title}：" + "；".join(current_items))
        return "；".join(grouped)
    return "；".join(f"{_subquestion_label(number)}{answer}" if number else answer for number, answer in parts)


def _single_answer_unit_blocks(
    unit: dict[str, Any],
    formulas: list[dict[str, Any]],
    stem: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
    analysis_out: list[dict[str, Any]] = []
    for item in _analysis_segment_items(unit.get("analysis_segments")):
        text = _normalize_multipart_text_layout(str(item.get("text") or ""), stem)
        if text:
            segments, _ = _segments_from_inline_formula_text(text, formula_ids)
            analysis_out.extend(segments)
        declared = _formula_ids_for_indices(item.get("formula_indices") or [], formula_ids)
        referenced = _formula_ids_in_segments(analysis_out)
        for formula_id in declared:
            if formula_id not in referenced:
                analysis_out.append({"type": "formula_ref", "formula_id": formula_id})

    unit_steps = _step_items(unit.get("steps"))
    steps_out = _calculation_step_segments(unit_steps, formulas) if unit_steps else []
    used = _formula_ids_in_segments(analysis_out) | _formula_ids_in_segments(steps_out)
    return analysis_out, steps_out, used


def _text_terms(value: str) -> set[str]:
    text = re.sub(r"\s+", "", str(value or ""))
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,}", text):
        if len(token) >= 2:
            terms.add(token)
        if re.search(r"[\u4e00-\u9fff]", token):
            terms.update(token[index : index + 2] for index in range(0, max(0, len(token) - 1)))
            terms.update(token[index : index + 3] for index in range(0, max(0, len(token) - 2)))
    return {term for term in terms if term not in {"分别", "指出", "已知", "一定", "过程", "大于", "小于", "还是", "有利"}}


def _sentence_chunks(text: str) -> list[str]:
    chunks = [chunk.strip() for chunk in re.findall(r"[^。！？\n]+[。！？]?", str(text or "")) if chunk.strip()]
    return chunks or ([str(text).strip()] if str(text).strip() else [])


def _score_text_against_row(text: str, row: dict[str, str]) -> int:
    stem_terms = _text_terms(row.get("stem", ""))
    text_terms = _text_terms(text)
    return len(stem_terms & text_terms)


def _split_text_by_answer_units(text: str, rows: list[dict[str, str]]) -> list[str]:
    chunks = _sentence_chunks(text)
    if len(rows) < 2 or len(chunks) <= 1:
        return [text]
    split_points = [0]
    cursor = 0
    for row_index in range(1, len(rows)):
        best_index = -1
        best_score = 0
        for chunk_index in range(cursor + 1, len(chunks)):
            score = _score_text_against_row(chunks[chunk_index], rows[row_index])
            if score > best_score:
                best_score = score
                best_index = chunk_index
        if best_index >= 0 and best_score >= 2 and best_index not in split_points:
            split_points.append(best_index)
            cursor = best_index
    if len(split_points) < len(rows):
        fallback_points = [int(len(chunks) * index / len(rows)) for index in range(len(rows))]
        split_points = sorted({min(len(chunks), max(0, point)) for point in [*split_points, *fallback_points]})
    while len(split_points) < len(rows):
        split_points.append(len(chunks))
    out: list[str] = []
    for index, start in enumerate(split_points[: len(rows)]):
        end = split_points[index + 1] if index + 1 < len(split_points[: len(rows)]) else len(chunks)
        out.append("".join(chunks[start:end]).strip())
    while len(out) < len(rows):
        out.append("")
    return out


def _analysis_has_answer_unit_headings(items: list[dict[str, Any]]) -> bool:
    for item in items:
        if _has_subquestion_heading(str(item.get("text") or "")):
            return True
    return False


def _answer_unit_analysis_items(question: dict[str, Any], items: list[dict[str, Any]], fallback_text: str) -> list[dict[str, Any]]:
    rows = _question_subquestion_rows(question)
    if len(rows) < 2:
        return items or ([{"text": fallback_text, "formula_indices": []}] if fallback_text else [])
    if items and _analysis_has_answer_unit_headings(items):
        return items
    source_items = items or ([{"text": fallback_text, "formula_indices": []}] if fallback_text else [])
    if not source_items:
        return []
    grouped: list[dict[str, Any]] = []
    last_parent = ""
    if len(source_items) == len(rows):
        for row, item in zip(rows, source_items):
            last_parent = _append_structured_heading_item(grouped, row, last_parent)
            grouped.append(item)
        return grouped
    if len(source_items) == 1:
        parts = _split_text_by_answer_units(str(source_items[0].get("text") or ""), rows)
        for row, part in zip(rows, parts):
            last_parent = _append_structured_heading_item(grouped, row, last_parent)
            if part:
                grouped.append({"text": part, "formula_indices": source_items[0].get("formula_indices", [])})
        return grouped
    total = len(source_items)
    buckets: dict[str, list[dict[str, Any]]] = {row["number"]: [] for row in rows}
    for index, item in enumerate(source_items):
        row = rows[min(len(rows) - 1, int(index * len(rows) / max(total, 1)))]
        buckets[row["number"]].append(item)
    last_parent = ""
    for row in rows:
        last_parent = _append_structured_heading_item(grouped, row, last_parent)
        grouped.extend(buckets.get(row["number"], []))
    return grouped


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


def _numeric_label(value: str) -> str:
    text = str(value or "").strip()
    chinese = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
    return chinese.get(text, text)


def _subquestion_label(number: str) -> str:
    return f"({_numeric_label(number)})"


def _requirement_label(number: str, fallback_index: str = "") -> str:
    raw = str(number or "").strip()
    suffix = raw.rsplit(".", 1)[-1] if "." in raw else raw
    suffix = _numeric_label(suffix or fallback_index)
    try:
        numeric = int(suffix)
    except ValueError:
        return suffix or raw
    return _CIRCLED_NUMBERS.get(numeric, f"({numeric})")


def _subquestion_title(row: dict[str, str]) -> str:
    number = row.get("number") or ""
    stem = str(row.get("stem") or "").strip(" ：:；;。")
    if row.get("level") == "requirement" or ("." in number and not row.get("synthetic_flattened")):
        label = _requirement_label(number, row.get("requirement_index", ""))
        return f"{label}、{stem}" if stem else f"{label}、"
    label = _subquestion_label(row.get("display_number") or number)
    return f"{label}{stem}" if stem else label


def _parent_subquestion_title(row: dict[str, str]) -> str:
    parent_number = row.get("parent_number") or ""
    parent_stem = str(row.get("parent_stem") or "").strip(" ：:；;。")
    label = _subquestion_label(parent_number)
    return f"{label}{parent_stem}" if parent_stem else label


def _append_structured_heading_segments(out: list[dict[str, Any]], row: dict[str, str], last_parent: str) -> str:
    if row.get("level") == "requirement" or ("." in str(row.get("number") or "") and not row.get("synthetic_flattened")):
        parent_number = str(row.get("parent_number") or "").strip()
        if parent_number and parent_number != last_parent:
            out.append({"type": "text", "text": _parent_subquestion_title(row) + "\n"})
            last_parent = parent_number
        out.append({"type": "text", "text": _subquestion_title(row) + "\n"})
        return last_parent
    out.append({"type": "text", "text": _subquestion_title(row) + "\n"})
    return ""


def _append_structured_heading_item(out: list[dict[str, Any]], row: dict[str, str], last_parent: str) -> str:
    if row.get("level") == "requirement" or ("." in str(row.get("number") or "") and not row.get("synthetic_flattened")):
        parent_number = str(row.get("parent_number") or "").strip()
        if parent_number and parent_number != last_parent:
            out.append({"text": _parent_subquestion_title(row), "formula_indices": [], "_subquestion_heading": True})
            last_parent = parent_number
        out.append({"text": _subquestion_title(row), "formula_indices": [], "_subquestion_heading": True})
        return last_parent
    out.append({"text": _subquestion_title(row), "formula_indices": [], "_subquestion_heading": True})
    return ""


def _step_has_payload(item: dict[str, Any]) -> bool:
    return bool(
        item.get("formula_indices")
        or item.get("relation_formula_indices")
        or item.get("substitution_formula_indices")
        or item.get("result_formula_indices")
        or str(item.get("result_text") or "").strip()
    )


def _group_calculation_steps_by_subquestion(steps: Any, question: dict[str, Any]) -> Any:
    items = _step_items(steps)
    subquestion_rows = _question_subquestion_rows(question)
    if len(subquestion_rows) >= 2 and items:
        valid_numbers = [row["number"] for row in subquestion_rows]
        valid_set = set(valid_numbers)
        buckets: dict[str, list[dict[str, Any]]] = {number: [] for number in valid_numbers}
        unassigned: list[dict[str, Any]] = []
        for item in items:
            normalized = dict(item)
            explicit_number = _normalize_subquestion_number(normalized.get("subquestion_number"))
            parsed = _extract_subquestion_heading(str(normalized.get("text") or ""))
            parsed_number = ""
            if parsed:
                parsed_number, body = parsed
                normalized["text"] = body
                if explicit_number and parsed_number and explicit_number != parsed_number:
                    normalized["_subquestion_conflict"] = True
            target = explicit_number if explicit_number in valid_set else parsed_number if parsed_number in valid_set else ""
            if target:
                normalized["subquestion_number"] = target
                buckets[target].append(normalized)
            else:
                unassigned.append(normalized)
        if unassigned:
            if len(unassigned) == len(valid_numbers) and not any(buckets.values()):
                for row, item in zip(subquestion_rows, unassigned):
                    item["subquestion_number"] = row["number"]
                    buckets[row["number"]].append(item)
            else:
                total = len(unassigned)
                for index, item in enumerate(unassigned):
                    row = subquestion_rows[min(len(subquestion_rows) - 1, int(index * len(subquestion_rows) / max(total, 1)))]
                    item["subquestion_number"] = row["number"]
                    buckets[row["number"]].append(item)
        grouped: list[dict[str, Any]] = []
        last_parent = ""
        for row in subquestion_rows:
            bucket = buckets.get(row["number"], [])
            if not bucket:
                continue
            before = len(grouped)
            last_parent = _append_structured_heading_item(grouped, row, last_parent)
            for heading in grouped[before:]:
                heading["subquestion_number"] = row["number"]
            grouped.extend(bucket)
        return grouped

    subquestion_count = _calculation_subquestion_count(question)
    if subquestion_count < 2 or not items:
        return steps
    if len(items) == subquestion_count:
        grouped: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            normalized = dict(item)
            body = _strip_subquestion_heading(_strip_step_heading(str(item.get("text") or "")))
            grouped.append({"text": f"第{index}小问：", "formula_indices": [], "_subquestion_heading": True, "subquestion_number": str(index)})
            normalized["text"] = body
            normalized["subquestion_number"] = str(index)
            grouped.append(normalized)
        return grouped

    grouped = []
    current_group = 0
    total = len(items)
    for index, item in enumerate(items):
        group_index = min(subquestion_count, int(index * subquestion_count / total) + 1)
        if group_index != current_group:
            grouped.append({"text": f"第{group_index}小问：", "formula_indices": [], "_subquestion_heading": True})
            current_group = group_index
        grouped.append(item)
    return grouped


def _formula_ids_for_indices(indices: list[int], formula_ids: list[str]) -> list[str]:
    ids: list[str] = []
    for formula_index in indices:
        if 1 <= formula_index <= len(formula_ids):
            formula_id = formula_ids[formula_index - 1]
            if formula_id not in ids:
                ids.append(formula_id)
    return ids


def _analysis_segment_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            text = _strip_program_citation_text(str(item.get("text") or item.get("analysis") or item.get("explanation") or ""))
            indices = _parse_formula_indices(item.get("formula_indices") or item.get("formulas"))
            if text or indices:
                out.append({"text": text, "formula_indices": indices})
        else:
            text = str(item).strip()
            if text:
                out.append({"text": text, "formula_indices": []})
    return out


# Accept the documented numeric placeholder and a common model spelling that
# appends a harmless mnemonic suffix (``{f5_b}``).  The numeric prefix remains
# the sole authority for formula selection; arbitrary names are not resolved.
FORMULA_PLACEHOLDER_RE = re.compile(r"\{[fF](\d+)(?:_[A-Za-z][A-Za-z0-9]*)?\}")


def _segments_from_formula_placeholder_text(text: str, formula_ids: list[str], *, inline: bool) -> tuple[list[dict[str, Any]], set[str]]:
    segments: list[dict[str, Any]] = []
    used: set[str] = set()
    cursor = 0
    for match in FORMULA_PLACEHOLDER_RE.finditer(text):
        plain = text[cursor:match.start()]
        if plain:
            segments.extend(_text_segments(plain))
        index = int(match.group(1))
        formula_id = _formula_ids_for_indices([index], formula_ids)
        if formula_id:
            fid = formula_id[0]
            segment: dict[str, Any] = {"type": "formula_ref", "formula_id": fid}
            if inline:
                segment["inline"] = True
            segments.append(segment)
            used.add(fid)
        else:
            segments.extend(_text_segments(match.group(0)))
        cursor = match.end()
    tail = text[cursor:]
    if tail:
        segments.extend(_text_segments(tail))
    return segments, used


def _segments_from_inline_formula_text(text: str, formula_ids: list[str]) -> tuple[list[dict[str, Any]], set[str]]:
    return _segments_from_formula_placeholder_text(text, formula_ids, inline=True)


NOTATION_FORMULA_ROLES = {
    "notation",
    "symbol",
    "label",
    "diagram_label",
    "axis_label",
    "unit",
    "phase_label",
    "miller_index",
}
NOTATION_FORMULA_MEANING_RE = re.compile(r"(符号|标注|标签|单位|坐标|坐标轴|图中|作图|示意图)")
UNIT_LATEX_RE = re.compile(r"\\mathrm\{(?:kg|g|mol|m|s|K|Pa|J|N|C|W|V|A|Hz|eV|cm|mm|nm|MPa|GPa)\}")
SIMPLE_NOTATION_LATEX_RE = re.compile(
    r"^(?:"
    r"\\mathbf\{?[A-Za-z]\}?|"
    r"[A-Za-z](?:_\{?[A-Za-z0-9]+\}?|<\\?[A-Za-z]+)?|"
    r"\\[A-Za-z]+|"
    r"[A-Za-z]/(?:\d+|[A-Za-z])|"
    r"\\left\([^{}]+\\right\)|"
    r"\([^{}]+,[^{}]+,[^{}]+\\right\)"
    r")$"
)


def _is_graphic_question_for_formula_policy(question: dict[str, Any]) -> bool:
    return question_has_type(question, "作图题") or question_kind(question) == "graphic"


def _is_notation_formula(formula: dict[str, Any], question: dict[str, Any]) -> bool:
    latex = str(formula.get("latex") or "").strip()
    role = str(formula.get("role") or "").strip().lower()
    meaning = str(formula.get("meaning") or formula.get("source_note") or "").strip()
    if role in NOTATION_FORMULA_ROLES:
        return True
    if NOTATION_FORMULA_MEANING_RE.search(meaning):
        return True
    policy_text = " ".join(
        [
            _clean_question_stem(question),
            meaning,
            latex,
        ]
    )
    if any(
        contribution.get("is_notation") is True
        for contribution in capability_policy_contributions(
            "notation_formula_classification",
            {"question": question, "formula": formula, "meaning": meaning, "text": policy_text},
            text=policy_text,
        )
        if isinstance(contribution, dict)
    ):
        return True
    if UNIT_LATEX_RE.search(latex):
        return True
    compact = re.sub(r"\s+", "", latex)
    if SIMPLE_NOTATION_LATEX_RE.fullmatch(compact):
        return True
    if _is_graphic_question_for_formula_policy(question):
        return True
    return False


def _plain_formula_text(formula: dict[str, Any]) -> str:
    latex = str(formula.get("latex") or "").strip()
    if not latex:
        return str(formula.get("source_note") or "").strip()
    replacements = {
        r"\alpha": "α",
        r"\beta": "β",
        r"\gamma": "γ",
        r"\delta": "δ",
        r"\theta": "θ",
        r"\Delta": "Δ",
        r"\rightarrow": "→",
        r"\to": "→",
        r"\quad": " ",
        r"\,": "",
        r"\ ": " ",
    }
    text = latex
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\\(?:mathrm|text)\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\\xrightarrow\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "→", text)
    text = re.sub(r"\\[A-Za-z]+", "", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("_", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([αβγδθλμπσνΔ])\s+([A-Za-z])", r"\1\2", text)
    return text or str(formula.get("source_note") or "").strip() or latex


def _replace_formula_placeholders_in_text(text: str, formulas: list[dict[str, Any]]) -> str:
    if not text or not formulas:
        return text

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if 1 <= index <= len(formulas):
            return _plain_formula_text(formulas[index - 1])
        return match.group(0)

    return FORMULA_PLACEHOLDER_RE.sub(replace, text)


def _replace_formula_placeholders_in_value(value: Any, formulas: list[dict[str, Any]]) -> Any:
    if isinstance(value, str):
        return _replace_formula_placeholders_in_text(value, formulas)
    if isinstance(value, list):
        return [_replace_formula_placeholders_in_value(item, formulas) for item in value]
    if isinstance(value, dict):
        return {key: _replace_formula_placeholders_in_value(item, formulas) for key, item in value.items()}
    return value


def _noncalculation_analysis_segments(draft: dict[str, Any], formulas: list[dict[str, Any]], question: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
    used: set[str] = set()
    stem = _clean_question_stem(question)
    fallback_text = _strip_program_citation_text(_normalize_multipart_text_layout(str(draft.get("analysis") or ""), stem))
    items = _answer_unit_analysis_items(question, _analysis_segment_items(draft.get("analysis_segments")), fallback_text)
    if not items:
        return _inline_formula_segments_from_text(fallback_text, formulas)
    segments: list[dict[str, Any]] = []
    for item in items:
        text = _normalize_multipart_text_layout(str(item.get("text") or ""), stem)
        if text:
            if segments and (FIRST_LEVEL_SUBQUESTION_RE.match(text) or item.get("_subquestion_heading")):
                segments.append({"type": "text", "text": "\n"})
            inline_segments, inline_used = _segments_from_inline_formula_text(text, formula_ids)
            segments.extend(inline_segments)
            if item.get("_subquestion_heading"):
                segments.append({"type": "text", "text": "\n"})
            used.update(inline_used)
    if not segments:
        segments, fallback_used = _inline_formula_segments_from_text(fallback_text, formulas)
        used.update(fallback_used)
    return segments, used


def _append_indexed_formula_refs(
    segments: list[dict[str, Any]],
    formula_ids: list[str],
    indices: list[int],
    used: set[str],
    label: str | None = None,
) -> None:
    ids = [formula_id for formula_id in _formula_ids_for_indices(indices, formula_ids) if formula_id not in used]
    if not ids:
        return
    if label:
        segments.append({"type": "text", "text": label})
    for formula_id in ids:
        segments.append({"type": "formula_ref", "formula_id": formula_id})
        used.add(formula_id)


def _calculation_step_intro(text: str, fallback_index: int) -> str:
    raw = _strip_program_citation_text(str(text or "")).strip()
    if not raw:
        return ""
    heading_match = re.match(r"^第[一二三四五六七八九十0-9]+步[:：\s]*", raw)
    body = raw[heading_match.end() :] if heading_match else raw

    parts = [part.strip(" ，,；;。") for part in re.split(r"[。；;]\s*", body) if part.strip(" ，,；;。")]
    kept: list[str] = []
    for part in parts:
        if FORMULA_PLACEHOLDER_RE.search(part):
            prefix = FORMULA_PLACEHOLDER_RE.split(part, 1)[0].strip(" ，,；;。")
            if prefix and not re.search(r"(根据定义|关系式为|代入|得到|求得|因此)$", prefix):
                kept.append(prefix)
            continue
        if re.fullmatch(r"(根据定义|关系式为|代入.*|得到.*|求得.*|因此)", part):
            continue
        kept.append(part)
    body_text = "。".join(dict.fromkeys(item for item in kept if item))
    if body_text:
        return f"{body_text}。"
    before_placeholder = FORMULA_PLACEHOLDER_RE.split(body, 1)[0].strip(" ，,；;。")
    if before_placeholder:
        return f"{before_placeholder}。"
    return ""


def _calculation_step_segments(steps: Any, formulas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = _step_items(steps)
    formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
    if not items and not formula_ids:
        return []
    segments: list[dict[str, Any]] = []
    used: set[str] = set()
    step_index = 0
    for item in items:
        text = str(item.get("text") or "").strip()
        if text:
            if item.get("_subquestion_heading"):
                heading_segments, heading_used = _segments_from_inline_formula_text(text, formula_ids)
                for segment in heading_segments:
                    if segment.get("type") == "formula_ref":
                        formula_id = str(segment.get("formula_id") or "")
                        if formula_id in used:
                            continue
                        used.add(formula_id)
                    segments.append(segment)
                continue
            text = _strip_subquestion_heading(text)
            if not _has_step_heading(text):
                step_index += 1
            elif not _has_subquestion_heading(text):
                step_index += 1
            segments.append({"type": "text", "text": _calculation_step_intro(text, step_index or 1)})
        relation_indices = item.get("relation_formula_indices") or item.get("formula_indices", [])
        _append_indexed_formula_refs(segments, formula_ids, relation_indices, used)
        _append_indexed_formula_refs(segments, formula_ids, item.get("substitution_formula_indices", []), used, "带入数值：")
        result_indices = [
            index
            for index in item.get("result_formula_indices", []) or []
            if isinstance(index, int)
            and 0 < index <= len(formulas)
            and not bool(formulas[index - 1].get("_program_mirrored_from_contract"))
        ]
        _append_indexed_formula_refs(segments, formula_ids, result_indices, used, "求得：")
        result_text = str(item.get("result_text") or "").strip()
        # A structured result formula is the authoritative visible layer.  The
        # model's result_text commonly repeats the same label/value/unit and,
        # after inline-expression promotion, can leave a second partial text
        # fallback next to the OMML object.  Keep prose-only results, but do not
        # render a duplicate fallback when visible result formulas exist.
        if result_text and not result_indices:
            result_segments, result_used = _segments_from_inline_formula_text(result_text, formula_ids)
            for segment in result_segments:
                if segment.get("type") == "formula_ref":
                    formula_id = str(segment.get("formula_id") or "")
                    if formula_id in used:
                        continue
                    used.add(formula_id)
                segments.append(segment)
    if not items:
        for formula_id in formula_ids:
            segments.append({"type": "formula_ref", "formula_id": formula_id})
    return segments


def _has_review_flag(fragment: dict[str, Any], code: str) -> bool:
    return any(isinstance(flag, dict) and str(flag.get("code", "")) == code for flag in fragment.get("_review_flags", []))


def _sign_contract_text(value: Any) -> str:
    """Normalize prose/LaTeX just enough to compare declared quantities."""

    text = str(value or "").replace("−", "-").replace("≤", "<").replace("≥", ">")
    previous = None
    wrappers = re.compile(r"\\(?:mathrm|mathit|mathbf|text)\{([^{}]*)\}")
    while previous != text:
        previous = text
        text = wrappers.sub(r"\1", text)
    text = text.replace(r"\left", "").replace(r"\right", "")
    text = text.replace(r"\Delta", "Delta").replace("Δ", "Delta").replace("∆", "Delta")
    text = text.replace(r"\partial", "partial").replace("∂", "partial")
    return re.sub(r"[\s{}_\\]", "", text)


def _difference_sign_consistency_issues(fragment: dict[str, Any]) -> list[str]:
    """Check the machine-verifiable sign of a declared two-term difference.

    For a relation ``A=B-C``, an explicit ``B<C`` premise determines ``A<0``.
    A conclusion formula claiming the opposite sign is internally inconsistent,
    independent of the domain or the names of the quantities.
    """

    draft = fragment.get("_draft") if isinstance(fragment.get("_draft"), dict) else {}
    formula_source = draft.get("formulas") if isinstance(draft.get("formulas"), list) else fragment.get("formulas", [])
    formulas = [item for item in formula_source or [] if isinstance(item, dict)]
    if not formulas:
        return []
    analysis_text = _sign_contract_text(
        "。".join(
            str(item.get("text") or "")
            for unit in fragment.get("answer_units", []) or []
            if isinstance(unit, dict)
            for item in _analysis_segment_items(unit.get("analysis_segments"))
        )
    )
    conclusions: dict[str, set[int]] = {}
    for formula in formulas:
        normalized = _sign_contract_text(formula.get("latex"))
        match = re.fullmatch(r"(.+?)([<>])0", normalized)
        if match:
            conclusions.setdefault(match.group(1), set()).add(1 if match.group(2) == ">" else -1)

    issues: list[str] = []
    for formula in formulas:
        normalized = _sign_contract_text(formula.get("latex"))
        if "=" not in normalized:
            continue
        lhs, rhs = normalized.split("=", 1)
        if rhs.count("-") != 1 or lhs not in conclusions:
            continue
        minuend, subtrahend = rhs.split("-", 1)
        if not minuend or not subtrahend:
            continue
        expected = 0
        if f"{minuend}<{subtrahend}" in analysis_text or f"{subtrahend}>{minuend}" in analysis_text:
            expected = -1
        elif f"{minuend}>{subtrahend}" in analysis_text or f"{subtrahend}<{minuend}" in analysis_text:
            expected = 1
        if expected and conclusions[lhs] == {-expected}:
            issues.append("difference_sign_contradiction:" + lhs[:80])
    return issues


def add_review_flag(fragment: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    flags = [flag for flag in fragment.get("_review_flags", []) if isinstance(flag, dict)]
    if not any(str(flag.get("code", "")) == code for flag in flags):
        flags.append({"code": code, "message": message})
    fragment["_review_flags"] = flags
    warnings = [str(warning) for warning in fragment.get("warnings", [])]
    if message not in warnings:
        warnings.append(message)
    fragment["warnings"] = warnings
    return fragment


def semantic_generation_issues(
    question: dict[str, Any],
    fragment: dict[str, Any],
    allow_formula_absence_after_retry: bool = False,
) -> list[str]:
    issues: list[str] = []
    figure_containers = [fragment]
    figure_containers.extend(
        item for item in (fragment.get("answer_units") or []) if isinstance(item, dict)
    )
    has_required_figure_output = any(
        any(
            isinstance(item, dict) and (
                str(item.get("asset_id") or "").strip()
                if key == "generated_images"
                else True
            )
            for item in (container.get(key) or [])
        )
        for container in figure_containers
        for key in ("generated_images", "figure_specs", "drawing_code_specs")
    )
    if answer_figure_required(question) and not has_required_figure_output:
        issues.append("missing_required_answer_figure")
    expected_units = _question_subquestion_rows(question)
    if len(expected_units) >= 2:
        units = fragment.get("answer_units") if isinstance(fragment.get("answer_units"), list) else []
        by_number = {
            _normalize_subquestion_number(unit.get("number")): unit
            for unit in units
            if isinstance(unit, dict) and _normalize_subquestion_number(unit.get("number"))
        }
        missing_units = [
            row["number"]
            for row in expected_units
            if not isinstance(by_number.get(row["number"]), dict) or not _answer_unit_has_payload(by_number[row["number"]])
        ]
        if missing_units:
            issues.append("missing_answer_units:" + ",".join(missing_units))
        missing_calculation_steps = [
            row["number"]
            for row in expected_units
            if row.get("question_type") == "计算题"
            and (
                not isinstance(by_number.get(row["number"]), dict)
                or not _unit_steps_have_payload(_step_items(by_number[row["number"]].get("steps")))
            )
        ]
        if missing_calculation_steps:
            issues.append("calculation_missing_subquestion_steps:" + ",".join(missing_calculation_steps))
        missing_drawing_outputs: list[str] = []
        generated_image_units = {
            _normalize_subquestion_number(item.get("answer_unit_number"))
            for item in fragment.get("generated_images", []) or []
            if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
        }
        for row in expected_units:
            if row.get("question_type") != "作图题":
                continue
            unit = by_number.get(row["number"])
            if not isinstance(unit, dict):
                missing_drawing_outputs.append(row["number"])
                continue
            expected_key = "drawing_code_specs" if question_drawing_mode(question) == "code" else "figure_specs"
            if not unit.get(expected_key) and row["number"] not in generated_image_units:
                missing_drawing_outputs.append(row["number"])
        if missing_drawing_outputs:
            issues.append("missing_drawing_answer_units:" + ",".join(missing_drawing_outputs))
        for number, unit in by_number.items():
            answer_text = str(unit.get("answer") or "")
            analysis_text = "。".join(
                str(item.get("text") or "")
                for item in _analysis_segment_items(unit.get("analysis_segments"))
            )
            answer_polarities = {
                1 if phrase == "大于零" else -1
                for phrase in re.findall(r"大于零|小于零", answer_text)
            }
            conclusion_polarities = {
                1 if phrase == "大于零" else -1
                for phrase in re.findall(
                    r"(?:故|因此|所以|即)[^。；]{0,48}?(大于零|小于零)",
                    analysis_text,
                )
            }
            if (
                len(answer_polarities) == 1
                and len(conclusion_polarities) == 1
                and answer_polarities != conclusion_polarities
            ):
                issues.append(f"answer_analysis_zero_polarity_contradiction:{number}")
    if (
        is_calculation_question(question)
        and not fragment.get("formulas")
        and not (allow_formula_absence_after_retry and _has_review_flag(fragment, "formula_absence_after_retry"))
    ):
        issues.append("calculation_missing_formula")
    if is_calculation_question(question):
        draft = copy.deepcopy(fragment.get("_draft")) if isinstance(fragment.get("_draft"), dict) else {}
        if not draft:
            # Durable fragments retain enough calculation state for later
            # review even after the transient generation draft is removed.
            draft = {
                key: copy.deepcopy(fragment.get(key))
                for key in ("formulas", "answer_units", "calculation_contract")
                if key in fragment
            }
        draft_formula_count = len([item for item in draft.get("formulas", []) or [] if isinstance(item, dict)])
        referenced_indices = _collect_formula_reference_indices(draft)
        invalid_indices = sorted({index for index in referenced_indices if index < 1 or index > draft_formula_count})
        if invalid_indices:
            issues.append(
                "formula_reference_out_of_range:"
                + ",".join(str(index) for index in invalid_indices)
                + f":formula_count={draft_formula_count}"
            )
        issues.extend(calculation_draft_consistency_issues(draft))
        calculation_units = [row for row in expected_units if row.get("question_type") == "计算题"]
        issues.extend(calculation_contract_issues(draft, calculation_units))
    issues.extend(_difference_sign_consistency_issues(fragment))
    return issues


def _selected_ids_from_evidence_selection(evidence_selection: dict[str, Any] | None) -> list[str]:
    if not evidence_selection:
        return []
    out: list[str] = []
    for point in evidence_selection.get("knowledge_points", []):
        if not isinstance(point, dict):
            continue
        for raw in point.get("selected_evidence_ids", []) or []:
            evidence_id = str(raw).strip()
            if evidence_id and evidence_id not in out:
                out.append(evidence_id)
    return out


def evidence_for_answer_generation(
    candidates: list[EvidenceCandidate],
    qid: str,
    evidence_selection: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    selected_ids = _selected_ids_from_evidence_selection(evidence_selection)
    if selected_ids:
        by_id = {candidate.evidence_id: candidate for candidate in candidates if candidate.question_id == qid}
        selected_rows = [asdict(by_id[evidence_id]) for evidence_id in selected_ids if evidence_id in by_id]
        if selected_rows:
            return selected_rows
    return candidates_for_question(candidates, qid)


def _truncate_text(text: Any, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _truncate_evidence_excerpt(text: Any, limit: int = 520) -> str:
    """Keep both the premise and conclusion of a long textbook paragraph."""

    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    head = max(1, int(limit * 0.56))
    tail = max(1, limit - head - 1)
    return value[:head].rstrip() + "…" + value[-tail:].lstrip()


def _knowledge_points_by_evidence_id(evidence_selection: dict[str, Any] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not evidence_selection:
        return out
    for point in evidence_selection.get("knowledge_points", []) or []:
        if not isinstance(point, dict):
            continue
        name = str(point.get("knowledge_point") or "").strip()
        if not name:
            continue
        for raw in point.get("selected_evidence_ids", []) or []:
            evidence_id = str(raw or "").strip()
            if not evidence_id:
                continue
            out.setdefault(evidence_id, [])
            if name not in out[evidence_id]:
                out[evidence_id].append(name)
    return out


def evidence_for_answer_prompt(
    evidence: list[dict[str, Any]],
    evidence_selection: dict[str, Any] | None = None,
    *,
    target_count: int | None = None,
) -> list[dict[str, Any]]:
    """Compact evidence for model generation while preserving full evidence for final citations."""
    if not evidence:
        return []
    target = target_count or answer_generation_evidence_target_count()
    target = max(1, target)
    by_point = _knowledge_points_by_evidence_id(evidence_selection)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    if evidence_selection:
        by_id = {str(row.get("evidence_id") or ""): row for row in evidence}
        for point in evidence_selection.get("knowledge_points", []) or []:
            if len(selected) >= target:
                break
            if not isinstance(point, dict):
                continue
            for raw in point.get("selected_evidence_ids", []) or []:
                evidence_id = str(raw or "").strip()
                if not evidence_id or evidence_id in seen or evidence_id not in by_id:
                    continue
                selected.append(by_id[evidence_id])
                seen.add(evidence_id)
                break
    if len(selected) < target:
        for row in evidence:
            evidence_id = str(row.get("evidence_id") or "").strip()
            key = evidence_id or f"row-{len(seen)}"
            if key in seen:
                continue
            selected.append(row)
            seen.add(key)
            if len(selected) >= target:
                break
    compacted: list[dict[str, Any]] = []
    for row in selected:
        copy = dict(row)
        evidence_id = str(copy.get("evidence_id") or "")
        points = "、".join(by_point.get(evidence_id, [])[:3])
        location = _citation_locations([copy])
        section = str(copy.get("chapter_section") or "").strip()
        original = _truncate_evidence_excerpt(copy.get("evidence_text") or "", 520)
        summary_parts = []
        if points:
            summary_parts.append(f"相关考点：{points}")
        if section:
            summary_parts.append(f"章节：{section}")
        if location:
            summary_parts.append(f"位置：{location}")
        summary = "；".join(summary_parts) or "相关教材依据"
        copy["evidence_text"] = f"摘要：{summary}。少量原文摘录：{original}"
        copy["_full_evidence_text_chars"] = len(str(row.get("evidence_text") or ""))
        copy["_compacted_for_answer_generation"] = True
        compacted.append(copy)
    return compacted


def fragment_from_analysis_draft(
    draft: dict[str, Any],
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    evidence_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft = reconcile_calculation_reference_structure(
        normalize_nested_calculation_payload(draft)
    )
    draft, normalized_zero_based_formula_refs = normalize_formula_reference_base(draft)
    qid = str(question.get("question_id") or draft.get("question_id") or "").strip()
    formulas = _draft_formulas(draft, qid)
    has_calculation_part = is_calculation_question(question)
    depth_profile = build_answer_depth_profile(question)
    if has_calculation_part:
        for formula in formulas:
            formula["display"] = True
    figure_specs = _replace_formula_placeholders_in_value(_draft_figure_specs(draft), formulas)
    drawing_code_specs = _replace_formula_placeholders_in_value(_draft_drawing_code_specs(draft, qid), formulas)
    generated_images = [
        {
            "asset_id": str(item.get("asset_id") or "").strip(),
            "caption": str(item.get("caption") or "答案图示").strip() or "答案图示",
            "placement": str(item.get("placement") or "analysis").strip() or "analysis",
            "answer_unit_number": str(item.get("answer_unit_number") or "").strip(),
        }
        for item in (draft.get("generated_images") or [])
        if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
    ]
    stem = _clean_question_stem(question)
    raw_analysis_text = _normalize_multipart_text_layout(str(draft.get("analysis", "")), stem)
    draft_analysis_text = _replace_formula_placeholders_in_text(raw_analysis_text, formulas)
    raw_draft_answer = _normalize_multipart_text_layout(str(draft.get("answer") or "待复核"), stem)
    draft_answer = _replace_formula_placeholders_in_text(raw_draft_answer, formulas)
    top_answer = "见解析" if formulas and looks_like_formula(draft_answer) else draft_answer
    answer_summary = draft_answer
    if top_answer == "见解析" and formulas:
        contract = draft.get("calculation_contract") if isinstance(draft.get("calculation_contract"), dict) else {}
        result_quantities = [
            item for item in contract.get("result_quantities", []) or []
            if isinstance(item, dict) and str(item.get("name") or "").strip() and item.get("value") is not None
        ]
        if len(result_quantities) > 1:
            rendered_results = []
            for item in result_quantities:
                try:
                    rendered_value = f"{float(item['value']):g}"
                except (TypeError, ValueError):
                    rendered_value = str(item.get("value") or "").strip()
                unit = str(item.get("unit") or "").strip()
                rendered_results.append(
                    f"{str(item['name']).strip()}={rendered_value}{(' ' + unit) if unit else ''}"
                )
            answer_summary = "；".join(rendered_results)
        else:
            result_formula = next(
                (formula for formula in reversed(formulas) if str(formula.get("role") or "") == "result" and str(formula.get("latex") or "").strip()),
                None,
            )
            if result_formula:
                answer_summary = f"${str(result_formula['latex']).strip()}$"
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": qid,
        "section": question.get("section", draft.get("section", "")),
        "question_type": question.get("question_type") or "",
        "subquestions": _clean_question_source_markup(question.get("subquestions") or []),
        "number": question.get("number", draft.get("number", "")),
        "display_number": question.get("display_number", question.get("number", draft.get("number", ""))),
        "question_id_base": question.get("question_id_base", ""),
        "question_id_occurrence": question.get("question_id_occurrence", 0),
        "question_id_collision_count": question.get("question_id_collision_count", 0),
        "answer": top_answer,
        "answer_summary": answer_summary,
        "evidence_ids": [],
        "blocks": [],
        "formulas": formulas,
        "figure_specs": figure_specs,
        "drawing_code_specs": drawing_code_specs,
        "generated_images": generated_images,
        "warnings": _warning_items(draft.get("uncertainties"), formulas),
        "answer_units": _normalized_answer_units(draft, question),
        # Keep the normalized numerical ledger in the durable fragment.  It is
        # an internal review artifact; renderers ignore it, while later repair
        # and correctness gates no longer depend on the transient `_draft`.
        "calculation_contract": copy.deepcopy(draft.get("calculation_contract", {})),
        "_draft": {
            "schema_version": "answer_book.answer_draft.v1",
            "question_id": qid,
            "answer": draft_answer,
            "analysis": draft_analysis_text,
            "analysis_segments": _replace_formula_placeholders_in_value(draft.get("analysis_segments", []), formulas),
            "answer_units": _replace_formula_placeholders_in_value(draft.get("answer_units", []), formulas),
            "option_analysis": _replace_formula_placeholders_in_value(draft.get("option_analysis", {}), formulas),
            "steps": _replace_formula_placeholders_in_value(draft.get("steps", []), formulas),
            "formulas": draft.get("formulas", []),
            "figure_specs": figure_specs,
            "drawing_code_specs": drawing_code_specs,
            "generated_images": generated_images,
            "mistake_notes": _replace_formula_placeholders_in_value(draft.get("mistake_notes", []), formulas),
            "uncertainties": _replace_formula_placeholders_in_value(draft.get("uncertainties", []), formulas),
            "calculation_contract": draft.get("calculation_contract", {}),
            "answer_depth_profile": depth_profile,
        },
    }
    if normalized_zero_based_formula_refs:
        fragment["_meta"] = {
            **dict(fragment.get("_meta") or {}),
            "formula_reference_normalization": "zero_based_to_one_based",
        }
    selected_ids = _selected_ids_from_evidence_selection(evidence_selection)
    if selected_ids:
        available = {str(row.get("evidence_id") or "").strip() for row in evidence}
        fragment["evidence_ids"] = [evidence_id for evidence_id in selected_ids if evidence_id in available]
    else:
        fragment = include_confirmed_evidence_ids(fragment, evidence)
    attach_program_evidence_block(fragment, evidence, evidence_selection)
    answer_units = fragment.get("answer_units") if isinstance(fragment.get("answer_units"), list) else []
    unit_rows = _question_subquestion_rows(question)
    if not _is_effective_answer_text(fragment.get("answer")):
        unit_answer = _answer_from_answer_units(answer_units, unit_rows)
        if unit_answer:
            fragment["answer_summary"] = unit_answer
            fragment["answer"] = "见解析" if looks_like_formula(unit_answer) else unit_answer
    if answer_units and len(unit_rows) >= 2:
        analysis_segments, step_segments, used_formula_ids = _answer_unit_blocks(answer_units, unit_rows, formulas)
    elif answer_units and len(answer_units) == 1 and len(unit_rows) < 2:
        unit_answer = str(answer_units[0].get("answer") or "").strip()
        if _is_effective_answer_text(unit_answer):
            if not _is_effective_answer_text(top_answer):
                fragment["answer"] = unit_answer
                fragment["answer_summary"] = unit_answer
        analysis_segments, step_segments, used_formula_ids = _single_answer_unit_blocks(answer_units[0], formulas, stem)
    elif has_calculation_part:
        analysis_segments, used_formula_ids = _segments_from_inline_formula_text(
            raw_analysis_text,
            [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")],
        )
        step_segments = _calculation_step_segments(_group_calculation_steps_by_subquestion(draft.get("steps"), question), formulas)
    else:
        analysis_segments, used_formula_ids = _noncalculation_analysis_segments(draft, formulas, question)
        step_segments = []
    if analysis_segments:
        fragment["blocks"].append({"label": "解析", "segments": analysis_segments})
    option_text = _option_analysis_text(draft.get("option_analysis"))
    if option_text:
        option_segments, option_used_formula_ids = _inline_formula_segments_from_text(option_text, formulas)
        used_formula_ids.update(option_used_formula_ids)
        fragment["blocks"].append({"label": "选项分析", "segments": option_segments})
    if answer_units and len(unit_rows) >= 2:
        if step_segments:
            fragment["blocks"].append({"label": "解题步骤", "segments": step_segments})
    elif answer_units and len(answer_units) == 1 and len(unit_rows) < 2:
        if step_segments:
            fragment["blocks"].append({"label": "解题步骤", "segments": step_segments})
    elif has_calculation_part:
        grouped_steps = _group_calculation_steps_by_subquestion(draft.get("steps"), question)
        fragment["_draft"]["steps"] = _replace_formula_placeholders_in_value(grouped_steps, formulas)
        if step_segments:
            fragment["blocks"].append({"label": "解题步骤", "segments": step_segments})
    else:
        steps_text = _list_text(draft.get("steps"))
        if steps_text:
            step_segments, step_used_formula_ids = _inline_formula_segments_from_text(steps_text, formulas)
            used_formula_ids.update(step_used_formula_ids)
            fragment["blocks"].append({"label": "解题步骤", "segments": step_segments})
    mistake_text = strip_internal_repair_provenance(
        _strip_program_citation_text(_list_text(draft.get("mistake_notes")))
    )
    if mistake_text:
        formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
        mistake_segments, _ = _segments_from_inline_formula_text(mistake_text, formula_ids)
        used_formula_ids.update(seg.get("formula_id", "") for seg in mistake_segments if seg.get("type") == "formula_ref")
        fragment["blocks"].append({"label": "易错点及注意事项", "segments": mistake_segments})
    if not has_calculation_part and formulas:
        formula_ids = [str(formula.get("formula_id", "")) for formula in formulas if formula.get("formula_id")]
        unplaced_formula_ids = [formula_id for formula_id in formula_ids if formula_id not in used_formula_ids]
        if unplaced_formula_ids:
            formula_by_id = {str(formula.get("formula_id", "")): formula for formula in formulas}
            notation_formula_ids = [
                formula_id
                for formula_id in unplaced_formula_ids
                if _is_notation_formula(formula_by_id.get(formula_id, {}), question)
            ]
            pending_formula_ids = [formula_id for formula_id in unplaced_formula_ids if formula_id not in notation_formula_ids]
            if is_term_explanation_question(question) and pending_formula_ids:
                # The established term-explanation document contract renders
                # only the complete definition answer. Extra model formulas
                # that were never referenced by that answer are dead content,
                # not a user-visible review obligation.
                pending_set = set(pending_formula_ids)
                formulas[:] = [
                    formula
                    for formula in formulas
                    if str(formula.get("formula_id") or "") not in pending_set
                ]
                fragment["formulas"] = formulas
                pending_formula_ids = []
            if notation_formula_ids:
                label = "作图依据与符号" if _is_graphic_question_for_formula_policy(question) else "符号与单位说明"
                intro = (
                    "以下为本题作图使用的符号、标注或几何关系。"
                    if _is_graphic_question_for_formula_policy(question)
                    else "以下为本题涉及的符号或单位说明。"
                )
                fragment["blocks"].append(
                    {
                        "label": label,
                        "segments": [
                            {"type": "text", "text": intro},
                            *[{"type": "formula_ref", "formula_id": formula_id} for formula_id in notation_formula_ids],
                        ],
                    }
                )
            if pending_formula_ids:
                fragment["blocks"].append(
                    {
                        "label": "待复核公式",
                        "audience": "review",
                        "delivery_projection": {
                            "label": "补充公式",
                            "segment_types": ["formula_ref"],
                        },
                        "segments": [
                            {"type": "text", "text": "以下公式未能自然融入解析，请复核其必要性与放置位置。"},
                            *[{"type": "formula_ref", "formula_id": formula_id} for formula_id in pending_formula_ids],
                        ],
                    }
                )
                fragment["warnings"].append("存在未融入解析正文的公式，已列入待复核公式。")
    if not fragment["blocks"]:
        fragment["blocks"].append({"label": "解析", "segments": _text_segments("本题解析内容缺失，需复核。")})
    return demote_simple_symbol_formulas(fragment)


def has_bound_evidence(fragment: dict[str, Any]) -> bool:
    return any(str(x).strip() for x in fragment.get("evidence_ids", []) if x is not None)


def bind_top_evidence(
    fragment: dict[str, Any],
    evidence: list[dict[str, Any]],
    limit: int = 1,
    reason: str = "模型未主动绑定候选证据，程序按检索排序补充最相关教材证据。",
) -> dict[str, Any]:
    evidence_ids = [str(e.get("evidence_id")) for e in evidence[:limit] if e.get("evidence_id")]
    if not evidence_ids:
        return fragment
    fragment["evidence_ids"] = evidence_ids
    meta = dict(fragment.get("_meta") or {})
    meta["evidence_binding"] = {
        "strategy": "program_top_evidence",
        "reason": reason,
        "bound_evidence_ids": evidence_ids,
    }
    fragment["_meta"] = meta
    warnings = list(fragment.get("warnings", []))
    warnings.append(f"程序自动绑定最相关教材证据：{reason}")
    fragment["warnings"] = warnings
    return attach_program_evidence_block(fragment, evidence)


def explain_missing_evidence_binding(
    client: OpenAICompatibleClient,
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    fragment: dict[str, Any],
    model: str,
) -> str:
    if not evidence:
        return "未检索到可用候选证据。"
    messages = [
        {
            "role": "system",
            "content": "你是真题解析平台的证据绑定审计器。只能返回 JSON。",
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "explain_why_no_evidence_id_selected",
                    "question": question,
                    "candidate_evidence": evidence,
                    "generated_fragment_without_evidence": fragment,
                    "output_schema": {
                        "reason": "一句话说明为什么原答案没有主动选择候选证据",
                        "closest_evidence_id": "从候选 evidence_id 中选最接近的一条",
                    },
                    "hard_rules": [
                        "Return exactly one valid JSON object.",
                        "Do not add Markdown.",
                        "reason must be concise Chinese.",
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        with prompt_contract("exam.evidence_binding_audit"):
            data = client.chat_json_object(
                messages,
                model=model,
                max_tokens=DEFAULT_MODEL_MAX_TOKENS,
                task_stage="review",
                item_ids=[str(question.get("question_id") or question.get("number") or "")],
                enforce_context_budget=True,
            )
        reason = str(data.get("reason") or "").strip()
        closest = str(data.get("closest_evidence_id") or "").strip()
        if reason and closest:
            return f"{reason}；程序补用最接近证据 {closest}。"
        if reason:
            return reason
    except Exception:
        pass
    return "模型多次生成后仍未主动选择候选证据，程序按检索排序补充最相关教材证据。"


def demote_simple_symbol_formulas(fragment: dict[str, Any]) -> dict[str, Any]:
    formulas = list(fragment.get("formulas", []))
    demote: dict[str, str] = {}
    kept = []
    for formula in formulas:
        fid = str(formula.get("formula_id", ""))
        latex = str(formula.get("latex", "")).strip()
        display = bool(formula.get("display", False))
        if not display and re.fullmatch(r"[A-Za-z]", latex):
            demote[fid] = latex
        else:
            kept.append(formula)
    if not demote:
        return fragment
    for block in fragment.get("blocks", []):
        segments = []
        for seg in block.get("segments", []):
            if seg.get("type") == "formula_ref" and str(seg.get("formula_id", "")) in demote:
                segments.append({"type": "text", "text": demote[str(seg.get("formula_id", ""))]})
            else:
                segments.append(seg)
        block["segments"] = segments
    fragment["formulas"] = kept
    return fragment


_MAIN_MODEL_IMAGE_TOOL_RULES = [
    (
        "The generate_image tool is the only image-execution path for this model call. You alone decide from the "
        "complete question, answer intent, source visuals, and textbook evidence whether the final answer needs an image. "
        "Do not let question_type, drawing_generation_mode, a keyword rule, or the mere availability of the tool make that decision for you."
    ),
    (
        "If you decide an image is needed, call generate_image and inspect the returned pixels against the question and "
        "evidence before accepting it. Correct the prompt and call again when needed; reject an incorrect image."
    ),
    (
        "When a registered source image materially defines the requested visual, pass its exact task-local path in "
        "referenced_image_paths so the image model receives the original pixels through an image edit request. Omit "
        "both image selectors for a new image. To revise an image you just generated and inspected, use "
        "num_last_images_to_include. Never combine the two selectors and never pass more than five references."
    ),
    (
        "Satisfy every explicit user-facing deliverable in the question. A request to draw, plot, sketch, show a structure, "
        "or provide a diagram is required answer content rather than optional decoration; do not claim such a requirement "
        "is complete with text alone. Understand that requirement yourself from the full question, then use generate_image "
        "when an actual visual is required."
    ),
    (
        "When you accept a generated image, bind its inspected asset_id through generated_images and leave figure_specs "
        "and drawing_code_specs empty for that visual. Do not substitute a program-rendered specification or drawing code "
        "for the image tool. For a drawing answer unit, set generated_images[].answer_unit_number to that unit number."
    ),
    (
        "Do not return figure_specs or drawing_code_specs anywhere in this response. They are intentionally unavailable "
        "while the real image tool is attached."
    ),
    (
        "If you decide no image is needed, do not call generate_image, keep generated_images empty, and return the normal "
        "text JSON in the first model turn."
    ),
    DEFAULT_EDUCATIONAL_IMAGE_STYLE_RULE,
]

_PROGRAM_FIGURE_OUTPUT_KEYS = {"figure_specs", "drawing_code_specs"}
_PROGRAM_FIGURE_RULE_MARKERS = (
    "figure_specs",
    "drawing_code_specs",
    "drawing_generation_mode",
    "custom_diagram",
    "registered schema",
    "schema's required_fields",
    "schema_resolution",
    "render_decision.strategy",
)


def _without_program_figure_outputs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_program_figure_outputs(item)
            for key, item in value.items()
            if str(key) not in _PROGRAM_FIGURE_OUTPUT_KEYS
        }
    if isinstance(value, list):
        return [_without_program_figure_outputs(item) for item in value]
    return value


def _with_main_model_image_tool_contract(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make the live image tool authoritative without deciding whether it should run."""

    routed = [dict(message) for message in messages]
    for index in range(len(routed) - 1, -1, -1):
        message = routed[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        text_part_index: int | None = None
        if isinstance(content, str):
            user_text = content
        elif isinstance(content, list):
            text_part_index = next(
                (
                    part_index
                    for part_index, part in enumerate(content)
                    if isinstance(part, dict)
                    and part.get("type") in {"text", "input_text"}
                    and isinstance(part.get("text"), str)
                ),
                None,
            )
            if text_part_index is None:
                continue
            user_text = str(content[text_part_index].get("text") or "")
        else:
            continue
        try:
            payload = json.loads(user_text)
        except (TypeError, ValueError):
            break
        if not isinstance(payload, dict):
            break
        hard_rules = [
            str(rule)
            for rule in payload.get("hard_rules", [])
            if str(rule).strip()
            and not any(marker in str(rule) for marker in _PROGRAM_FIGURE_RULE_MARKERS)
        ]
        payload = _without_program_figure_outputs(payload)
        payload["hard_rules"] = [*hard_rules, *_MAIN_MODEL_IMAGE_TOOL_RULES]
        payload["image_tool_orchestration"] = "main_model_tool_loop"
        routed_text = json.dumps(payload, ensure_ascii=False)
        if text_part_index is None:
            routed[index] = {**message, "content": routed_text}
        else:
            routed_content = [dict(part) if isinstance(part, dict) else part for part in content]
            routed_content[text_part_index] = {**routed_content[text_part_index], "text": routed_text}
            routed[index] = {**message, "content": routed_content}
        break
    if routed and routed[0].get("role") == "system" and isinstance(routed[0].get("content"), str):
        routed[0] = {
            **routed[0],
            "content": routed[0]["content"]
            + " When generate_image is available, its tool-routing rules in the task payload override any earlier "
            "program-rendering preference; the main model still decides whether an image is needed.",
        }
    return routed


def generate_one_fragment(
    client: OpenAICompatibleClient,
    provider: ProviderConfig,
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    model: str,
    evidence_selection: dict[str, Any] | None = None,
    prompt_evidence: list[dict[str, Any]] | None = None,
    attempt_callback: Any | None = None,
    retries: int = 1,
    deadline_monotonic: float | None = None,
    tool_loop: ModelToolLoop | None = None,
    include_textbook_evidence: bool = True,
) -> tuple[dict[str, Any] | None, list[str]]:
    messages = build_answer_draft_prompt(
        question,
        prompt_evidence if prompt_evidence is not None else evidence,
        include_textbook_evidence=include_textbook_evidence,
    )
    if tool_loop is not None:
        messages = _with_main_model_image_tool_contract(messages)
    understanding = question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
    direct_visual_input = bool(
        needs_vision_model(question)
        and not understanding.get("vision_used")
        and provider_model_supports_vision(provider, model)
    )
    if direct_visual_input:
        messages = attach_question_visuals(messages, question)
    last_issues: list[str] = []
    data: dict[str, Any] | None = None
    max_tokens = structured_answer_max_tokens(provider, question)
    formula_repair_requested = False
    for attempt in range(retries + 1):
        assistant_content = ""
        thinking_mode = answer_generation_attempt_thinking_mode(provider, question, attempt)
        timeout_seconds = answer_generation_timeout_seconds(question, thinking_mode=thinking_mode)
        if deadline_monotonic is not None:
            remaining_seconds = deadline_monotonic - time.monotonic()
            if remaining_seconds <= 0:
                raise LLMError("question model-call budget exhausted")
            timeout_seconds = min(timeout_seconds, max(1, math.ceil(remaining_seconds)))
        try:
            agent_result = None
            with prompt_contract("exam.answer_draft_single"):
                if tool_loop is not None:
                    agent_result = tool_loop.run_json(
                        messages,
                        model=model,
                        max_tokens=max_tokens,
                        thinking=thinking_mode,
                        timeout=timeout_seconds,
                    )
                    data = agent_result.value
                else:
                    data = client.chat_json_object(
                        messages,
                        model=model,
                        max_tokens=max_tokens,
                        attempt_callback=attempt_callback,
                        attempts=1,
                        thinking=thinking_mode,
                        timeout=timeout_seconds,
                        task_stage="answer_generation",
                        required_evidence_refs=[str(item.get("evidence_id") or "") for item in (prompt_evidence if prompt_evidence is not None else evidence) if item.get("evidence_id")],
                        delivered_evidence_refs=[str(item.get("evidence_id") or "") for item in (prompt_evidence if prompt_evidence is not None else evidence) if item.get("evidence_id")],
                        item_ids=[str(question.get("question_id") or question.get("number") or "")],
                        enforce_context_budget=True,
                        response_model=AnswerDraftOutput,
                        validation_retries=1,
                    )
            assistant_content = json.dumps(data, ensure_ascii=False)
        except StructuredOutputError as exc:
            last_issues = [str(exc)]
            if attempt >= retries:
                break
            assistant_content = exc.content
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "repair_invalid_json_only",
                            "error": str(exc),
                            "hard_rules": [
                                "Return exactly one valid JSON object.",
                                "Do not add Markdown fences.",
                                "Do not add explanation before or after JSON.",
                                "Keep schema_version as answer_book.answer_draft.v1.",
                                "If a field is uncertain, use a warning string, not prose outside JSON.",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            continue
        except LLMError as exc:
            last_issues = [str(exc)]
            error_info = classify_provider_error(
                exc,
                status_code=getattr(exc, "status_code", None),
                transport_phase=str(getattr(exc, "transport_phase", "") or ""),
                retry_after_seconds=getattr(exc, "retry_after_seconds", None),
            )
            if not error_info.retryable or bool(getattr(exc, "partial_output_received", False)):
                break
            continue
        data = fragment_from_analysis_draft(data, question, evidence, evidence_selection)
        data = promote_inline_reactions(data)
        data = promote_inline_mathematical_expressions(data)
        if agent_result is not None:
            data.setdefault("_meta", {})["image_tool_loop"] = {
                "steps": agent_result.steps,
                "tool_calls": agent_result.tool_calls,
                "generated_artifacts": agent_result.generated_artifacts,
                "tool_event_log": getattr(agent_result, "tool_event_log", ""),
            }
        if looks_like_formula(str(data.get("answer", ""))) and data.get("formulas"):
            data["answer"] = "见解析"
        syntax_issues = validate_v4_answer_fragment(data)
        semantic_issues = semantic_generation_issues(question, data)
        if semantic_issues == ["calculation_missing_formula"] and formula_repair_requested:
            data = add_review_flag(
                data,
                "formula_absence_after_retry",
                "按题型应有公式，模型二次生成仍未给出公式，已特殊放行并进入存疑题目审查文档。",
            )
            semantic_issues = semantic_generation_issues(question, data, allow_formula_absence_after_retry=True)
        issues = syntax_issues + semantic_issues
        if not issues and (has_bound_evidence(data) or not evidence):
            retry_report = getattr(client, "last_json_retry_report", {})
            success_attempts = [item for item in retry_report.get("attempts", []) if not item.get("error")]
            actual_model = str((success_attempts[-1].get("model") if success_attempts else model) or model)
            meta = dict(data.get("_meta") or {})
            meta.update(
                {
                    "provider": provider.name,
                    "model": actual_model,
                    "attempt": attempt + 1,
                    "direct_visual_input": direct_visual_input,
                    "llm_retry": retry_report,
                }
            )
            data["_meta"] = meta
            return data, []
        if semantic_issues == ["calculation_missing_formula"] and not formula_repair_requested:
            last_issues = semantic_issues
            formula_repair_requested = True
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "repair_calculation_formula_objects",
                            "validation_issues": semantic_issues,
                            "hard_rules": [
                                "This calculation-style question normally needs key relation formulas.",
                                "Regenerate the answer_draft JSON once.",
                                "If a key relation formula is actually needed, put it in formulas array.",
                                "If no formula is needed for this specific question, keep formulas empty and explain why in uncertainties.",
                                "Return exactly one valid JSON object.",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            continue
        if not issues and evidence and not has_bound_evidence(data):
            last_issues = ["evidence_ids is empty while retrieval candidates exist"]
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "repair_missing_evidence_binding",
                            "validation_issues": last_issues,
                            "available_evidence_ids": [str(row.get("evidence_id")) for row in evidence if row.get("evidence_id")],
                            "hard_rules": [
                                "Do not add evidence_id. The program will merge confirmed textbook references.",
                                "Repair only the answer, analysis, option_analysis, steps, formulas, mistake_notes, and uncertainties fields.",
                                "Return exactly one valid JSON object.",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            continue
        last_issues = issues
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "repair_analysis_draft_to_pass_program_conversion",
                        "validation_issues": issues[:30],
                        "hard_rule": "Return answer_draft JSON. Put formulas in formulas array; calculation questions must include every key relation formula object; keep analysis as explanatory prose.",
                    },
                    ensure_ascii=False,
                ),
            }
        )
    if data is None:
        return None, last_issues or ["model output could not be repaired to valid JSON"]
    if evidence and not has_bound_evidence(data):
        reason = explain_missing_evidence_binding(client, question, evidence, data, model)
        data = bind_top_evidence(data, evidence, reason=reason)
        meta = dict(data.get("_meta") or {})
        meta.update({
            "provider": provider.name,
            "model": meta.get("model") or model,
            "recovered_by": "program_evidence_binding",
            "llm_retry": getattr(client, "last_json_retry_report", {}),
        })
        data["_meta"] = meta
        return data, []
    if isinstance(data, dict):
        retry_report = getattr(client, "last_json_retry_report", {})
        success_attempts = [item for item in retry_report.get("attempts", []) if not item.get("error")] if isinstance(retry_report, dict) else []
        actual_model = str((success_attempts[-1].get("model") if success_attempts else model) or model)
        meta = dict(data.get("_meta") or {})
        meta.update(
            {
                "provider": provider.name,
                "model": meta.get("model") or actual_model,
                "llm_retry": retry_report,
            }
        )
        data["_meta"] = meta
    return data, last_issues


def _single_prompt_payload(question: dict[str, Any], evidence: list[dict[str, Any]], *, include_textbook_evidence: bool = True) -> dict[str, Any]:
    messages = build_answer_draft_prompt(question, evidence, include_textbook_evidence=include_textbook_evidence)
    user_content = messages[-1].get("content") if messages else ""
    if isinstance(user_content, list):
        user_content = next((part.get("text", "") for part in user_content if isinstance(part, dict) and part.get("type") == "text"), "")
    return json.loads(str(user_content or "{}"))


def build_answer_batch_prompt(batch_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads = [
        _single_prompt_payload(
            item["question"],
            item.get("prompt_evidence") or item["evidence"],
            include_textbook_evidence=bool(item.get("include_textbook_evidence", True)),
        )
        for item in batch_items
    ]
    first = payloads[0] if payloads else {}
    batch_payload = {
        "task": "generate_question_analysis_draft_batch",
        "analysis_profile": first.get("analysis_profile", "evidence_backed"),
        "answer_content_quality_requirements": first.get("answer_content_quality_requirements", {}),
        "hard_rules": [
            *(first.get("hard_rules") or []),
            "Return exactly one valid JSON object with an items array.",
            "items must contain exactly one answer_draft object for each input question.",
            "Every item.question_id must exactly match one input question_id.",
            "Do not merge questions, do not omit questions, and do not let evidence or formulas from one question affect another.",
        ],
        "output_schema": {
            "items": [first.get("output_schema_example", {})],
        },
        "questions": [
            {
                "question_id": payload.get("question", {}).get("question_id", ""),
                "question": payload.get("question", {}),
                "answer_depth_profile": payload.get("answer_depth_profile", {}),
                **({"textbook_content": payload.get("textbook_content", [])} if "textbook_content" in payload else {}),
            }
            for payload in payloads
        ],
    }
    return ensure_generation_image_label_language_requirement([
        {
            "role": "system",
            "content": (
                "你是专业考研题目解析教师。你要批量生成多个短题解析草稿，不得使用或输出教材依据，只输出一个合法 JSON object。"
                if first.get("analysis_profile") == "question_only"
                else "你是专业考研真题解析教师。你要批量生成多个短题解析草稿，只输出一个合法 JSON object，不要输出 Markdown 或 JSON 之外的任何文字。"
            ),
        },
        {"role": "user", "content": json.dumps(batch_payload, ensure_ascii=False)},
    ])


def _batch_prompt_estimated_tokens(batch_items: list[dict[str, Any]]) -> int:
    return _rough_token_estimate(build_answer_batch_prompt(batch_items))


def _extract_batch_drafts(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "answers", "drafts", "fragments"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _fragment_from_batch_draft(
    draft: dict[str, Any],
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    evidence_selection: dict[str, Any] | None,
    *,
    provider: ProviderConfig,
    model: str,
    retry_report: dict[str, Any],
    batch_question_ids: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    data = fragment_from_analysis_draft(draft, question, evidence, evidence_selection)
    data = promote_inline_reactions(data)
    data = promote_inline_mathematical_expressions(data)
    if looks_like_formula(str(data.get("answer", ""))) and data.get("formulas"):
        data["answer"] = "见解析"
    issues = validate_v4_answer_fragment(data) + semantic_generation_issues(question, data)
    if not issues and evidence and not has_bound_evidence(data):
        issues.append("evidence_ids is empty while retrieval candidates exist")
    if issues:
        return None, issues
    success_attempts = [item for item in retry_report.get("attempts", []) if not item.get("error")]
    actual_model = str((success_attempts[-1].get("model") if success_attempts else model) or model)
    data["_meta"] = {
        "provider": provider.name,
        "model": actual_model,
        "attempt": 1,
        "batched": True,
        "batch_question_ids": batch_question_ids,
        "llm_retry": retry_report,
    }
    return data, []


def generate_batch_fragments(
    client: OpenAICompatibleClient,
    provider: ProviderConfig,
    batch_items: list[dict[str, Any]],
    model: str,
    attempt_callback: Any | None = None,
    tool_loop: ModelToolLoop | None = None,
) -> list[dict[str, Any]]:
    messages = build_answer_batch_prompt(batch_items)
    if tool_loop is not None:
        messages = _with_main_model_image_tool_contract(messages)
    thinking_mode = answer_generation_thinking_mode(provider)
    max_tokens = min(
        STRUCTURED_ANSWER_MAX_TOKENS,
        sum(structured_answer_max_tokens(provider, item["question"]) for item in batch_items),
    )
    agent_result = None
    with prompt_contract("exam.answer_draft_batch"):
        if tool_loop is not None:
            agent_result = tool_loop.run_json(
                messages,
                model=model,
                max_tokens=max_tokens,
                thinking=thinking_mode,
                timeout=answer_generation_timeout_seconds(thinking_mode=thinking_mode),
            )
            raw = agent_result.value
        else:
            raw = client.chat_json_object(
                messages,
                model=model,
                max_tokens=max_tokens,
                attempt_callback=attempt_callback,
                attempts=1,
                timeout=answer_generation_timeout_seconds(thinking_mode=thinking_mode),
                thinking=thinking_mode,
                task_stage="answer_generation",
                item_ids=[str(item["question"].get("question_id") or "") for item in batch_items],
                enforce_context_budget=True,
                response_model=AnswerDraftBatchOutput,
                validation_retries=1,
            )
    drafts = _extract_batch_drafts(raw)
    drafts_by_qid = {str(item.get("question_id") or "").strip(): item for item in drafts if str(item.get("question_id") or "").strip()}
    batch_qids = [str(item["question"].get("question_id") or "").strip() for item in batch_items]
    retry_report = getattr(client, "last_json_retry_report", {})
    results: list[dict[str, Any]] = []
    for item in batch_items:
        question = item["question"]
        qid = str(question.get("question_id") or "").strip()
        draft = drafts_by_qid.get(qid)
        if not draft:
            results.append({"question_id": qid, "fragment": None, "issues": ["batch output missing this question_id"]})
            continue
        fragment, issues = _fragment_from_batch_draft(
            draft,
            question,
            item["evidence"],
            item.get("evidence_selection"),
            provider=provider,
            model=model,
            retry_report=retry_report,
            batch_question_ids=batch_qids,
        )
        if fragment is not None and agent_result is not None:
            fragment.setdefault("_meta", {})["image_tool_loop"] = {
                "steps": agent_result.steps,
                "tool_calls": agent_result.tool_calls,
                "generated_artifacts": agent_result.generated_artifacts,
                "tool_event_log": getattr(agent_result, "tool_event_log", ""),
            }
        results.append({"question_id": qid, "fragment": fragment, "issues": issues})
    return results


def generate_answer_fragments(
    structured_exam: dict[str, Any],
    candidates: list[EvidenceCandidate],
    provider: ProviderConfig,
    model: str,
    output_json: Path,
    allow_fallback: bool = False,
    progress_json: Path | None = None,
    evidence_selections: dict[str, dict[str, Any]] | None = None,
    reusable_fragments: dict[str, dict[str, Any]] | None = None,
    image_provider: ProviderConfig | None = None,
    image_model: str = "",
    include_textbook_evidence: bool = True,
) -> GenerationResult:
    fragments: list[dict[str, Any]] = []
    answer_drafts: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    recovery_events: list[dict[str, Any]] = []
    model_token_feedback: list[dict[str, Any]] = []
    fallback_count = 0
    questions = list(structured_exam.get("items", []))
    question_ids = [str(question.get("question_id") or "") for question in questions]
    reusable_fragments = {
        str(qid): copy.deepcopy(fragment)
        for qid, fragment in (reusable_fragments or {}).items()
        if str(qid) in question_ids and isinstance(fragment, dict)
    }
    prior_drafts_path = output_json.parent / "answer_drafts.json"
    if reusable_fragments and prior_drafts_path.exists():
        try:
            prior_drafts_data = json.loads(prior_drafts_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            prior_drafts_data = {}
        prior_drafts = prior_drafts_data.get("drafts") if isinstance(prior_drafts_data, dict) else []
        answer_drafts = [
            copy.deepcopy(draft)
            for draft in prior_drafts or []
            if isinstance(draft, dict)
            and str(draft.get("question_id") or "") in reusable_fragments
        ]
    pending_questions = [
        question
        for question in questions
        if str(question.get("question_id") or "") not in reusable_fragments
    ]
    reused_fragment_count = len(reusable_fragments)
    max_workers = answer_generation_worker_count()
    parallel_enabled = max_workers > 1 and len(questions) > 1
    image_tool_enabled = bool(
        image_provider is not None
        and getattr(image_provider, "api_key", "")
        and str(image_model or getattr(image_provider, "image_model", "") or "").strip()
        and provider_supports_image_generation(image_provider)
        and tool_loop_supported(OpenAICompatibleClient(provider), provider, model)
    )
    image_tool_route_requested = bool(image_provider is not None and str(image_model or "").strip())
    if image_tool_route_requested and not image_tool_enabled:
        raise ValueError(
            "main-model image tool route was requested but could not be initialized; "
            "the answer generator will not fall back to the legacy figure pipeline"
        )
    batch_enabled = answer_generation_batch_enabled()
    batch_size = answer_generation_batch_size()
    batch_token_budget = answer_generation_batch_token_budget()
    evidence_target_count = answer_generation_evidence_target_count()
    started_at = time.time()
    completed_counter = {"value": 0}
    active_progress: dict[str, Any] = {}
    progress_events: list[dict[str, Any]] = []

    def write_progress(status: str, completed: int, question: dict[str, Any] | None = None) -> None:
        if progress_json is None:
            return
        elapsed_seconds = max(0, int(time.time() - started_at))
        payload = {
            "stage": "answer_generation",
            "status": status,
            "total": len(questions),
            "completed": completed,
            "current_question_id": str(question.get("question_id", "")) if question else "",
            "current_number": str(question.get("number", "")) if question else "",
            "fragment_count": completed,
            "issue_count": sum(len(x.get("issues", [])) for x in all_issues),
            "max_workers": max_workers,
            "parallel_enabled": parallel_enabled,
            "batch_enabled": batch_enabled,
            "batch_size": batch_size,
            "batch_token_budget": batch_token_budget,
            "evidence_target_count": evidence_target_count,
            "reused_fragment_count": reused_fragment_count,
            "elapsed_seconds": elapsed_seconds,
            "elapsed_text": _format_elapsed(elapsed_seconds),
            "active": dict(active_progress),
            "recent_events": progress_events[-12:],
        }
        progress_json.parent.mkdir(parents=True, exist_ok=True)
        progress_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_progress_event(question: dict[str, Any], status: str, detail: dict[str, Any]) -> None:
        event = {
            "time": time.strftime("%H:%M:%S"),
            "status": status,
            "question_id": str(question.get("question_id") or ""),
            "number": str(question.get("number") or ""),
            "model": detail.get("model"),
            "strategy": detail.get("strategy"),
            "thinking": detail.get("thinking"),
            "max_tokens": detail.get("max_tokens"),
            "error": str(detail.get("error") or "")[:240],
        }
        progress_events.append(event)
        active_progress.clear()
        active_progress.update({key: value for key, value in event.items() if value not in {None, ""}})
        active_progress["elapsed_seconds"] = max(0, int(time.time() - started_at))
        active_progress["elapsed_text"] = _format_elapsed(active_progress["elapsed_seconds"])
        write_progress("running", completed_counter["value"], question)

    def generate_question_inner(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        _, question = item
        qid = str(question.get("question_id", ""))
        evidence_selection = (evidence_selections or {}).get(qid)
        evidence = evidence_for_answer_generation(candidates, qid, evidence_selection)
        prompt_evidence = evidence_for_answer_prompt(evidence, evidence_selection, target_count=evidence_target_count)
        active_progress.clear()
        active_progress.update(
            {
                "status": "preparing",
                "question_id": qid,
                "number": str(question.get("number") or ""),
                "model": model,
                "full_evidence_count": len(evidence),
                "prompt_evidence_count": len(prompt_evidence),
            }
        )
        write_progress("running", completed_counter["value"], question)
        question_deadline = time.monotonic() + answer_generation_question_budget_seconds()
        model_candidates = _answer_model_candidates_for_question(provider, model, question)[
            : answer_generation_max_model_candidates()
        ]
        local_client = OpenAICompatibleClient(provider)
        if image_tool_enabled:
            # A model retry is only equivalent when the candidate can execute
            # the same native image-tool loop. Never turn a user-selected
            # main-model route into a silent text-only fallback.
            model_candidates = _equivalent_tool_loop_model_candidates(
                local_client,
                provider,
                model_candidates,
            )
        fragment = None
        issues: list[str] = []
        used_model = model_candidates[0] if model_candidates else model
        local_recovery_events: list[dict[str, Any]] = []
        local_token_feedback: list[dict[str, Any]] = []
        local_fallback_count = 0
        if not model_candidates:
            issues = [
                (
                    f"no equivalent native image-tool model is registered for provider {provider.name}"
                    if image_tool_enabled
                    else f"question requires vision model, but provider {provider.name} does not declare supports_vision and vision_model"
                )
            ]
        for candidate_model in model_candidates:
            if time.monotonic() >= question_deadline:
                issues = ["question model-call budget exhausted before another model fallback"]
                break
            try:
                tool_loop = None
                if image_tool_enabled and image_provider is not None:
                    artifact_store = ImageArtifactStore(output_json.parent / "agent_images" / qid)
                    image_tool = ImageGenerationTool(
                        image_provider,
                        image_model,
                        artifact_store,
                        reference_images=question.get("image_refs") or [],
                    )
                    tool_loop = ModelToolLoop(
                        local_client,
                        [image_tool],
                        artifact_store,
                        session_id=f"answer_generation:{qid}",
                    )

                def attempt_callback(status: str, report: dict[str, Any]) -> None:
                    record_progress_event(question, status, report)

                try:
                    fragment, issues = generate_one_fragment(
                        local_client,
                        provider,
                        question,
                        evidence,
                        candidate_model,
                        evidence_selection=evidence_selection,
                        prompt_evidence=prompt_evidence,
                        attempt_callback=attempt_callback,
                        deadline_monotonic=question_deadline,
                        tool_loop=tool_loop,
                        include_textbook_evidence=include_textbook_evidence,
                    )
                except TypeError as exc:
                    if "prompt_evidence" not in str(exc) and "attempt_callback" not in str(exc):
                        raise
                    fragment, issues = generate_one_fragment(
                        local_client,
                        provider,
                        question,
                        evidence,
                        candidate_model,
                        evidence_selection=evidence_selection,
                        include_textbook_evidence=include_textbook_evidence,
                    )
            except LLMError as exc:
                fragment = None
                issues = [str(exc)]
            if fragment is not None and not issues:
                used_model = str((fragment.get("_meta") or {}).get("model") or candidate_model)
                retry_report = (fragment.get("_meta") or {}).get("llm_retry")
                if isinstance(retry_report, dict):
                    local_token_feedback.append({"question_id": qid, "stage": "answer_generation", **retry_report})
                if used_model != model:
                    local_recovery_events.append({"question_id": qid, "strategy": "model_retry", "model": used_model})
                break

        local_issues = []
        draft_payload = None
        if issues or fragment is None:
            if fragment is None and not issues:
                issues = ["model did not return an answer fragment"]
            local_issues.append({"question_id": qid, "issues": issues})
            if isinstance(fragment, dict):
                draft_payload = fragment.pop("_draft", None)
                retry_report = (fragment.get("_meta") or {}).get("llm_retry")
                if isinstance(retry_report, dict):
                    local_token_feedback.append({"question_id": qid, "stage": "answer_generation", **retry_report})
                meta = dict(fragment.get("_meta") or {})
                meta.update(
                    {
                        "provider": provider.name,
                        "model": used_model,
                        "recovered_by": "review_candidate_preserved",
                        "review_candidate_issues": issues[:20],
                    }
                )
                fragment["_meta"] = meta
                add_review_flag(fragment, "answer_generation_review_candidate", "模型生成内容存在审查问题，已保留当前候选内容进入正式文件并在审查记录中标记。")
                fragment["_review_candidate_issues"] = issues[:20]
                local_recovery_events.append({"question_id": qid, "strategy": "review_candidate_preserved", "issues": issues[:5]})
            else:
                fallback = fallback_fragment(question, evidence, "模型结构化解析生成失败：" + "；".join(issues[:5]))
                fallback = attach_program_evidence_block(fallback, evidence, evidence_selection)
                fallback["_meta"] = {"provider": provider.name, "model": used_model, "recovered_by": "failure_placeholder"}
                fragment = fallback
                local_fallback_count += 1
                local_recovery_events.append({"question_id": qid, "strategy": "failure_placeholder", "issues": issues[:5]})
        else:
            draft_payload = fragment.pop("_draft", None)
            if used_model != model:
                meta = dict(fragment.get("_meta") or {})
                meta.update({"provider": provider.name, "model": used_model, "recovered_by": "model_retry"})
                fragment["_meta"] = meta
        return {
            "question": question,
            "fragment": fragment,
            "draft": draft_payload if isinstance(draft_payload, dict) else None,
            "issues": local_issues,
            "recovery_events": local_recovery_events,
            "fallback_count": local_fallback_count,
            "model_token_feedback": local_token_feedback,
        }

    def generate_question(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        question = item[1]
        active_item = str(question.get("question_id") or question.get("number") or "")
        with model_call_context(stage="answer_generation", active_item=active_item):
            return generate_question_inner(item)

    def prepared_batch_item(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        _, question = item
        qid = str(question.get("question_id", ""))
        evidence_selection = (evidence_selections or {}).get(qid)
        evidence = evidence_for_answer_generation(candidates, qid, evidence_selection)
        prompt_evidence = evidence_for_answer_prompt(evidence, evidence_selection, target_count=evidence_target_count)
        return {
            "question": question,
            "evidence": evidence,
            "prompt_evidence": prompt_evidence,
            "evidence_selection": evidence_selection,
            "include_textbook_evidence": include_textbook_evidence,
        }

    def generate_batch_question_results(items: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
        batch_items = [prepared_batch_item(item) for item in items]
        estimated_tokens = _batch_prompt_estimated_tokens(batch_items)
        if len(batch_items) < 2 or estimated_tokens > batch_token_budget:
            return [generate_question(item) for item in items]
        batch_qids = [str(item["question"].get("question_id") or "") for item in batch_items]
        local_client = OpenAICompatibleClient(provider)
        batch_tool_loop = None
        if image_tool_enabled and image_provider is not None:
            batch_scope = hashlib.sha256("|".join(batch_qids).encode("utf-8")).hexdigest()[:16]
            artifact_store = ImageArtifactStore(output_json.parent / "agent_images" / f"batch_{batch_scope}")
            batch_reference_images = [
                image_ref
                for item in batch_items
                for image_ref in (item["question"].get("image_refs") or [])
            ]
            image_tool = ImageGenerationTool(
                image_provider,
                image_model,
                artifact_store,
                reference_images=batch_reference_images,
            )
            batch_tool_loop = ModelToolLoop(
                local_client,
                [image_tool],
                artifact_store,
                session_id="answer_generation:batch",
            )
        try:
            def attempt_callback(status: str, report: dict[str, Any]) -> None:
                question = batch_items[0]["question"] if batch_items else {}
                record_progress_event(question, status, {**report, "batch_size": len(batch_items)})

            with model_call_context(stage="answer_generation", active_item=",".join(batch_qids)):
                batch_results = generate_batch_fragments(
                    local_client,
                    provider,
                    batch_items,
                    model,
                    attempt_callback=attempt_callback,
                    tool_loop=batch_tool_loop,
                )
            by_qid = {str(item.get("question_id") or ""): item for item in batch_results}
            if set(by_qid) != set(batch_qids):
                raise RuntimeError("batch output question_id set does not match input")
            out: list[dict[str, Any]] = []
            for item in batch_items:
                question = item["question"]
                qid = str(question.get("question_id") or "")
                batch_result = by_qid.get(qid) or {}
                fragment = batch_result.get("fragment")
                issues = list(batch_result.get("issues") or [])
                if not isinstance(fragment, dict) or issues:
                    raise RuntimeError(f"{qid}: {'; '.join(issues or ['batch fragment missing'])}")
                draft_payload = fragment.pop("_draft", None)
                retry_report = (fragment.get("_meta") or {}).get("llm_retry")
                out.append(
                    {
                        "question": question,
                        "fragment": fragment,
                        "draft": draft_payload if isinstance(draft_payload, dict) else None,
                        "issues": [],
                        "recovery_events": [],
                        "fallback_count": 0,
                        "model_token_feedback": [{"question_id": qid, "stage": "answer_generation", **retry_report}] if isinstance(retry_report, dict) else [],
                    }
                )
            return out
        except Exception as exc:
            out = []
            for item in items:
                result = generate_question(item)
                result.setdefault("recovery_events", []).append(
                    {
                        "question_id": str(item[1].get("question_id") or ""),
                        "strategy": "batch_fallback_to_single",
                        "batch_question_ids": batch_qids,
                        "issues": [str(exc)[:500]],
                    }
                )
                out.append(result)
            return out

    def build_work_units() -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        buffer: list[tuple[int, dict[str, Any]]] = []
        buffer_key: tuple[str, str, str] | None = None

        def flush_buffer() -> None:
            nonlocal buffer, buffer_key
            if not buffer:
                return
            if len(buffer) >= 2:
                units.append({"kind": "batch", "items": buffer})
            else:
                units.append({"kind": "single", "items": buffer})
            buffer = []
            buffer_key = None

        for item in list(enumerate(pending_questions, start=1)):
            question = item[1]
            if not batch_enabled or not _is_microbatch_candidate(question):
                flush_buffer()
                units.append({"kind": "single", "items": [item]})
                continue
            key = _batch_group_key(question)
            if buffer and (buffer_key != key or len(buffer) >= batch_size):
                flush_buffer()
            buffer.append(item)
            buffer_key = key
        flush_buffer()
        return units

    def generate_unit(unit: dict[str, Any]) -> dict[str, Any]:
        items = list(unit.get("items") or [])
        if unit.get("kind") == "batch":
            results = generate_batch_question_results(items)
        else:
            results = [generate_question(item) for item in items]
        return {
            "kind": unit.get("kind"),
            "results": results,
            "question": results[-1].get("question") if results else None,
        }

    completed_results = reused_fragment_count
    completed_counter["value"] = reused_fragment_count

    def on_unit_complete(_index: int, _item: dict[str, Any], result: dict[str, Any]) -> None:
        nonlocal completed_results
        unit_results = list(result.get("results") or [])
        completed_results += len(unit_results)
        completed_counter["value"] = completed_results
        for item_result in unit_results:
            all_issues.extend(item_result.get("issues") or [])
        write_progress("running", completed_results, result.get("question") or {})

    write_progress("running", reused_fragment_count)
    work_units = build_work_units()
    results = run_limited_concurrent(
        work_units,
        generate_unit,
        max_workers=max_workers,
        on_complete=on_unit_complete,
    )
    all_issues = []
    flat_results = [item_result for unit_result in results for item_result in list(unit_result.get("results") or [])]
    generated_by_id: dict[str, dict[str, Any]] = {}
    for result in flat_results:
        generated_by_id[str(result["fragment"].get("question_id") or "")] = result["fragment"]
        if result.get("draft"):
            answer_drafts.append(result["draft"])
        all_issues.extend(result.get("issues") or [])
        recovery_events.extend(result.get("recovery_events") or [])
        model_token_feedback.extend(result.get("model_token_feedback") or [])
        fallback_count += int(result.get("fallback_count") or 0)
    fragments = [
        copy.deepcopy(reusable_fragments[qid]) if qid in reusable_fragments else generated_by_id[qid]
        for qid in question_ids
        if qid in reusable_fragments or qid in generated_by_id
    ]
    issue_count = sum(len(x.get("issues", [])) for x in all_issues)
    completion = generation_completion_state(
        len(questions), len(fragments), issue_count=issue_count, fallback_count=fallback_count
    )
    output = {
        "schema_version": "answer_book.answer_fragments.v4",
        "source_contract": answer_source_contract(structured_exam),
        "provider": provider.name,
        "model": model,
        "image_generation_orchestration": "main_model_tool_loop" if image_tool_enabled else "legacy_figure_pipeline",
        "fragments": fragments,
        "issues": all_issues,
        "recovery_events": recovery_events,
        "recovered_count": len(recovery_events),
        "fallback_count": fallback_count,
        "delivery_readiness": completion["delivery_readiness"],
        "coverage_complete": completion["coverage_complete"],
        "model_token_feedback": model_token_feedback,
        "concurrency": {
            "max_workers": max_workers,
            "parallel_enabled": parallel_enabled,
            "batch_enabled": batch_enabled,
            "batch_size": batch_size,
            "batch_token_budget": batch_token_budget,
            "work_unit_count": len(work_units),
            "batch_unit_count": sum(1 for unit in work_units if unit.get("kind") == "batch"),
            "reused_fragment_count": reused_fragment_count,
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    drafts_output = {
        "schema_version": "answer_book.answer_drafts.v1",
        "provider": provider.name,
        "model": model,
        "drafts": answer_drafts,
    }
    (output_json.parent / "answer_drafts.json").write_text(json.dumps(drafts_output, ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress("completed" if not all_issues else "completed_with_issues", len(fragments))
    return GenerationResult(
        # A complete candidate must continue into the local quality-governance
        # and repair stages. Issues/fallbacks lower its delivery tier; they do
        # not discard otherwise usable work and force a costly restart.
        ok=completion["ok"],
        question_count=len(questions),
        fragment_count=len(fragments),
        issue_count=issue_count,
        output_json=str(output_json),
        recovered_count=len(recovery_events),
        fallback_count=fallback_count,
        max_workers=max_workers,
        parallel_enabled=parallel_enabled,
        reused_fragment_count=reused_fragment_count,
        review_required=completion["review_required"],
    )


def write_demo_fragments(structured_exam: dict[str, Any], candidates: list[EvidenceCandidate], output_json: Path) -> GenerationResult:
    fragments = []
    review_count = 0
    for question in structured_exam.get("items", []):
        qid = str(question.get("question_id", ""))
        evidence = candidates_for_question(candidates, qid)
        fragment = attach_program_evidence_block(
            fallback_fragment(question, evidence, "demo fragment; configure provider API key for real generation"),
            evidence,
        )
        if needs_vision_model(question):
            review_count += 1
            add_review_flag(
                fragment,
                "offline_visual_placeholder",
                "离线演示模式未调用视觉模型；已保留原图与待复核占位，不宣称该题解析完成。",
            )
            fragment.setdefault("_meta", {})["recovered_by"] = "offline_visual_placeholder"
        fragments.append(fragment)
    output = {
        "schema_version": "answer_book.answer_fragments.v4",
        "source_contract": answer_source_contract(structured_exam),
        "provider": "demo",
        "model": "demo",
        "fragments": fragments,
        "issues": (
            [{"code": "offline_visual_placeholders", "count": review_count}]
            if review_count
            else []
        ),
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return GenerationResult(
        True,
        len(structured_exam.get("items", [])),
        len(fragments),
        review_count,
        str(output_json),
        fallback_count=review_count,
        review_required=bool(review_count),
    )
