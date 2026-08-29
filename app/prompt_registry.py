from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from .paths import LOGS_DIR

PROMPT_REGISTRY_SCHEMA = "answer_book.prompt_registry.v1"
PROMPT_OBSERVATION_SCHEMA = "answer_book.prompt_observation.v1"


@dataclass(frozen=True)
class PromptContract:
    prompt_id: str
    version: str
    task_profiles: tuple[str, ...]
    section_order: tuple[str, ...]
    output_contract: str
    consumers: tuple[str, ...]
    disabled_sections_by_profile: tuple[tuple[str, tuple[str, ...]], ...]


_EXAM_GENERATION_SECTIONS = (
    "base_rules",
    "task_profile",
    "question",
    "textbook_evidence",
    "output_schema",
    "tool_instructions",
)
_PRACTICE_GENERATION_SECTIONS = (
    "base_rules",
    "task_profile",
    "source_evidence",
    "blueprint",
    "output_schema",
    "tool_instructions",
)
_EXAM_REPAIR_SECTIONS = (
    "base_rules",
    "task_profile",
    "question",
    "textbook_evidence",
    "previous_candidate",
    "current_issues",
    "output_schema",
    "tool_instructions",
)


def _contract(
    prompt_id: str,
    *,
    profiles: tuple[str, ...],
    sections: tuple[str, ...],
    output: str,
    consumers: tuple[str, ...],
    disabled: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> PromptContract:
    return PromptContract(
        prompt_id=prompt_id,
        version="1",
        task_profiles=profiles,
        section_order=sections,
        output_contract=output,
        consumers=consumers,
        disabled_sections_by_profile=disabled,
    )


_CONTRACTS = (
    _contract(
        "system.provider_connection_text",
        profiles=("system",),
        sections=("connectivity_probe", "output_schema"),
        output="json_object",
        consumers=("provider_connection_test",),
    ),
    _contract(
        "system.provider_connection_image",
        profiles=("system",),
        sections=("connectivity_probe", "image_request"),
        output="image",
        consumers=("provider_connection_test",),
    ),
    _contract(
        "exam.question_understanding",
        profiles=("exam", "question_only"),
        sections=("base_rules", "task_profile", "question", "visual_evidence", "output_schema"),
        output="json_object",
        consumers=("question_understanding",),
    ),
    _contract(
        "exam.knowledge_planning",
        profiles=("exam",),
        sections=("base_rules", "question", "visual_evidence", "output_schema"),
        output="json_object",
        consumers=("knowledge_planning",),
    ),
    _contract(
        "exam.evidence_selection",
        profiles=("exam",),
        sections=("base_rules", "question", "candidate_evidence", "visual_evidence", "output_schema"),
        output="json_object",
        consumers=("evidence_selection",),
    ),
    _contract(
        "exam.figure_schema_planning",
        profiles=("exam", "question_only"),
        sections=("base_rules", "question", "visual_evidence", "figure_schema", "output_schema"),
        output="json_object",
        consumers=("figure_schema_planning",),
    ),
    _contract(
        "exam.evidence_binding_audit",
        profiles=("exam",),
        sections=("base_rules", "question", "candidate_evidence", "answer_candidate", "output_schema"),
        output="json_object",
        consumers=("answer_generation",),
    ),
    _contract(
        "exam.answer_draft_single",
        profiles=("exam", "question_only"),
        sections=_EXAM_GENERATION_SECTIONS,
        output="json_object",
        consumers=("answer_generation", "answer_preview"),
        disabled=(("question_only", ("textbook_evidence",)),),
    ),
    _contract(
        "exam.answer_draft_batch",
        profiles=("exam", "question_only"),
        sections=_EXAM_GENERATION_SECTIONS,
        output="json_object",
        consumers=("answer_generation",),
        disabled=(("question_only", ("textbook_evidence",)),),
    ),
    _contract(
        "exam.answer_audit_repair",
        profiles=("exam", "question_only"),
        sections=_EXAM_REPAIR_SECTIONS,
        output="json_object",
        consumers=("audit_model_repair",),
        disabled=(("question_only", ("textbook_evidence",)),),
    ),
    _contract(
        "exam.answer_docx_repair",
        profiles=("exam", "question_only"),
        sections=_EXAM_REPAIR_SECTIONS[:-1],
        output="json_object",
        consumers=("docx_model_repair",),
        disabled=(("question_only", ("textbook_evidence",)),),
    ),
    _contract(
        "exam.selective_review",
        profiles=("exam", "question_only"),
        sections=("base_rules", "review_profile", "answer_candidates", "review_dimensions", "output_schema"),
        output="json_object",
        consumers=("selective_review",),
    ),
    _contract(
        "figure.drawing_code",
        profiles=("exam", "question_only"),
        sections=("base_rules", "question", "answer_candidate", "figure_spec", "output_schema"),
        output="text_or_json_object",
        consumers=("drawing_code", "figure_repair"),
    ),
    _contract(
        "figure.visual_review",
        profiles=("exam", "question_only"),
        sections=("base_rules", "question", "figure_asset", "review_dimensions", "output_schema"),
        output="json_object",
        consumers=("figure_quality_review",),
    ),
    _contract(
        "figure.direct_image_generation",
        profiles=("exam", "question_only"),
        sections=("question", "answer_candidate", "figure_spec", "image_constraints"),
        output="image",
        consumers=("legacy_figure_generation",),
    ),
    _contract(
        "practice.source_analysis",
        profiles=("practice", "knowledge"),
        sections=("base_rules", "task_profile", "source_material", "visual_evidence", "output_schema"),
        output="json_object",
        consumers=("practice_analysis",),
    ),
    _contract(
        "practice.planning",
        profiles=("practice", "knowledge"),
        sections=("base_rules", "task_profile", "source_analysis", "user_constraints", "output_schema"),
        output="json_object",
        consumers=("practice_planning",),
    ),
    _contract(
        "practice.generation",
        profiles=("practice", "knowledge"),
        sections=_PRACTICE_GENERATION_SECTIONS,
        output="json_object",
        consumers=("practice_generation",),
    ),
    _contract(
        "practice.semantic_review",
        profiles=("practice", "knowledge"),
        sections=("base_rules", "task_profile", "source_or_evidence", "answer_candidates", "review_dimensions", "output_schema"),
        output="json_object",
        consumers=("practice_semantic_review",),
    ),
    _contract(
        "practice.blueprint_revision",
        profiles=("practice", "knowledge"),
        sections=("base_rules", "source_analysis", "current_blueprint_item", "user_instruction", "output_schema"),
        output="json_object_in_text",
        consumers=("practice_blueprint_revision",),
    ),
    _contract(
        "practice.direct_image_generation",
        profiles=("practice", "knowledge"),
        sections=("exercise_requirement", "image_constraints"),
        output="image",
        consumers=("practice_legacy_image_generation",),
    ),
    _contract(
        "practice.figure_repair",
        profiles=("practice", "knowledge"),
        sections=("base_rules", "exercise", "figure_spec", "current_issues", "output_schema"),
        output="json_object",
        consumers=("practice_figure_repair",),
    ),
    _contract(
        "figure.tool_repair",
        profiles=("exam", "question_only"),
        sections=("base_rules", "question", "answer_candidate", "figure_asset", "current_issues", "tool_instructions", "output_schema"),
        output="json_object",
        consumers=("figure_tool_repair",),
    ),
    _contract(
        "tool.image_generation",
        profiles=("exam", "question_only", "practice", "knowledge"),
        sections=("main_model_tool_request", "reference_images", "image_constraints"),
        output="image",
        consumers=("main_model_image_tool",),
    ),
)

PROMPT_CONTRACTS = {contract.prompt_id: contract for contract in _CONTRACTS}
_PRACTICE_STAGE_CONTRACTS = {
    "source_analysis": "practice.source_analysis",
    "planning": "practice.planning",
    "generation": "practice.generation",
    "semantic_review": "practice.semantic_review",
}
_ACTIVE_PROMPT_CONTRACT: ContextVar[str] = ContextVar(
    "active_prompt_contract",
    default="",
)


@contextmanager
def prompt_contract(prompt_id: str) -> Iterator[None]:
    """Attach a shadow-only prompt identity without changing request content."""

    token = _ACTIVE_PROMPT_CONTRACT.set(str(prompt_id or "").strip())
    try:
        yield
    finally:
        _ACTIVE_PROMPT_CONTRACT.reset(token)


def current_prompt_contract_id() -> str:
    return _ACTIVE_PROMPT_CONTRACT.get()


def practice_prompt_contract_id(task_stage: str) -> str:
    return _PRACTICE_STAGE_CONTRACTS.get(str(task_stage or ""), "unregistered.practice")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _content_shape(value: Any) -> dict[str, Any]:
    image_count = 0
    text_bytes = 0
    tool_result_count = 0

    def visit(item: Any) -> None:
        nonlocal image_count, text_bytes, tool_result_count
        if isinstance(item, str):
            if item.startswith("data:image/"):
                image_count += 1
            else:
                text_bytes += len(item.encode("utf-8"))
            return
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        item_type = str(item.get("type") or "")
        if item_type in {"input_image", "image_url", "image"}:
            image_count += 1
        if item_type in {"function_call_output", "tool_result"} or item.get("role") == "tool":
            tool_result_count += 1
        for key, child in item.items():
            if key in {"image_url", "url"} and isinstance(child, str) and child.startswith("data:image/"):
                continue
            visit(child)

    visit(value)
    return {
        "text_bytes": text_bytes,
        "image_count": image_count,
        "tool_result_count": tool_result_count,
    }


def _transport_sections(payload: Any) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(payload, dict):
        return [], "unknown"
    if isinstance(payload.get("messages"), list):
        items = payload["messages"]
        transport_shape = "chat_messages"
    elif isinstance(payload.get("input"), list):
        items = payload["input"]
        transport_shape = "responses_input"
    else:
        items = []
        transport_shape = "image_prompt" if "prompt" in payload else "unknown"
    sections: list[dict[str, Any]] = []
    if isinstance(payload.get("system"), str):
        system = payload["system"]
        sections.append(
            {
                "index": 0,
                "role": "system",
                "content_sha256": _sha256(system),
                **_content_shape(system),
            }
        )
    offset = len(sections)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        content = item.get("content", item)
        sections.append(
            {
                "index": index + offset,
                "role": str(item.get("role") or item.get("type") or "unknown")[:40],
                "content_sha256": _sha256(content),
                **_content_shape(content),
            }
        )
    if not sections and "prompt" in payload:
        prompt = payload.get("prompt")
        sections.append(
            {
                "index": 0,
                "role": "image_prompt",
                "content_sha256": _sha256(prompt),
                **_content_shape(prompt),
            }
        )
    return sections, transport_shape


def observe_prompt_request(request_payload: Any) -> dict[str, Any]:
    """Return a content-free description of the exact transport prompt."""

    prompt_id = current_prompt_contract_id()
    contract = PROMPT_CONTRACTS.get(prompt_id)
    sections, transport_shape = _transport_sections(request_payload)
    tools = request_payload.get("tools") if isinstance(request_payload, dict) else None
    prompt_material = {
        key: request_payload[key]
        for key in (
            "messages",
            "input",
            "system",
            "prompt",
            "tools",
            "response_format",
            "text",
            "output_config",
        )
        if isinstance(request_payload, dict) and key in request_payload
    }
    return {
        "schema_version": PROMPT_OBSERVATION_SCHEMA,
        "mode": "shadow",
        "authority": "observation_only",
        "prompt_id": prompt_id or "unregistered",
        "registered": contract is not None,
        "version": contract.version if contract else "",
        "task_profiles": list(contract.task_profiles) if contract else [],
        "declared_section_order": list(contract.section_order) if contract else [],
        "disabled_sections_by_profile": (
            [
                {"profile": profile, "sections": list(sections)}
                for profile, sections in contract.disabled_sections_by_profile
            ]
            if contract
            else []
        ),
        "output_contract": contract.output_contract if contract else "unknown",
        "transport_shape": transport_shape,
        "observed_sections": sections,
        "message_count": len(sections),
        "tool_schema_count": len(tools) if isinstance(tools, list) else 0,
        "prompt_fingerprint_sha256": _sha256(prompt_material),
        "assembly_order_enforced": False,
        "behavior_changed": False,
    }


def _read_execution_intents(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("event_type") == "invocation.intent":
                    rows.append(event)
    except OSError:
        pass
    return rows


def build_prompt_registry_report(
    *,
    model_execution_ledger: Path = LOGS_DIR / "model_execution_events.jsonl",
) -> dict[str, Any]:
    intents = _read_execution_intents(model_execution_ledger)
    contract_counts: Counter[str] = Counter()
    transport_counts: Counter[str] = Counter()
    fingerprints: dict[str, set[str]] = defaultdict(set)
    observed_count = 0
    registered_count = 0
    unavailable_count = 0
    for event in intents:
        observation = event.get("prompt_observation")
        if not isinstance(observation, dict):
            continue
        observed_count += 1
        if observation.get("report_unavailable"):
            unavailable_count += 1
        prompt_id = str(observation.get("prompt_id") or "unregistered")
        contract_counts[prompt_id] += 1
        transport_counts[str(observation.get("transport_shape") or "unknown")] += 1
        fingerprint = str(observation.get("prompt_fingerprint_sha256") or "")
        if fingerprint:
            fingerprints[prompt_id].add(fingerprint)
        if observation.get("registered"):
            registered_count += 1
    catalog = [
        {
            **asdict(contract),
            "observed_invocation_count": contract_counts.get(contract.prompt_id, 0),
            "observed_prompt_variant_count": len(fingerprints.get(contract.prompt_id, set())),
        }
        for contract in _CONTRACTS
    ]
    unregistered_count = observed_count - registered_count - unavailable_count
    return {
        "schema_version": PROMPT_REGISTRY_SCHEMA,
        "mode": "shadow",
        "authority": "observation_only",
        "enforced": False,
        "behavior_changed": False,
        "catalog_count": len(catalog),
        "catalog": catalog,
        "execution_intent_count": len(intents),
        "prompt_observation_count": observed_count,
        "legacy_intent_without_prompt_observation_count": len(intents) - observed_count,
        "registered_observation_count": registered_count,
        "unregistered_observation_count": max(0, unregistered_count),
        "observation_unavailable_count": unavailable_count,
        "observed_contract_counts": dict(sorted(contract_counts.items())),
        "observed_transport_counts": dict(sorted(transport_counts.items())),
        "readiness": {
            "registry_authoritative": False,
            "reasons": [
                "business_modules_still_assemble_prompt_text",
                "declared_section_order_is_not_enforced",
                "legacy_execution_intents_lack_prompt_observation",
                "fixed_real_task_corpus_quality_review_not_completed",
            ],
        },
        "privacy": {
            "prompt_or_response_content_included": False,
            "task_ids_included": False,
            "content_fingerprints_only": True,
        },
        "added_model_calls": 0,
        "added_tokens": 0,
        "added_network_requests": 0,
    }
