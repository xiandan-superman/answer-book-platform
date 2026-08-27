from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .capabilities.catalog import (
    capability_ids_for_text,
    get_schema,
    match_schema_for_text,
    planner_system_context,
    registry_snapshot,
    schema_prompt_catalog,
)
from .capabilities.figure_semantics import (
    build_figure_semantic_contract,
    choose_figure_render_strategy,
)
from .concurrency import model_request_slot, run_limited_concurrent
from .drawing_code import question_drawing_mode
from .llm_client import LLMError, OpenAICompatibleClient
from .prompts import question_image_parts
from .question_requirements import answer_figure_required
from .question_types import explicit_question_type, iter_leaf_question_parts, question_has_type
from .settings import DEFAULT_MODEL_MAX_TOKENS

SCHEMA_VERSION = "answer_book.figure_schema_plan.v1"
ROUTING_POLICY_VERSION = "answer_book.figure_routing.v6"


def figure_schema_planning_worker_count() -> int:
    raw = os.environ.get("FIGURE_SCHEMA_PLANNING_MAX_WORKERS", "6")
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 6



def _question_text(question: dict[str, Any]) -> str:
    chunks = [str(question.get("stem") or ""), str(question.get("section") or ""), str(question.get("section_raw") or "")]
    for sub in question.get("subquestions") or []:
        if not isinstance(sub, dict):
            continue
        chunks.append(str(sub.get("stem") or ""))
        for req in sub.get("requirements") or []:
            if isinstance(req, dict):
                chunks.append(str(req.get("stem") or ""))
    return "\n".join(chunks)


def _drawing_scope(question: dict[str, Any]) -> dict[str, Any]:
    """Return only confirmed drawing units for planning/model contracts."""

    drawing_parts = [
        part
        for part in iter_leaf_question_parts(question)
        if answer_figure_required(part)
    ]
    if drawing_parts:
        scoped = dict(question)
        scoped["stem"] = "\n".join(
            f"{str(part.get('marker') or part.get('number') or '').strip()} {str(part.get('stem') or '').strip()}".strip()
            for part in drawing_parts
            if str(part.get("stem") or "").strip()
        )
        scoped["subquestions"] = drawing_parts
        scoped.pop("requirements", None)
        raw_understanding = question.get("question_understanding")
        understanding = dict(raw_understanding) if isinstance(raw_understanding, dict) else {}
        understanding["question_requirements"] = [
            str(part.get("stem") or "").strip()
            for part in drawing_parts
            if str(part.get("stem") or "").strip()
        ]
        scoped["question_understanding"] = understanding
        return scoped
    return question


def _drawing_leaf_parts(question: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part
        for part in iter_leaf_question_parts(question)
        if answer_figure_required(part)
    ]


def _leaf_planning_question(question: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
    """Build an independent planning surface for one drawing answer unit."""

    scoped = dict(question)
    scoped["stem"] = str(part.get("stem") or "").strip()
    scoped["subquestions"] = [dict(part)]
    scoped.pop("requirements", None)
    scoped["question_type"] = "作图题"
    scoped["confirmed_question_type"] = "作图题"
    raw_understanding = question.get("question_understanding")
    understanding = dict(raw_understanding) if isinstance(raw_understanding, dict) else {}
    understanding["question_requirements"] = [scoped["stem"]]
    scoped["question_understanding"] = understanding
    return scoped


def infer_schema_kind_locally(question: dict[str, Any]) -> tuple[str, str]:
    text = _question_text(question)
    match = match_schema_for_text(text)
    if match:
        return match.schema_kind, f"{match.evidence}，匹配 {match.schema_kind} schema。"
    return "unregistered_diagram", "未获得足够的本地能力匹配证据，交由通用图形降级链路处理。"


def _string_list(value: Any, limit: int = 20, item_limit: int = 300) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = str(item or "").strip()[:item_limit]
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def _source_image_policy(question: dict[str, Any]) -> str:
    if not question.get("image_refs"):
        return "none"
    text = _question_text(question)
    if re.search(r"(?:在|于).{0,8}(?:原图|图中|下图|上图|坐标图).{0,12}(?:标|补|画|绘)|补全|续画", text):
        return "preserve_and_overlay"
    return "reference_only"


def _semantic_contract(question: dict[str, Any], raw: Any = None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    raw_understanding = question.get("question_understanding")
    understanding: dict[str, Any] = raw_understanding if isinstance(raw_understanding, dict) else {}
    visible_labels: list[str] = []
    observations: list[str] = []
    for image in understanding.get("images") or []:
        if not isinstance(image, dict):
            continue
        visible_labels.extend(_string_list(image.get("detected_labels"), limit=20))
        observations.extend(_string_list(image.get("answer_relevant_observations"), limit=20))
    # Whether an attached question image must be preserved is a routing fact, not
    # a visual guess.  A vision model may correctly describe a source diagram but
    # still overreach and request an overlay for a *new* curve or schematic that
    # the student must draw.  Keep the deterministic wording check authoritative:
    # only explicit instructions such as "在原图中标出" may lock the pipeline to
    # preserve-and-overlay.  Ordinary attached figures remain reference evidence.
    policy = _source_image_policy(question)
    explicit_outputs = [
        str(part.get("stem") or "").strip()
        for part in iter_leaf_question_parts(question)
        if answer_figure_required(part) and str(part.get("stem") or "").strip()
    ]
    if not explicit_outputs and answer_figure_required(question):
        explicit_outputs = [str(question.get("stem") or "").strip()]
    return build_figure_semantic_contract(
        figure_role="answer_required",
        source_image_policy=policy,
        # The set of requested drawing outputs is structural truth.  A visual
        # planner may contribute labels and scientific relations, but an
        # attached reference image must not make it invent extra panels (for
        # example, redrawing a phase diagram when only a cooling curve and a
        # microstructure were requested).
        required_elements=explicit_outputs or _string_list(source.get("required_elements")) or observations[:12],
        # Labels detected in a reference figure describe the input evidence, not
        # automatically the answer figure. Only overlay tasks must preserve the
        # full source-label surface; a newly drawn curve/schematic uses labels
        # explicitly selected for that output.
        required_labels=_string_list(source.get("required_labels"))
        or (visible_labels[:20] if policy == "preserve_and_overlay" else []),
        relationship_constraints=_string_list(source.get("relationship_constraints")),
        forbidden_assumptions=_string_list(source.get("forbidden_assumptions")),
        original_image_available=bool(question.get("image_refs")),
    ).to_dict()


def _model_plan_prompt(question: dict[str, Any], *, include_images: bool = False) -> list[dict[str, Any]]:
    understanding = question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
    active_capability_ids = capability_ids_for_text(_question_text(question))
    payload = {
        "task": "plan_professional_figure_schema",
        "hard_rules": [
            "Only return one valid JSON object.",
            "Only choose a kind from available_schema_registry when it satisfies the drawing need.",
            "If no registry schema matches, return status schema_proposed and provide proposed_kind plus schema_proposal.",
            "Do not solve the question. Do not generate figure_specs parameters. Only plan the schema.",
            "Create a semantic figure contract before selecting a renderer.",
            "If the task requires drawing on an existing source image, set source_image_policy to preserve_and_overlay; never silently replace the original base image.",
            "Required elements and relationships must be observable requirements, not decorative suggestions.",
        ],
        "question": {
            "question_id": question.get("question_id", ""),
            "question_type": question.get("question_type", ""),
            "stem": question.get("stem", ""),
            "subquestions": question.get("subquestions", []),
            "question_understanding": understanding,
            "original_image_count": len(question.get("image_refs") or []),
        },
        "active_capability_ids": list(active_capability_ids),
        "available_schema_registry": schema_prompt_catalog(active_capability_ids),
        "output_schema": {
            "professional_diagram_type": "kind from registry or proposed kind",
            "reason": "why this schema is needed",
            "figure_semantic_contract": {
                "figure_role": "answer_required",
                "source_image_policy": "none | reference_only | preserve_and_overlay",
                "required_elements": ["必须画出的对象、曲线、区域或阶段"],
                "required_labels": ["必须出现的标签、坐标或符号"],
                "relationship_constraints": ["方向、相对位置、连接、变化趋势或专业关系"],
                "forbidden_assumptions": ["题目未提供且不得擅自添加的数据或结构"],
            },
            "schema_resolution": {
                "status": "schema_found | schema_proposed",
                "kind": "registry kind when found",
                "schema_id": "schema id when found",
                "schema_proposal": {},
            },
        },
    }
    user_content: Any = json.dumps(payload, ensure_ascii=False)
    image_parts = question_image_parts(question) if include_images else []
    if image_parts:
        user_content = [{"type": "text", "text": user_content}, *image_parts]
    return [
        {
            "role": "system",
            "content": (
                "你是多学科真题专业作图 schema 规划器。根据每道题本身所需能力进行选择，不要根据测试材料推断平台学科边界。"
                "只输出 JSON。\n" + planner_system_context(active_capability_ids)
            ),
        },
        {"role": "user", "content": user_content},
    ]


def _resolve_with_model(question: dict[str, Any], provider: Any, model: str) -> dict[str, Any] | None:
    if provider is None or not getattr(provider, "api_key", ""):
        return None
    client = OpenAICompatibleClient(provider)
    try:
        with model_request_slot(provider):
            result = client.chat_json_object(
                _model_plan_prompt(question, include_images=bool(getattr(provider, "supports_vision", False))),
                model=model or getattr(provider, "default_model", ""),
                max_tokens=DEFAULT_MODEL_MAX_TOKENS,
                task_stage="figure_schema",
                item_ids=[str(question.get("question_id") or question.get("number") or "")],
                enforce_context_budget=True,
            )
    except (LLMError, Exception):
        return None
    if not isinstance(result, dict):
        return None
    raw_resolution = result.get("schema_resolution")
    resolution: dict[str, Any] = raw_resolution if isinstance(raw_resolution, dict) else {}
    semantic_contract = _semantic_contract(question, result.get("figure_semantic_contract"))
    kind = str(resolution.get("kind") or result.get("professional_diagram_type") or "").strip()
    entry = get_schema(kind)
    if entry:
        return {
            "kind": entry["kind"],
            "reason": str(result.get("reason") or f"模型选择 {entry['kind']} schema。"),
            "figure_semantic_contract": semantic_contract,
            "schema_resolution": {
                "status": "schema_found",
                "schema_id": entry["schema_id"],
                "kind": entry["kind"],
                "renderer": entry["renderer"],
                "schema_source": "registry",
                "selected_by": "model",
            },
        }
    raw_proposal = resolution.get("schema_proposal")
    proposal: dict[str, Any] = raw_proposal if isinstance(raw_proposal, dict) else {}
    proposed_kind = str(resolution.get("proposed_kind") or result.get("professional_diagram_type") or "").strip()
    if proposed_kind or proposal:
        return {
            "kind": proposed_kind,
            "reason": str(result.get("reason") or "模型认为现有 registry 未覆盖该专业图。"),
            "figure_semantic_contract": semantic_contract,
            "schema_resolution": {
                "status": "schema_proposed",
                "proposed_kind": proposed_kind,
                "requires_renderer_creation": True,
                "schema_source": "model_proposal",
                "schema_proposal": proposal,
            },
        }
    return None


def _plan_single_drawing(question: dict[str, Any], provider: Any | None = None, model: str = "") -> dict[str, Any] | None:
    qid = str(question.get("question_id") or "").strip()
    if not qid or not answer_figure_required(question):
        return None
    planning_question = _drawing_scope(question)
    kind, reason = infer_schema_kind_locally(planning_question)
    entry = get_schema(kind)
    resolved = None
    if entry:
        resolved = {
            "kind": entry["kind"],
            "reason": reason,
            "figure_semantic_contract": _semantic_contract(planning_question),
            "schema_resolution": {
                "status": "schema_found",
                "schema_id": entry["schema_id"],
                "kind": entry["kind"],
                "renderer": entry["renderer"],
                "schema_source": "registry",
                "selected_by": "local_keyword",
            },
        }
    elif provider is not None:
        resolved = _resolve_with_model(planning_question, provider, model)
    if resolved is None:
        safe_kind = re.sub(r"[^a-z0-9_]+", "_", kind.lower()).strip("_") or "unregistered_diagram"
        resolved = {
            "kind": safe_kind,
            "reason": reason,
            "figure_semantic_contract": _semantic_contract(planning_question),
            "schema_resolution": {
                "status": "schema_proposed",
                "proposed_kind": safe_kind,
                "requires_renderer_creation": True,
                "schema_source": "local_proposal",
                "schema_proposal": {},
            },
        }
    semantic_contract = resolved.get("figure_semantic_contract") or _semantic_contract(planning_question)
    resolution = resolved["schema_resolution"]
    render_decision = choose_figure_render_strategy(
        build_figure_semantic_contract(
            figure_role=str(semantic_contract.get("figure_role") or "answer_required"),
            source_image_policy=str(semantic_contract.get("source_image_policy") or "none"),
            required_elements=semantic_contract.get("required_elements") or (),
            required_labels=semantic_contract.get("required_labels") or (),
            relationship_constraints=semantic_contract.get("relationship_constraints") or (),
            forbidden_assumptions=semantic_contract.get("forbidden_assumptions") or (),
            original_image_available=bool(semantic_contract.get("original_image_available")),
        ),
        schema_status=str(resolution.get("status") or ""),
        schema_kind=str(resolution.get("kind") or resolution.get("proposed_kind") or ""),
        renderer=str(resolution.get("renderer") or ""),
        drawing_mode=question_drawing_mode(question),
        # This records the bounded fallback route.  Runtime provider availability
        # is checked later by prepare_figures_for_fragments; planning an
        # "unavailable" route here would prevent a configured image provider from
        # ever being tried for a valid unregistered/multi-panel diagram.
        image_model_available=question_drawing_mode(question) == "figure_specs",
    )
    return {
        "question_id": qid,
        "confirmed_question_type": question.get("question_type") or question.get("confirmed_question_type") or "",
        "diagram_intent": {
            "needs_figure": True,
            "professional_diagram_type": resolved["kind"],
            "reason": resolved["reason"],
        },
        "figure_semantic_contract": semantic_contract,
        "render_decision": render_decision.to_dict(),
        "schema_resolution": resolution,
    }


def _plan_one(question: dict[str, Any], provider: Any | None = None, model: str = "") -> dict[str, Any] | None:
    qid = str(question.get("question_id") or "").strip()
    if not qid or not answer_figure_required(question):
        return None
    leaves = _drawing_leaf_parts(question)
    if not leaves:
        return _plan_single_drawing(question, provider=provider, model=model)

    unit_plans: list[dict[str, Any]] = []
    for index, part in enumerate(leaves, start=1):
        unit_number = str(part.get("number") or index).strip()
        plan = _plan_single_drawing(
            _leaf_planning_question(question, part),
            provider=provider,
            model=model,
        )
        if not plan:
            continue
        plan["answer_unit_number"] = unit_number
        plan["answer_unit_stem"] = str(part.get("stem") or "").strip()
        unit_plans.append(plan)
    if len(unit_plans) <= 1:
        plan = unit_plans[0] if unit_plans else _plan_single_drawing(question, provider=provider, model=model)
        if plan is not None:
            unit_snapshot = dict(plan)
            plan["figure_units"] = [unit_snapshot]
        return plan

    strategies = [str((plan.get("render_decision") or {}).get("strategy") or "") for plan in unit_plans]
    if all(strategy in {"programmatic_renderer", "source_image_overlay"} for strategy in strategies):
        aggregate_strategy = "programmatic_renderer"
    elif any(strategy == "model_code_renderer" for strategy in strategies):
        aggregate_strategy = "model_code_renderer"
    elif any(strategy == "image_model_fallback" for strategy in strategies):
        aggregate_strategy = "image_model_fallback"
    else:
        aggregate_strategy = "unavailable"
    aggregate_contract = _semantic_contract(_drawing_scope(question))
    return {
        "question_id": qid,
        "confirmed_question_type": question.get("question_type") or question.get("confirmed_question_type") or "",
        "diagram_intent": {
            "needs_figure": True,
            "professional_diagram_type": "multi_figure",
            "reason": "题目包含多个独立作图单元，按原始小问分别规划和渲染。",
        },
        "figure_semantic_contract": aggregate_contract,
        "render_decision": {
            "strategy": aggregate_strategy,
            "reason": "aggregate of independently planned drawing answer units",
            "semantic_contract_id": str(aggregate_contract.get("contract_id") or ""),
            "schema_kind": "multi_figure",
            "renderer": "",
            "fallback_allowed": all(bool((plan.get("render_decision") or {}).get("fallback_allowed", True)) for plan in unit_plans),
        },
        "schema_resolution": {
            "status": "schema_composite",
            "kind": "multi_figure",
            "schema_source": "answer_unit_composition",
            "unit_count": len(unit_plans),
        },
        "figure_units": unit_plans,
    }


def plan_figure_schemas(
    structured_exam: dict[str, Any],
    output_json: Path,
    *,
    provider: Any | None = None,
    model: str = "",
) -> dict[str, Any]:
    questions = [
        question
        for question in structured_exam.get("items") or []
        if isinstance(question, dict) and answer_figure_required(question)
    ]
    max_workers = figure_schema_planning_worker_count() if len(questions) > 1 else 1
    items = [
        plan
        for plan in run_limited_concurrent(
            questions,
            lambda question: _plan_one(question, provider=provider, model=model),
            max_workers=max_workers,
        )
        if plan
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "routing_policy_version": ROUTING_POLICY_VERSION,
        "planned_count": len(items),
        "items": items,
        "registry": registry_snapshot(),
        "concurrency": {
            "max_workers": max_workers,
            "parallel_enabled": max_workers > 1 and len(questions) > 1,
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def attach_figure_schema_plans(structured_exam: dict[str, Any], plan_report: dict[str, Any]) -> dict[str, Any]:
    plans_by_id = {
        str(item.get("question_id") or "").strip(): item
        for item in plan_report.get("items", []) or []
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    }
    for question in structured_exam.get("items") or []:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        if qid in plans_by_id:
            plan = plans_by_id[qid]
            question["figure_schema_plan"] = plan
            units_by_number = {
                str(unit.get("answer_unit_number") or "").strip(): unit
                for unit in plan.get("figure_units", [])
                if isinstance(unit, dict) and str(unit.get("answer_unit_number") or "").strip()
            }
            for part in iter_leaf_question_parts(question):
                number = str(part.get("number") or "").strip()
                if number in units_by_number:
                    part["figure_schema_plan"] = units_by_number[number]
    return structured_exam
