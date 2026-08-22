from __future__ import annotations

import copy
import json
import math
import os
import random
import re
import time
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any, Callable

from .concurrency import model_request_slot
from .llm_client import LLMError, OpenAICompatibleClient
from .practice_batch_contracts import complete_practice_slots, partition_practice_batch_rows
from .practice_export import (
    has_unrenderable_practice_markup,
    normalize_practice_markup,
    normalize_practice_question_text,
)
from .practice_inputs import parse_practice_sources
from .practice_result_assembly import (
    PracticeGenerationMetadataContext,
    build_practice_generation_metadata,
    build_practice_result_groups,
)
from .practice_runtime import (
    PracticeGenerationStopped,
    ensure_practice_generation_active,
    iter_bounded_futures,
    load_practice_generation_checkpoint,
)
from .runtime_capacity import practice_inner_concurrency
from .settings import DEFAULT_MODEL_MAX_TOKENS, get_provider, provider_model_supports_vision, resolve_provider_model

SCHEMA_VERSION = "answer_book.practice_set.v1"
DIFFICULTY_LEVELS = ("基础", "进阶", "挑战")
ALLOWED_DIFFICULTIES = set(DIFFICULTY_LEVELS)
ALLOWED_TYPES = {"单选题", "多选题", "判断题", "填空题", "简答题", "计算题", "作图题", "综合题"}


DIFFICULTY_LEVERS = (
    "条件直接程度",
    "条件识别或转换要求",
    "方法选择与组合要求",
    "知识综合与迁移程度",
    "隐含关系识别",
    "正向、逆向、比较、评价或优化任务",
    "提示和解题支架程度",
    "计算、论证或数据处理负担",
)
DIFFICULTY_MECHANISMS = {
    "基础": (
        "直接条件与明确路径",
        "必要提示或解题支架",
        "单一核心关系识别",
        "基础表示或图表读取",
    ),
    "进阶": (
        "条件识别或转换",
        "方法选择",
        "知识组合",
        "表示转换",
        "中间关系建立",
    ),
    "挑战": (
        "跨情境迁移",
        "隐含关系或边界判断",
        "逆向推理",
        "比较评价或优化",
        "模型建立",
        "纠错或反证",
    ),
}
DIFFICULTY_BOUNDARIES = {
    "基础": "路径清晰，可给必要支架；不删必考知识。",
    "进阶": "有一个实质的条件加工、方法决策或知识组合瓶颈；不只加数字或小问。",
    "挑战": "有一个真实的高阶认知瓶颈；不只加计算、数据、小问或背景。",
}
COMPREHENSIVE_STRATEGIES = {"targeted_set", "knowledge_overall"}
ALLOWED_COVERAGE_ROLES = {"变式", "铺垫", "连接", "综合", "迁移"}
STRUCTURAL_CHANGE_TYPES = (
    "改变未知量",
    "改变求解路径",
    "增加边界条件",
    "逆向求解",
    "比较与优化",
    "跨情境迁移",
    "增加多步约束",
    "改变子问结构",
)

DIVERSITY_CHANGE_DIRECTIVES = {
    "改变未知量": "主要未知量必须不同于来源题及同源其它题；不得只把同一求解任务改成选择、填空或换符号。",
    "改变求解路径": "必须改变中间量或推导顺序，使核心公式链或条件使用顺序发生实质变化。",
    "增加边界条件": "新增边界必须改变适用条件、方程选择或结论，不能只是附加一个不参与求解的限制。",
    "逆向求解": "从目标结论、观测量或允许范围反求条件，主要未知量和推理方向都要改变。",
    "比较与优化": "必须比较至少两个可行方案并依据明确指标作出选择，不能退化为两个独立的直接计算。",
    "跨情境迁移": "更换表层对象、数据背景和叙述骨架，仅保留知识边界；不得复述来源题的实体、数值组合和句式。",
    "增加多步约束": "至少一个新增中间约束必须参与后续推导并改变原有直接求解路径。",
    "改变子问结构": "子问之间必须存在依赖、反推或比较关系，不能把原题拆成若干同路径小问。",
}


def _clean(value: Any, limit: int = 8000) -> str:
    text = re.sub(r"\r\n?", "\n", str(value or "")).strip()
    return text[:limit]


def _nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def blueprint_refinement_concurrency(payload: dict[str, Any]) -> int:
    return practice_inner_concurrency(payload, stage="blueprint")


def practice_generation_concurrency(payload: dict[str, Any]) -> int:
    # Long Responses streams are substantially less stable when a single task
    # opens every batch at once.  Three workers still overlap useful work while
    # keeping one practice task from exhausting the provider connection pool.
    return practice_inner_concurrency(payload, stage="generation")


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item, 500) for item in value[:limit] if _clean(item, 500)]


def _unique_strings(value: Any, *, limit: int = 12, item_limit: int = 500) -> list[str]:
    rows: list[str] = []
    for item in value if isinstance(value, list) else []:
        cleaned = _clean(item, item_limit)
        if cleaned and cleaned not in rows:
            rows.append(cleaned)
        if len(rows) >= limit:
            break
    return rows


def _difficulty_design(
    difficulty: str,
    question_type: str,
    *,
    levers: Any = None,
    rationale: Any = None,
    structural_change: Any = None,
    target_skill: Any = None,
) -> tuple[list[str], str]:
    """Return a type-aware difficulty design without imposing fixed step counts."""
    level = _clean(difficulty, 20) if _clean(difficulty, 20) in ALLOWED_DIFFICULTIES else "进阶"
    selected = [lever for lever in _unique_strings(levers, limit=4, item_limit=100) if lever in DIFFICULTY_LEVERS]
    change = _clean(structural_change, 100)
    skill = _clean(target_skill, 160) or "目标能力"
    if not selected:
        defaults = {
            "基础": ["条件直接程度", "提示和解题支架程度"],
            "进阶": ["条件识别或转换要求", "方法选择与组合要求"],
            "挑战": ["知识综合与迁移程度", "隐含关系识别", "正向、逆向、比较、评价或优化任务"],
        }
        selected = list(defaults[level])
        if question_type in {"计算题", "综合题"} and "计算、论证或数据处理负担" not in selected:
            selected.append("计算、论证或数据处理负担")
        if any(token in change for token in ("逆向", "比较", "优化", "评价")):
            selected = ["正向、逆向、比较、评价或优化任务", *selected]
        selected = list(dict.fromkeys(selected))[:3]
    text = _clean(rationale, 500)
    if not text:
        if level == "基础":
            text = f"围绕{skill}保留必考知识点，条件表达更直接，并提供必要提示或支架。"
        elif level == "进阶":
            text = f"围绕{skill}要求识别或转换条件，并完成方法选择或知识组合。"
        else:
            text = f"围绕{skill}设置综合迁移、隐含关系或{change or '逆向/比较/优化'}要求，需作出独立判断。"
    return selected, text


def _difficulty_intent(item: dict[str, Any], *, set_position: int = 0) -> dict[str, Any]:
    """Give the model a design space, not a mandatory difficulty recipe."""
    level = _clean(item.get("difficulty"), 20)
    if level not in ALLOWED_DIFFICULTIES:
        level = "进阶"
    candidates = list(DIFFICULTY_MECHANISMS[level])
    change = _clean(item.get("structural_change") or item.get("variation_type"), 100)
    preferred = ""
    for token, mechanism in (
        ("逆向", "逆向推理"),
        ("比较", "比较评价或优化"),
        ("优化", "比较评价或优化"),
        ("评价", "比较评价或优化"),
        ("迁移", "跨情境迁移"),
        ("边界", "隐含关系或边界判断"),
    ):
        if token in change and mechanism in candidates:
            preferred = mechanism
            break
    if candidates:
        offset = max(0, set_position) % len(candidates)
        candidates = candidates[offset:] + candidates[:offset]
    if preferred:
        candidates = [preferred, *[value for value in candidates if value != preferred]]
    return {
        "level": level,
        "boundary": DIFFICULTY_BOUNDARIES[level],
        "candidate_mechanisms": candidates,
        "selection_rule": "选择最适合本题的一种主要机制，必要时再选一种辅助机制；不要把候选项全部堆叠进一道题。如有更自然的机制，允许使用简短自定义描述。",
        "blueprint_hint": _clean(item.get("difficulty_rationale"), 300),
    }


def _batch_difficulty_policy(batch_plan: list[dict[str, Any]]) -> dict[str, Any]:
    levels = [
        level for level in DIFFICULTY_LEVELS
        if any(_clean(item.get("difficulty"), 20) == level for item in batch_plan if isinstance(item, dict))
    ]
    return {
        "boundaries": {level: DIFFICULTY_BOUNDARIES[level] for level in levels},
        "selection_rule": "自主选一种主机制，最多一种辅助；候选池非模板，可自定义更自然的机制。",
        "portfolio_rule": "同层级优先分散主机制，不为形式差异硬堆任务。",
    }


def _normalized_difficulty_evidence(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {
        "primary_mechanism": _clean(raw.get("primary_mechanism"), 120),
        "student_bottleneck": _clean(raw.get("student_bottleneck"), 400),
    }


def _include_source_content_in_generation(payload: dict[str, Any] | None, plan: dict[str, Any] | None = None) -> bool:
    """Resolve the persisted formal-generation source-material preference.

    Older requests and blueprints predate this setting, so they intentionally
    retain the historical behavior of including source content by default.
    """
    payload = payload if isinstance(payload, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    for value in (
        payload.get("include_source_content_in_generation"),
        plan.get("include_source_content_in_generation"),
        blueprint.get("include_source_content_in_generation"),
    ):
        if isinstance(value, bool):
            return value
    return True


def _required_knowledge_points_for_refs(
    source_refs: list[str],
    source_catalog: list[dict[str, Any]],
    fallback: Any = None,
) -> list[str]:
    """Derive one plan item's mandatory knowledge-point combination.

    Source scope is authoritative. A model-provided list is only a fallback
    for legacy or free-form knowledge tasks that have no selectable unit.
    """
    source_by_id = {
        _clean(source.get("source_question_id"), 80): source
        for source in source_catalog
        if isinstance(source, dict) and _clean(source.get("source_question_id"), 80)
    }
    points: list[str] = []
    for source_ref in source_refs:
        source = source_by_id.get(_clean(source_ref, 80), {})
        for point in _string_list(source.get("knowledge_points"), limit=60):
            if point not in points:
                points.append(point)
    if points:
        return points[:60]
    return _unique_strings(fallback, limit=60, item_limit=500)


_CONSTRAINT_FIELDS = (
    "essential_definitions",
    "essential_formulas",
    "applicable_boundaries",
)


def _required_constraints_for_refs(
    source_refs: list[str],
    source_catalog: list[dict[str, Any]],
    fallback: Any = None,
) -> dict[str, list[str]]:
    """Collect formal-generation constraints from the sources bound to one plan item."""
    source_by_id = {
        _clean(source.get("source_question_id"), 80): source
        for source in source_catalog
        if isinstance(source, dict) and _clean(source.get("source_question_id"), 80)
    }
    constraints = {field: [] for field in _CONSTRAINT_FIELDS}
    for source_ref in source_refs:
        source = source_by_id.get(_clean(source_ref, 80), {})
        nested = source.get("required_constraints") if isinstance(source.get("required_constraints"), dict) else {}
        for field in _CONSTRAINT_FIELDS:
            for value in _string_list(nested.get(field) or source.get(field), limit=30):
                if value not in constraints[field]:
                    constraints[field].append(value)
    if any(constraints.values()):
        return constraints
    fallback = fallback if isinstance(fallback, dict) else {}
    nested_fallback = (
        fallback.get("required_constraints")
        if isinstance(fallback.get("required_constraints"), dict)
        else fallback
    )
    return {
        field: _string_list(nested_fallback.get(field) or fallback.get(field), limit=30)
        for field in _CONSTRAINT_FIELDS
    }


def _required_constraints_for_plan_item(
    source_refs: list[str],
    source_catalog: list[dict[str, Any]],
    planned_constraints: Any,
    generation_strategy: str,
    fallback: Any = None,
    *,
    allow_partition: bool = False,
) -> dict[str, list[str]]:
    """Resolve constraints without overwriting a comprehensive item's own selection.

    A comprehensive blueprint may intentionally use only part of the knowledge
    covered by its bound sources.  In that mode, a non-empty
    ``required_constraints`` emitted or edited on the blueprint item is the
    authoritative selection.  One-to-one and per-source variants continue to
    use the full constraint set of their bound source.
    """
    explicit = _required_constraints_for_refs([], [], planned_constraints)
    if allow_partition and isinstance(planned_constraints, dict):
        available = _required_constraints_for_refs(source_refs, source_catalog, fallback)
        return {
            field: [value for value in explicit[field] if value in available[field]]
            for field in _CONSTRAINT_FIELDS
        }
    if _mode_kind(generation_strategy) == "comprehensive" and any(explicit.values()):
        return explicit
    return _required_constraints_for_refs(source_refs, source_catalog, fallback)


def _required_knowledge_points_for_plan_item(
    source_refs: list[str],
    source_catalog: list[dict[str, Any]],
    fallback: Any,
    generation_strategy: str,
    *,
    allow_partition: bool = False,
) -> list[str]:
    """Resolve required points without flattening comprehensive-plan allocations.

    One-to-one and per-source variants preserve the entire bound source set.
    Comprehensive plans may deliberately choose a subset from their bound
    sources, provided that the full plan still covers the confirmed scope.
    """
    available = _required_knowledge_points_for_refs(source_refs, source_catalog)
    supplied = _unique_strings(fallback, limit=60, item_limit=500)
    if allow_partition and available:
        selected = [point for point in supplied if point in available]
        return selected or available
    if _mode_kind(generation_strategy) != "comprehensive":
        return available or supplied
    if available:
        selected = [point for point in supplied if point in available]
        return selected or available
    return supplied


def _required_knowledge_point_issue(generated: dict[str, Any], planned_item: dict[str, Any]) -> dict[str, Any] | None:
    required = _unique_strings(planned_item.get("required_knowledge_points"), limit=60, item_limit=500)
    if not required:
        return None
    actual = _unique_strings(generated.get("knowledge_points"), limit=60, item_limit=500)
    if set(actual) == set(required):
        return None
    missing = [point for point in required if point not in actual]
    extra = [point for point in actual if point not in required]
    return {
        "required_knowledge_points": required,
        "actual_knowledge_points": actual,
        "missing_knowledge_points": missing,
        "extra_knowledge_points": extra,
        "reason": "输出知识点与蓝图要求的知识点组合不一致。",
    }


def _mode_kind(strategy: str) -> str:
    return "comprehensive" if _clean(strategy, 40) in COMPREHENSIVE_STRATEGIES else "single_source"


def _strategy_prompt_requirement(strategy: str, *, knowledge_mode: bool, source_count: int = 0, exercise_count: int = 0) -> str:
    strategy = _clean(strategy, 40)
    if strategy in {"targeted_set", "knowledge_overall"}:
        if source_count > 0 and exercise_count > 0 and exercise_count < source_count:
            return "综合模式：题量少于已选来源数，优先覆盖核心知识点并设置连接或综合项；允许部分来源或知识点留待下一套练习覆盖，不得伪造已全量覆盖。"
        return "综合模式：按蓝图项合理分配或组合绑定来源的知识点；整套蓝图覆盖全部确认范围，并设置连接或综合项。"
    if strategy in {"parallel_exam"}:
        return "一一对应模式：每项只绑定一道来源原题，并完整保留该原题的必考知识点组合。"
    if strategy == "per_question":
        return "逐题变式模式：每项只绑定一道来源原题，完整保留该原题的必考知识点组合，并在能力或变化方式上形成差异。"
    if strategy == "knowledge_item_wise":
        return (
            "逐知识单元模式：每项只绑定一个知识来源；同一来源只有一题时完整保留知识点组合，"
            "同一来源分配多题时按每题目标分配相关知识点和约束，整组题合计覆盖该来源全部确认知识点，"
            "不得让每题虚假声明覆盖未实际考查的子主题。"
        )
    return "单项训练模式：只围绕已确认范围和当前训练目标设计。"


def _default_structural_change(index: int, *, mode: str, has_multiple_sources: bool) -> str:
    if mode == "comprehensive" and has_multiple_sources and index % 3 == 2:
        return "跨情境迁移"
    return STRUCTURAL_CHANGE_TYPES[index % len(STRUCTURAL_CHANGE_TYPES)]


def _number_masked_text(value: Any) -> str:
    """Normalize text for detecting a question that only changes numeric data."""
    text = _clean(value, 12000).casefold()
    # Keep units and words, but collapse every Arabic/decimal/scientific literal.
    text = re.sub(r"(?<![a-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?", "<number>", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _text_similarity(left: Any, right: Any) -> float:
    left_text = _number_masked_text(left)
    right_text = _number_masked_text(right)
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _regeneration_surface_text(item: dict[str, Any]) -> str:
    options = _normalize_options(item.get("options"))
    return " ".join([
        _clean(item.get("stem"), 12000),
        *(_clean(option.get("text"), 2000) for option in options),
    ])


def _regenerated_exercise_substantively_changed(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Reject a regenerate response that merely returns the current question."""
    current_text = _regeneration_surface_text(current)
    candidate_text = _regeneration_surface_text(candidate)
    if not candidate_text:
        return False
    return _text_similarity(current_text, candidate_text) < 0.92


def _normalized_diversity_signature(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {
        "scenario_family": _clean(raw.get("scenario_family"), 240),
        "asked_quantity": _clean(raw.get("asked_quantity"), 240),
        "solution_family": _clean(raw.get("solution_family"), 360),
        "cognitive_operation": _clean(raw.get("cognitive_operation"), 120),
    }


def _formula_token_signature(item: dict[str, Any]) -> set[str]:
    text = " ".join(
        _clean(formula.get("latex"), 1000)
        for formula in (item.get("formulas") or [])
        if isinstance(formula, dict)
    )
    tokens = {
        token.casefold().lstrip("\\")
        for token in re.findall(r"\\[A-Za-z]+|[A-Za-z]{1,12}|[ΩΔη]", text)
    }
    formatting = {
        "mathrm", "text", "left", "right", "cdot", "times", "frac", "dfrac",
        "begin", "end", "quad", "qquad", "rm", "mathbf",
    }
    return {token for token in tokens if token not in formatting}


def _set_similarity(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _plan_change_contract(item: dict[str, Any]) -> dict[str, str]:
    change = _clean(item.get("structural_change") or item.get("variation_type"), 100) or "结构变化"
    directive = DIVERSITY_CHANGE_DIRECTIVES.get(
        change,
        "必须改变情境、主要未知量、认知操作或核心公式链中的至少一项，并说明可由题干直接核验的差异。",
    )
    return {"kind": change, "required_difference": directive}


def _compact_required_constraints(value: Any) -> dict[str, list[str]]:
    normalized = _required_constraints_for_refs([], [], value)
    return {key: rows for key, rows in normalized.items() if rows}


def _semantic_diversity_context(
    exercise_plan: list[dict[str, Any]],
    batch_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe set-level differences without sending persistent IDs or prior stems."""
    rows: list[dict[str, Any]] = []
    for batch_index, item in enumerate(batch_plan, start=1):
        refs = set(_unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80))
        peer_skills: list[str] = []
        peer_changes: list[str] = []
        current_id = _clean(item.get("plan_item_id"), 80)
        for peer in exercise_plan:
            if not refs:
                break
            if not isinstance(peer, dict) or _clean(peer.get("plan_item_id"), 80) == current_id:
                continue
            peer_refs = set(_unique_strings(peer.get("source_refs") or [peer.get("source_question_id")], limit=3, item_limit=80))
            if refs and not refs.intersection(peer_refs):
                continue
            skill = _clean(peer.get("target_skill"), 160)
            change = _plan_change_contract(peer)["kind"]
            if skill and skill not in peer_skills:
                peer_skills.append(skill)
            if change and change not in peer_changes:
                peer_changes.append(change)
            if len(peer_skills) >= 4 and len(peer_changes) >= 4:
                break
        rows.append({
            "batch_index": batch_index,
            "change_contract": _plan_change_contract(item),
            "same_source_peer_designs": {
                "target_skills": peer_skills[:4],
                "change_kinds": peer_changes[:4],
            },
        })
    return {
        "policy": [
            "同来源题的情境、主要未知量、认知操作、核心公式链至少两项不同；不得只换数字、单位、名称、题型或措辞。",
            "diversity_signature 仅作内部去重元数据，不得写入题干或包含答案。",
        ],
        "difficulty_policy": _batch_difficulty_policy(batch_plan),
        "items": rows,
    }


def validate_reference_calculation_variation(
    source: dict[str, Any],
    generated: dict[str, Any],
    planned_item: dict[str, Any],
) -> dict[str, Any]:
    """Reject a generated calculation that is only a numeric rewrite of its source."""
    source_type = _clean(source.get("question_type"), 100)
    target_type = _clean(planned_item.get("question_type"), 20)
    source_text = _number_masked_text(source.get("stem_excerpt") or source.get("excerpt"))
    generated_text = _number_masked_text(generated.get("stem"))
    applicable = "计算题" in source_type and target_type == "计算题" and bool(source_text and generated_text)
    if not applicable:
        return {"status": "not_applicable", "reason": "非参考计算题→计算题，或缺少可比题干。"}
    ratio = SequenceMatcher(None, source_text, generated_text).ratio()
    same_after_number_mask = source_text == generated_text
    failed = len(source_text) >= 30 and len(generated_text) >= 30 and (same_after_number_mask or ratio >= 0.97)
    return {
        "status": "failed" if failed else "passed",
        "reason": "题干去数字后仍高度一致，疑似只替换数据。" if failed else "检测到题干结构变化。",
        "normalized_similarity": round(ratio, 4),
        "same_after_number_mask": same_after_number_mask,
        "required_change": _clean(planned_item.get("structural_change"), 100),
    }


def _batch_variation_issues(
    batch_exercises: list[dict[str, Any]],
    batch_plan: list[dict[str, Any]],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    scope = plan.get("source_scope") if isinstance(plan.get("source_scope"), dict) else {}
    catalog = [item for item in (plan.get("selected_source_questions") or scope.get("questions") or []) if isinstance(item, dict)]
    by_id = {_clean(item.get("source_question_id"), 80): item for item in catalog if _clean(item.get("source_question_id"), 80)}
    issues: list[dict[str, Any]] = []
    for raw_item in batch_exercises:
        if not isinstance(raw_item, dict):
            continue
        try:
            local_index = int(raw_item.get("batch_index")) - 1
        except (TypeError, ValueError):
            continue
        if local_index < 0 or local_index >= len(batch_plan):
            continue
        planned_item = batch_plan[local_index]
        refs = _unique_strings(planned_item.get("source_refs") or [planned_item.get("source_question_id")], limit=3, item_limit=80)
        source = by_id.get(refs[0], {}) if refs else {}
        report = validate_reference_calculation_variation(source, raw_item, planned_item)
        if report["status"] == "failed":
            issues.append({"batch_index": raw_item.get("batch_index"), **report})
    return issues


def _batch_sibling_variant_issues(
    batch_exercises: list[dict[str, Any]],
    batch_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject sibling variants that are effectively the same question after number changes."""
    by_parent: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for raw_item in batch_exercises:
        if not isinstance(raw_item, dict):
            continue
        try:
            local_index = int(raw_item.get("batch_index")) - 1
        except (TypeError, ValueError):
            continue
        if local_index < 0 or local_index >= len(batch_plan):
            continue
        parent_id = _clean(batch_plan[local_index].get("parent_plan_item_id"), 80)
        if parent_id:
            by_parent.setdefault(parent_id, []).append((local_index, raw_item))
    issues: list[dict[str, Any]] = []
    for siblings in by_parent.values():
        for first_position, (first_index, first) in enumerate(siblings):
            first_text = _number_masked_text(first.get("stem"))
            if len(first_text) < 30:
                continue
            for second_index, second in siblings[first_position + 1:]:
                second_text = _number_masked_text(second.get("stem"))
                if len(second_text) < 30:
                    continue
                similarity = SequenceMatcher(None, first_text, second_text).ratio()
                if first_text == second_text or similarity >= 0.92:
                    for local_index in (first_index, second_index):
                        issues.append({
                            "batch_index": local_index + 1,
                            "status": "failed",
                            "reason": "同一蓝图生成的变式题结构过于相似，疑似仅替换数字或措辞。",
                            "normalized_similarity": round(similarity, 4),
                            "required_change": batch_plan[local_index].get("structural_change"),
                        })
    return issues


def practice_diversity_issues(practice: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect set-level stem and solution-template collisions without an LLM judge."""
    raw_exercises = practice.get("exercises") if isinstance(practice.get("exercises"), list) else []
    exercises = [
        item for item in raw_exercises
        if isinstance(item, dict) and item.get("generation_status") != "failed" and _clean(item.get("stem"), 6000)
    ]
    original_positions = {id(item): index for index, item in enumerate(raw_exercises) if isinstance(item, dict)}
    blueprint = practice.get("blueprint") if isinstance(practice.get("blueprint"), dict) else {}
    strategy = _clean(practice.get("generation_strategy") or blueprint.get("generation_strategy"), 40)
    comprehensive = strategy in COMPREHENSIVE_STRATEGIES
    source_catalog = [
        item for item in (practice.get("selected_source_questions") or (practice.get("source_scope") or {}).get("questions") or [])
        if isinstance(item, dict)
    ]
    source_by_id = {
        _clean(item.get("source_question_id"), 80): item
        for item in source_catalog
        if _clean(item.get("source_question_id"), 80)
    }
    issues: list[dict[str, Any]] = []

    if comprehensive:
        for index, item in enumerate(exercises):
            source = source_by_id.get(_clean(item.get("source_question_id"), 80), {})
            source_text = _clean(source.get("source_content") or source.get("stem_excerpt") or source.get("excerpt"), 6000)
            if len(_number_masked_text(source_text)) < 40:
                continue
            similarity = _text_similarity(source_text, item.get("stem"))
            if similarity >= 0.62:
                issues.append({
                    "code": "source_surface_reuse",
                    "blocking": True,
                    "exercise_index": original_positions.get(id(item), index),
                    "peer_index": None,
                    "similarity": round(similarity, 4),
                    "message": f"第 {item.get('number') or index + 1} 题与绑定来源题面过于接近，综合训练不得复用来源情境和句式骨架。",
                })

    for first_position, first in enumerate(exercises):
        first_text = _number_masked_text(first.get("stem"))
        first_source = _clean(first.get("source_question_id"), 80)
        first_parent = _clean(first.get("parent_plan_item_id"), 80)
        first_signature = _normalized_diversity_signature(first.get("diversity_signature"))
        first_formula_tokens = _formula_token_signature(first)
        first_points = set(_unique_strings(first.get("knowledge_points"), limit=20, item_limit=500))
        for second_position in range(first_position + 1, len(exercises)):
            second = exercises[second_position]
            second_text = _number_masked_text(second.get("stem"))
            second_source = _clean(second.get("source_question_id"), 80)
            second_parent = _clean(second.get("parent_plan_item_id"), 80)
            same_source = bool(first_source and first_source == second_source)
            sibling_variants = bool(first_parent and first_parent == second_parent)
            text_similarity = _text_similarity(first.get("stem"), second.get("stem"))
            text_collision = (
                first_text == second_text and len(first_text) >= 20
            ) or (
                sibling_variants and min(len(first_text), len(second_text)) >= 80 and text_similarity >= 0.88
            ) or (
                comprehensive and same_source and not sibling_variants
                and min(len(first_text), len(second_text)) >= 80 and text_similarity >= 0.72
            ) or (
                not same_source and min(len(first_text), len(second_text)) >= 80 and text_similarity >= 0.84
            )

            second_signature = _normalized_diversity_signature(second.get("diversity_signature"))
            solution_similarity = _text_similarity(first_signature["solution_family"], second_signature["solution_family"])
            asked_similarity = _text_similarity(first_signature["asked_quantity"], second_signature["asked_quantity"])
            scenario_similarity = _text_similarity(first_signature["scenario_family"], second_signature["scenario_family"])
            same_operation = bool(
                first_signature["cognitive_operation"]
                and first_signature["cognitive_operation"] == second_signature["cognitive_operation"]
            )
            declared_collision = (
                comprehensive
                and bool(first_signature["solution_family"] and second_signature["solution_family"])
                and bool(first_signature["asked_quantity"] and second_signature["asked_quantity"])
                and solution_similarity >= 0.88
                and asked_similarity >= 0.88
                and (scenario_similarity >= 0.75 or same_operation)
            )

            second_formula_tokens = _formula_token_signature(second)
            second_points = set(_unique_strings(second.get("knowledge_points"), limit=20, item_limit=500))
            formula_similarity = _set_similarity(first_formula_tokens, second_formula_tokens)
            formula_collision = (
                comprehensive
                and not sibling_variants
                and len(first_formula_tokens) >= 4
                and len(second_formula_tokens) >= 4
                and formula_similarity >= 0.68
                and text_similarity >= 0.22
                and solution_similarity >= 0.82
                and asked_similarity >= 0.78
                and bool(first_points and first_points == second_points)
            )
            if not (text_collision or declared_collision or formula_collision):
                continue
            reasons = []
            if text_collision:
                reasons.append(f"题干结构相似度 {text_similarity:.2f}")
            if declared_collision:
                reasons.append("主要未知量和解法族重复")
            if formula_collision:
                reasons.append(f"核心公式特征重合度 {formula_similarity:.2f}")
            issues.append({
                "code": "set_diversity_collision",
                "blocking": True,
                "exercise_index": original_positions.get(id(second), second_position),
                "peer_index": original_positions.get(id(first), first_position),
                "similarity": round(max(text_similarity, formula_similarity), 4),
                "message": (
                    f"第 {second.get('number') or second_position + 1} 题与第 "
                    f"{first.get('number') or first_position + 1} 题实质近似：{'；'.join(reasons)}。"
                ),
            })
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int | None]] = set()
    for issue in issues:
        key = (str(issue.get("code")), int(issue.get("exercise_index") or 0), issue.get("peer_index"))
        if key not in seen:
            deduplicated.append(issue)
            seen.add(key)
    return deduplicated


def practice_difficulty_observations(practice: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-blocking difficulty drift observations for new generated items."""
    exercises = [
        item for item in (practice.get("exercises") or [])
        if isinstance(item, dict) and item.get("generation_status") != "failed"
    ]
    known_by_level = {level: set(values) for level, values in DIFFICULTY_MECHANISMS.items()}
    observations: list[dict[str, Any]] = []
    mechanisms_by_level: dict[str, list[tuple[int, str]]] = {}
    for index, item in enumerate(exercises):
        if "difficulty_evidence" not in item:
            continue
        evidence = _normalized_difficulty_evidence(item.get("difficulty_evidence"))
        level = _clean(item.get("difficulty"), 20)
        primary = evidence["primary_mechanism"]
        bottleneck = evidence["student_bottleneck"]
        number = item.get("number") or index + 1
        if not primary or not bottleneck:
            observations.append({
                "code": "difficulty_evidence_incomplete",
                "severity": "low",
                "exercise_index": index,
                "message": f"第 {number} 题未完整记录主要难度机制和学生瓶颈；题目仍可使用，但难度校准信心较低。",
            })
            continue
        mechanisms_by_level.setdefault(level, []).append((index, primary))
        if level == "挑战" and primary in known_by_level["基础"]:
            observations.append({
                "code": "possible_difficulty_drift",
                "severity": "high",
                "exercise_index": index,
                "message": f"第 {number} 题标为挑战，但自报的主要机制是“{primary}”，可能偏向基础层级；本次不阻断成题。",
            })
        elif level == "基础" and primary in known_by_level["挑战"]:
            observations.append({
                "code": "possible_difficulty_drift",
                "severity": "medium",
                "exercise_index": index,
                "message": f"第 {number} 题标为基础，但主要机制是“{primary}”，可能偏难；本次不阻断成题。",
            })
        normalized_primary = _number_masked_text(primary)
        if level == "挑战" and normalized_primary and any(
            token in normalized_primary for token in ("计算量", "数据量", "小问数", "运算负担")
        ) and not any(
            token in normalized_primary for token in ("判断", "迁移", "逆向", "比较", "评价", "优化", "建模", "纠错", "反证")
        ):
            observations.append({
                "code": "execution_only_challenge",
                "severity": "high",
                "exercise_index": index,
                "message": f"第 {number} 题的挑战机制似乎仅来自执行负担，而非高阶认知瓶颈；本次不阻断成题。",
            })

    for level, rows in mechanisms_by_level.items():
        if len(rows) < 3:
            continue
        counts = {mechanism: sum(1 for _, value in rows if value == mechanism) for _, mechanism in rows}
        mechanism, count = max(counts.items(), key=lambda pair: pair[1])
        if count <= max(2, (len(rows) + 1) // 2):
            continue
        observations.append({
            "code": "difficulty_mechanism_concentration",
            "severity": "medium",
            "exercise_index": None,
            "message": f"{level}题中有 {count}/{len(rows)} 道都使用“{mechanism}”作为主要难度机制，整套可能出现层级设计同质化；本次不阻断成题。",
        })
    return observations


def validate_practice_mode_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Deterministically prove that single-source and comprehensive plans differ.

    This gate intentionally uses only server-owned fields. It does not add an
    LLM judge call and it never treats the model's own explanation as proof.
    Comprehensive coverage gaps remain visible as warnings for the user to
    review. They are not a hard gate because one integrated item can cover
    multiple sources and the model's declared labels cannot prove actual scope.
    """
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    items = [item for item in (blueprint.get("exercise_plan") or []) if isinstance(item, dict)]
    strategy = _clean(blueprint.get("generation_strategy"), 40)
    mode = _mode_kind(strategy)
    catalog = plan.get("selected_source_questions") or (plan.get("source_scope") or {}).get("questions") or []
    selected_ids = [
        _clean(item.get("source_question_id"), 80)
        for item in catalog
        if isinstance(item, dict) and _clean(item.get("source_question_id"), 80)
    ]
    selected_ids = list(dict.fromkeys(selected_ids))
    refs_by_item = [
        _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
        for item in items
    ]
    errors: list[str] = []
    warnings: list[str] = []
    covered = {ref for refs in refs_by_item for ref in refs}
    multi_source_count = sum(1 for refs in refs_by_item if len(refs) >= 2)
    roles = {_clean(item.get("coverage_role"), 20) for item in items if _clean(item.get("coverage_role"), 20)}
    required_multi = max(1, (len(items) + 4) // 5) if len(selected_ids) >= 2 and items else 0
    if mode == "single_source":
        if selected_ids and any(len(refs) != 1 for refs in refs_by_item):
            errors.append("单项模式每个蓝图项必须且只能绑定一个来源。")
        expected_source_counts = {
            _clean(source_id, 80): max(0, int(expected_count))
            for source_id, expected_count in (blueprint.get("expected_source_counts") or {}).items()
            if _clean(source_id, 80) and str(expected_count).isdigit()
        } if isinstance(blueprint.get("expected_source_counts"), dict) else {}
        actual_source_counts = {
            source_id: sum(1 for refs in refs_by_item if refs and refs[0] == source_id)
            for source_id in expected_source_counts
        }
        count_mismatches = [
            f"{source_id}应为{expected_count}题、实际{actual_source_counts.get(source_id, 0)}题"
            for source_id, expected_count in expected_source_counts.items()
            if actual_source_counts.get(source_id, 0) != expected_count
        ]
        if count_mismatches:
            errors.append("单项模式未按用户设定的逐来源题数分配：" + "；".join(count_mismatches) + "。")
        grouped: dict[str, list[tuple[str, str]]] = {}
        for item, refs in zip(items, refs_by_item):
            if refs:
                grouped.setdefault(refs[0], []).append((_clean(item.get("variation_type"), 200), _clean(item.get("target_skill"), 500)))
        for source_id, variants in grouped.items():
            if len(variants) > 1 and len(set(variants)) == 1:
                errors.append(f"单项模式来源 {source_id} 的多个变式缺少能力或变化方式差异。")
    else:
        missing = [source_id for source_id in selected_ids if source_id not in covered]
        if missing:
            message = f"综合模式未覆盖全部选中来源：{missing}。"
            if len(items) < len(selected_ids):
                warnings.append(f"当前题量少于已选来源数（{len(items)} 题 / {len(selected_ids)} 项）；{message} 可提高题量以覆盖更完整范围。")
            else:
                warnings.append(f"{message} 请在蓝图审查中确认是否需要补充该来源。")
        if multi_source_count < required_multi:
            errors.append(f"综合模式跨来源题不足：至少 {required_multi} 题，实际 {multi_source_count} 题。")
        if len(selected_ids) >= 2 and not roles.intersection({"连接", "综合"}):
            errors.append("综合模式至少需要一个连接或综合角色。")
    return {
        "status": "failed" if errors else ("warning" if warnings else "passed"),
        "mode": mode,
        "strategy": strategy,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "selected_source_count": len(selected_ids),
            "covered_source_count": len(set(selected_ids).intersection(covered)),
            "exercise_count": len(items),
            "multi_source_count": multi_source_count,
            "required_multi_source_count": required_multi,
            "coverage_roles": sorted(role for role in roles if role),
            "expected_source_counts": blueprint.get("expected_source_counts") or {},
        },
    }


def _scope_evidence_text(source: dict[str, Any]) -> str:
    """Return all server-confirmed scope evidence for one source."""
    constraints = source.get("required_constraints") if isinstance(source.get("required_constraints"), dict) else {}
    return " ".join([
        *(
            _clean(source.get(field), 18000)
            for field in ("title", "stem_excerpt", "source_content", "knowledge_points")
        ),
        *(
            " ".join(_string_list(constraints.get(field), limit=60))
            for field in ("essential_definitions", "essential_formulas", "applicable_boundaries")
        ),
    ])


def _canonical_scope_text(value: Any) -> str:
    text = _clean(value, 40000).lower()
    text = text.replace("复杂反应", "复合反应")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _source_evidence_covers_anchor(anchor: str, evidence: str) -> bool:
    """Treat aliases and compound labels as covered by the bound source evidence."""
    canonical_evidence = _canonical_scope_text(evidence)
    canonical_anchor = _canonical_scope_text(anchor)
    if not canonical_anchor:
        return True
    if canonical_anchor in canonical_evidence:
        return True
    base = re.sub(
        r"(?:的)?(?:定义|确定步骤|标定步骤|表示方法|计算方法|基本原理|原理|结果|适用范围)$",
        "",
        _clean(anchor, 100),
    )
    canonical_base = _canonical_scope_text(base)
    if len(canonical_base) >= 4 and canonical_base in canonical_evidence:
        return True
    terms = [
        _canonical_scope_text(term)
        for term in re.split(r"(?:与|和|及|、|/)", base)
        if len(_canonical_scope_text(term)) >= 4
    ]
    return len(terms) >= 2 and all(term in canonical_evidence for term in terms)


def _is_non_assessment_bridge(field: str, value: str, anchor: str) -> bool:
    """Recognise a progression note that mentions, but does not assess, another topic."""
    if field != "design_intent":
        return False
    for sentence in re.split(r"[。！？;；\n]", value):
        if anchor not in sentence:
            continue
        if (
            ("为后续" in sentence and "基础" in sentence)
            or "作为后续" in sentence
            or "用于衔接" in sentence
            or "形成呼应" in sentence
        ):
            return True
    return False


def audit_practice_blueprint(plan: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic confirmation-time checks on a user-edited blueprint."""
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    items = [item for item in (blueprint.get("exercise_plan") or []) if isinstance(item, dict)]
    strategy = _clean(blueprint.get("generation_strategy"), 40)
    mode = _mode_kind(strategy)
    errors: list[str] = []
    warnings: list[str] = []
    ids = [str(item.get("plan_item_id") or "").strip() for item in items]
    if not items:
        errors.append("蓝图没有可生成的计划项。")
    if any(not item_id for item_id in ids):
        errors.append("蓝图存在缺少 plan_item_id 的计划项。")
    duplicates = sorted({item_id for item_id in ids if item_id and ids.count(item_id) > 1})
    if duplicates:
        errors.append(f"蓝图计划项 ID 重复：{duplicates}。")
    invalid_types = [str(item.get("question_type") or "") for item in items if str(item.get("question_type") or "") not in ALLOWED_TYPES]
    invalid_difficulties = [str(item.get("difficulty") or "") for item in items if str(item.get("difficulty") or "") not in ALLOWED_DIFFICULTIES]
    if invalid_types:
        errors.append(f"蓝图存在无效题型：{sorted(set(invalid_types))}。")
    if invalid_difficulties:
        errors.append(f"蓝图存在无效难度：{sorted(set(invalid_difficulties))}。")
    missing_fields = [
        str(item.get("number") or index + 1)
        for index, item in enumerate(items)
        if not _clean(item.get("target_skill"), 500)
        or not _clean(item.get("variation_type"), 200)
        or not _clean(item.get("design_intent"), 800)
    ]
    if missing_fields:
        errors.append(f"第 {','.join(missing_fields)} 项缺少目标能力、变化方式或设计意图。")
    invalid_difficulty_designs = [
        str(item.get("number") or index + 1)
        for index, item in enumerate(items)
        if not _unique_strings(item.get("difficulty_levers"), limit=4, item_limit=100)
        or any(lever not in DIFFICULTY_LEVERS for lever in _unique_strings(item.get("difficulty_levers"), limit=4, item_limit=100))
        or not _clean(item.get("difficulty_rationale"), 500)
    ]
    if invalid_difficulty_designs:
        warnings.append(
            f"第 {','.join(invalid_difficulty_designs)} 项缺少完整的难度方向或学生瓶颈说明；"
            "系统将使用对应层级的软难度意图，不因此阻断生成。"
        )
    source_scope = plan.get("source_scope") if isinstance(plan.get("source_scope"), dict) else {}
    source_catalog = [
        item for item in (plan.get("selected_source_questions") or source_scope.get("questions") or [])
        if isinstance(item, dict)
    ]
    source_by_id = {
        _clean(item.get("source_question_id"), 80): item
        for item in source_catalog
        if _clean(item.get("source_question_id"), 80)
    }
    source_slot_counts: dict[str, int] = {}
    for item in items:
        refs = _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
        if refs:
            source_slot_counts[refs[0]] = source_slot_counts.get(refs[0], 0) + 1
    if isinstance(blueprint.get("expected_source_counts"), dict):
        for source_id, raw_count in blueprint["expected_source_counts"].items():
            try:
                expected_count = max(0, int(raw_count))
            except (TypeError, ValueError):
                continue
            clean_source_id = _clean(source_id, 80)
            if clean_source_id:
                source_slot_counts[clean_source_id] = max(
                    source_slot_counts.get(clean_source_id, 0),
                    expected_count,
                )
    analysis_fallback_points = _string_list(
        (plan.get("source_analysis") or {}).get("knowledge_points")
        if isinstance(plan.get("source_analysis"), dict)
        else [],
        limit=60,
    )
    missing_required_points: list[str] = []
    invalid_required_points: list[str] = []
    expected_scope_points: list[str] = []
    for source in source_catalog:
        for point in _string_list(source.get("knowledge_points"), limit=60):
            if point not in expected_scope_points:
                expected_scope_points.append(point)
    planned_scope_points: list[str] = []
    image_source_ids = {
        source_id
        for source_id, source in source_by_id.items()
        if "⟦IMAGE_REF:" in _clean(source.get("source_content") or source.get("stem_excerpt"), 18000)
    }
    image_dependent_source_ids: set[str] = set()
    boundary_review_items: list[str] = []
    cross_source_leak_items: list[str] = []
    cross_source_reference_items: list[str] = []
    findings: list[dict[str, Any]] = []
    planned_points_by_source: dict[str, list[str]] = {}
    for index, item in enumerate(items, start=1):
        required = _unique_strings(item.get("required_knowledge_points"), limit=60, item_limit=500)
        refs = _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
        if item.get("stem_figure_required") is True:
            image_dependent_source_ids.update(ref for ref in refs if ref in image_source_ids)
        expected = _required_knowledge_points_for_refs(refs, list(source_by_id.values()), analysis_fallback_points)
        partitioned_knowledge_item = (
            strategy == "knowledge_item_wise"
            and len(refs) == 1
            and source_slot_counts.get(refs[0], 0) > 1
        )
        if expected and not required:
            missing_required_points.append(str(item.get("number") or index))
        if expected:
            if mode == "single_source" and not partitioned_knowledge_item and set(required) != set(expected):
                invalid_required_points.append(str(item.get("number") or index))
            elif (mode == "comprehensive" or partitioned_knowledge_item) and not set(required).issubset(set(expected)):
                invalid_required_points.append(str(item.get("number") or index))
        if refs:
            for point in required:
                if point not in planned_points_by_source.setdefault(refs[0], []):
                    planned_points_by_source[refs[0]].append(point)
        for point in required:
            if point not in planned_scope_points:
                planned_scope_points.append(point)
        constraints = item.get("required_constraints") if isinstance(item.get("required_constraints"), dict) else {}
        boundaries = _unique_strings(constraints.get("applicable_boundaries"), limit=20, item_limit=500)
        design_fields = {
            "target_skill": _clean(item.get("target_skill"), 500),
            "variation_type": _clean(item.get("variation_type"), 500),
            "design_intent": _clean(item.get("design_intent"), 800),
            "difficulty_rationale": _clean(item.get("difficulty_rationale"), 800),
            "difficulty_levers": " ".join(
                _unique_strings(item.get("difficulty_levers"), limit=12, item_limit=200)
            ),
        }
        design_text = " ".join(design_fields.values())
        own_source_text = " ".join(_scope_evidence_text(source_by_id.get(ref, {})) for ref in refs)
        foreign_anchors: dict[str, list[dict[str, str]]] = {}
        for source_id, other_source in source_by_id.items():
            if source_id in refs:
                continue
            for point in _string_list(other_source.get("knowledge_points"), limit=60):
                anchor = re.sub(r"[（(].*?[）)]", "", point)
                anchor = re.sub(r"(?:的)?(?:定义|确定步骤|标定步骤|表示方法|计算方法|基本原理|原理|结果)$", "", anchor)
                anchor = _clean(anchor, 100)
                if len(anchor) >= 4 and not _source_evidence_covers_anchor(anchor, own_source_text):
                    foreign_anchors.setdefault(anchor, []).append({
                        "source_id": source_id,
                        "source_title": _clean(other_source.get("title"), 200),
                        "knowledge_point": _clean(point, 500),
                    })
        anchor_matches = []
        bridge_matches = []
        for anchor, foreign_sources in foreign_anchors.items():
            matched_fields = [
                name
                for name, value in design_fields.items()
                if anchor in value and not _is_non_assessment_bridge(name, value, anchor)
            ]
            contextual_fields = [
                name
                for name, value in design_fields.items()
                if anchor in value and _is_non_assessment_bridge(name, value, anchor)
            ]
            if matched_fields:
                anchor_matches.append({
                    "anchor": anchor,
                    "matched_fields": matched_fields,
                    "foreign_sources": foreign_sources,
                })
            if contextual_fields:
                bridge_matches.append({
                    "anchor": anchor,
                    "matched_fields": contextual_fields,
                    "foreign_sources": foreign_sources,
                })
        if anchor_matches:
            item_number = str(item.get("number") or index)
            cross_source_leak_items.append(item_number)
            findings.append({
                "code": "cross_source_design_leak",
                "item_number": item_number,
                "plan_item_id": _clean(item.get("plan_item_id"), 120),
                "bound_source_refs": refs,
                "design_fields": design_fields,
                "matches": anchor_matches,
            })
        if bridge_matches:
            item_number = str(item.get("number") or index)
            cross_source_reference_items.append(item_number)
            findings.append({
                "code": "cross_source_context_reference",
                "severity": "warning",
                "item_number": item_number,
                "plan_item_id": _clean(item.get("plan_item_id"), 120),
                "bound_source_refs": refs,
                "matches": bridge_matches,
            })
        compact_boundaries = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", "".join(boundaries))
        boundary_phrases = {
            compact_boundaries[start : start + size]
            for size in range(2, min(6, len(compact_boundaries)) + 1)
            for start in range(0, len(compact_boundaries) - size + 1)
        }
        negates_boundary = any(
            f"{prefix}{phrase}" in design_text
            for phrase in boundary_phrases
            for prefix in ("非", "不", "无")
        )
        if boundaries and ("改变边界" in design_text or negates_boundary):
            boundary_review_items.append(str(item.get("number") or index))
    if missing_required_points:
        errors.append(f"第 {','.join(missing_required_points)} 项缺少必考知识点组合。")
    if invalid_required_points:
        message = "第 {} 项的必考知识点与绑定来源规则不一致。"
        errors.append(message.format(",".join(invalid_required_points)))
    if strategy == "knowledge_item_wise":
        incomplete_sources = []
        for source_id, count_for_source in source_slot_counts.items():
            if count_for_source <= 1:
                continue
            expected = _required_knowledge_points_for_refs([source_id], list(source_by_id.values()))
            actual = planned_points_by_source.get(source_id, [])
            missing = [point for point in expected if point not in actual]
            if missing:
                missing_text = "、".join(missing[:6])
                incomplete_sources.append(f"{source_id}缺少{missing_text}")
        if incomplete_sources:
            errors.append("逐知识单元多题分配未在整组覆盖全部确认知识点：" + "；".join(incomplete_sources) + "。")
    if cross_source_leak_items:
        errors.append(
            f"第 {','.join(dict.fromkeys(cross_source_leak_items))} 项的目标、设计意图或难度说明混入了未绑定来源的子主题。"
        )
    if cross_source_reference_items:
        warnings.append(
            f"第 {','.join(dict.fromkeys(cross_source_reference_items))} 项仅在教学衔接说明中提到其它主题；"
            "该说明不扩大本题必考范围，本次不阻断蓝图。"
        )
    missing_scope_points = [point for point in expected_scope_points if point not in planned_scope_points]
    if missing_scope_points:
        message = f"蓝图未覆盖已确认范围的知识点：{'、'.join(missing_scope_points[:12])}。"
        if mode == "comprehensive":
            if len(items) < len(source_catalog):
                warnings.append(f"当前题量少于已选来源数，{message} 建议增加题量以获得更完整覆盖。")
            else:
                warnings.append(f"{message} 请在蓝图审查中确认是否需要补充知识点。")
        else:
            errors.append(message)
    image_without_dependency = sorted(image_source_ids - image_dependent_source_ids)
    if image_without_dependency:
        warnings.append(
            "所选来源包含原图，但对应蓝图均未要求学生读取题干配图："
            f"{','.join(image_without_dependency[:8])}。请确认这是有意改为纯文字训练，而不是遗漏读图能力。"
        )
    if boundary_review_items:
        warnings.append(
            f"第 {','.join(boundary_review_items)} 项声明改变已有适用边界；"
            "请确认新条件仍在来源知识范围内且信息足以作答，不能仅因是变式就引入未提供的理论或参数。"
        )
    signatures = []
    for item in items:
        signatures.append((
            tuple(_unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)),
            _clean(item.get("question_type"), 20),
            _clean(item.get("difficulty"), 20),
            _clean(item.get("target_skill"), 500),
            _clean(item.get("variation_type"), 200),
        ))
    duplicate_signatures = sum(1 for signature in set(signatures) if signatures.count(signature) > 1)
    knowledge_without_selected_scope = (
        _clean(plan.get("source_mode"), 30) == "knowledge"
        and not (plan.get("selected_source_questions") or [])
    )
    if duplicate_signatures:
        message = f"蓝图存在 {duplicate_signatures} 组完全重复的计划项。"
        (warnings if knowledge_without_selected_scope else errors).append(message)
    if not _clean(blueprint.get("training_goal"), 1000):
        warnings.append("蓝图尚未填写整体训练目标。")
    if not (blueprint.get("progression") or blueprint.get("design_notes")):
        warnings.append("蓝图缺少整体梯度或设计说明，建议人工补充。")
    mode_contract = validate_practice_mode_contract(plan)
    errors.extend(str(error) for error in mode_contract.get("errors") or [])
    warnings.extend(str(warning) for warning in mode_contract.get("warnings") or [])
    cover = plan.get("scope_cover") if isinstance(plan.get("scope_cover"), dict) else {}
    selected_units = int((cover.get("counts") or {}).get("selected_units") or 0)
    if selected_units > 0 and cover.get("complete") is False:
        if mode == "comprehensive":
            if len(items) < selected_units:
                warnings.append("当前题量少于已选来源数，蓝图未逐项覆盖全部来源单元。建议增加题量。")
            else:
                warnings.append("蓝图未逐项覆盖全部来源单元。请在蓝图审查中确认是否需要补充来源。")
        else:
            errors.append("蓝图未覆盖全部已确认来源单元。")
    return {
        "status": "blocked" if errors else ("warning" if warnings else "passed"),
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "findings": findings,
        "metrics": {
            "plan_count": len(items),
            "unique_plan_item_count": len(set(ids)),
            "duplicate_signature_count": duplicate_signatures,
        },
    }


def _raise_plan_gate_error(message: str, plan: dict[str, Any], failure_type: str) -> None:
    """Raise a user-facing gate error while preserving the rejected plan for support diagnosis."""
    error = ValueError(message)
    error.failure_context = {
        "schema_version": 1,
        "failure_type": failure_type,
        "source_mode": plan.get("source_mode"),
        "selected_source_questions": plan.get("selected_source_questions") or [],
        "source_analysis": plan.get("source_analysis") or {},
        "blueprint": plan.get("blueprint") or {},
        "blueprint_audit": plan.get("blueprint_audit") or {},
        "blueprint_audit_repair": plan.get("blueprint_audit_repair") or {},
        "mode_contract": plan.get("mode_contract") or {},
        "scope_cover": plan.get("scope_cover") or {},
        "blueprint_refinement": plan.get("blueprint_refinement") or {},
    }
    raise error


def ensure_practice_blueprint_defaults(plan: dict[str, Any]) -> dict[str, Any]:
    """Upgrade legacy or user-edited plans before applying the strict gate."""
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    items = [item for item in (blueprint.get("exercise_plan") or []) if isinstance(item, dict)]
    mode = _mode_kind(_clean(blueprint.get("generation_strategy"), 40))
    catalog = plan.get("selected_source_questions") or (plan.get("source_scope") or {}).get("questions") or []
    source_catalog = [item for item in catalog if isinstance(item, dict)]
    has_multiple_sources = len(source_catalog) >= 2
    source_analysis = plan.get("source_analysis") if isinstance(plan.get("source_analysis"), dict) else {}
    skills = _string_list(source_analysis.get("skills"), limit=20)
    fallback_points = _string_list(source_analysis.get("knowledge_points"), limit=60)
    include_source_content = _include_source_content_in_generation({}, plan)
    strategy = _clean(blueprint.get("generation_strategy"), 40)
    source_slot_counts: dict[str, int] = {}
    for item in items:
        refs = _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
        if refs:
            source_slot_counts[refs[0]] = source_slot_counts.get(refs[0], 0) + 1
    if isinstance(blueprint.get("expected_source_counts"), dict):
        for source_id, raw_count in blueprint["expected_source_counts"].items():
            try:
                expected_count = max(0, int(raw_count))
            except (TypeError, ValueError):
                continue
            clean_source_id = _clean(source_id, 80)
            if clean_source_id:
                source_slot_counts[clean_source_id] = max(source_slot_counts.get(clean_source_id, 0), expected_count)
    for index, item in enumerate(items):
        item.setdefault("number", index + 1)
        item["plan_item_id"] = _clean(item.get("plan_item_id"), 80) or f"plan_item_{index + 1:02d}"
        item["difficulty"] = _clean(item.get("difficulty"), 20) if _clean(item.get("difficulty"), 20) in ALLOWED_DIFFICULTIES else "进阶"
        previous_design_difficulty = _clean(item.get("difficulty_design_level"), 20)
        difficulty_changed = previous_design_difficulty in ALLOWED_DIFFICULTIES and previous_design_difficulty != item["difficulty"]
        structural_change = _clean(item.get("structural_change"), 100)
        if structural_change not in STRUCTURAL_CHANGE_TYPES:
            structural_change = _default_structural_change(index, mode=mode, has_multiple_sources=has_multiple_sources)
        item["structural_change"] = structural_change
        item["target_skill"] = _clean(item.get("target_skill"), 500) or (skills[index % len(skills)] if skills else "核心能力")
        item["variation_type"] = _clean(item.get("variation_type"), 200) or structural_change
        item["design_intent"] = _clean(item.get("design_intent"), 800) or f"围绕{item['target_skill']}完成{item['variation_type']}训练。"
        item["difficulty_levers"], item["difficulty_rationale"] = _difficulty_design(
            item["difficulty"],
            _clean(item.get("question_type"), 20),
            levers=None if difficulty_changed else item.get("difficulty_levers"),
            rationale=None if difficulty_changed else item.get("difficulty_rationale"),
            structural_change=structural_change,
            target_skill=item["target_skill"],
        )
        item["difficulty_design_level"] = item["difficulty"]
        if not item.get("source_refs") and _clean(item.get("source_question_id"), 80):
            item["source_refs"] = [_clean(item.get("source_question_id"), 80)]
        refs = _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
        allow_partition = strategy == "knowledge_item_wise" and bool(refs) and source_slot_counts.get(refs[0], 0) > 1
        item["required_knowledge_points"] = _required_knowledge_points_for_plan_item(
            refs,
            source_catalog,
            item.get("required_knowledge_points") or item.get("knowledge_points") or fallback_points,
            strategy,
            allow_partition=allow_partition,
        )
        item["required_constraints"] = _required_constraints_for_plan_item(
            refs,
            source_catalog,
            item.get("required_constraints"),
            strategy,
            source_analysis if len(source_catalog) <= 1 else None,
            allow_partition=allow_partition,
        )
        stem_figure_required = item.get("stem_figure_required") is True or item.get("requires_figure") is True
        item["stem_figure_required"] = stem_figure_required
        item["figure_design"] = _figure_design(item.get("figure_design"), required=stem_figure_required)
    blueprint["exercise_plan"] = items
    blueprint["include_source_content_in_generation"] = include_source_content
    plan["blueprint"] = blueprint
    plan["include_source_content_in_generation"] = include_source_content
    return plan


def _blueprint_multi_question_config(
    payload: dict[str, Any],
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    """Normalize the optional one-blueprint-item-to-many-exercises contract."""
    stored = blueprint.get("multi_question") if isinstance(blueprint.get("multi_question"), dict) else {}
    enabled = (
        payload.get("blueprint_multi_question_enabled") is True
        if "blueprint_multi_question_enabled" in payload
        else stored.get("enabled") is True
    )
    try:
        variants_per_item = int(
            payload.get("blueprint_variants_per_item")
            if "blueprint_variants_per_item" in payload
            else stored.get("variants_per_item") or 2
        )
    except (TypeError, ValueError):
        variants_per_item = 2
    variants_per_item = max(2, min(3, variants_per_item)) if enabled else 1
    mode = _clean(
        payload.get("blueprint_variant_mode")
        if "blueprint_variant_mode" in payload
        else stored.get("mode") or "progressive",
        30,
    )
    if mode not in {"progressive", "same_difficulty"}:
        mode = "progressive"
    base_item_count = len([item for item in blueprint.get("exercise_plan") or [] if isinstance(item, dict)])
    difficulty_precedence = "progressive"
    raw_counts = payload.get("difficulty_counts") if isinstance(payload.get("difficulty_counts"), dict) else None
    if raw_counts and base_item_count:
        exact_counts = normalize_difficulty_counts(payload, base_item_count)
        positive_levels = [level for level, value in exact_counts.items() if int(value or 0) > 0]
        difficulty_order = _nonnegative_int(payload.get("difficulty_selection_order"))
        variant_order = _nonnegative_int(payload.get("blueprint_variant_selection_order"))
        # A final explicit single-level allocation (for example, 全挑战) is
        # never diluted by a previously selected progressive template.  For
        # mixed allocations, the most recently changed control wins.  Older
        # clients have no order metadata, so their exact counts remain the
        # authoritative user choice.
        if (
            len(positive_levels) == 1
            or mode == "same_difficulty"
            or difficulty_order >= variant_order
            or (difficulty_order == 0 and variant_order == 0)
        ):
            difficulty_precedence = "confirmed_counts"
    return {
        "enabled": enabled,
        "variants_per_item": variants_per_item,
        "mode": mode,
        "difficulty_precedence": difficulty_precedence,
        "base_item_count": base_item_count,
        "total_count": base_item_count * variants_per_item if enabled else base_item_count,
    }


def _expand_blueprint_items_for_generation(
    exercise_plan: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create stable child generation slots while retaining the reviewed parent blueprint."""
    if not config.get("enabled"):
        return [copy.deepcopy(item) for item in exercise_plan]
    variants_per_item = int(config.get("variants_per_item") or 2)
    progressive_difficulties = {
        2: ["基础", "进阶"],
        3: ["基础", "进阶", "挑战"],
    }
    progressive_roles = ["基础巩固", "条件转换", "综合迁移"]
    confirmed_difficulty_roles = ["核心巩固", "条件转换", "综合迁移"]
    same_difficulty_roles = ["情境变换", "求解路径变换", "边界与比较变换"]
    progressive_changes = ["改变未知量", "改变求解路径", "比较与优化"]
    same_difficulty_changes = ["跨情境迁移", "改变求解路径", "增加边界条件"]
    expanded: list[dict[str, Any]] = []
    for parent_index, source_item in enumerate(exercise_plan, start=1):
        parent_id = _clean(source_item.get("plan_item_id"), 80) or f"plan_item_{parent_index:02d}"
        for variant_index in range(1, variants_per_item + 1):
            item = copy.deepcopy(source_item)
            progressive = config.get("mode") == "progressive"
            preserve_confirmed_difficulty = config.get("difficulty_precedence") == "confirmed_counts"
            role = (
                confirmed_difficulty_roles
                if progressive and preserve_confirmed_difficulty
                else progressive_roles if progressive
                else same_difficulty_roles
            )[variant_index - 1]
            structural_change = (progressive_changes if progressive else same_difficulty_changes)[variant_index - 1]
            difficulty = (
                progressive_difficulties[variants_per_item][variant_index - 1]
                if progressive and not preserve_confirmed_difficulty
                else _clean(source_item.get("difficulty"), 20) or "进阶"
            )
            item.update({
                "number": len(expanded) + 1,
                "plan_item_id": f"variant_plan_{parent_index:02d}_{variant_index:02d}",
                "parent_plan_item_id": parent_id,
                "variant_id": f"{parent_id}::variant::{variant_index}",
                "variant_index": variant_index,
                "variant_count": variants_per_item,
                "variant_mode": config.get("mode"),
                "variant_role": role,
                "difficulty": difficulty,
                "difficulty_design_level": difficulty,
                "structural_change": structural_change,
                "variation_type": f"{_clean(source_item.get('variation_type'), 160) or '结构变式'} · {role}",
                "design_intent": (
                    f"{_clean(source_item.get('design_intent'), 600)} "
                    f"本组第 {variant_index}/{variants_per_item} 道采用“{role}”，"
                    "必须与同组其它题在未知量、求解路径、边界条件、子问结构或应用情境上形成实质差异。"
                ).strip(),
            })
            item["difficulty_levers"], item["difficulty_rationale"] = _difficulty_design(
                difficulty,
                _clean(item.get("question_type"), 20),
                structural_change=structural_change,
                target_skill=item.get("target_skill"),
            )
            expanded.append(item)
    return expanded


def _normalize_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(value[:8]):
        if isinstance(raw, dict):
            text = _clean(raw.get("text"), 1200)
        else:
            text = _clean(raw, 1200)
        # Some models repeat the visible label inside the option body (for
        # example {"label": "A", "text": "A. ..."}). Labels are owned by
        # the renderer, so keep the stored option body label-free.
        text = re.sub(r"^\s*[A-Ha-h]\s*(?:[.．、:：]|[）)])\s*", "", text, count=1)
        text = re.sub(r"^\s*[（(]\s*[A-Ha-h]\s*[）)]\s*", "", text, count=1)
        if text:
            # 选项顺序由程序拥有，避免模型重复返回两个 A/B 等标签。
            rows.append({"label": chr(65 + index), "text": text})
    return rows


def _has_fill_in_blank(stem: Any) -> bool:
    """Return whether a fill-in question visibly provides a response slot."""
    text = _clean(stem, 6000)
    return bool(re.search(r"_{2,}|[（(]\s*[）)]", text))


def _question_structure_issue(
    exercise: dict[str, Any],
    *,
    question_type: str,
) -> dict[str, Any] | None:
    """Validate only the student-facing structure required by a blueprint.

    Practice generation deliberately excludes answers and explanations.  This
    gate therefore never uses answer-related fields: selection questions need
    visible options, while fill-in questions need a visible blank.
    """
    if not _clean(exercise.get("stem"), 6000):
        return {
            "code": "missing_stem",
            "message": "题目缺少题干。",
        }
    if question_type in {"单选题", "多选题"}:
        option_count = len(_normalize_options(exercise.get("options")))
        if option_count < 2:
            return {
                "code": "choice_options_missing",
                "message": f"{question_type}缺少有效选项（当前 {option_count} 个，至少需要 2 个）。",
            }
    if question_type == "填空题" and not _has_fill_in_blank(exercise.get("stem")):
        return {
            "code": "fill_in_blank_missing",
            "message": "填空题缺少可填写的空位。",
        }
    return None


def _effective_question_type(exercise: dict[str, Any], planned_item: dict[str, Any] | None = None) -> str:
    """Use the confirmed blueprint type whenever it is available."""
    planned_type = _clean((planned_item or {}).get("question_type"), 20)
    if planned_type in ALLOWED_TYPES:
        return planned_type
    return _clean(exercise.get("question_type"), 20)


def _normalize_formulas(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    for index, raw in enumerate(value[:16], start=1):
        if not isinstance(raw, dict):
            continue
        latex = _clean(raw.get("latex"), 3000)
        if latex:
            rows.append(
                {
                    "formula_id": _clean(raw.get("formula_id"), 50) or f"f{index}",
                    "latex": latex,
                    "location": _clean(raw.get("location"), 20) or "stem",
                    "display": bool(raw.get("display", True)),
                    "caption": _clean(raw.get("caption"), 300),
                }
            )
    return rows


def _normalize_tables(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    tables = []
    for index, raw in enumerate(value[:6], start=1):
        if not isinstance(raw, dict):
            continue
        headers = _string_list(raw.get("headers"), limit=10)
        rows = []
        for row in (raw.get("rows") or [])[:30]:
            if isinstance(row, list):
                rows.append([_clean(cell, 500) for cell in row[:10]])
        if headers or rows:
            tables.append(
                {
                    "table_id": _clean(raw.get("table_id"), 50) or f"t{index}",
                    "location": _clean(raw.get("location"), 20) or "stem",
                    "title": _clean(raw.get("title"), 300),
                    "headers": headers,
                    "rows": rows,
                }
            )
    return tables


def _normalize_generated_markup(value: Any, *, limit: int = 6000) -> tuple[str, bool]:
    """Repair deterministic markup defects and report anything still unsafe."""
    normalized = normalize_practice_markup(_clean(value, limit), limit=limit)
    return normalized, has_unrenderable_practice_markup(normalized)


def _normalize_generated_stem(value: Any, *, limit: int = 6000) -> tuple[str, bool]:
    """Apply the platform-owned generated-question layout after markup repair."""
    normalized, unsafe = _normalize_generated_markup(value, limit=limit)
    return normalize_practice_question_text(normalized, limit=limit), unsafe


def _normalize_generated_options(value: Any) -> tuple[list[dict[str, str]], bool]:
    options = _normalize_options(value)
    unsafe = False
    for option in options:
        option["text"], current_unsafe = _normalize_generated_markup(option.get("text"), limit=1200)
        unsafe = unsafe or current_unsafe
    return options, unsafe


def _normalize_generated_tables(value: Any) -> tuple[list[dict[str, Any]], bool]:
    tables = _normalize_tables(value)
    unsafe = False
    for table in tables:
        table["title"], current_unsafe = _normalize_generated_markup(table.get("title"), limit=300)
        unsafe = unsafe or current_unsafe
        headers: list[str] = []
        for header in table.get("headers") or []:
            normalized, current_unsafe = _normalize_generated_markup(header, limit=500)
            headers.append(normalized)
            unsafe = unsafe or current_unsafe
        table["headers"] = headers
        rows: list[list[str]] = []
        for row in table.get("rows") or []:
            normalized_row: list[str] = []
            for cell in row:
                normalized, current_unsafe = _normalize_generated_markup(cell, limit=500)
                normalized_row.append(normalized)
                unsafe = unsafe or current_unsafe
            rows.append(normalized_row)
        table["rows"] = rows
    return tables, unsafe


def _markdown_table_cells(line: str) -> list[str] | None:
    """Return cells for one pipe-table row, or None for ordinary text."""
    raw = str(line or "").strip()
    if "|" not in raw:
        return None
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    cells = [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", raw)]
    return cells if len(cells) >= 2 else None


def _is_markdown_table_divider(cells: list[str] | None) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _extract_markdown_tables(stem: str) -> tuple[str, list[dict[str, Any]]]:
    """Lift provider-written Markdown pipe tables into the structured table field.

    The generation contract already provides ``tables``. This fallback keeps a
    model that writes a Markdown table in ``stem`` from becoming literal pipes
    in the browser clipboard or a Word export.
    """
    lines = str(stem or "").split("\n")
    kept: list[str] = []
    tables: list[dict[str, Any]] = []
    index = 0
    table_number = 0
    while index < len(lines):
        headers = _markdown_table_cells(lines[index])
        divider = _markdown_table_cells(lines[index + 1]) if index + 1 < len(lines) else None
        if not headers or len(headers) < 2 or not divider or len(divider) != len(headers) or not _is_markdown_table_divider(divider):
            kept.append(lines[index])
            index += 1
            continue
        rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines):
            row = _markdown_table_cells(lines[cursor])
            if not row or len(row) != len(headers):
                break
            rows.append(row)
            cursor += 1
        if not rows:
            kept.append(lines[index])
            index += 1
            continue
        table_number += 1
        tables.append(
            {
                "table_id": f"markdown_t{table_number}",
                "location": "stem",
                "title": "",
                "headers": headers,
                "rows": rows,
            }
        )
        # Keep one separator line so the prose before and after a lifted table
        # stays as separate paragraphs in all renderers.
        if kept and kept[-1].strip():
            kept.append("")
        index = cursor
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, tables


def _merge_stem_markdown_tables(stem: str, tables: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    cleaned_stem, extracted = _extract_markdown_tables(stem)
    if not extracted:
        return stem, tables
    merged = list(tables)
    known = {
        (tuple(table.get("headers") or []), tuple(tuple(row) for row in (table.get("rows") or [])))
        for table in merged
        if isinstance(table, dict)
    }
    for table in extracted:
        signature = (tuple(table["headers"]), tuple(tuple(row) for row in table["rows"]))
        if signature not in known:
            table["table_id"] = f"t{len(merged) + 1}"
            merged.append(table)
            known.add(signature)
    return cleaned_stem, merged


def _normalize_figures(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    figures = []
    for index, raw in enumerate(value[:6], start=1):
        if not isinstance(raw, dict):
            continue
        series = []
        for raw_series in (raw.get("series") or [])[:8]:
            if not isinstance(raw_series, dict):
                continue
            points = []
            for point in (raw_series.get("points") or [])[:80]:
                if isinstance(point, list) and len(point) >= 2:
                    try:
                        points.append([float(point[0]), float(point[1])])
                    except (TypeError, ValueError):
                        continue
            series.append({"name": _clean(raw_series.get("name"), 100), "points": points})
        uses_chart_coordinates = any(len(row.get("points") or []) >= 2 for row in series)
        nodes: list[dict[str, Any]] = []
        for raw_node in (raw.get("nodes") or [])[:30]:
            if not isinstance(raw_node, dict):
                continue
            try:
                x = float(raw_node.get("x"))
                y = float(raw_node.get("y"))
            except (TypeError, ValueError):
                continue
            if not uses_chart_coordinates:
                x = max(0.0, min(1.0, x))
                y = max(0.0, min(1.0, y))
            node_id = _clean(raw_node.get("id"), 50)
            label = _clean(raw_node.get("label"), 200)
            if not node_id or not label:
                continue
            shape = _clean(raw_node.get("shape"), 20).lower()
            nodes.append({
                "id": node_id,
                "label": label,
                "x": x,
                "y": y,
                "shape": shape if shape in {"box", "circle", "ellipse"} else "box",
            })
        node_ids = {node["id"] for node in nodes}
        edges: list[dict[str, Any]] = []
        for raw_edge in (raw.get("edges") or [])[:50]:
            if not isinstance(raw_edge, dict):
                continue
            source = _clean(raw_edge.get("from"), 50)
            target = _clean(raw_edge.get("to"), 50)
            if source not in node_ids or target not in node_ids or source == target:
                continue
            edges.append({
                "from": source,
                "to": target,
                "label": _clean(raw_edge.get("label"), 120),
                "directed": raw_edge.get("directed") is not False,
            })
        semantic = raw.get("semantic_contract") if isinstance(raw.get("semantic_contract"), dict) else {}
        figures.append(
            {
                "figure_id": _clean(raw.get("figure_id"), 50) or f"g{index}",
                "location": _clean(raw.get("location"), 20) or "stem",
                "figure_type": _clean(raw.get("figure_type"), 30) or "diagram",
                "title": _clean(raw.get("title"), 300),
                "description": _clean(raw.get("description"), 1500),
                "x_label": _clean(raw.get("x_label"), 100),
                "y_label": _clean(raw.get("y_label"), 100),
                "series": series,
                "nodes": nodes,
                "edges": edges,
                "semantic_contract": {
                    "required_elements": _unique_strings(semantic.get("required_elements"), limit=20, item_limit=200),
                    "relationship_constraints": _unique_strings(semantic.get("relationship_constraints"), limit=20, item_limit=300),
                    "question_dependency": _clean(semantic.get("question_dependency"), 500),
                },
            }
        )
    return figures


def _figure_is_renderable(figure: dict[str, Any]) -> bool:
    if not isinstance(figure, dict):
        return False
    if any(
        isinstance(series, dict) and len(series.get("points") or []) >= 2
        for series in figure.get("series") or []
    ):
        return True
    nodes = [node for node in (figure.get("nodes") or []) if isinstance(node, dict) and node.get("id") and node.get("label")]
    return len(nodes) >= 2


def _plan_requires_stem_figure(item: dict[str, Any]) -> bool:
    return item.get("stem_figure_required") is True or item.get("requires_figure") is True


def _figure_design(value: Any, *, required: bool) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    kind = _clean(source.get("kind"), 30).lower()
    if kind not in {"line", "bar", "scatter", "diagram"}:
        kind = "diagram"
    return {
        "role": "stem_required" if required else "none",
        "kind": kind,
        "required_elements": _unique_strings(source.get("required_elements"), limit=20, item_limit=200),
        "relationship_constraints": _unique_strings(source.get("relationship_constraints"), limit=20, item_limit=300),
        "question_dependency": _clean(source.get("question_dependency"), 500),
    }


def _normalized_figure_term(value: Any) -> str:
    text = _clean(value, 500).lower()
    text = text.replace("压力", "p").replace("体积", "v")
    text = text.replace("坐标图", "坐标系").replace("图像", "图")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _figure_series(figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for figure in figures
        for row in (figure.get("series") or [])
        if isinstance(row, dict) and len(row.get("points") or []) >= 2
    ]


def _points_close(first: list[Any], second: list[Any]) -> bool:
    try:
        scale = max(1.0, *(abs(float(value)) for value in [*first[:2], *second[:2]]))
        return abs(float(first[0]) - float(second[0])) <= scale * 1e-6 and abs(float(first[1]) - float(second[1])) <= scale * 1e-6
    except (TypeError, ValueError, IndexError):
        return False


def _interpolated_series_y(series: dict[str, Any], x_value: float) -> float | None:
    """Return the piecewise-linear y value for a chart series at ``x_value``.

    Generated scientific plots are deliberately stored as plain point series.
    Interpolation lets the deterministic gate verify relationships between
    curves instead of trusting labels or a model-written semantic contract.
    """
    points = series.get("points") or []
    for first, second in zip(points, points[1:]):
        try:
            x1, y1 = float(first[0]), float(first[1])
            x2, y2 = float(second[0]), float(second[1])
        except (TypeError, ValueError, IndexError):
            continue
        lower, upper = sorted((x1, x2))
        tolerance = max(1.0, abs(x1), abs(x2), abs(x_value)) * 1e-9
        if x_value < lower - tolerance or x_value > upper + tolerance:
            continue
        if abs(x2 - x1) <= tolerance:
            if abs(x_value - x1) <= tolerance:
                return (y1 + y2) / 2.0
            continue
        ratio = (x_value - x1) / (x2 - x1)
        return y1 + ratio * (y2 - y1)
    return None


def _phase_diagram_relationship_issues(
    figures: list[dict[str, Any]],
    constraints: list[str],
) -> list[dict[str, Any]]:
    """Verify numeric consistency for two-boundary phase diagrams.

    The check is activated only when the confirmed blueprint explicitly asks
    for gas/liquid phase boundaries.  It catches a common generative failure:
    a plausible-looking chart whose curves cross incorrectly or whose staged
    path does not actually touch the phase boundaries.
    """
    constraint_text = "".join(_normalized_figure_term(value) for value in constraints)
    if not (
        "气相线" in constraint_text
        and "液相线" in constraint_text
        and ("两相区" in constraint_text or "相界" in constraint_text)
    ):
        return []
    series = _figure_series(figures)
    gas = next((row for row in series if "气相线" in _normalized_figure_term(row.get("name"))), None)
    liquid = next((row for row in series if "液相线" in _normalized_figure_term(row.get("name"))), None)
    if not gas or not liquid:
        return []

    gas_x = [float(point[0]) for point in gas.get("points") or []]
    liquid_x = [float(point[0]) for point in liquid.get("points") or []]
    if not gas_x or not liquid_x:
        return []
    overlap_min = max(min(gas_x), min(liquid_x))
    overlap_max = min(max(gas_x), max(liquid_x))
    raw_x = sorted({x for x in [*gas_x, *liquid_x] if overlap_min <= x <= overlap_max})
    sample_x = sorted({*raw_x, *((first + second) / 2.0 for first, second in zip(raw_x, raw_x[1:]))})
    comparisons: list[tuple[float, float, float]] = []
    for x_value in sample_x:
        gas_y = _interpolated_series_y(gas, x_value)
        liquid_y = _interpolated_series_y(liquid, x_value)
        if gas_y is not None and liquid_y is not None:
            comparisons.append((x_value, gas_y, liquid_y))
    if not comparisons:
        return []
    y_scale = max(1.0, *(abs(value) for _, gas_y, liquid_y in comparisons for value in (gas_y, liquid_y)))
    tolerance = y_scale * 1e-4
    signs = {
        1 if gas_y - liquid_y > tolerance else -1
        for _, gas_y, liquid_y in comparisons
        if abs(gas_y - liquid_y) > tolerance
    }
    issues: list[dict[str, Any]] = []
    if len(signs) > 1:
        issues.append({
            "code": "figure_phase_boundaries_cross",
            "message": "题图的气相线与液相线在两相区内错误交叉。",
        })

    staged_constraint = any(
        "逐级" in _normalized_figure_term(value)
        and "相界" in _normalized_figure_term(value)
        for value in constraints
    )
    paths = [
        row for row in series
        if row is not gas
        and row is not liquid
        and re.search(r"路径|逐级|汽化|冷凝", _clean(row.get("name"), 100))
    ]
    if staged_constraint and paths:
        y_values = [value for _, gas_y, liquid_y in comparisons for value in (gas_y, liquid_y)]
        boundary_span = max(y_values) - min(y_values)
        boundary_tolerance = max(tolerance * 10.0, boundary_span * 0.02)
        for path in paths:
            off_boundary = []
            for point_index, point in enumerate((path.get("points") or [])[1:], start=2):
                try:
                    x_value, y_value = float(point[0]), float(point[1])
                except (TypeError, ValueError, IndexError):
                    off_boundary.append(point_index)
                    continue
                boundary_values = [
                    value
                    for value in (
                        _interpolated_series_y(gas, x_value),
                        _interpolated_series_y(liquid, x_value),
                    )
                    if value is not None
                ]
                if not boundary_values or min(abs(y_value - value) for value in boundary_values) > boundary_tolerance:
                    off_boundary.append(point_index)
            if off_boundary:
                issues.append({
                    "code": "figure_staged_path_off_boundary",
                    "message": "题图的逐级汽化/冷凝路径没有在气、液相界之间交替落点。",
                    "series": _clean(path.get("name"), 100),
                    "point_indexes": off_boundary[:12],
                })
    return issues


def _series_x_at_y_between(
    series: dict[str, Any],
    y_value: float,
    *,
    start_x: float,
    end_x: float,
) -> float | None:
    lower, upper = sorted((start_x, end_x))
    direction = 1.0 if end_x >= start_x else -1.0
    roots: list[float] = []
    for first, second in zip(series.get("points") or [], (series.get("points") or [])[1:]):
        try:
            x1, y1 = float(first[0]), float(first[1])
            x2, y2 = float(second[0]), float(second[1])
        except (TypeError, ValueError, IndexError):
            continue
        segment_lower, segment_upper = sorted((x1, x2))
        if segment_upper < lower or segment_lower > upper:
            continue
        y_tolerance = max(1.0, abs(y1), abs(y2), abs(y_value)) * 1e-8
        if y_value < min(y1, y2) - y_tolerance or y_value > max(y1, y2) + y_tolerance:
            continue
        if abs(y2 - y1) <= y_tolerance:
            candidates = [max(lower, segment_lower), min(upper, segment_upper)]
        else:
            ratio = (y_value - y1) / (y2 - y1)
            candidates = [x1 + ratio * (x2 - x1)] if -1e-8 <= ratio <= 1 + 1e-8 else []
        roots.extend(
            candidate
            for candidate in candidates
            if lower - 1e-8 <= candidate <= upper + 1e-8
            and direction * (candidate - start_x) > max(1.0, abs(start_x)) * 1e-7
        )
    if not roots:
        return None
    return min(roots, key=lambda value: direction * (value - start_x))


def _stabilize_phase_diagram_geometry(figure: dict[str, Any], design: dict[str, Any]) -> None:
    """Deterministically repair a gas/liquid boundary chart and its step path.

    This is intentionally contract-gated.  It never invents a phase diagram
    for an unrelated chart; it only normalizes numeric geometry already
    requested and returned by the model.
    """
    constraints = design.get("relationship_constraints") or []
    constraint_text = "".join(_normalized_figure_term(value) for value in constraints)
    if not (
        "气相线" in constraint_text
        and "液相线" in constraint_text
        and ("两相区" in constraint_text or "相界" in constraint_text)
    ):
        return
    series = [row for row in (figure.get("series") or []) if isinstance(row, dict)]
    gas = next((row for row in series if "气相线" in _normalized_figure_term(row.get("name"))), None)
    liquid = next((row for row in series if "液相线" in _normalized_figure_term(row.get("name"))), None)
    if not gas or not liquid:
        return
    gas_x = [float(point[0]) for point in gas.get("points") or []]
    liquid_x = [float(point[0]) for point in liquid.get("points") or []]
    if len(gas_x) < 2 or len(liquid_x) < 2:
        return
    overlap_min = max(min(gas_x), min(liquid_x))
    overlap_max = min(max(gas_x), max(liquid_x))
    x_values = sorted({x for x in [*gas_x, *liquid_x] if overlap_min <= x <= overlap_max})
    paired = []
    for x_value in x_values:
        gas_y = _interpolated_series_y(gas, x_value)
        liquid_y = _interpolated_series_y(liquid, x_value)
        if gas_y is not None and liquid_y is not None:
            paired.append((x_value, max(gas_y, liquid_y), min(gas_y, liquid_y)))
    if len(paired) < 5:
        return

    nodes = [node for node in (figure.get("nodes") or []) if isinstance(node, dict)]
    azeotrope = next((node for node in nodes if "恒沸" in _normalized_figure_term(node.get("label"))), None)
    if azeotrope is not None:
        try:
            azeotrope_x = float(azeotrope.get("x"))
        except (TypeError, ValueError):
            azeotrope_x = float("nan")
    else:
        azeotrope_x = float("nan")
    if not math.isfinite(azeotrope_x) or not overlap_min < azeotrope_x < overlap_max:
        interior = paired[1:-1]
        if not interior:
            return
        azeotrope_x = min(interior, key=lambda row: abs(row[1] - row[2]))[0]
    try:
        supplied_azeotrope_y = float(azeotrope.get("y")) if azeotrope is not None else float("nan")
    except (TypeError, ValueError):
        supplied_azeotrope_y = float("nan")
    raw_azeotrope_gas_y = _interpolated_series_y(gas, azeotrope_x)
    raw_azeotrope_liquid_y = _interpolated_series_y(liquid, azeotrope_x)
    if raw_azeotrope_gas_y is None or raw_azeotrope_liquid_y is None:
        return
    azeotrope_y = (
        supplied_azeotrope_y
        if math.isfinite(supplied_azeotrope_y)
        else (raw_azeotrope_gas_y + raw_azeotrope_liquid_y) / 2.0
    )

    # A model can return two curves that have the right labels but cross or
    # form an extra false extremum. Rebuild the two-phase envelope from the
    # returned pure-component endpoints and confirmed azeotrope. This retains
    # the supplied scale while enforcing one physically coherent envelope.
    left_endpoint_y = (paired[0][1] + paired[0][2]) / 2.0
    right_endpoint_y = (paired[-1][1] + paired[-1][2]) / 2.0
    envelope_x = sorted({*(row[0] for row in paired), azeotrope_x})
    gas_points: list[list[float]] = []
    liquid_points: list[list[float]] = []
    for x_value in envelope_x:
        if x_value <= azeotrope_x:
            width = max(azeotrope_x - overlap_min, 1e-12)
            fraction = max(0.0, min(1.0, (x_value - overlap_min) / width))
            endpoint_y = left_endpoint_y
        else:
            width = max(overlap_max - azeotrope_x, 1e-12)
            fraction = max(0.0, min(1.0, (overlap_max - x_value) / width))
            endpoint_y = right_endpoint_y
        base_y = endpoint_y + fraction * (azeotrope_y - endpoint_y)
        separation = abs(endpoint_y - azeotrope_y) * 0.08 * math.sin(math.pi * fraction)
        gas_points.append([x_value, base_y + separation / 2.0])
        liquid_points.append([x_value, base_y - separation / 2.0])
    gas["points"] = gas_points
    liquid["points"] = liquid_points
    if azeotrope is not None:
        azeotrope["x"], azeotrope["y"] = azeotrope_x, azeotrope_y

    paths = [
        row for row in series
        if row is not gas
        and row is not liquid
        and re.search(r"路径|逐级|汽化|冷凝", _clean(row.get("name"), 100))
    ]
    for path in paths:
        initial_node = next((node for node in nodes if "初始" in _normalized_figure_term(node.get("label"))), None)
        try:
            current_x = float(initial_node.get("x")) if initial_node is not None else float(path["points"][0][0])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not overlap_min < current_x < overlap_max or abs(current_x - azeotrope_x) <= 1e-8:
            continue
        current_y = _interpolated_series_y(liquid, current_x)
        if current_y is None:
            continue
        repaired_path = [[current_x, current_y]]
        for _ in range(3):
            vapor_x = _series_x_at_y_between(gas, current_y, start_x=current_x, end_x=azeotrope_x)
            if vapor_x is None:
                break
            repaired_path.append([vapor_x, current_y])
            next_y = _interpolated_series_y(liquid, vapor_x)
            if next_y is None:
                break
            repaired_path.append([vapor_x, next_y])
            current_x, current_y = vapor_x, next_y
        if len(repaired_path) >= 5:
            path["points"] = repaired_path
            if initial_node is not None:
                initial_node["x"], initial_node["y"] = repaired_path[0]


def _has_axis_pair(figures: list[dict[str, Any]], element: str) -> bool:
    normalized = _normalized_figure_term(element)
    if "pv" not in normalized and "vp" not in normalized:
        return False
    return any(
        "v" in _normalized_figure_term(figure.get("x_label"))
        and "p" in _normalized_figure_term(figure.get("y_label"))
        for figure in figures
    )


def _series_has_segment(series: dict[str, Any], *, horizontal: bool = False, vertical: bool = False) -> bool:
    points = series.get("points") or []
    for first, second in zip(points, points[1:]):
        try:
            dx = abs(float(second[0]) - float(first[0]))
            dy = abs(float(second[1]) - float(first[1]))
            scale = max(1.0, abs(float(first[0])), abs(float(first[1])), abs(float(second[0])), abs(float(second[1])))
        except (TypeError, ValueError, IndexError):
            continue
        if horizontal and dx > scale * 1e-6 and dy <= scale * 1e-6:
            return True
        if vertical and dy > scale * 1e-6 and dx <= scale * 1e-6:
            return True
    return False


def _series_has_horizontal_then_vertical(series: dict[str, Any]) -> bool:
    points = series.get("points") or []
    horizontal_indexes: list[int] = []
    vertical_indexes: list[int] = []
    for index, (first, second) in enumerate(zip(points, points[1:])):
        try:
            dx = abs(float(second[0]) - float(first[0]))
            dy = abs(float(second[1]) - float(first[1]))
            scale = max(1.0, abs(float(first[0])), abs(float(first[1])), abs(float(second[0])), abs(float(second[1])))
        except (TypeError, ValueError, IndexError):
            continue
        if dx > scale * 1e-6 and dy <= scale * 1e-6:
            horizontal_indexes.append(index)
        if dy > scale * 1e-6 and dx <= scale * 1e-6:
            vertical_indexes.append(index)
    return any(horizontal < vertical for horizontal in horizontal_indexes for vertical in vertical_indexes)


def _figure_element_present(element: str, figures: list[dict[str, Any]], visible_values: list[str]) -> bool:
    normalized = _normalized_figure_term(element)
    visible_normalized = [_normalized_figure_term(value) for value in visible_values if _clean(value, 500)]
    combined = "".join(visible_normalized)
    series = _figure_series(figures)
    nodes = [node for figure in figures for node in (figure.get("nodes") or []) if isinstance(node, dict)]

    if _has_axis_pair(figures, element):
        return True
    if "终态点1和终态点2" in normalized or ("终态点" in normalized and "和" in normalized):
        return len([node for node in nodes if "终态" in _normalized_figure_term(node.get("label"))]) >= 2
    if "初态点" in normalized:
        if any("初态" in _normalized_figure_term(node.get("label")) for node in nodes):
            return True
        starts = [row.get("points", [])[0] for row in series if row.get("points")]
        return len(starts) >= 2 and any(_points_close(starts[0], point) for point in starts[1:])

    # Two independently visible domain elements are often written as one
    # planning phrase. The schema stores them as separate labels, so prove
    # each side instead of requiring the whole sentence in one label.
    conjunction_parts = [part.strip() for part in re.split(r"[、，,与]", str(element or "")) if part.strip()]
    if len(conjunction_parts) >= 2:
        generic_suffix = re.compile(r"(?:两|各)?(?:条|个)?(?:成分线|曲线|节点|位置|标记|点)$")
        normalized_parts = [
            _normalized_figure_term(generic_suffix.sub("", part))
            for part in conjunction_parts
        ]
        if all(
            part
            and (
                part in combined
                or any(SequenceMatcher(None, part, value).ratio() >= 0.78 for value in visible_normalized if value)
            )
            for part in normalized_parts
        ):
            return True

    # Compound annotation requirements are often phrased as one instruction,
    # e.g. ``标注 II、III 阶段`` or ``标出 A、B 两点``. The figure schema
    # correctly stores those as separate visible labels. Prove every requested
    # ASCII/Roman label instead of requiring the instruction sentence itself
    # to appear verbatim in one label.
    if re.search(r"[、，,]", str(element or "")):
        requested_labels = list(dict.fromkeys(
            match.group(0).lower()
            for match in re.finditer(
                r"(?<![A-Za-z0-9])(?:[IVX]{2,}|[A-Z]|\d+)(?![A-Za-z0-9])",
                str(element or ""),
                flags=re.IGNORECASE,
            )
        ))
        if len(requested_labels) >= 2:
            raw_visible = [str(value or "") for value in visible_values]
            labels_present = all(
                any(
                    re.search(
                        rf"(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])",
                        visible,
                        flags=re.IGNORECASE,
                    )
                    for visible in raw_visible
                )
                for label in requested_labels
            )
            semantic_tail = re.sub(
                r"(?:请)?(?:标注|标出|注明|显示|体现|绘制)|"
                r"(?<![A-Za-z0-9])(?:[IVX]{2,}|[A-Z]|\d+)(?![A-Za-z0-9])|"
                r"[、，,]|两|各|和|及|与",
                "",
                str(element or ""),
                flags=re.IGNORECASE,
            )
            tail_normalized = _normalized_figure_term(semantic_tail)
            if labels_present and (not tail_normalized or tail_normalized in combined):
                return True

    base = re.split(r"[（(]", element, maxsplit=1)[0]
    base_normalized = _normalized_figure_term(base)
    base_present = bool(base_normalized and (
        base_normalized in combined
        or any(SequenceMatcher(None, base_normalized, value).ratio() >= 0.78 for value in visible_normalized if value)
    ))
    if "水平线" in normalized:
        horizontal = any(_series_has_segment(row, horizontal=True) for row in series)
        if "终压" in normalized:
            named = any("终压" in _normalized_figure_term(row.get("name")) for row in series)
            return horizontal and (named or base_present)
        if "后垂直线" not in normalized and "再垂直线" not in normalized:
            return horizontal and (base_present or not base_normalized)
    if "垂直线" in normalized:
        vertical = any(_series_has_segment(row, vertical=True) for row in series)
        if "水平线" in normalized:
            return base_present and vertical and any(_series_has_horizontal_then_vertical(row) for row in series)
        return vertical and (base_present or not base_normalized)
    if normalized in combined or any(normalized in value for value in visible_normalized):
        return True
    return base_present


def _figure_relationship_issues(figures: list[dict[str, Any]], constraints: list[str]) -> list[dict[str, Any]]:
    """Validate deterministic geometric relationships when the schema can prove them.

    Unknown natural-language constraints remain for human/subject review. They
    are not rejected merely because the deterministic renderer cannot prove
    them yet.
    """
    series = _figure_series(figures)
    process_series = [
        row for row in series
        if not re.search(r"终压|辅助|坐标轴|水平线|垂直线", _clean(row.get("name"), 100))
    ]
    issues: list[dict[str, Any]] = []
    for constraint in constraints:
        normalized = _normalized_figure_term(constraint)
        applicable = False
        passed = True
        if "同一初态" in normalized and len(process_series) >= 2:
            applicable = True
            starts = [row.get("points", [])[0] for row in process_series]
            passed = all(_points_close(starts[0], point) for point in starts[1:])
        elif ("相同终压" in normalized or "终态p相同" in normalized) and len(process_series) >= 2:
            applicable = True
            ends = [row.get("points", [])[-1] for row in process_series]
            try:
                values = [float(point[1]) for point in ends]
                scale = max(1.0, *(abs(value) for value in values))
                passed = max(values) - min(values) <= scale * 1e-6
            except (TypeError, ValueError, IndexError):
                passed = False
        elif "右侧" in normalized:
            external = next((row for row in process_series if "恒外压" in _normalized_figure_term(row.get("name"))), None)
            reversible = next((row for row in process_series if "可逆" in _normalized_figure_term(row.get("name")) and row is not external), None)
            if external and reversible:
                applicable = True
                try:
                    passed = float(external["points"][-1][0]) > float(reversible["points"][-1][0])
                except (TypeError, ValueError, IndexError):
                    passed = False
        if applicable and not passed:
            issues.append({
                "code": "figure_relationship_constraint_failed",
                "message": "题图数据不满足蓝图规定的几何关系。",
                "relationship_constraint": constraint,
            })
    issues.extend(_phase_diagram_relationship_issues(figures, constraints))
    return issues


def _complete_generated_figure(exercise: dict[str, Any], planned_item: dict[str, Any]) -> None:
    """Fill deterministic chart annotations that should not depend on prose generation."""
    if not _plan_requires_stem_figure(planned_item):
        return
    figures = [figure for figure in (exercise.get("figures") or []) if isinstance(figure, dict)]
    if not figures:
        return
    design = _figure_design(planned_item.get("figure_design"), required=True)
    figure = next((row for row in figures if _figure_series([row])), figures[0])
    _stabilize_phase_diagram_geometry(figure, design)
    series = [
        row for row in _figure_series([figure])
        if not re.search(r"终压|辅助|坐标轴|水平线|垂直线", _clean(row.get("name"), 100))
    ]
    required_text = " ".join(design["required_elements"])
    if re.search(r"P\s*[-—–]?\s*V|P.?V", required_text, flags=re.IGNORECASE):
        figure["x_label"] = _clean(figure.get("x_label"), 100) or "V"
        figure["y_label"] = _clean(figure.get("y_label"), 100) or "P"

    nodes = [node for node in (figure.get("nodes") or []) if isinstance(node, dict)]
    used_ids = {_clean(node.get("id"), 50) for node in nodes}

    def upsert_node(label: str, point: list[Any], fallback_id: str) -> None:
        node = next((row for row in nodes if _normalized_figure_term(row.get("label")) == _normalized_figure_term(label)), None)
        if node is None:
            node_id = fallback_id
            suffix = 2
            while node_id in used_ids:
                node_id = f"{fallback_id}_{suffix}"
                suffix += 1
            node = {"id": node_id, "label": label, "shape": "circle"}
            nodes.append(node)
            used_ids.add(node_id)
        elif not _clean(node.get("id"), 50):
            node_id = fallback_id
            suffix = 2
            while node_id in used_ids:
                node_id = f"{fallback_id}_{suffix}"
                suffix += 1
            node["id"] = node_id
            used_ids.add(node_id)
        node["shape"] = _clean(node.get("shape"), 20) or "circle"
        node["x"], node["y"] = point[0], point[1]

    starts = [row.get("points", [])[0] for row in series if row.get("points")]
    ends = [row.get("points", [])[-1] for row in series if row.get("points")]
    if starts:
        common_start = starts[0]
        if len(starts) == 1 or all(_points_close(common_start, point) for point in starts[1:]):
            for node in nodes:
                if "初态" in _clean(node.get("label"), 200):
                    node["x"], node["y"] = common_start[0], common_start[1]
    for node in nodes:
        label = _clean(node.get("label"), 200)
        if "终态" not in label:
            continue
        qualifiers = [term for term in ("恒温", "绝热", "恒外压", "等温") if term in label]
        matched = next(
            (
                row for row in series
                if qualifiers and all(term in _clean(row.get("name"), 100) for term in qualifiers)
            ),
            None,
        )
        if matched and matched.get("points"):
            endpoint = matched["points"][-1]
            node["x"], node["y"] = endpoint[0], endpoint[1]
    if "初态点" in required_text and starts:
        common_start = starts[0]
        if len(starts) == 1 or all(_points_close(common_start, point) for point in starts[1:]):
            upsert_node("初态点", common_start, "initial_state")
    if "终态点1和终态点2" in required_text and len(ends) >= 2:
        upsert_node("终态点1", ends[0], "final_state_1")
        upsert_node("终态点2", ends[1], "final_state_2")
    figure["nodes"] = nodes

    if "终压水平线" in required_text and len(ends) >= 2:
        try:
            terminal_pressures = [float(point[1]) for point in ends]
            scale = max(1.0, *(abs(value) for value in terminal_pressures))
            same_pressure = max(terminal_pressures) - min(terminal_pressures) <= scale * 1e-6
            already_present = any("终压" in _normalized_figure_term(row.get("name")) for row in _figure_series([figure]))
            if same_pressure and not already_present:
                xs = [float(point[0]) for row in _figure_series([figure]) for point in (row.get("points") or [])]
                figure.setdefault("series", []).append({
                    "name": "终压水平线",
                    "points": [[min(xs), terminal_pressures[0]], [max(xs), terminal_pressures[0]]],
                })
        except (TypeError, ValueError, IndexError):
            pass
    figure["semantic_contract"] = {
        "required_elements": list(design["required_elements"]),
        "relationship_constraints": list(design["relationship_constraints"]),
        "question_dependency": design["question_dependency"],
    }


def _exercise_figure_issues(exercise: dict[str, Any], planned_item: dict[str, Any], *, batch_index: Any = None) -> list[dict[str, Any]]:
    if not _plan_requires_stem_figure(planned_item):
        return []
    prefix = {"batch_index": batch_index} if batch_index is not None else {}
    figures = [figure for figure in (exercise.get("figures") or []) if isinstance(figure, dict)]
    issues: list[dict[str, Any]] = []
    if not figures:
        return [{**prefix, "code": "missing_stem_figure", "message": "蓝图要求题干依赖图，但未返回 figures。"}]
    if not any(_figure_is_renderable(figure) for figure in figures):
        issues.append({**prefix, "code": "unrenderable_stem_figure", "message": "题图只有文字说明，没有可渲染的数据点或节点关系。"})
    if not re.search(r"图|曲线|坐标|示意", _clean(exercise.get("stem"), 6000)):
        issues.append({**prefix, "code": "stem_does_not_reference_figure", "message": "题干没有明确引用所附题图。"})
    design = _figure_design(planned_item.get("figure_design"), required=True)
    dependency = _clean(design.get("question_dependency"), 500)
    # A stem chart is evidence for the student, not an answer key.  If the
    # confirmed dependency explicitly says that stages/positions are
    # unlabelled and must be identified, visible node or edge labels must not
    # reveal those names.  This is deterministic and applies across subjects.
    if "未标注" in dependency:
        visible_annotations = [
            _clean(node.get("label"), 200)
            for figure in figures
            for node in (figure.get("nodes") or [])
            if isinstance(node, dict) and _clean(node.get("label"), 200)
        ]
        visible_annotations.extend(
            _clean(edge.get("label"), 200)
            for figure in figures
            for edge in (figure.get("edges") or [])
            if isinstance(edge, dict) and _clean(edge.get("label"), 200)
        )
        revealing = [
            label
            for label in visible_annotations
            if any(
                term
                and len(term) >= 2
                and term in _normalized_figure_term(label)
                for element in design["required_elements"]
                for term in re.split(r"与|及|和|、|，|,", _normalized_figure_term(element))
            )
        ]
        if revealing:
            issues.append({
                **prefix,
                "code": "figure_reveals_requested_identification",
                "message": "题图直接标出了题干要求学生识别的未标注信息。",
                "revealing_labels": list(dict.fromkeys(revealing))[:12],
            })
    visible_content = []
    for figure in figures:
        visible_content.extend([
            _clean(figure.get("title"), 300),
            _clean(figure.get("description"), 1500),
            _clean(figure.get("x_label"), 100),
            _clean(figure.get("y_label"), 100),
        ])
        visible_content.extend(_clean(series.get("name"), 100) for series in figure.get("series") or [] if isinstance(series, dict))
        visible_content.extend(_clean(node.get("label"), 200) for node in figure.get("nodes") or [] if isinstance(node, dict))
        visible_content.extend(_clean(edge.get("label"), 120) for edge in figure.get("edges") or [] if isinstance(edge, dict))
    missing_elements = [
        element
        for element in design["required_elements"]
        if not _figure_element_present(element, figures, visible_content)
    ]
    if missing_elements:
        issues.append({
            **prefix,
            "code": "figure_missing_required_elements",
            "message": "题图未体现蓝图要求的元素。",
            "missing_elements": missing_elements[:12],
        })
    curve_series = [
        row for row in _figure_series(figures)
        if re.search(r"曲线|绝热|恒温|可逆", _clean(row.get("name"), 100))
        and not re.search(r"水平|垂直|终压|辅助", _clean(row.get("name"), 100))
    ]
    undersampled = [_clean(row.get("name"), 100) or "未命名曲线" for row in curve_series if len(row.get("points") or []) < 5]
    if undersampled:
        issues.append({
            **prefix,
            "code": "figure_curve_under_sampled",
            "message": "题图曲线数据点过少，导出后会退化成直线或粗糙折线。",
            "series": undersampled[:8],
        })
    chart_series = [
        row for row in _figure_series(figures)
        if not re.search(r"终压|辅助|坐标轴|水平线|垂直线", _clean(row.get("name"), 100))
    ]
    chart_nodes = [node for figure in figures for node in (figure.get("nodes") or []) if isinstance(node, dict)]
    starts = [row.get("points", [])[0] for row in chart_series if row.get("points")]
    ends = [row.get("points", [])[-1] for row in chart_series if row.get("points")]
    mismatched_nodes: list[str] = []
    for node in chart_nodes:
        label = _clean(node.get("label"), 200)
        point = [node.get("x"), node.get("y")]
        candidates = starts if "初态" in label else ends if "终态" in label else []
        if candidates and not any(_points_close(point, candidate) for candidate in candidates):
            mismatched_nodes.append(label)
    if mismatched_nodes:
        issues.append({
            **prefix,
            "code": "figure_node_coordinate_mismatch",
            "message": "题图的初态或终态节点没有落在对应曲线端点上。",
            "nodes": mismatched_nodes[:12],
        })
    for relationship_issue in _figure_relationship_issues(figures, design["relationship_constraints"]):
        issues.append({**prefix, **relationship_issue})
    return issues


_BOUNDARY_CONTRADICTION_MARKERS: dict[str, tuple[str, ...]] = {
    "平衡": ("非平衡", "未达到平衡", "偏离平衡", "未完全发生", "不完全发生"),
    "缓慢冷却": ("快速冷却", "急冷", "淬火", "冷却条件波动", "冷却速率波动"),
    "恒温": ("变温", "升温过程", "降温过程", "温度变化"),
    "恒压": ("变压", "压力变化"),
    "可逆": ("不可逆", "非可逆"),
    "稳态": ("非稳态", "瞬态"),
    "绝热": ("非绝热", "与外界换热", "有热交换"),
}


def _exercise_boundary_issues(exercise: dict[str, Any], planned_item: dict[str, Any]) -> list[dict[str, Any]]:
    """Find explicit stem text that contradicts a confirmed hard boundary.

    This deliberately handles only direct antonyms and process changes. More
    subtle subject-matter questions remain for the conditional semantic review.
    """
    constraints = planned_item.get("required_constraints") if isinstance(planned_item.get("required_constraints"), dict) else {}
    boundaries = _unique_strings(constraints.get("applicable_boundaries"), limit=20, item_limit=500)
    stem = _clean(exercise.get("stem"), 12000)
    issues: list[dict[str, Any]] = []
    for boundary in boundaries:
        normalized_boundary = _normalized_figure_term(boundary)
        matched = [
            marker
            for key, markers in _BOUNDARY_CONTRADICTION_MARKERS.items()
            if _normalized_figure_term(key) in normalized_boundary
            for marker in markers
            if marker in stem
        ]
        if matched:
            issues.append({
                "code": "applicable_boundary_contradiction",
                "message": "题干引入了与已确认适用边界相冲突的条件。",
                "boundary": boundary,
                "markers": list(dict.fromkeys(matched)),
            })
    return issues


def _practice_semantic_review_risks(practice: dict[str, Any]) -> list[dict[str, Any]]:
    review = practice.get("semantic_review") if isinstance(practice.get("semantic_review"), dict) else {}
    risks: list[dict[str, Any]] = []
    for item in review.get("items") or []:
        if not isinstance(item, dict):
            continue
        number = str(item.get("number") or "").strip()
        for risk in item.get("risks") or []:
            if not isinstance(risk, dict):
                continue
            severity = _clean(risk.get("severity"), 20).lower()
            message = _clean(risk.get("message"), 800)
            if severity in {"high", "medium", "low"} and message:
                risks.append({
                    "number": number,
                    "severity": severity,
                    "code": _clean(risk.get("code"), 100) or "semantic_risk",
                    "message": message,
                    "evidence": _clean(risk.get("evidence"), 800),
                    "suggested_action": _clean(risk.get("suggested_action"), 800),
                })
    return risks


def ensure_unique_figure_ids(exercises: list[dict[str, Any]]) -> None:
    """Namespace figure IDs by exercise so exports and DOM lookups cannot collide.

    The operation is intentionally idempotent because quality recomputation,
    history saving, and single-question regeneration may all touch the same
    public result.
    """
    used: set[str] = set()
    for exercise_index, exercise in enumerate(exercises, start=1):
        if not isinstance(exercise, dict):
            continue
        exercise_id = _clean(exercise.get("exercise_id"), 100) or f"practice_{exercise_index:02d}"
        for figure_index, figure in enumerate(exercise.get("figures") or [], start=1):
            if not isinstance(figure, dict):
                continue
            raw_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", _clean(figure.get("figure_id"), 500)).strip("._")
            current_prefix = f"{exercise_id}_"
            while raw_id.startswith(current_prefix):
                raw_id = raw_id[len(current_prefix):]
            # One-item normalization temporarily uses practice_01 before a
            # regenerated question's stable exercise_id is restored. Remove
            # those generated slot namespaces before applying the stable one.
            raw_id = re.sub(r"^(?:practice_[0-9]+_)+", "", raw_id)
            raw_id = raw_id[: max(1, 100 - len(exercise_id) - 1)].rstrip("._")
            base = f"{exercise_id}_{raw_id or f'fig_{figure_index:02d}'}"
            candidate = base
            suffix = 2
            while candidate in used:
                candidate = f"{base}_{suffix}"
                suffix += 1
            figure["figure_id"] = candidate
            used.add(candidate)


def recompute_practice_quality(practice: dict[str, Any]) -> dict[str, Any]:
    """Recalculate deterministic delivery checks after edit/regeneration.

    This is intentionally a structural/coverage gate, not a claim that the
    generated mathematics or science is correct.  That distinction is exposed
    to the UI so ``passed`` cannot be mistaken for subject-matter acceptance.
    """
    exercises = practice.get("exercises") if isinstance(practice, dict) else []
    exercises = exercises if isinstance(exercises, list) else []
    blueprint = practice.get("blueprint") if isinstance(practice.get("blueprint"), dict) else {}
    planned = [item for item in blueprint.get("exercise_plan") or [] if isinstance(item, dict)]
    planned_by_id = {
        str(item.get("plan_item_id") or "").strip(): item
        for item in planned
        if str(item.get("plan_item_id") or "").strip()
    }
    multi_config = (
        practice.get("blueprint_multi_question")
        if isinstance(practice.get("blueprint_multi_question"), dict)
        else blueprint.get("multi_question") if isinstance(blueprint.get("multi_question"), dict) else {}
    )
    multi_enabled = multi_config.get("enabled") is True
    warnings: list[str] = []
    blocking_issues: list[str] = []
    failed_count = 0
    boundary_issues: list[dict[str, Any]] = []
    complex_review_candidates: list[str] = []
    for index, item in enumerate(exercises, start=1):
        question_number = str(item.get("number") or index) if isinstance(item, dict) else str(index)
        if isinstance(item, dict) and item.get("generation_status") == "failed":
            failed_count += 1
            message = _clean((item.get("generation_error") or {}).get("message"), 500) or "上游模型未返回本题。"
            blocking_issues.append(f"第 {question_number} 题生成失败：{message}")
            continue
        if not isinstance(item, dict) or not _clean(item.get("stem"), 6000):
            failed_count += 1
            blocking_issues.append(f"第 {question_number} 题缺少题干。")
            continue
        plan_item_id = (
            str(item.get("parent_plan_item_id") or "").strip()
            if multi_enabled
            else str(item.get("plan_item_id") or "").strip()
        )
        question_type = _effective_question_type(item, planned_by_id.get(plan_item_id))
        planned_item = planned_by_id.get(plan_item_id) or {}
        if question_type in {"综合题", "作图题"} or _clean(item.get("difficulty"), 20) == "挑战":
            complex_review_candidates.append(question_number)
        structure_issue = _question_structure_issue(item, question_type=question_type)
        if structure_issue:
            failed_count += 1
            blocking_issues.append(f"第 {question_number} 题{structure_issue['message']}")
        if question_type in {"单选题", "多选题"} and len(_normalize_options(item.get("options"))) > 8:
            warnings.append(f"第 {question_number} 题选择项超过 8 个，需人工确认题目结构。")
        if not isinstance(item.get("knowledge_points"), list) or not item.get("knowledge_points"):
            warnings.append(f"第 {question_number} 题没有记录知识点，无法核对蓝图覆盖。")
        if (
            not _clean(item.get("verification_note"), 1000)
            and _clean(item.get("answerability_check_status"), 30) != "reported"
        ):
            warnings.append(f"第 {question_number} 题没有条件充分性与可作答性自检记录。")
    ensure_unique_figure_ids(exercises)
    requested_count = int(practice.get("requested_count") or len(planned) or len(exercises))
    if len(exercises) != requested_count:
        blocking_issues.append(f"请求生成 {requested_count} 题，实际得到 {len(exercises)} 题。")
    plan_ids = [str(item.get("plan_item_id") or "").strip() for item in planned if str(item.get("plan_item_id") or "").strip()]
    result_ids = [str(item.get("plan_item_id") or "").strip() for item in exercises if isinstance(item, dict)]
    multi_config = (
        practice.get("blueprint_multi_question")
        if isinstance(practice.get("blueprint_multi_question"), dict)
        else blueprint.get("multi_question") if isinstance(blueprint.get("multi_question"), dict) else {}
    )
    multi_enabled = multi_config.get("enabled") is True
    if multi_enabled:
        variants_per_item = max(2, min(3, _nonnegative_int(multi_config.get("variants_per_item"), 2)))
        result_parent_ids = [
            str(item.get("parent_plan_item_id") or "").strip()
            for item in exercises
            if isinstance(item, dict)
        ]
        parent_counts = {plan_id: result_parent_ids.count(plan_id) for plan_id in plan_ids}
        unknown_parents = sorted({parent_id for parent_id in result_parent_ids if parent_id not in set(plan_ids)})
        incomplete = [plan_id for plan_id, actual in parent_counts.items() if actual != variants_per_item]
        if planned and (incomplete or unknown_parents or len(result_ids) != len(set(result_ids))):
            blocking_issues.append("生成结果没有按设定变式数完整覆盖已确认蓝图。")
    elif planned and (len(result_ids) != len(plan_ids) or set(result_ids) != set(plan_ids)):
        blocking_issues.append("生成结果没有完整覆盖已确认蓝图。")
    for index, exercise in enumerate(exercises, start=1):
        if not isinstance(exercise, dict) or exercise.get("generation_status") == "failed":
            continue
        question_number = str(exercise.get("number") or index)
        lookup_plan_id = (
            str(exercise.get("parent_plan_item_id") or "").strip()
            if multi_enabled
            else str(exercise.get("plan_item_id") or "").strip()
        )
        planned_item = planned_by_id.get(lookup_plan_id)
        if not planned_item:
            continue
        issue = _required_knowledge_point_issue(exercise, planned_item)
        if issue:
            blocking_issues.append(
                f"第 {question_number} 题未完整匹配蓝图必考知识点："
                f"缺少 {'、'.join(issue['missing_knowledge_points']) or '无'}；"
                f"额外 {'、'.join(issue['extra_knowledge_points']) or '无'}。"
            )
        for figure_issue in _exercise_figure_issues(exercise, planned_item):
            blocking_issues.append(f"第 {question_number} 题题图不合格：{figure_issue['message']}")
        for boundary_issue in _exercise_boundary_issues(exercise, planned_item):
            boundary_issues.append({"number": question_number, **boundary_issue})
            warnings.append(
                f"第 {question_number} 题与蓝图边界“{boundary_issue['boundary']}”冲突："
                f"题干出现 {'、'.join(boundary_issue['markers'])}。"
            )
    source_mode = str(practice.get("source_mode") or "exam").strip()
    strategy = str(practice.get("generation_strategy") or blueprint.get("generation_strategy") or "").strip()
    selected_sources = practice.get("selected_source_questions") if isinstance(practice.get("selected_source_questions"), list) else []
    selected_ids = {str(item.get("source_question_id") or "").strip() for item in selected_sources if isinstance(item, dict) and str(item.get("source_question_id") or "").strip()}
    result_sources = {str(item.get("source_question_id") or "").strip() for item in exercises if isinstance(item, dict) and str(item.get("source_question_id") or "").strip()}
    if selected_ids and strategy in {"parallel_exam", "per_question", "knowledge_item_wise"}:
        missing = sorted(selected_ids - result_sources)
        if missing:
            blocking_issues.append(f"已选来源未全部覆盖：{', '.join(missing[:8])}。")
    diversity_issues = practice_diversity_issues(practice)
    for issue in diversity_issues:
        message = _clean(issue.get("message"), 800)
        if not message:
            continue
        if issue.get("blocking") is True:
            blocking_issues.append(message)
        else:
            warnings.append(message)
    difficulty_observations = practice_difficulty_observations(practice)
    warnings.extend(
        _clean(observation.get("message"), 800)
        for observation in difficulty_observations
        if _clean(observation.get("message"), 800)
    )
    semantic_review = practice.get("semantic_review") if isinstance(practice.get("semantic_review"), dict) else {}
    semantic_review_status = _clean(semantic_review.get("status"), 30)
    semantic_review_risks = _practice_semantic_review_risks(practice)
    actionable_semantic_risks = [risk for risk in semantic_review_risks if risk["severity"] in {"high", "medium"}]
    for risk in semantic_review_risks:
        if risk["severity"] in {"high", "medium"}:
            warnings.append(f"第 {risk['number'] or '?'} 题语义复核：{risk['message']}")
    semantic_review_completed = semantic_review_status in {"passed", "warning"}
    if semantic_review_status == "failed":
        warnings.append("语义质量审查未完成，已保留题目并标记为待复核。")
    subject_review_required = bool(boundary_issues or complex_review_candidates) and (
        not semantic_review_completed or bool(actionable_semantic_risks)
    )
    checks = {
        "scope_coverage": not any("蓝图" in issue or "来源未全部覆盖" in issue for issue in blocking_issues),
        "field_structure": not any("题干" in issue or "选项" in issue or "填空" in issue for issue in blocking_issues),
        "figure_integrity": not any("题图" in issue for issue in blocking_issues),
        "resource_ids": True,
        "content_diversity": not any(issue.get("blocking") is True for issue in diversity_issues),
        "difficulty_alignment": not any(
            observation.get("severity") == "high" for observation in difficulty_observations
        ),
        "applicable_boundary_consistency": not boundary_issues,
        "semantic_review_completed": semantic_review_completed,
        "semantic_review_passed": semantic_review_completed and not actionable_semantic_risks,
        "subject_matter_review_required": subject_review_required,
    }
    return {
        "status": "blocked" if blocking_issues else "passed",
        "warnings": list(dict.fromkeys(warnings)),
        "blocking_issues": list(dict.fromkeys(blocking_issues)),
        "generated_count": len(exercises) - failed_count,
        "failed_count": failed_count,
        "total_count": len(exercises),
        "partial_success": failed_count > 0,
        "recomputed": True,
        "gate": "structural_and_scope",
        "checks": checks,
        "source_mode": source_mode,
        "generation_strategy": strategy,
        "difficulty_observations": difficulty_observations,
        "boundary_issues": boundary_issues,
        "subject_review_candidates": list(dict.fromkeys(complex_review_candidates)),
        "semantic_review_risks": semantic_review_risks,
    }


def reconcile_practice_generation(practice: dict[str, Any]) -> dict[str, Any]:
    """Make current exercises, quality counters and generation metadata agree.

    Initial batch diagnostics remain available, but stale batch errors must not
    keep a repaired task in partial-success forever.  The live error list is
    rebuilt from the current question slots after every edit or regeneration.
    """
    data = copy.deepcopy(practice) if isinstance(practice, dict) else {}
    quality = recompute_practice_quality(data)
    exercises = data.get("exercises") if isinstance(data.get("exercises"), list) else []
    current_errors: list[dict[str, Any]] = []
    for index, item in enumerate(exercises, start=1):
        if not isinstance(item, dict):
            continue
        error = item.get("generation_error") if isinstance(item.get("generation_error"), dict) else {}
        if item.get("generation_status") == "failed":
            current_errors.append({
                "plan_item_id": _clean(item.get("plan_item_id"), 80) or f"plan_item_{index:02d}",
                "code": _clean(error.get("code"), 100) or "provider_generation_missing",
                "message": _clean(error.get("message"), 500) or "上游模型未返回本题。",
                "retryable": error.get("retryable") is not False,
                "detail": _clean(error.get("detail"), 800),
            })
    generation = dict(data.get("generation") or {})
    generation.update({
        "status": "partial_success" if quality["failed_count"] else "completed",
        "partial_success": quality["failed_count"] > 0,
        "generated_count": quality["generated_count"],
        "failed_count": quality["failed_count"],
        "batch_errors": current_errors,
    })
    data["quality"] = quality
    data["generation"] = generation
    return data


def _generation_error_detail(exc: Exception) -> dict[str, Any]:
    raw = _clean(str(exc) or exc.__class__.__name__, 800)
    lowered = raw.lower()
    if "524" in lowered:
        code = "provider_http_524"
        message = "上游模型响应超时（HTTP 524）。"
    elif "timeout" in lowered or "timed out" in lowered or "超时" in raw:
        code = "provider_timeout"
        message = "上游模型响应超时。"
    elif isinstance(exc, ValueError):
        code = "generation_response_invalid"
        message = "模型返回的题目结构不完整，无法生成本题。"
    else:
        status_match = re.search(r"(?:provider\s+)?http\s+(\d{3})", raw, flags=re.IGNORECASE)
        code = f"provider_http_{status_match.group(1)}" if status_match else "provider_generation_failed"
        message = f"上游模型请求失败（HTTP {status_match.group(1)}）。" if status_match else "上游模型生成失败。"
    return {"code": code, "message": message, "retryable": True, "detail": raw}


def _generation_gate_error(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn a per-question deterministic gate result into an actionable error."""
    issue_codes = {str(issue.get("code") or "") for issue in issues if isinstance(issue, dict)}
    missing_elements = _unique_strings(
        [element for issue in issues if isinstance(issue, dict) for element in (issue.get("missing_elements") or [])],
        limit=12,
        item_limit=200,
    )
    if "figure_reveals_requested_identification" in issue_codes:
        message = "题图直接泄露了本应由学生识别的答案标签。"
    elif "figure_missing_required_elements" in issue_codes:
        message = "题图不完整，未包含蓝图要求的关键元素。"
    elif "figure_relationship_constraint_failed" in issue_codes:
        message = "题图中的坐标或几何关系与蓝图要求不一致。"
    elif "figure_node_coordinate_mismatch" in issue_codes:
        message = "题图的状态点没有落在对应曲线端点上。"
    elif "figure_curve_under_sampled" in issue_codes:
        message = "题图曲线的数据点不足，无法形成可靠曲线。"
    elif "missing_stem_figure" in issue_codes:
        message = "蓝图要求题干配图，但模型没有返回可用题图。"
    elif "unrenderable_stem_figure" in issue_codes:
        message = "模型返回的题图无法绘制。"
    elif "stem_does_not_reference_figure" in issue_codes:
        message = "题干没有明确引用所需题图。"
    elif any("knowledge" in code for code in issue_codes):
        message = "题目未完整覆盖蓝图规定的必考知识点。"
    elif "choice_options_missing" in issue_codes:
        message = "选择题缺少有效选项。"
    elif "fill_in_blank_missing" in issue_codes:
        message = "填空题缺少可填写的空位。"
    elif "missing_stem" in issue_codes:
        message = "题目缺少题干。"
    else:
        message = "题目未通过已确认蓝图的生成质量检查。"
    detail_parts = [str(issue.get("message") or "") for issue in issues if isinstance(issue, dict) and issue.get("message")]
    if missing_elements:
        detail_parts.append("缺少元素：" + "、".join(missing_elements))
    return {
        "code": "generation_quality_gate_failed",
        "message": message,
        "retryable": True,
        "detail": "；".join(dict.fromkeys(detail_parts))[:800],
    }


def _repair_exercise_figures(
    exercise: dict[str, Any],
    planned_item: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    provider,
    model: str,
    payload: dict[str, Any],
    ensure_active: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Repair only a question's figure specification using the selected model."""
    design = _figure_design(planned_item.get("figure_design"), required=True)
    figure_contract = _exercise_contract()["figures"][0]
    task = f"""# 任务

只修复下面这道题的题干配图数据。不得改写题干、题型、知识点或任何题目文字，也不得输出答案和解析。

## 已确认题干

{_clean(exercise.get("stem"), 6000)}

## 题图设计要求

{json.dumps(design, ensure_ascii=False, indent=2)}

## 当前题图

{json.dumps(exercise.get("figures") or [], ensure_ascii=False, indent=2)}

## 未通过的检查

{json.dumps(issues, ensure_ascii=False, indent=2)}

## 绘图规则

- 返回 figures 数组，不返回题目正文。
- required_elements 中的每个元素必须真实落实到坐标轴、series、nodes 或 edges，不能只写在 description 或 semantic_contract。
- required_elements 中括号内的“水平、垂直、先后、曲线”等描述是几何要求，不是需要机械复制的图例文字。
- 曲线至少提供 5 个有序数据点；水平线、垂直线等辅助线至少提供两个点。
- 含 series 时，nodes 的 x/y 与 series 使用相同的数据坐标；节点必须落在对应曲线端点或交点上。
- 不含 series 的纯示意图才使用 0 到 1 的 nodes 画布坐标。
- 同一初态、相同终压、左右位置和上下关系必须由实际坐标证明。
- 系列名称、节点标签和辅助线名称使用题目中的标准术语。
- 只输出合法 JSON。

## 输出结构

{json.dumps({"figures": [figure_contract]}, ensure_ascii=False, indent=2)}
"""
    raw = _call_practice_json(
        _practice_generation_client(provider, model),
        [
            {"role": "system", "content": "你是专业科学制图数据修复器。只修复题图 JSON，不修改题目。"},
            {"role": "user", "content": task},
        ],
        model=model,
        temperature=0.15,
        # This call only repairs a bounded drawing schema. Extended reasoning
        # adds substantial latency/cost without improving the constrained JSON
        # transformation, so keep it deterministic and independently bounded.
        thinking="disabled",
        timeout_seconds=_practice_stage_timeout("figure_repair", 120),
        ensure_active=ensure_active,
    )
    return [figure for figure in (raw.get("figures") or []) if isinstance(figure, dict)][:6]


def _failed_exercise_placeholder(
    planned_item: dict[str, Any],
    *,
    index: int,
    error: dict[str, Any],
) -> dict[str, Any]:
    plan_item_id = _clean(planned_item.get("plan_item_id"), 80) or f"plan_item_{index:02d}"
    message = _clean(error.get("message"), 500) or "上游模型未返回本题。"
    result = {
        "plan_item_id": plan_item_id,
        "parent_plan_item_id": _clean(planned_item.get("parent_plan_item_id"), 80),
        "variant_id": _clean(planned_item.get("variant_id"), 100),
        "variant_index": _nonnegative_int(planned_item.get("variant_index")),
        "variant_count": _nonnegative_int(planned_item.get("variant_count")),
        "variant_mode": _clean(planned_item.get("variant_mode"), 30),
        "variant_role": _clean(planned_item.get("variant_role"), 100),
        "source_question_id": _clean(planned_item.get("source_question_id"), 80),
        "question_type": _clean(planned_item.get("question_type"), 20) or "综合题",
        "difficulty": _clean(planned_item.get("difficulty"), 12) or "进阶",
        "target_skill": _clean(planned_item.get("target_skill"), 500) or _clean(planned_item.get("title"), 500),
        "variation_type": "生成失败占位",
        "stem": f"本题生成失败：{message}已保留蓝图位置，可在页面点击“重新生成本题”补齐。",
        "options": [],
        "knowledge_points": _string_list(
            planned_item.get("required_knowledge_points") or planned_item.get("knowledge_points"),
            limit=60,
        ),
        "verification_note": "本题未生成，不作为有效练习题。",
        "formulas": [],
        "tables": [],
        "figures": [],
        "figure_generation": {
            "required": _plan_requires_stem_figure(planned_item),
            "repair_attempted": _plan_requires_stem_figure(planned_item),
            "status": "failed" if _plan_requires_stem_figure(planned_item) else "not_required",
            "initial_issue_codes": [],
            "final_issue_codes": [],
            "repair_error": "",
        },
        "generation_status": "failed",
        "generation_error": {
            "code": _clean(error.get("code"), 100) or "provider_generation_failed",
            "message": message,
            "retryable": bool(error.get("retryable", True)),
            "detail": _clean(error.get("detail"), 800),
        },
    }
    return result


def _type_plan(selected: list[str], count: int) -> list[str]:
    rng = random.SystemRandom()
    pool = selected or sorted(ALLOWED_TYPES)
    plan: list[str] = []
    while len(plan) < count:
        cycle = list(pool)
        rng.shuffle(cycle)
        plan.extend(cycle)
    return plan[:count]


def _escape_invalid_json_backslashes(value: str) -> str:
    """Escape model-produced LaTeX backslashes that are invalid in JSON strings."""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if escaped:
            output.append(char)
            escaped = False
            index += 1
            continue
        if char == '"':
            output.append(char)
            in_string = False
            index += 1
            continue
        if char != "\\":
            output.append(char)
            index += 1
            continue
        next_char = value[index + 1] if index + 1 < len(value) else ""
        valid_escape = next_char in '"\\/bfnrt'
        if next_char == "u":
            valid_escape = bool(re.fullmatch(r"[0-9a-fA-F]{4}", value[index + 2 : index + 6]))
        output.append("\\" if valid_escape else "\\\\")
        if valid_escape:
            escaped = True
        index += 1
    return "".join(output)


def _parse_practice_json(content: str) -> dict[str, Any]:
    """Parse practice output without changing the platform-wide strict parser."""
    cleaned = str(content or "").strip()
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        extracted = cleaned[start : end + 1]
        if extracted != cleaned:
            candidates.append(extracted)
    last_error: Exception | None = None
    for candidate in candidates:
        repaired_candidate = _escape_invalid_json_backslashes(candidate)
        for attempt in (candidate, repaired_candidate):
            for strict in (True, False):
                try:
                    value = json.loads(attempt, strict=strict)
                    if not isinstance(value, dict):
                        raise ValueError("模型 JSON 输出必须是对象。")
                    return value
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = exc
    preview = cleaned.replace("\n", "\\n")[:220]
    raise LLMError(f"专项练习 JSON 解析失败：{last_error}；内容预览：{preview}")


def _practice_control_character_issues(value: Any, path: str = "$") -> list[dict[str, Any]]:
    r"""Find JSON-valid control characters that can corrupt generated LaTeX."""
    issues: list[dict[str, Any]] = []
    if isinstance(value, str):
        for index, char in enumerate(value):
            code = ord(char)
            if (code < 32 and char != "\n") or code == 127:
                issues.append({"path": path, "index": index, "code": f"U+{code:04X}"})
                if len(issues) >= 20:
                    break
        return issues
    if isinstance(value, dict):
        for key, item in value.items():
            issues.extend(_practice_control_character_issues(item, f"{path}.{key}"))
            if len(issues) >= 20:
                break
        return issues[:20]
    if isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_practice_control_character_issues(item, f"{path}[{index}]"))
            if len(issues) >= 20:
                break
    return issues[:20]


def _parse_safe_practice_json(content: str) -> dict[str, Any]:
    raw = _parse_practice_json(content)
    issues = _practice_control_character_issues(raw)
    if issues:
        raise LLMError(
            "专项练习模型输出包含非法控制字符，已拒绝保存："
            + json.dumps(issues[:8], ensure_ascii=False)
        )
    return raw


def normalize_practice_set(
    raw: dict[str, Any],
    *,
    requested_count: int,
    subject: str,
    planned_types: list[str] | None = None,
    planned_source_ids: list[str] | None = None,
    planned_plan_ids: list[str] | None = None,
    planned_difficulties: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("模型输出必须是 JSON 对象。")
    control_issues = _practice_control_character_issues(raw)
    if control_issues:
        raise ValueError(
            "专项练习数据包含非法控制字符，不能进入规范化或保存："
            + json.dumps(control_issues[:8], ensure_ascii=False)
        )
    source = raw.get("source_analysis") if isinstance(raw.get("source_analysis"), dict) else {}
    blueprint = raw.get("blueprint") if isinstance(raw.get("blueprint"), dict) else {}
    exercises_raw = raw.get("exercises")
    if not isinstance(exercises_raw, list) or not exercises_raw:
        raise ValueError("模型没有生成 exercises 列表。")

    exercises: list[dict[str, Any]] = []
    accepted_plan_ids = planned_plan_ids or [f"plan_item_{index + 1:02d}" for index in range(requested_count)]
    plan_lookup = {str(plan_id): index for index, plan_id in enumerate(accepted_plan_ids)}
    for raw_index, item in enumerate(exercises_raw, start=1):
        if not isinstance(item, dict):
            continue
        plan_item_id = _clean(item.get("plan_item_id"), 80)
        if not plan_item_id or plan_item_id not in plan_lookup:
            raise ValueError(f"模型输出缺少或包含未知 plan_item_id：{plan_item_id or '空'}")
        plan_index = plan_lookup[plan_item_id] + 1
        try:
            plan_index = max(1, min(requested_count, int(plan_index)))
        except (TypeError, ValueError):
            plan_index = raw_index
        index = plan_index
        # Preserve provider line boundaries long enough to lift a Markdown
        # pipe table, then own the visible question layout afterwards.
        stem, stem_has_unrenderable_markup = _normalize_generated_markup(item.get("stem"), limit=6000)
        if not stem:
            continue
        tables, tables_have_unrenderable_markup = _normalize_generated_tables(item.get("tables"))
        stem, tables = _merge_stem_markdown_tables(stem, tables)
        stem, post_table_merge_stem_has_unrenderable_markup = _normalize_generated_stem(stem, limit=6000)
        tables, post_table_merge_tables_have_unrenderable_markup = _normalize_generated_tables(tables)
        target_skill, target_skill_has_unrenderable_markup = _normalize_generated_markup(item.get("target_skill"), limit=500)
        options, options_have_unrenderable_markup = _normalize_generated_options(item.get("options"))
        markup_issue = (
            stem_has_unrenderable_markup
            or post_table_merge_stem_has_unrenderable_markup
            or target_skill_has_unrenderable_markup
            or options_have_unrenderable_markup
            or tables_have_unrenderable_markup
            or post_table_merge_tables_have_unrenderable_markup
        )
        model_marked_failed = item.get("generation_status") == "failed"
        generation_error = (
            {
                "code": "unrenderable_markup",
                "message": "题目包含无法自动修复的 Markdown/LaTeX 标记，已阻止展示；请重新生成本题。",
                "retryable": True,
                "detail": "题干、选项或表格中仍存在缺少公式定界符的 LaTeX 标记。",
            }
            if markup_issue
            else (
                {
                    "code": _clean((item.get("generation_error") or {}).get("code"), 100),
                    "message": _clean((item.get("generation_error") or {}).get("message"), 500),
                    "retryable": bool((item.get("generation_error") or {}).get("retryable", True)),
                    "detail": _clean((item.get("generation_error") or {}).get("detail"), 800),
                }
                if model_marked_failed
                else {}
            )
        )
        difficulty = _clean(item.get("difficulty"), 12)
        if planned_difficulties and index <= len(planned_difficulties):
            difficulty = planned_difficulties[index - 1]
        question_type = _clean(item.get("question_type"), 20)
        if planned_types and index <= len(planned_types):
            question_type = planned_types[index - 1]
        difficulty_evidence = (
            _normalized_difficulty_evidence(item.get("difficulty_evidence"))
            if isinstance(item.get("difficulty_evidence"), dict)
            else None
        )
        exercises.append(
            {
                "exercise_id": f"practice_{index:02d}",
                "plan_item_id": plan_item_id,
                "parent_plan_item_id": _clean(item.get("parent_plan_item_id"), 80),
                "variant_id": _clean(item.get("variant_id"), 100),
                "variant_index": _nonnegative_int(item.get("variant_index")),
                "variant_count": _nonnegative_int(item.get("variant_count")),
                "variant_mode": _clean(item.get("variant_mode"), 30),
                "variant_role": _clean(item.get("variant_role"), 100),
                "number": index,
                "question_type": question_type if question_type in ALLOWED_TYPES else "综合题",
                "source_question_id": (
                    _clean(planned_source_ids[index - 1], 80)
                    if planned_source_ids and index <= len(planned_source_ids)
                    else _clean(item.get("source_question_id"), 80)
                ),
                "difficulty": difficulty if difficulty in ALLOWED_DIFFICULTIES else "进阶",
                "target_skill": target_skill,
                "variation_type": _clean(item.get("variation_type"), 100),
                "stem": stem,
                "options": options,
                # A blueprint may legitimately bind more than ten atomic
                # points to one comprehensive item. Truncating this metadata
                # after the batch gate made a previously accepted question
                # fail the final exact-coverage check.
                "knowledge_points": _string_list(item.get("knowledge_points"), limit=60),
                "verification_note": _clean(item.get("verification_note"), 1000),
                "diversity_signature": _normalized_diversity_signature(item.get("diversity_signature")),
                **({"difficulty_evidence": difficulty_evidence} if difficulty_evidence is not None else {}),
                "formulas": _normalize_formulas(item.get("formulas")),
                "tables": tables,
                "figures": _normalize_figures(item.get("figures")),
                "figure_generation": {
                    "required": bool((item.get("figure_generation") or {}).get("required")),
                    "repair_attempted": bool((item.get("figure_generation") or {}).get("repair_attempted")),
                    "status": _clean((item.get("figure_generation") or {}).get("status"), 40),
                    "initial_issue_codes": _unique_strings((item.get("figure_generation") or {}).get("initial_issue_codes"), limit=20, item_limit=100),
                    "final_issue_codes": _unique_strings((item.get("figure_generation") or {}).get("final_issue_codes"), limit=20, item_limit=100),
                    "repair_error": _clean((item.get("figure_generation") or {}).get("repair_error"), 500),
                } if isinstance(item.get("figure_generation"), dict) else {},
                "generation_status": "failed" if model_marked_failed or markup_issue else "completed",
                "generation_error": generation_error,
            }
        )

    if not exercises:
        raise ValueError("生成结果缺少可用题干。")

    warnings: list[str] = []
    if len(exercises) != requested_count:
        warnings.append(f"请求生成 {requested_count} 题，实际得到 {len(exercises)} 题。")
    failed_exercises = [item for item in exercises if item.get("generation_status") == "failed"]
    for item in failed_exercises:
        warnings.append(
            f"第 {item['number']} 题生成失败："
            f"{_clean((item.get('generation_error') or {}).get('message'), 500) or '上游模型未返回本题。'}"
        )
    for item in exercises:
        if item.get("generation_status") == "failed":
            continue
        if item["question_type"] in {"单选题", "多选题"} and len(item["options"]) < 2:
            warnings.append(f"第 {item['number']} 题为选择题，但有效选项少于 2 个。")

    ensure_unique_figure_ids(exercises)

    result = {
        "schema_version": SCHEMA_VERSION,
        "requested_count": requested_count,
        "source_analysis": {
            "subject": _clean(source.get("subject"), 100) or _clean(subject, 100) or "未指定",
            "question_type": _clean(source.get("question_type"), 100),
            "knowledge_points": _string_list(source.get("knowledge_points")),
            "skills": _string_list(source.get("skills")),
            "difficulty": _clean(source.get("difficulty"), 100),
            "solution_strategy": _string_list(source.get("solution_strategy")),
            "essential_definitions": _string_list(source.get("essential_definitions"), limit=20),
            "essential_formulas": _string_list(source.get("essential_formulas"), limit=20),
            "applicable_boundaries": _string_list(source.get("applicable_boundaries"), limit=20),
            "common_errors": _string_list(source.get("common_errors")),
            "uncertainties": _string_list(source.get("uncertainties")),
        },
        "blueprint": {
            "training_goal": _clean(blueprint.get("training_goal"), 1000),
            "progression": _string_list(blueprint.get("progression"), limit=requested_count),
            "design_notes": _string_list(blueprint.get("design_notes")),
        },
        "exercises": exercises,
        "quality": {
        "status": "warning" if warnings else "passed",
            "warnings": warnings,
            "generated_count": len(exercises) - len(failed_exercises),
            "failed_count": len(failed_exercises),
            "total_count": len(exercises),
            "partial_success": bool(failed_exercises),
        },
    }
    # Recompute once more from the normalized, program-owned representation so
    # edits and model output use exactly the same gate implementation.
    result["quality"] = recompute_practice_quality(result)
    return result


def normalize_difficulty_counts(payload: dict[str, Any], total_count: int) -> dict[str, int]:
    """Validate exact user-owned difficulty quotas for one confirmed scope."""
    total_count = max(1, min(30, int(total_count or 1)))
    raw = payload.get("difficulty_counts")
    if not isinstance(raw, dict):
        planned = _difficulty_plan(_clean(payload.get("difficulty"), 100) or "基础到进阶", total_count)
        return {level: planned.count(level) for level in DIFFICULTY_LEVELS}
    counts: dict[str, int] = {}
    for level in DIFFICULTY_LEVELS:
        value = raw.get(level, 0)
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{level}题数必须是整数。") from exc
        if value < 0 or value > total_count:
            raise ValueError(f"{level}题数必须在 0 到 {total_count} 之间。")
        counts[level] = value
    allocated = sum(counts.values())
    if allocated != total_count:
        raise ValueError(f"难度题数合计必须等于总题量 {total_count}，当前为 {allocated}。")
    return counts


def _difficulty_slots(counts: dict[str, int]) -> list[str]:
    return [level for level in DIFFICULTY_LEVELS for _ in range(int(counts.get(level, 0)))]


def build_generation_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic hard-constraint contract used when review is skipped."""
    source_mode = _clean(payload.get("source_mode"), 30)
    is_knowledge_mode = source_mode == "knowledge"
    include_source_content = _include_source_content_in_generation(payload)
    selected_types = [
        _clean(item, 20) for item in (payload.get("question_types") or [])
        if _clean(item, 20) in ALLOWED_TYPES
    ]
    selected = [
        {
            "source_question_id": _clean(item.get("source_question_id"), 80),
            "number": _clean(item.get("number"), 50),
            "title": _clean(item.get("title"), 300),
            "stem_excerpt": _clean(item.get("stem_excerpt"), 1200),
            "source_content": _clean(item.get("source_content") or item.get("source_text"), 18000),
            "question_type": _clean(item.get("question_type"), 100),
            "source_difficulty": _clean(item.get("source_difficulty") or item.get("difficulty"), 20),
            "knowledge_points": _string_list(item.get("knowledge_points"), limit=60),
            "required_constraints": _required_constraints_for_refs([], [], item),
            "constraint_status": _clean(item.get("constraint_status"), 20),
        }
        for item in (payload.get("selected_source_questions") or [])
        if isinstance(item, dict) and _clean(item.get("source_question_id"), 80)
    ]
    incomplete_selected_ids = [
        item["source_question_id"]
        for item in selected
        if item.get("constraint_status") == "incomplete"
    ]
    if incomplete_selected_ids:
        raise ValueError(
            "所选题目的逐题生成约束尚未补齐，不能进入题目生成："
            + "、".join(incomplete_selected_ids)
        )
    if not selected:
        raise ValueError("缺少已确认的出题范围。")
    if is_knowledge_mode:
        requested_strategy = _clean(payload.get("generation_strategy"), 40)
        if requested_strategy in {"knowledge_item_wise", "per_question"}:
            variants = max(1, min(3, int(payload.get("variants_per_question") or 1)))
            rows = [(item, copy_index) for item in selected for copy_index in range(variants)][:30]
            strategy = "knowledge_item_wise"
            count = len(rows)
            planned_source_ids = [item["source_question_id"] for item, _ in rows]
        else:
            strategy = "knowledge_overall"
            count = max(1, min(20, int(payload.get("strategy_count") or payload.get("count") or 5)))
            planned_source_ids = []
        planned_types = _type_plan(selected_types, count)
    else:
        strategy, count, planned_types, planned_source_ids = _strategy_plan(
            payload, selected_source_questions=selected, selected_types=selected_types
        )
    difficulty_counts = normalize_difficulty_counts(payload, count)
    planned_difficulties = _difficulty_slots(difficulty_counts)
    source_scope = _normalize_source_scope(payload.get("source_scope"))
    deterministic = _normalize_plan(
        {"source_analysis": payload.get("source_analysis") or {}, "blueprint": {"exercise_plan": []}},
        count=count,
        planned_types=planned_types,
        difficulty="精确题数",
        planned_difficulties=planned_difficulties,
        selected_types=selected_types,
        source_files=[],
        source_scope=source_scope,
        selected_source_questions=selected,
        planned_source_ids=planned_source_ids,
        generation_strategy=strategy,
        include_source_content_in_generation=include_source_content,
    )
    slots = []
    for index, item in enumerate(deterministic["blueprint"]["exercise_plan"], start=1):
        slots.append({**item, "plan_item_id": f"generation_slot_{index:02d}", "slot_id": f"generation_slot_{index:02d}"})
    coverage = scope_cover_summary(source_scope, selected, slots)
    contract = {
        "schema_version": "answer_book.generation_contract.v1",
        "source_mode": "knowledge" if is_knowledge_mode else "exam",
        "source_scope": source_scope,
        "source_analysis": payload.get("source_analysis") if isinstance(payload.get("source_analysis"), dict) else {},
        "selected_source_questions": selected,
        "generation_strategy": strategy,
        "total_count": count,
        "difficulty_counts": difficulty_counts,
        "question_types": selected_types,
        "focus": _clean(payload.get("focus"), 1000),
        "include_source_content_in_generation": include_source_content,
        "source_coverage": coverage,
        "slots": slots,
    }
    audit_generation_contract(contract)
    return contract


def audit_generation_contract(contract: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    slots = contract.get("slots") if isinstance(contract.get("slots"), list) else []
    total = int(contract.get("total_count") or 0)
    if total < 1 or len(slots) != total:
        errors.append(f"生成槽位必须恰好为 {total} 项，当前为 {len(slots)} 项。")
    slot_ids = [str(item.get("slot_id") or "") for item in slots if isinstance(item, dict)]
    if any(not item for item in slot_ids) or len(slot_ids) != len(set(slot_ids)):
        errors.append("生成槽位 ID 缺失或重复。")
    expected_counts = contract.get("difficulty_counts") if isinstance(contract.get("difficulty_counts"), dict) else {}
    actual_counts = {level: sum(1 for item in slots if item.get("difficulty") == level) for level in DIFFICULTY_LEVELS}
    if actual_counts != {level: int(expected_counts.get(level, 0)) for level in DIFFICULTY_LEVELS}:
        errors.append("生成槽位的难度题数与用户确认配额不一致。")
    coverage = contract.get("source_coverage") if isinstance(contract.get("source_coverage"), dict) else {}
    if coverage and coverage.get("complete") is False and _mode_kind(_clean(contract.get("generation_strategy"), 40)) == "comprehensive":
        warnings.append("综合生成约束未逐项覆盖全部已选来源，将按当前综合蓝图优先生成核心范围。")
    elif coverage and coverage.get("complete") is False:
        errors.append("生成约束未覆盖全部已选来源。")
    if errors:
        raise ValueError("生成约束未通过校验：" + "；".join(errors))
    return {
        "status": "warning" if warnings else "passed",
        "errors": [],
        "warnings": warnings,
        "slot_count": len(slots),
        "difficulty_counts": actual_counts,
    }


def _exercise_contract() -> dict[str, Any]:
    return {
        "plan_item_id": "必须逐字复制本批 exercise_plan 中的唯一 plan_item_id",
        "source_question_id": "对应的原题 ID；单题可为空",
        "question_type": "单选题/多选题/判断题/填空题/简答题/计算题/作图题/综合题",
        "difficulty": "基础/进阶/挑战",
        "target_skill": "本题训练的具体能力",
        "variation_type": "变式类型",
        "stem": "仅包含完整独立题干，不得写第 N 题、题目标题、Markdown 标题、答案或解析；普通题干连续成段，不用手动换行。综合/计算/作图题的一级小问必须独占一行并写成 ASCII 英文括号 (1)、(2)；细项才用 ①、②；数据表不得写成 Markdown 管道表，必须放入 tables。题干中的每个 LaTeX 片段（包括化学式）必须完整包在 $...$ 或 \\[...\\] 中，绝对禁止在普通文字中裸写任何 LaTeX 命令",
        "options": [{"label": "A", "text": "选项内容"}],
        "knowledge_points": ["知识点"],
        "verification_note": "仅记录题干条件充分性检查；不得包含答案、结论、推导或解题过程",
        "diversity_signature": {
            "scenario_family": "不含具体数值的题目情境概括",
            "asked_quantity": "主要未知量、判断目标或设计目标",
            "solution_family": "核心方法或公式族的短语，不写答案和推导步骤",
            "cognitive_operation": "计算/逆向/比较/评价/设计/纠错之一",
        },
        "difficulty_evidence": {
            "primary_mechanism": "实际使用的主难度机制短语",
            "student_bottleneck": "主要认知瓶颈；不含答案或推导",
        },
        "formulas": [
            {
                "formula_id": "f1",
                "latex": "不含美元符号的 LaTeX",
                "location": "stem",
                "display": True,
                "caption": "可选说明",
            }
        ],
        "tables": [
            {
                "table_id": "t1",
                "location": "stem",
                "title": "表题；数据表存在时必须填写，且不得在 stem 中重复 Markdown 表格",
                "headers": ["列名"],
                "rows": [["单元格"]],
            }
        ],
        "figures": [
            {
                "figure_id": "g1",
                "location": "stem",
                "figure_type": "line/bar/scatter/diagram",
                "title": "图题",
                "description": "完整图示说明",
                "x_label": "横轴",
                "y_label": "纵轴",
                "series": [{"name": "图中可见的准确系列名称；曲线至少给 5 个点，辅助直线至少给 2 个点", "points": [[0, 0], [1, 1]]}],
                "nodes": [{"id": "n1", "label": "图中可见的节点名称", "x": 0.2, "y": 0.5, "shape": "box；有 series 时 x/y 必须使用与坐标轴相同的数据单位，无 series 时使用 0 到 1 的画布坐标"}],
                "edges": [{"from": "n1", "to": "n2", "label": "关系", "directed": True}],
                "semantic_contract": {
                    "required_elements": ["题图必须出现的元素"],
                    "relationship_constraints": ["元素之间必须保持的关系"],
                    "question_dependency": "学生必须从图中读取什么信息才能作答",
                },
            }
        ],
    }


def _public_exercise_contract() -> dict[str, Any]:
    contract = _exercise_contract()
    contract.pop("plan_item_id", None)
    contract.pop("source_question_id", None)
    return contract


def _plan_item_content_flags(item: dict[str, Any]) -> dict[str, bool]:
    question_type = _clean(item.get("question_type"), 20)
    text = " ".join([
        _clean(item.get("target_skill"), 500),
        _clean(item.get("variation_type"), 200),
        _clean(item.get("design_intent"), 800),
    ])
    return {
        "options": question_type in {"单选题", "多选题"},
        "formulas": question_type == "计算题" or bool(_required_constraints_for_refs([], [], item.get("required_constraints")).get("essential_formulas")),
        "tables": bool(item.get("requires_table")) or "表格" in text or "数据表" in text,
        "figures": _plan_requires_stem_figure(item),
    }


def _question_type_generation_requirements(item: dict[str, Any]) -> list[str]:
    question_type = _clean(item.get("question_type"), 20)
    requirements = {
        "单选题": ["提供可判定的选项，只有一个最佳答案；options 仅包含本题选项。"],
        "多选题": ["提供可判定的选项，允许多个正确项；options 仅包含本题选项。"],
        "判断题": ["题干给出可独立判断的明确命题，不生成选择题选项。"],
        "填空题": ["题干设置明确空位和可判定填答，不生成选择题选项。"],
        "简答题": ["题干明确说明需要解释、论证或推导的对象，不生成选择题选项。"],
        "计算题": ["给出完成计算所需条件、单位和边界；公式仅在题干确实使用时写入 formulas。"],
        "作图题": ["明确学生需要绘制的目标、坐标/标注要求和判定条件；学生作图本身不等于题干需要配图，只有 stem_figure_required=true 时才返回 figures。"],
        "综合题": ["组织必要子问或任务关系，避免把无关题型结构混入同一题。"],
    }
    resolved = list(requirements.get(question_type, ["题干必须与本题型一致、条件充分且可作答。"]))
    if question_type in {"单选题", "多选题"} and _clean(item.get("difficulty"), 20) == "挑战":
        resolved.append(
            "挑战层级选择题必须要求多概念联合判断、迁移、边界纠错或反例识别；"
            "各干扰项必须在局部条件下合理，不得用一个教材原句正确项搭配三个明显错误的绝对化陈述。"
        )
    return resolved


def _practice_output_format_requirements() -> list[str]:
    """The generated-question layout is owned by the platform, not the model."""
    return [
        "stem 只写题面正文；不要写“第 N 题”、题目标题、Markdown 标题、答案、解析或提示语。",
        "普通题干使用连续自然段，不得为视觉排版插入手动换行；系统会自动换行。",
        "综合题、计算题和作图题的一级小问必须独占一行，统一写为 ASCII 英文括号 (1)、(2)、(3)，禁止使用全角（1）或 1.、1、。",
        "仅在一个小问内有多项并列要求时使用 ①、②；选择项只能放在 options 字段，由系统输出 A.、B.、C.、D.。",
        "题干需图表时在正文中明确写“根据图/表…”，图表数据分别放入 figures/tables；不得在 stem 写 Markdown 管道表。",
        "当作答取决于多个过程的先后、交替、同时或分别进行时，题干必须明确写出过程关系，不得使用可导致多种作答理解的笼统并列。",
    ]


def _subject_format_requirements(plan: dict[str, Any], batch_plan: list[dict[str, Any]]) -> list[str]:
    analysis = plan.get("source_analysis") if isinstance(plan.get("source_analysis"), dict) else {}
    text = " ".join([
        _clean(analysis.get("subject"), 100),
        *(_string_list(analysis.get("knowledge_points"), limit=30)),
        *(_string_list(analysis.get("essential_formulas"), limit=20)),
        *(_string_list(analysis.get("essential_definitions"), limit=20)),
        *(" ".join(_string_list(item.get("required_knowledge_points"), limit=30)) for item in batch_plan),
    ])
    if not re.search(r"化学|电化学|化学反应|离子|电极|电池", text, flags=re.IGNORECASE):
        return []
    requirements = [
        "化学式、离子、电极/电池表示法、反应式及热力学符号在题干原位置使用 LaTeX；化学式使用 \\mathrm{}。",
        "题干中的每一个 LaTeX 片段必须完整置于 $...$ 或 \\[...\\] 中；例如必须写 $\\mathrm{H_2O(l)}$，严禁在普通文字中裸写 \\mathrm{H_2O(l)}。formulas 字段的 latex 值是唯一例外：其中不写美元符号。",
    ]
    if re.search(r"标准态|ΔG|Delta G|K.?θ|E.?θ", text, flags=re.IGNORECASE):
        requirements.append("涉及标准态时，标准态上标统一使用 ^{\\theta}；禁止用字母 `o/O` 或 \\circ 代替。")
    return requirements


def _batch_output_contract(batch_plan: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose only fields each batch item can actually use."""
    exercises = []
    for index, item in enumerate(batch_plan, start=1):
        contract = _public_exercise_contract()
        flags = _plan_item_content_flags(item)
        if not flags["options"]:
            contract.pop("options", None)
        if not flags["formulas"]:
            contract.pop("formulas", None)
        if not flags["tables"]:
            contract.pop("tables", None)
        if not flags["figures"]:
            contract.pop("figures", None)
        exercises.append({"batch_index": index, **contract})
    return {"exercises": exercises}


def _batch_prompt_contract(batch_plan: list[dict[str, Any]]) -> dict[str, Any]:
    """Use one shared schema per batch instead of repeating verbose field help per item."""
    conditional_fields: dict[str, dict[str, Any]] = {}
    for field in ("options", "formulas", "tables", "figures"):
        indexes = [
            index for index, item in enumerate(batch_plan, start=1)
            if _plan_item_content_flags(item).get(field)
        ]
        if indexes:
            conditional_fields[field] = {
                "batch_indexes": indexes,
                "schema": _public_exercise_contract()[field],
            }
    return {
        "exercises": f"数组，恰好 {len(batch_plan)} 项；batch_index 依次为 1 到 {len(batch_plan)}",
        "item_schema": {
            "batch_index": "整数；对应本批蓝图临时序号",
            "question_type": "逐字复制对应蓝图题型",
            "difficulty": "逐字复制对应蓝图难度",
            "target_skill": "对应蓝图目标能力",
            "variation_type": "复制 change_contract.kind",
            "stem": "完整独立题干；不含题号、标题、答案、解析或提示；公式使用 LaTeX 定界符",
            "knowledge_points": "与 required_knowledge_points 完整一致的字符串数组",
            "verification_note": "只写条件充分性检查，不含答案或推导",
            "diversity_signature": _public_exercise_contract()["diversity_signature"],
            "difficulty_evidence": _public_exercise_contract()["difficulty_evidence"],
        },
        "conditional_fields": conditional_fields,
    }


def _exercise_output_contract_for_plan_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return the minimal formal-output schema for one confirmed plan item."""
    contract = dict(_batch_output_contract([item])["exercises"][0])
    contract.pop("batch_index", None)
    return contract


def _without_internal_ids(value: Any) -> Any:
    """Remove storage linkage fields before serializing data into an LLM prompt."""
    hidden = {"plan_item_id", "source_question_id", "source_refs", "exercise_id", "source_ref", "parent_id"}
    if isinstance(value, dict):
        return {key: _without_internal_ids(item) for key, item in value.items() if key not in hidden}
    if isinstance(value, list):
        return [_without_internal_ids(item) for item in value]
    return value


def _semantic_batch_context(
    plan: dict[str, Any],
    batch_plan: list[dict[str, Any]],
    *,
    knowledge_mode: bool,
    include_source_content: bool = True,
) -> dict[str, Any]:
    """Return the source context permitted for this formal-generation batch."""
    scope = plan.get("source_scope") if isinstance(plan.get("source_scope"), dict) else {}
    candidates = [item for item in (plan.get("selected_source_questions") or []) if isinstance(item, dict)]
    candidates.extend(item for item in (scope.get("questions") or []) if isinstance(item, dict))
    by_id: dict[str, dict[str, Any]] = {}
    for item in candidates:
        source_id = _clean(item.get("source_question_id"), 80)
        if source_id and source_id not in by_id:
            by_id[source_id] = item

    sources: list[dict[str, Any]] = []
    for batch_index, item in enumerate(batch_plan, start=1):
        refs = _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
        item_sources = []
        for source_index, source_id in enumerate(refs, start=1):
            source = by_id.get(source_id, {})
            item_source = {
                "source_index": source_index,
                "knowledge_points": _string_list(source.get("knowledge_points"), limit=20),
                "source_type": _clean(source.get("question_type"), 100),
                "source_difficulty": _clean(source.get("source_difficulty"), 30),
            }
            if include_source_content:
                item_source.update({
                    "title": _clean(source.get("title"), 300),
                    "source_content": _clean(source.get("source_content") or source.get("stem_excerpt") or source.get("excerpt"), 18000),
                })
            item_sources.append(item_source)
        sources.append({
            "batch_index": batch_index,
            "coverage_role": _clean(item.get("coverage_role"), 20),
            "sources": item_sources,
        })
    return {
        "mode": "knowledge" if knowledge_mode else "exam",
        "title": _clean(plan.get("knowledge_title") or scope.get("title"), 300),
        "source_usage": (
            "边界证据而非题面模板：保留知识、公式与适用条件，但必须改写情境、实体、数值组合和句式。"
            if _clean((plan.get("blueprint") or {}).get("generation_strategy"), 40) in COMPREHENSIVE_STRATEGIES
            else "按已确认模式参考绑定来源；即使是平行题，也不得只替换数字或单位。"
        ),
        "items": sources,
    }


def _hydrate_single_source_content(plan: dict[str, Any], source_text: str) -> None:
    """Keep legacy single-source requests useful without leaking multi-source raw input.

    New source analysis persists ``source_content`` per source unit.  Older plans
    only have an excerpt, so a direct generation request with exactly one source
    can safely use its submitted text as that source's content.  Multi-source
    requests must be re-analysed instead of assigning the whole submission to
    every item.
    """
    text = _clean(source_text, 18000)
    if not text:
        return
    scope = plan.get("source_scope") if isinstance(plan.get("source_scope"), dict) else {}
    candidates = [row for row in (plan.get("selected_source_questions") or []) if isinstance(row, dict)]
    candidates.extend(row for row in (scope.get("questions") or []) if isinstance(row, dict))
    by_id: dict[str, dict[str, Any]] = {}
    for row in candidates:
        source_id = _clean(row.get("source_question_id"), 80)
        if source_id and source_id not in by_id:
            by_id[source_id] = row
    if len(by_id) == 1:
        source = next(iter(by_id.values()))
        if not _clean(source.get("source_content") or source.get("source_text"), 18000):
            excerpt = _clean(source.get("stem_excerpt") or source.get("excerpt"), 1200)
            source["source_content"] = "\n\n".join(part for part in (excerpt, text) if part)


def _semantic_batch_plan(
    batch_plan: list[dict[str, Any]],
    exercise_plan: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Expose semantic design constraints while keeping storage identifiers server-side."""
    set_positions = {
        _clean(item.get("plan_item_id"), 80): position
        for position, item in enumerate(exercise_plan or [], start=1)
        if isinstance(item, dict) and _clean(item.get("plan_item_id"), 80)
    }
    rows = []
    for index, item in enumerate(batch_plan, start=1):
        difficulty_intent = _difficulty_intent(
            item,
            set_position=set_positions.get(_clean(item.get("plan_item_id"), 80), index) - 1,
        )
        row = {
            "batch_index": index,
            "question_type": _clean(item.get("question_type"), 20),
            "difficulty": _clean(item.get("difficulty"), 20),
            "difficulty_intent": {
                "candidate_mechanisms": difficulty_intent["candidate_mechanisms"][:3],
                "blueprint_hint": difficulty_intent["blueprint_hint"],
            },
            "target_skill": _clean(item.get("target_skill"), 500),
            "change_contract": _plan_change_contract(item),
            "coverage_role": _clean(item.get("coverage_role"), 20),
            "required_knowledge_points": _string_list(item.get("required_knowledge_points"), limit=60),
            "type_rule": (_question_type_generation_requirements(item) or [""])[0],
            "stem_figure_required": _plan_requires_stem_figure(item),
        }
        constraints = _compact_required_constraints(item.get("required_constraints"))
        if constraints:
            row["required_constraints"] = constraints
        if item.get("parent_plan_item_id"):
            row.update({
                "variant_index": int(item.get("variant_index") or 1),
                "variant_count": int(item.get("variant_count") or 1),
                "variant_mode": _clean(item.get("variant_mode"), 30),
                "variant_role": _clean(item.get("variant_role"), 100),
            })
        if row["stem_figure_required"]:
            row["figure_design"] = _figure_design(item.get("figure_design"), required=True)
        rows.append(row)
    return rows


def _abstract_generation_context(plan: dict[str, Any], *, knowledge_mode: bool) -> dict[str, Any]:
    """Return only task-level metadata when source content is disabled.

    Source-specific definitions, formulas and boundaries live in each plan
    item's ``required_constraints``. Keeping them out of this global block
    prevents one source's constraints from leaking into another blueprint item.
    """
    analysis = plan.get("source_analysis") if isinstance(plan.get("source_analysis"), dict) else {}
    return {
        "mode": "knowledge" if knowledge_mode else "exam",
        "subject": _clean(analysis.get("subject"), 100),
        "task_level": "仅可使用每个蓝图项的 required_knowledge_points 和 required_constraints；不得借用其它来源约束。",
    }


def _batch_required_knowledge_point_issues(
    batch_exercises: list[dict[str, Any]],
    batch_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for raw_item in batch_exercises:
        if not isinstance(raw_item, dict):
            continue
        try:
            local_index = int(raw_item.get("batch_index")) - 1
        except (TypeError, ValueError):
            continue
        if local_index < 0 or local_index >= len(batch_plan):
            continue
        issue = _required_knowledge_point_issue(raw_item, batch_plan[local_index])
        if issue:
            issues.append({"batch_index": raw_item.get("batch_index"), **issue})
    return issues


def _batch_figure_issues(
    batch_exercises: list[dict[str, Any]],
    batch_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for raw_item in batch_exercises:
        if not isinstance(raw_item, dict):
            continue
        try:
            local_index = int(raw_item.get("batch_index")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= local_index < len(batch_plan):
            issues.extend(_exercise_figure_issues(raw_item, batch_plan[local_index], batch_index=raw_item.get("batch_index")))
    return issues


def _batch_needs_visual_reference(semantic_sources: dict[str, Any], batch_plan: list[dict[str, Any]]) -> bool:
    """Only attach original images to batches whose semantics indicate a visual dependency."""
    text = json.dumps({"sources": semantic_sources, "plan": batch_plan}, ensure_ascii=False)
    return bool(re.search(r"图|曲线|坐标|示意|figure|diagram|scatter|line|bar", text, flags=re.IGNORECASE))


def _provider_model_supports_vision(provider, model: str) -> bool:
    return provider_model_supports_vision(provider, model)


def _model_runtime(payload: dict[str, Any], has_images: bool):
    primary = get_provider(_clean(payload.get("provider"), 100) or None)
    primary_model = resolve_provider_model(primary, _clean(payload.get("model"), 200) or None)
    if not has_images or _provider_model_supports_vision(primary, primary_model):
        return primary, primary_model

    fallback = get_provider(_clean(payload.get("vision_provider"), 100) or None)
    requested_fallback = _clean(payload.get("vision_model"), 200) or _clean(fallback.vision_model, 200)
    fallback_model = resolve_provider_model(fallback, requested_fallback or None)
    if not _provider_model_supports_vision(fallback, fallback_model):
        raise ValueError(
            f"主模型 {primary.name}/{primary_model} 不支持读图，"
            f"图片回退模型 {fallback.name}/{fallback_model} 也未配置视觉能力。"
        )
    return fallback, fallback_model


def _primary_model_runtime(payload: dict[str, Any]):
    """Return the model explicitly selected for content generation.

    Vision fallback is an input-decoding concern. Once a source image has been
    analysed into the confirmed source snapshot, planning and question
    generation must not silently switch away from the user's selected model.
    """
    primary = get_provider(_clean(payload.get("provider"), 100) or None)
    return primary, resolve_provider_model(primary, _clean(payload.get("model"), 200) or None)


def _chat_protocol_provider(provider):
    """Clone a provider onto Chat Completions without mutating shared config."""
    if str(getattr(provider, "api_protocol", "") or "").strip().lower() in {
        "chat_completions",
        "openai_compatible",
        "",
    }:
        return provider
    return replace(provider, api_protocol="chat_completions", responses_streaming=False)


def _is_lingsuan_gpt(provider, model: str) -> bool:
    provider_name = str(getattr(provider, "name", "") or "").strip().lower()
    base_url = str(getattr(provider, "base_url", "") or "").strip().lower()
    model_name = str(model or "").strip().lower()
    return (
        ("lingsuan" in provider_name or "lingsuan.top" in base_url)
        and model_name.startswith("gpt-")
    )


def _practice_generation_client(provider, model: str) -> OpenAICompatibleClient:
    """Keep only Lingsuan GPT practice generation off the Responses API."""
    routed_provider = _chat_protocol_provider(provider) if _is_lingsuan_gpt(provider, model) else provider
    return OpenAICompatibleClient(routed_provider)


def _chat_fallback_client(client: OpenAICompatibleClient) -> OpenAICompatibleClient:
    """Move a failed Responses result to Chat and discard the unsafe result."""
    config = getattr(client, "config", None)
    protocol = str(getattr(config, "api_protocol", "") or "").strip().lower()
    if config is None or protocol not in {"responses", "responses_api"}:
        return client
    return OpenAICompatibleClient(_chat_protocol_provider(config))


def _model_route(payload: dict[str, Any], has_images: bool, provider, model: str) -> str:
    if not has_images:
        return "text_only"
    primary = get_provider(_clean(payload.get("provider"), 100) or None)
    primary_model = resolve_provider_model(primary, _clean(payload.get("model"), 200) or None)
    return "primary_multimodal" if provider.name == primary.name and model == primary_model else "vision_fallback"


def _user_content(text: str, images: list[str]) -> Any:
    if not images:
        return text
    return [
        {"type": "text", "text": text},
        *[{"type": "image_url", "image_url": {"url": image}} for image in images],
    ]


def _call_practice_json(
    client: OpenAICompatibleClient,
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float,
    thinking: str | None,
    timeout_seconds: int | None = None,
    ensure_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if timeout_seconds is None:
        timeout_seconds = _practice_stage_timeout("general", 300)
    if ensure_active is not None:
        ensure_active()
    with model_request_slot(getattr(client, "config", None)):
        result = client.chat_json(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max(DEFAULT_MODEL_MAX_TOKENS, 10000),
            thinking=thinking,
            timeout=timeout_seconds,
        )
    try:
        raw = _parse_safe_practice_json(result.content)
    except LLMError as first_error:
        repair_client = _chat_fallback_client(client)
        repair_messages = [
            *messages,
            {"role": "assistant", "content": result.content},
            {
                "role": "user",
                "content": (
                    "只修复上一个回答的 JSON 语法和字符串转义，不改变题目内容。"
                    "LaTeX 命令的反斜杠必须在 JSON 字符串中正确双写；"
                    "不得将 \\beta、\\frac、\\theta、\\rm 等命令转成退格、换页、制表或回车字符。"
                    "只输出一个合法 JSON 对象，不要 Markdown 代码围栏。"
                ),
            },
        ]
        if ensure_active is not None:
            ensure_active()
        with model_request_slot(getattr(repair_client, "config", None)):
            repaired = repair_client.chat_json(
                repair_messages,
                model=model,
                temperature=0,
                max_tokens=max(DEFAULT_MODEL_MAX_TOKENS, 10000),
                thinking="disabled",
                timeout=timeout_seconds,
            )
        try:
            raw = _parse_safe_practice_json(repaired.content)
        except LLMError as repair_error:
            raise LLMError(f"{first_error}；Chat 修复后仍失败：{repair_error}") from repair_error
    return raw


def _practice_stage_timeout(stage: str, default: int) -> int:
    key = f"PRACTICE_{stage.upper()}_TIMEOUT_SECONDS"
    raw = os.environ.get(key, os.environ.get("PRACTICE_MODEL_TIMEOUT_SECONDS", str(default)))
    try:
        return max(60, min(1800, int(raw)))
    except (TypeError, ValueError):
        return default


def _practice_semantic_review_should_run(practice: dict[str, Any], payload: dict[str, Any]) -> bool:
    if payload.get("semantic_review_enabled") is not True and payload.get("formal_quality_review") is not True:
        return False
    quality = practice.get("quality") if isinstance(practice.get("quality"), dict) else recompute_practice_quality(practice)
    if quality.get("blocking_issues") or int(quality.get("failed_count") or 0) > 0:
        return False
    return bool(quality.get("subject_review_candidates") or quality.get("boundary_issues") or payload.get("formal_quality_review"))


def _sample_series_points_for_review(series: dict[str, Any], *, limit: int = 24) -> list[list[float]]:
    """Expose chart geometry to review without allowing one chart to dominate the prompt."""
    points = [point for point in (series.get("points") or []) if isinstance(point, list) and len(point) >= 2]
    if len(points) <= limit:
        selected = points
    else:
        indexes = sorted({round(index * (len(points) - 1) / (limit - 1)) for index in range(limit)})
        selected = [points[index] for index in indexes]
    normalized: list[list[float]] = []
    for point in selected:
        try:
            normalized.append([float(point[0]), float(point[1])])
        except (TypeError, ValueError, IndexError):
            continue
    return normalized


def review_practice_semantics(practice: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Review one complete practice set in one bounded model operation.

    The reviewer reports risks only. It never rewrites questions, reveals
    answers, or turns an otherwise usable set into a missing result.
    """
    exercises = [
        item for item in (practice.get("exercises") or [])
        if isinstance(item, dict) and item.get("generation_status") != "failed"
    ]
    if not exercises:
        return {"status": "skipped", "reason": "no_usable_exercises", "items": []}
    blueprint = practice.get("blueprint") if isinstance(practice.get("blueprint"), dict) else {}
    planned_by_id = {
        _clean(item.get("plan_item_id"), 80): item
        for item in (blueprint.get("exercise_plan") or [])
        if isinstance(item, dict) and _clean(item.get("plan_item_id"), 80)
    }
    review_rows: list[dict[str, Any]] = []
    for index, exercise in enumerate(exercises, start=1):
        plan_item_id = _clean(exercise.get("parent_plan_item_id") or exercise.get("plan_item_id"), 80)
        planned_item = planned_by_id.get(plan_item_id) or {}
        review_rows.append({
            "number": exercise.get("number") or index,
            "question_type": _clean(exercise.get("question_type"), 30),
            "difficulty": _clean(exercise.get("difficulty"), 20),
            "stem": _clean(exercise.get("stem"), 12000),
            "options": _normalize_options(exercise.get("options")),
            "knowledge_points": _unique_strings(exercise.get("knowledge_points"), limit=60, item_limit=500),
            "formulas": [
                {
                    "latex": _clean(formula.get("latex"), 1000),
                    "caption": _clean(formula.get("caption"), 300),
                }
                for formula in (exercise.get("formulas") or [])[:20]
                if isinstance(formula, dict)
            ],
            "tables": [
                {
                    "headers": _unique_strings(table.get("headers"), limit=20, item_limit=300),
                    "rows": [row[:20] for row in (table.get("rows") or [])[:30] if isinstance(row, list)],
                }
                for table in (exercise.get("tables") or [])[:8]
                if isinstance(table, dict)
            ],
            "figure_summary": [
                {
                    "title": _clean(figure.get("title"), 300),
                    "x_label": _clean(figure.get("x_label"), 100),
                    "y_label": _clean(figure.get("y_label"), 100),
                    "series": [
                        {
                            "name": _clean(series.get("name"), 200),
                            "sampled_points": _sample_series_points_for_review(series),
                        }
                        for series in (figure.get("series") or [])[:8]
                        if isinstance(series, dict)
                    ],
                    "nodes": [
                        {
                            "label": _clean(node.get("label"), 200),
                            "x": node.get("x"),
                            "y": node.get("y"),
                        }
                        for node in (figure.get("nodes") or [])[:30]
                        if isinstance(node, dict)
                    ],
                    "renderer_contract": {
                        "series_names_are_visible_legend_labels": True,
                        "nodes_are_visible_markers_with_text_labels": True,
                    },
                }
                for figure in (exercise.get("figures") or [])
                if isinstance(figure, dict)
            ],
            "blueprint": {
                "target_skill": _clean(planned_item.get("target_skill"), 500),
                "required_knowledge_points": _unique_strings(
                    planned_item.get("required_knowledge_points"), limit=60, item_limit=500
                ),
                "required_constraints": planned_item.get("required_constraints") or {},
                "figure_design": planned_item.get("figure_design") or {},
            },
        })
    task = f"""# 任务

对下面整套研究生练习题做一次语义质量审查。只报告题干和蓝图可直接证明的问题，不输出答案、解析或完整解题过程。

每题、每个小问分别检查：学科事实，条件充分性，蓝图适用边界，术语与图/坐标语义，是否有唯一合理作答方向，任务是否偏离蓝图。

- high：事实错误、条件矛盾/缺少关键条件、违反硬边界，正式交付前必须修复。
- medium：明显歧义或教研上需要确认。
- low：纯表达建议，不影响作答。
- 逐项核对 blueprint.required_knowledge_points：题干必须实际要求学生使用每个必考知识点；练习题自己的 knowledge_points 字段只是声明，不是已经考查的证据。声明覆盖但题干未考查属于蓝图偏离。
- 逐项核对 required_constraints：只有与本题目标相关的定义、公式和适用边界才应被绑定；若蓝图把其它子主题的约束误绑到本题，应报告蓝图内部不一致，不应强迫题干堆入无关内容。
- 根据题干实际需要的识记、转换、方法选择、多概念综合、迁移、纠错或评价负担核对 difficulty；不得把 difficulty_evidence 或 difficulty_rationale 的自我声明当作证明。若“挑战”题仅为教材原句识记、唯一正确项明显的直接判断，应报告难度偏低。
- 不得为了显得严格而编造问题；不要把“要求学生掌握标准示意图”误判为题干必须给出全部曲线数据。
- figure_summary.renderer_contract 是实际导出器的可视规则；已声明会显示的图例和节点标签，不得误报为“图中未标注”。

## 待审查内容

{json.dumps(review_rows, ensure_ascii=False, indent=2)}

## 输出结构

只输出合法 JSON：
{{"items":[{{"number":1,"status":"passed|risk","risks":[{{"severity":"high|medium|low","code":"...","message":"简明问题","evidence":"题干中的直接证据","suggested_action":"最小修复方向"}}]}}],"set_summary":"..."}}
每题必须返回一项；无风险时 risks=[]。
"""
    provider, model = _primary_model_runtime(payload)
    raw = _call_practice_json(
        _practice_generation_client(provider, model),
        [
            {"role": "system", "content": "你只做题目语义质量审查，只输出合法 JSON。"},
            {"role": "user", "content": task},
        ],
        model=model,
        temperature=0,
        thinking="disabled",
        timeout_seconds=_practice_stage_timeout("semantic_review", 180),
        ensure_active=lambda: ensure_practice_generation_active(payload),
    )
    raw_by_number = {
        str(item.get("number") or "").strip(): item
        for item in (raw.get("items") or [])
        if isinstance(item, dict) and str(item.get("number") or "").strip()
    }
    items: list[dict[str, Any]] = []
    missing_numbers: list[str] = []
    for index, exercise in enumerate(exercises, start=1):
        number = str(exercise.get("number") or index)
        raw_item = raw_by_number.get(number)
        if not raw_item:
            missing_numbers.append(number)
            items.append({"number": exercise.get("number") or index, "status": "not_reviewed", "risks": []})
            continue
        risks: list[dict[str, Any]] = []
        for risk in raw_item.get("risks") or []:
            if not isinstance(risk, dict):
                continue
            severity = _clean(risk.get("severity"), 20).lower()
            message = _clean(risk.get("message"), 800)
            if severity not in {"high", "medium", "low"} or not message:
                continue
            risks.append({
                "severity": severity,
                "code": _clean(risk.get("code"), 100) or "semantic_risk",
                "message": message,
                "evidence": _clean(risk.get("evidence"), 800),
                "suggested_action": _clean(risk.get("suggested_action"), 800),
            })
        items.append({
            "number": exercise.get("number") or index,
            "status": "risk" if risks else "passed",
            "risks": risks,
        })
    actionable = [
        risk for item in items for risk in item.get("risks") or []
        if risk.get("severity") in {"high", "medium"}
    ]
    return {
        "status": "failed" if missing_numbers else ("warning" if actionable else "passed"),
        "triggered": True,
        "review_scope": "complete_set",
        "provider": provider.name,
        "model": model,
        "thinking": "disabled",
        "items": items,
        "risk_count": sum(len(item.get("risks") or []) for item in items),
        "actionable_risk_count": len(actionable),
        "missing_numbers": missing_numbers,
        "set_summary": _clean(raw.get("set_summary"), 1000),
    }


def _merge_incremental_semantic_review(
    practice: dict[str, Any],
    replacement_report: dict[str, Any],
    *,
    target_number: int,
) -> dict[str, Any]:
    """Replace one review item while retaining reviews for unchanged questions."""
    existing = practice.get("semantic_review") if isinstance(practice.get("semantic_review"), dict) else {}
    by_number = {
        str(item.get("number") or "").strip(): item
        for item in (existing.get("items") or [])
        if isinstance(item, dict) and str(item.get("number") or "").strip()
    }
    replacement_by_number = {
        str(item.get("number") or "").strip(): item
        for item in (replacement_report.get("items") or [])
        if isinstance(item, dict) and str(item.get("number") or "").strip()
    }
    target_key = str(target_number)
    by_number[target_key] = replacement_by_number.get(target_key) or {
        "number": target_number,
        "status": "not_reviewed",
        "risks": [],
    }

    candidate_numbers = set(
        recompute_practice_quality({**practice, "semantic_review": {}}).get("subject_review_candidates") or []
    )
    items: list[dict[str, Any]] = []
    missing_numbers: list[str] = []
    for index, exercise in enumerate(practice.get("exercises") or [], start=1):
        if not isinstance(exercise, dict) or exercise.get("generation_status") == "failed":
            continue
        number = str(exercise.get("number") or index)
        item = by_number.get(number) or {
            "number": exercise.get("number") or index,
            "status": "not_required",
            "risks": [],
        }
        if number in candidate_numbers and item.get("status") in {"not_reviewed", None}:
            missing_numbers.append(number)
        items.append(item)
    actionable = [
        risk
        for item in items
        for risk in (item.get("risks") or [])
        if isinstance(risk, dict) and _clean(risk.get("severity"), 20).lower() in {"high", "medium"}
    ]
    failed = replacement_report.get("status") == "failed" or bool(missing_numbers)
    return {
        **{
            key: value
            for key, value in replacement_report.items()
            if key not in {"items", "missing_numbers", "set_summary", "status", "review_scope"}
        },
        "status": "failed" if failed else ("warning" if actionable else "passed"),
        "triggered": True,
        "review_scope": "incremental_set",
        "items": items,
        "risk_count": sum(len(item.get("risks") or []) for item in items),
        "actionable_risk_count": len(actionable),
        "missing_numbers": missing_numbers,
        "set_summary": "重生成题目已增量复核；未修改题目沿用最近一次有效复核结论。",
    }


def _is_transport_generation_error(exc: Exception) -> bool:
    text = _clean(str(exc), 1000).lower()
    return isinstance(exc, LLMError) and any(token in text for token in (
        "provider streaming",
        "provider request",
        "provider http",
        "timeout",
        "timed out",
        "connection",
        "remote end closed",
        "empty reply",
    ))


def _call_practice_json_with_transport_retry(
    client: OpenAICompatibleClient,
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float,
    thinking: str | None,
    timeout_seconds: int,
    attempts: int = 2,
    backoff_seconds: float = 0.5,
    attempt_log: list[dict[str, Any]] | None = None,
    ensure_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        if ensure_active is not None:
            ensure_active()
        try:
            result = _call_practice_json(
                client,
                messages,
                model=model,
                temperature=temperature,
                thinking=thinking,
                timeout_seconds=timeout_seconds,
                ensure_active=ensure_active,
            )
            if attempt_log is not None:
                attempt_log.append({"attempt": attempt, "status": "succeeded"})
            return result
        except Exception as exc:
            if isinstance(exc, PracticeGenerationStopped):
                raise
            last_error = exc
            retryable = _is_transport_generation_error(exc)
            if attempt_log is not None:
                detail = _generation_error_detail(exc)
                attempt_log.append({
                    "attempt": attempt,
                    "status": "failed",
                    "error_code": detail["code"],
                    "retryable": retryable,
                })
            if not retryable or attempt >= max(1, attempts):
                raise
            delay = max(0.0, float(backoff_seconds)) * (2 ** (attempt - 1))
            if delay:
                time.sleep(delay + random.uniform(0, min(0.25, delay / 2)))
    raise last_error or LLMError("上游模型生成失败。")


def _normalize_plan(
    raw: dict[str, Any],
    *,
    count: int,
    planned_types: list[str],
    difficulty: str,
    planned_difficulties: list[str] | None = None,
    selected_types: list[str],
    source_files: list[str],
    source_scope: dict[str, Any] | None = None,
    selected_source_questions: list[dict[str, str]] | None = None,
    planned_source_ids: list[str] | None = None,
    generation_strategy: str = "single",
    include_source_content_in_generation: bool = True,
) -> dict[str, Any]:
    source = raw.get("source_analysis") if isinstance(raw.get("source_analysis"), dict) else {}
    blueprint = raw.get("blueprint") if isinstance(raw.get("blueprint"), dict) else {}
    raw_plan = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []
    exercise_plan = []
    default_difficulties = ["基础", "进阶", "挑战"]
    source_catalog = [item for item in (selected_source_questions or []) if isinstance(item, dict)]
    if not source_catalog and isinstance(source_scope, dict):
        source_catalog = [item for item in (source_scope.get("questions") or []) if isinstance(item, dict)]
    catalog_ids = [
        _clean(item.get("source_question_id"), 80)
        for item in source_catalog
        if _clean(item.get("source_question_id"), 80)
    ]
    planned_source_counts = {
        source_id: (planned_source_ids or []).count(source_id)
        for source_id in dict.fromkeys(planned_source_ids or [])
        if source_id
    }
    catalog_ids = list(dict.fromkeys(catalog_ids))
    alias_to_id = {f"S{index}": source_id for index, source_id in enumerate(catalog_ids, start=1)}
    selected_ids = {
        _clean(item.get("source_question_id"), 80)
        for item in source_catalog
        if _clean(item.get("source_question_id"), 80)
    }
    mode = _mode_kind(generation_strategy)
    has_multiple_sources = len(catalog_ids) >= 2
    required_multi = max(1, (count + 4) // 5) if mode == "comprehensive" and len(catalog_ids) >= 2 else 0
    multi_indexes = set(range(max(0, count - required_multi), count))
    for index in range(count):
        row = raw_plan[index] if index < len(raw_plan) and isinstance(raw_plan[index], dict) else {}
        locked_source_id = (
            _clean(planned_source_ids[index], 80)
            if planned_source_ids and index < len(planned_source_ids)
            else ""
        )
        source_question_id = _clean(row.get("source_question_id"), 80)
        if source_question_id in alias_to_id:
            source_question_id = alias_to_id[source_question_id]
        if locked_source_id:
            source_question_id = locked_source_id
        elif selected_ids and source_question_id not in selected_ids:
            source_question_id = _clean(
                source_catalog[index % len(source_catalog)].get("source_question_id"),
                80,
            )
        raw_refs = _unique_strings(row.get("source_refs"), limit=3, item_limit=80)
        source_refs: list[str] = []
        for ref in raw_refs:
            resolved = alias_to_id.get(ref, ref)
            if resolved in selected_ids and resolved not in source_refs:
                source_refs.append(resolved)
        if source_refs:
            source_question_id = source_refs[0]
        if source_question_id and source_question_id not in source_refs:
            source_refs.insert(0, source_question_id)
        if not source_refs and catalog_ids:
            source_refs = [catalog_ids[index % len(catalog_ids)]]
        if mode == "single_source":
            source_refs = source_refs[:1]
        elif index in multi_indexes and len(catalog_ids) >= 2 and len(source_refs) < 2:
            secondary = next(source_id for source_id in catalog_ids if source_id != source_refs[0])
            source_refs.append(secondary)
        source_question_id = source_refs[0] if source_refs else source_question_id
        if locked_source_id:
            # Per-source slot allocation is a user/program contract. Model
            # source_refs may describe intent but must never reassign the slot.
            source_question_id = locked_source_id
            source_refs = [locked_source_id]
        coverage_role = _clean(row.get("coverage_role"), 20)
        if coverage_role not in ALLOWED_COVERAGE_ROLES:
            if mode == "single_source":
                coverage_role = "变式"
            elif len(source_refs) >= 2:
                coverage_role = "综合"
            elif index == 0:
                coverage_role = "铺垫"
            elif index == count - 1:
                coverage_role = "迁移"
            else:
                coverage_role = "连接"
        target_skill = _clean(row.get("target_skill"), 500) or _clean((source.get("skills") or [""])[0], 500) or "核心能力"
        variation_type = _clean(row.get("variation_type"), 200) or _default_structural_change(index, mode=mode, has_multiple_sources=has_multiple_sources)
        design_intent = _clean(row.get("design_intent"), 800) or f"围绕{target_skill}完成{variation_type}训练。"
        item_difficulty = (
            planned_difficulties[index]
            if planned_difficulties and index < len(planned_difficulties)
            else _clean(row.get("difficulty"), 20)
            if _clean(row.get("difficulty"), 20) in ALLOWED_DIFFICULTIES
            else default_difficulties[min(index * 3 // max(count, 1), 2)]
        )
        structural_change = _clean(row.get("structural_change"), 100)
        if structural_change not in STRUCTURAL_CHANGE_TYPES:
            structural_change = _default_structural_change(index, mode=mode, has_multiple_sources=has_multiple_sources)
        difficulty_levers, difficulty_rationale = _difficulty_design(
            item_difficulty,
            planned_types[index],
            levers=row.get("difficulty_levers"),
            rationale=row.get("difficulty_rationale"),
            structural_change=structural_change,
            target_skill=target_skill,
        )
        allow_partition = (
            generation_strategy == "knowledge_item_wise"
            and bool(source_question_id)
            and planned_source_counts.get(source_question_id, 0) > 1
        )
        required_knowledge_points = _required_knowledge_points_for_plan_item(
            source_refs,
            source_catalog,
            row.get("required_knowledge_points") or row.get("knowledge_points") or source.get("knowledge_points"),
            generation_strategy,
            allow_partition=allow_partition,
        )
        required_constraints = _required_constraints_for_plan_item(
            source_refs,
            source_catalog,
            row.get("required_constraints"),
            generation_strategy,
            source if len(source_catalog) <= 1 else None,
            allow_partition=allow_partition,
        )
        stem_figure_required = row.get("stem_figure_required") is True or row.get("requires_figure") is True
        exercise_plan.append(
            {
                "number": index + 1,
                "plan_item_id": f"plan_item_{index + 1:02d}",
                "question_type": planned_types[index],
                "difficulty": item_difficulty,
                "difficulty_design_level": item_difficulty,
                "target_skill": target_skill,
                "variation_type": variation_type,
                "structural_change": structural_change,
                "design_intent": design_intent,
                "difficulty_levers": difficulty_levers,
                "difficulty_rationale": difficulty_rationale,
                "source_question_id": source_question_id,
                "source_refs": source_refs,
                "coverage_role": coverage_role,
                "required_knowledge_points": required_knowledge_points,
                "required_constraints": required_constraints,
                "stem_figure_required": stem_figure_required,
                "figure_design": _figure_design(row.get("figure_design"), required=stem_figure_required),
            }
        )
    plan = {
        "schema_version": "answer_book.practice_plan.v1",
        "source_analysis": {
            "subject": _clean(source.get("subject"), 100) or "未指定",
            "question_type": _clean(source.get("question_type"), 100),
            "knowledge_points": _string_list(source.get("knowledge_points")),
            "skills": _string_list(source.get("skills")),
            "difficulty": _clean(source.get("difficulty"), 100),
            "solution_strategy": _string_list(source.get("solution_strategy")),
            "essential_definitions": _string_list(source.get("essential_definitions"), limit=20),
            "essential_formulas": _string_list(source.get("essential_formulas"), limit=20),
            "applicable_boundaries": _string_list(source.get("applicable_boundaries"), limit=20),
            "common_errors": _string_list(source.get("common_errors")),
            "uncertainties": _string_list(source.get("uncertainties")),
        },
        "blueprint": {
            "training_goal": _clean(blueprint.get("training_goal"), 1000),
            "progression": _string_list(blueprint.get("progression"), limit=count),
            "design_notes": _string_list(blueprint.get("design_notes")),
            "exercise_plan": exercise_plan,
            "question_type_mode": "selected" if selected_types else "random",
            "question_type_plan": planned_types,
            "difficulty_mode": difficulty,
            "generation_strategy": generation_strategy,
            "expected_source_counts": {
                source_id: planned_source_ids.count(source_id)
                for source_id in dict.fromkeys(planned_source_ids or [])
                if source_id
            },
            "include_source_content_in_generation": include_source_content_in_generation,
        },
        "source_files": source_files,
        "source_scope": source_scope or {"mode": "single", "questions": []},
        "selected_source_questions": selected_source_questions or [],
        "include_source_content_in_generation": include_source_content_in_generation,
        "scope_cover": scope_cover_summary(
            source_scope,
            source_catalog,
            exercise_plan,
        ),
    }
    plan["mode_contract"] = validate_practice_mode_contract(plan)
    plan["blueprint_audit"] = audit_practice_blueprint(plan)
    return plan


def _ensure_selected_source_coverage(
    exercise_plan: list[dict[str, Any]],
    selected_source_questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically repair model plans that omit selected source units.

    The user-selected scope is authoritative. When there are enough planned
    exercises, replace duplicate source assignments with missing selected IDs
    so the later coverage gate cannot reject a valid user selection merely
    because the model under-covered two units. Smaller comprehensive sets are
    intentionally left untouched and reported as an incomplete-coverage warning.
    """
    selected_ids = [
        _clean(item.get("source_question_id"), 80)
        for item in selected_source_questions or []
        if isinstance(item, dict) and _clean(item.get("source_question_id"), 80)
    ]
    if not selected_ids or len(exercise_plan) < len(selected_ids):
        return exercise_plan
    selected_set = set(selected_ids)
    counts: dict[str, int] = {}
    for item in exercise_plan:
        source_id = _clean(item.get("source_question_id"), 80)
        if source_id in selected_set:
            counts[source_id] = counts.get(source_id, 0) + 1
    missing = [source_id for source_id in selected_ids if not counts.get(source_id)]
    if not missing:
        return exercise_plan
    replacement_indexes = [
        index
        for index, item in enumerate(exercise_plan)
        if counts.get(_clean(item.get("source_question_id"), 80), 0) > 1
    ]
    for index, source_id in zip(replacement_indexes, missing):
        previous = _clean(exercise_plan[index].get("source_question_id"), 80)
        counts[previous] = max(0, counts.get(previous, 0) - 1)
        exercise_plan[index]["source_question_id"] = source_id
        refs = _unique_strings(exercise_plan[index].get("source_refs"), limit=3, item_limit=80)
        exercise_plan[index]["source_refs"] = [source_id, *[ref for ref in refs if ref != source_id]][:3]
        counts[source_id] = counts.get(source_id, 0) + 1
    return exercise_plan


def _blueprint_unit_key(item: dict[str, Any], *, knowledge_mode: bool) -> tuple[str, str]:
    """Return the logical owner of a blueprint item.

    A logical unit is what the user recognises as one piece of work (one
    original question, one knowledge point, or one cross-source synthesis).
    It deliberately differs from an individual provider request: a large unit
    may be split into several small call batches while retaining one unit ID.
    """
    refs = _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
    if len(refs) >= 2:
        label = "综合：" + "、".join(refs)
        return "unit_cross_" + "_".join(refs), label
    if refs:
        return f"unit_source_{refs[0]}", f"来源：{refs[0]}"
    points = _unique_strings(item.get("required_knowledge_points"), limit=3, item_limit=120)
    if points:
        label = "知识点：" + "、".join(points[:2])
        key = re.sub(r"[^0-9A-Za-z_]+", "_", "_".join(points[:2])).strip("_") or "general"
        return f"unit_knowledge_{key}", label
    return ("unit_knowledge_general", "知识点综合") if knowledge_mode else ("unit_exam_general", "原题综合")


def _blueprint_batch_size(items: list[dict[str, Any]]) -> int:
    """Keep complex blueprint refinement calls intentionally small."""
    complex_types = {"计算题", "综合题", "作图题"}
    return 3 if any(_clean(item.get("question_type"), 20) in complex_types for item in items) else 5


def build_blueprint_generation_units(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a blueprint into user-meaningful units and bounded call batches."""
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    items = [item for item in (blueprint.get("exercise_plan") or []) if isinstance(item, dict)]
    knowledge_mode = _clean(plan.get("source_mode"), 30) == "knowledge"
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        unit_id, label = _blueprint_unit_key(item, knowledge_mode=knowledge_mode)
        group = grouped.setdefault(unit_id, {"unit_id": unit_id, "label": label, "items": []})
        group["items"].append(item)
    units: list[dict[str, Any]] = []
    for group in grouped.values():
        rows = group["items"]
        size = _blueprint_batch_size(rows)
        batches = []
        for offset in range(0, len(rows), size):
            batch = rows[offset : offset + size]
            batches.append({
                "batch_id": f"{group['unit_id']}_batch_{len(batches) + 1:02d}",
                "plan_item_ids": [_clean(row.get("plan_item_id"), 80) for row in batch],
                "size": len(batch),
                "status": "pending",
                "retry_count": 0,
            })
        units.append({
            "unit_id": group["unit_id"],
            "label": group["label"],
            "plan_item_ids": [_clean(row.get("plan_item_id"), 80) for row in rows],
            "call_batches": batches,
        })
    return units


def _blueprint_signature(item: dict[str, Any]) -> tuple[tuple[str, ...], str, str, str, str]:
    return (
        tuple(_unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)),
        _clean(item.get("question_type"), 20),
        _clean(item.get("difficulty"), 20),
        _clean(item.get("target_skill"), 500),
        _clean(item.get("variation_type"), 200),
    )


def _blueprint_duplicate_item_ids(items: list[dict[str, Any]]) -> list[list[str]]:
    grouped: dict[tuple[tuple[str, ...], str, str, str, str], list[str]] = {}
    for item in items:
        grouped.setdefault(_blueprint_signature(item), []).append(_clean(item.get("plan_item_id"), 80))
    return [ids for ids in grouped.values() if len(ids) > 1]


def _blueprint_refinement_context(plan: dict[str, Any], batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Supply only the semantics relevant to a blueprint batch, never whole files."""
    scope = plan.get("source_scope") if isinstance(plan.get("source_scope"), dict) else {}
    catalog = [row for row in (plan.get("selected_source_questions") or scope.get("questions") or []) if isinstance(row, dict)]
    required_refs = {
        ref
        for item in batch
        for ref in _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80)
    }
    sources = []
    for row in catalog:
        source_id = _clean(row.get("source_question_id"), 80)
        if source_id and source_id not in required_refs:
            continue
        sources.append({
            "source_question_id": source_id,
            "title": _clean(row.get("title"), 300),
            "stem_excerpt": _clean(row.get("stem_excerpt") or row.get("excerpt"), 1200),
            "question_type": _clean(row.get("question_type"), 100),
            "knowledge_points": _string_list(row.get("knowledge_points"), limit=20),
            "required_constraints": _required_constraints_for_refs([], [], row),
        })
    bound_knowledge_points: list[str] = []
    for source in sources:
        for point in _string_list(source.get("knowledge_points"), limit=20):
            if point not in bound_knowledge_points:
                bound_knowledge_points.append(point)
    analysis = plan.get("source_analysis") if isinstance(plan.get("source_analysis"), dict) else {}
    return {
        "subject": _clean(analysis.get("subject"), 100),
        "bound_knowledge_points": bound_knowledge_points,
        "sources": sources,
    }


def _refine_blueprint_batch(
    plan: dict[str, Any],
    batch: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    occupied: list[dict[str, Any]],
    retry_reason: str = "",
) -> list[dict[str, Any]]:
    """Ask one provider call to fill the details of already reserved slots."""
    ensure_practice_generation_active(payload)
    if not batch:
        return []
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    provider, model = _model_runtime(payload, False)
    locked_fields = {
        "plan_item_id", "number", "source_question_id", "source_refs", "question_type", "difficulty",
        "coverage_role", "required_knowledge_points", "required_constraints",
    }
    slots = [{key: value for key, value in item.items() if key in locked_fields or key in {"target_skill", "variation_type", "structural_change"}} for item in batch]
    occupied_summary = [
        {
            "source_refs": row.get("source_refs"),
            "question_type": row.get("question_type"),
            "difficulty": row.get("difficulty"),
            "target_skill": row.get("target_skill"),
            "variation_type": row.get("variation_type"),
        }
        for row in occupied[:30]
    ]
    output_contract = {
        "plan_items": [
            {
                "plan_item_id": "必须等于预留槽位 ID",
                "target_skill": "具体、可考查的能力目标",
                "variation_type": "具体变式方式",
                "structural_change": "改变未知量/改变求解路径/增加边界条件/逆向求解/比较与优化/跨情境迁移/增加多步约束/改变子问结构",
                "design_intent": "本题的教学设计意图",
                "difficulty_levers": ["选择一种主要难度方向，必要时再选一种辅助方向"],
                "difficulty_rationale": "预期学生的主要认知瓶颈，不写固定步数",
            }
        ]
    }
    task = f"""# 任务

细化一组已由全局规划锁定的研究生训练蓝图槽位。只补全每项的设计细节，绝不能改变题号、来源绑定、题型、难度、必考知识点或题量。

## 任务级语义

{json.dumps(_blueprint_refinement_context(plan, batch), ensure_ascii=False, indent=2)}

## 全局训练目标

{json.dumps({"training_goal": blueprint.get("training_goal"), "progression": blueprint.get("progression"), "design_notes": blueprint.get("design_notes")}, ensure_ascii=False)}

## 本批预留槽位（必须逐项返回）

{json.dumps(slots, ensure_ascii=False, indent=2)}

## 已被其它槽位占用的设计签名（不得重复）

{json.dumps(occupied_summary, ensure_ascii=False, indent=2)}

## 约束

- 必须恰好返回 {len(batch)} 项，且 plan_item_id 与预留槽位一一对应
- target_skill 与 variation_type 的组合不得与已占用摘要或本批其它项完全相同
- 设计意图必须体现本项的题型、难度与必考知识点；不得只写“换数字”
- 每项只能使用其 sources、required_knowledge_points 与 required_constraints 已确认的知识范围；科目名称、全局训练目标和其它槽位只用于保持整套连贯，不能为本项增加知识点
- difficulty_levers 是方向而非检查表；自主选择一种最合适的主要方向，最多再加一种辅助方向，不得全部堆叠或用固定步数说明难度
- {retry_reason or "这是首次细化，请优先保证整套蓝图的差异性。"}
- 只输出合法 JSON

## 输出结构

{json.dumps(output_contract, ensure_ascii=False, indent=2)}
"""
    raw = _call_practice_json(
        _practice_generation_client(provider, model),
        [{"role": "system", "content": "你是研究生教研专家。只细化预留蓝图槽位并输出合法 JSON。"}, {"role": "user", "content": task}],
        model=model,
        temperature=0.25,
        thinking=_clean(payload.get("thinking"), 20) or None,
        timeout_seconds=240,
        ensure_active=lambda: ensure_practice_generation_active(payload),
    )
    ensure_practice_generation_active(payload)
    rows = raw.get("plan_items") if isinstance(raw.get("plan_items"), list) else raw.get("exercise_plan")
    rows = [row for row in rows or [] if isinstance(row, dict)]
    expected_ids = {_clean(item.get("plan_item_id"), 80) for item in batch}
    actual = {_clean(row.get("plan_item_id"), 80) for row in rows}
    if len(rows) != len(batch) or actual != expected_ids:
        raise ValueError(f"蓝图细化批次返回的计划项不完整：期望 {sorted(expected_ids)}，实际 {sorted(actual)}。")
    by_id = {_clean(row.get("plan_item_id"), 80): row for row in rows}
    refined = []
    for original in batch:
        candidate = by_id[_clean(original.get("plan_item_id"), 80)]
        target_skill = _clean(candidate.get("target_skill"), 500)
        variation_type = _clean(candidate.get("variation_type"), 200)
        design_intent = _clean(candidate.get("design_intent"), 800)
        if not target_skill or not variation_type or not design_intent:
            raise ValueError("蓝图细化批次缺少目标能力、变化方式或设计意图。")
        structural_change = _clean(candidate.get("structural_change"), 100)
        if structural_change not in STRUCTURAL_CHANGE_TYPES:
            structural_change = _clean(original.get("structural_change"), 100)
        levers, rationale = _difficulty_design(
            _clean(original.get("difficulty"), 20),
            _clean(original.get("question_type"), 20),
            levers=candidate.get("difficulty_levers"),
            rationale=candidate.get("difficulty_rationale"),
            structural_change=structural_change,
            target_skill=target_skill,
        )
        refined.append({
            **original,
            "target_skill": target_skill,
            "variation_type": variation_type,
            "structural_change": structural_change,
            "design_intent": design_intent,
            "difficulty_levers": levers,
            "difficulty_rationale": rationale,
            "difficulty_design_level": _clean(original.get("difficulty"), 20),
        })
    return refined


def refine_blueprint_units(plan: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Run bounded concurrent blueprint-detail calls and retry collision items once."""
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    items = [item for item in (blueprint.get("exercise_plan") or []) if isinstance(item, dict)]
    units = build_blueprint_generation_units(plan)
    by_id = {_clean(item.get("plan_item_id"), 80): item for item in items}
    max_workers = blueprint_refinement_concurrency(payload)
    batch_records = [batch for unit in units for batch in unit.get("call_batches") or []]
    failures: list[dict[str, Any]] = []
    call_count = 0

    def report_progress(completed: int, total: int, message: str) -> None:
        job_id = _clean(payload.get("_job_id"), 100)
        if not job_id:
            return
        try:
            from .practice_jobs import update_practice_job
            update_practice_job(
                job_id,
                total_count=total,
                generated_count=completed,
                current_operation=message,
                progress_message=f"蓝图全局分配已完成；正在设计第 {completed}/{total} 个生成批次。{message}",
            )
        except Exception:
            # Progress persistence is observability only; it must not make a
            # valid blueprint fail when the job file is unavailable.
            pass

    def run_batch(record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        ensure_practice_generation_active(payload)
        selected = [by_id[item_id] for item_id in record.get("plan_item_ids") or [] if item_id in by_id]
        selected_ids = set(record.get("plan_item_ids") or [])
        occupied = [item for item_id, item in by_id.items() if item_id not in selected_ids]
        return record, _refine_blueprint_batch(plan, selected, payload=payload, occupied=occupied)

    progress_total = 1 + len(batch_records)
    report_progress(1, progress_total, "已完成全局槽位分配")
    completed_batches = 0
    for record, future in iter_bounded_futures(
        batch_records,
        run_batch,
        max_workers=max_workers,
        thread_name_prefix="blueprint-unit",
        ensure_active=lambda: ensure_practice_generation_active(payload),
    ):
        call_count += 1
        try:
            _, rows = future.result()
            ensure_practice_generation_active(payload)
            for row in rows:
                by_id[_clean(row.get("plan_item_id"), 80)] = row
            record["status"] = "completed"
        except PracticeGenerationStopped:
            raise
        except Exception:
            record["retry_count"] = 1
            # One automatic retry is deliberately local to this batch. A
            # transient provider/JSON failure should not restart the whole
            # user task or overwrite other completed units.
            call_count += 1
            try:
                _, rows = run_batch(record)
                for row in rows:
                    by_id[_clean(row.get("plan_item_id"), 80)] = row
                record["status"] = "completed_after_retry"
            except PracticeGenerationStopped:
                raise
            except Exception as retry_exc:
                # A failed multi-item refinement must not discard its
                # healthy siblings. Retry every item independently and
                # retain any successful refinement in the shared plan.
                isolated_successes = 0
                isolated_failures: list[str] = []
                for item_id in record.get("plan_item_ids") or []:
                    item = by_id.get(_clean(item_id, 80))
                    if not item:
                        continue
                    call_count += 1
                    try:
                        occupied = [row for pid, row in by_id.items() if pid != item_id]
                        by_id[item_id] = _refine_blueprint_batch(
                            plan,
                            [item],
                            payload=payload,
                            occupied=occupied,
                            retry_reason="原批次细化失败；仅重试本项，其他计划项保持不变。",
                        )[0]
                        isolated_successes += 1
                    except PracticeGenerationStopped:
                        raise
                    except Exception as item_exc:
                        isolated_failures.append(item_id)
                        failures.append({
                            "batch_id": f"{record.get('batch_id')}_item_{item_id}",
                            "plan_item_ids": [item_id],
                            "error": _clean(item_exc, 600),
                            "initial_error": _clean(retry_exc, 600),
                            "retryable": True,
                        })
                record["status"] = "partial_after_isolation" if isolated_successes else "fallback"
                record["error"] = _clean(retry_exc, 600)
                record["isolated_success_count"] = isolated_successes
                record["isolated_failed_item_ids"] = isolated_failures
        completed_batches += 1
        report_progress(1 + completed_batches, progress_total, f"已处理 {record.get('batch_id')}")

    # A collision has an unambiguous repair target: keep the first item and
    # re-design only the later items. This avoids throwing away healthy units.
    collision_ids = [item_id for group in _blueprint_duplicate_item_ids(list(by_id.values())) for item_id in group[1:]]
    for item_id in collision_ids:
        ensure_practice_generation_active(payload)
        item = by_id.get(item_id)
        if not item:
            continue
        call_count += 1
        try:
            occupied = [row for pid, row in by_id.items() if pid != item_id]
            by_id[item_id] = _refine_blueprint_batch(
                plan,
                [item],
                payload=payload,
                occupied=occupied,
                retry_reason="此前与另一计划项发生完全重复；本次必须改变目标能力或变化方式，且不得复用已占用组合。",
            )[0]
        except PracticeGenerationStopped:
            raise
        except Exception as exc:
            failures.append({"batch_id": f"repair_{item_id}", "plan_item_ids": [item_id], "error": _clean(exc, 600), "retryable": True})

    ordered = [by_id.get(_clean(item.get("plan_item_id"), 80), item) for item in items]
    blueprint["exercise_plan"] = ordered
    plan["blueprint"] = blueprint
    return {
        "enabled": True,
        "unit_count": len(units),
        "call_count": call_count,
        "max_concurrency": max_workers,
        "units": units,
        "failures": failures,
        "fallback_item_count": sum(len(row.get("plan_item_ids") or []) for row in failures if row.get("batch_id", "").startswith("unit_")),
        "duplicate_repair_attempted": len(collision_ids),
    }


def repair_blueprint_audit_findings(
    plan: dict[str, Any],
    payload: dict[str, Any],
    audit: dict[str, Any],
    *,
    max_items: int = 6,
) -> dict[str, Any]:
    """Repair only blueprint items proven to contain a foreign assessed topic."""
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    items = [item for item in (blueprint.get("exercise_plan") or []) if isinstance(item, dict)]
    by_id = {_clean(item.get("plan_item_id"), 80): item for item in items}
    findings = [
        finding
        for finding in (audit.get("findings") or [])
        if isinstance(finding, dict) and finding.get("code") == "cross_source_design_leak"
    ]
    finding_by_id = {
        _clean(finding.get("plan_item_id"), 80): finding
        for finding in findings
        if _clean(finding.get("plan_item_id"), 80)
    }
    target_ids = list(finding_by_id)[: max(0, int(max_items))]
    repaired: list[str] = []
    failures: list[dict[str, Any]] = []
    call_count = 0
    for item_id in target_ids:
        ensure_practice_generation_active(payload)
        item = by_id.get(item_id)
        finding = finding_by_id[item_id]
        if not item:
            continue
        forbidden = [
            _clean(match.get("anchor"), 100)
            for match in (finding.get("matches") or [])
            if isinstance(match, dict) and _clean(match.get("anchor"), 100)
        ]
        call_count += 1
        try:
            occupied = [row for pid, row in by_id.items() if pid != item_id]
            by_id[item_id] = _refine_blueprint_batch(
                plan,
                [item],
                payload=payload,
                occupied=occupied,
                retry_reason=(
                    "确认门禁发现本项实际考查了未绑定主题："
                    + "、".join(forbidden[:6])
                    + "。保持所有锁定字段不变，只重写目标、变式、设计意图和难度说明，"
                    "并严格限制在本项绑定来源、必考知识点与约束内；不要用未绑定主题作考查要求。"
                ),
            )[0]
            repaired.append(item_id)
        except PracticeGenerationStopped:
            raise
        except Exception as exc:
            failures.append({
                "plan_item_id": item_id,
                "error": _clean(exc, 600),
                "retryable": False,
            })
    blueprint["exercise_plan"] = [by_id.get(_clean(item.get("plan_item_id"), 80), item) for item in items]
    plan["blueprint"] = blueprint
    return {
        "enabled": bool(target_ids),
        "attempted_item_ids": target_ids,
        "repaired_item_ids": repaired,
        "unattempted_item_ids": list(finding_by_id)[len(target_ids):],
        "call_count": call_count,
        "failures": failures,
    }


def _difficulty_plan(mode: str, count: int) -> list[str]:
    count = max(1, count)
    if mode in ALLOWED_DIFFICULTIES:
        return [mode] * count
    if mode == "基础为主":
        basic_count = max(1, round(count * 0.7))
        return (["基础"] * basic_count + ["进阶"] * count)[:count]
    if mode == "基础到进阶":
        basic_count = (count + 1) // 2
        return ["基础"] * basic_count + ["进阶"] * (count - basic_count)
    if mode in {"进阶为主", "同等难度"}:
        # "同等难度" is replaced by source_difficulty_plan when the user
        # selected source questions. Without source metadata, use a balanced
        # fallback instead of silently turning every item into advanced.
        if mode == "同等难度":
            defaults = ["基础", "进阶", "挑战"]
            return [defaults[min(index * 3 // count, 2)] for index in range(count)]
        advanced_count = max(1, round(count * 0.7))
        challenge_count = count - advanced_count
        return ["进阶"] * advanced_count + ["挑战"] * challenge_count
    if mode == "进阶到挑战":
        advanced_count = (count + 1) // 2
        return ["进阶"] * advanced_count + ["挑战"] * (count - advanced_count)
    defaults = ["基础", "进阶", "挑战"]
    return [defaults[min(index * 3 // count, 2)] for index in range(count)]


def _source_difficulty_plan(selected_source_questions: list[dict[str, Any]], count: int) -> list[str] | None:
    values = []
    for index in range(count):
        item = selected_source_questions[index % len(selected_source_questions)] if selected_source_questions else {}
        value = _clean(item.get("source_difficulty") or item.get("difficulty"), 20)
        if value not in ALLOWED_DIFFICULTIES:
            return None
        values.append(value)
    return values or None


def _normalize_source_scope(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    questions = []
    for index, item in enumerate(value.get("questions") or [], start=1):
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title"), 300)
        excerpt = _clean(item.get("stem_excerpt"), 1200)
        if not title and not excerpt:
            continue
        parent_id = _clean(item.get("parent_id"), 80)
        questions.append(
            {
                "source_question_id": _clean(item.get("source_question_id"), 80) or f"source_{index:02d}",
                "number": _clean(item.get("number"), 50) or str(index),
                "title": title or excerpt[:80],
                "stem_excerpt": excerpt,
                "source_content": _clean(item.get("source_content") or item.get("source_text"), 18000),
                "question_type": _clean(item.get("question_type"), 100),
                "source_difficulty": _clean(item.get("source_difficulty") or item.get("difficulty"), 20),
                "knowledge_points": _string_list(item.get("knowledge_points"), limit=60),
                "required_constraints": _required_constraints_for_refs([], [], item),
                "parent_id": parent_id,
                "source_ref": {
                    "page": _clean(item.get("source_ref", {}).get("page") if isinstance(item.get("source_ref"), dict) else item.get("page"), 50),
                    "block": _clean(item.get("source_ref", {}).get("block") if isinstance(item.get("source_ref"), dict) else item.get("block"), 200),
                    "fragment": _clean(item.get("source_ref", {}).get("fragment") if isinstance(item.get("source_ref"), dict) else item.get("stem_excerpt"), 1200),
                },
            }
        )
    mode = _clean(value.get("mode"), 30)
    if mode not in {"single", "question_set"}:
        mode = "question_set" if len(questions) > 1 else "single"
    if mode == "question_set" and len(questions) < 2:
        mode = "single"
    # 默认展示粒度：存在顶层项（无 parent_id）时取 top_level，否则取 atomic
    has_top = any(not q.get("parent_id") for q in questions)
    granularity = _clean(value.get("granularity"), 20)
    if granularity not in {"top_level", "atomic"}:
        granularity = "top_level" if has_top else "atomic"
    return {
        "mode": mode,
        "title": _clean(value.get("title"), 300),
        "granularity": granularity,
        "has_hierarchy": has_top and any(q.get("parent_id") for q in questions),
        "questions": questions,
    }


def _scope_question_flat(source_scope: dict[str, Any]) -> list[dict[str, Any]]:
    """返回按当前粒度展开后的扁平单元列表（含子项）。"""
    questions = source_scope.get("questions") or []
    return [q for q in questions if isinstance(q, dict)]


def _unit_children(questions: list[dict[str, Any]], unit_id: str) -> list[dict[str, Any]]:
    """返回给定单元的直接子项（parent_id 匹配）。"""
    return [q for q in questions if q.get("parent_id") == unit_id]


def _aggregate_unit_content(questions: list[dict[str, Any]], unit: dict[str, Any]) -> dict[str, Any]:
    """把顶层父项的内容聚合为其全部子项知识并集。

    修复 P0-1：此前父项只复制了第一个子项内容，导致 A 场景的其余 5 个
    名词解释子项漏考。这里把子项的题干摘要拼接、知识点取并集，保证顶层题
    反映所有子项主题。
    """
    children = _unit_children(questions, unit.get("source_question_id") or "")
    if not children:
        return unit
    fragment_join = "\n\n".join(
        [str(u.get("stem_excerpt") or "") for u in children if u.get("stem_excerpt")]
        or [str(unit.get("stem_excerpt") or "")]
    )
    kps: list[str] = []
    constraints = {field: [] for field in _CONSTRAINT_FIELDS}
    for u in [*children, unit]:
        for kp in u.get("knowledge_points") or []:
            kp = _clean(kp, 200)
            if kp and kp not in kps:
                kps.append(kp)
        unit_constraints = _required_constraints_for_refs([], [], u)
        for field in _CONSTRAINT_FIELDS:
            for value in unit_constraints[field]:
                if value not in constraints[field]:
                    constraints[field].append(value)
    return {
        **unit,
        "stem_excerpt": fragment_join.strip(),
        "source_content": "\n\n".join(
            _clean(u.get("source_content") or u.get("stem_excerpt"), 18000)
            for u in children
            if _clean(u.get("source_content") or u.get("stem_excerpt"), 18000)
        ),
        "knowledge_points": kps[:60],
        "required_constraints": constraints,
        "_child_count": len(children),
        "_child_ids": [u.get("source_question_id") for u in children],
    }


def resolve_scope_granularity(source_scope: dict[str, Any], granularity: str | None = None) -> list[dict[str, Any]]:
    """
    按指定粒度解析 source_scope 的可选单元列表。

    - top_level：只保留无 parent_id 的顶层题；父项内容聚合其全部子项（见 _aggregate_unit_content）。
    - atomic：保留所有原子项；有子项的父项不再展开（叶子节点集合）。
    - default：对齐 source_scope.granularity。

    返回已归一化的单元字典列表。
    """
    g = _clean(granularity, 20) or _clean(source_scope.get("granularity"), 20)
    questions = [q for q in (source_scope.get("questions") or []) if isinstance(q, dict)]
    has_hierarchy = any(q.get("parent_id") for q in questions)
    if g == "atomic" and has_hierarchy:
        # 原子粒度下，有子项的父项不再展开（避免聚合+子项重复），只保留没有子项的叶节点
        parent_ids = {q.get("parent_id") for q in questions if q.get("parent_id")}
        leaves = [q for q in questions if q["source_question_id"] not in parent_ids]
        return leaves
    if g == "top_level":
        # 顶层父项聚合其全部子项内容（题干/知识点并集），保证计划输入覆盖全部子项主题。
        return [_aggregate_unit_content(questions, q) for q in questions if not q.get("parent_id")]
    # 默认：存在层级则用原子叶子全集，否则原样返回
    if g == "atomic":
        return questions
    return questions


def scope_cover_summary(
    source_scope: dict[str, Any] | None,
    selected_units: list[dict[str, Any]],
    exercise_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    生成「已选来源 × 计划题目」的覆盖摘要（父项聚合感知，修复 P0-1 假阳性）。

    规则：
    - 每个已选单元被计划引用一次视为覆盖；
    - 若已选单元是含子项的顶层父项，则必须已聚合其全部子项内容（知识点并集覆盖），
      否则即使被引用也判定为“未完成覆盖”（防止“父项只保留第 1 个子项却 complete=true”）。
    - complete 仅在全部已选单元被引用且父项聚合完整时为 true。
    """
    scope = source_scope if isinstance(source_scope, dict) else {}
    all_questions = [q for q in (scope.get("questions") or []) if isinstance(q, dict)]
    selected_map: dict[str, dict[str, Any]] = {}
    for q in selected_units or []:
        sid = _clean(q.get("source_question_id"), 80)
        if sid:
            selected_map[sid] = q
    plan_ids = set()
    for item in (exercise_plan or []):
        if isinstance(item, dict):
            for pid in _unique_strings(item.get("source_refs") or [item.get("source_question_id")], limit=3, item_limit=80):
                plan_ids.add(pid)
    per_unit: dict[str, int] = {}
    covered_map: dict[str, bool] = {}
    degraded: list[str] = []
    sole_unit = selected_map[next(iter(selected_map))]["source_question_id"] if len(selected_map) == 1 else None
    for sid, unit in selected_map.items():
        count = 0
        for p in exercise_plan or []:
            if not isinstance(p, dict):
                continue
            refs = _unique_strings(p.get("source_refs") or [p.get("source_question_id")], limit=3, item_limit=80)
            if sid in refs or (not refs and sole_unit == sid):
                count += 1
        per_unit[sid] = count
        children = _unit_children(all_questions, sid)
        if children:
            # 父项必须聚合其全部子项知识点：任何子项知识点未出现在父项知识点并集中，视为退化
            parent_kps = {_clean(k, 200) for k in (unit.get("knowledge_points") or []) if _clean(k, 200)}
            for c in children:
                ckps = {_clean(k, 200) for k in (c.get("knowledge_points") or []) if _clean(k, 200)}
                missing = ckps - parent_kps
                if missing:
                    degraded.append(sid)
                    break
            covered_map[sid] = count > 0 and sid not in degraded
        else:
            covered_map[sid] = count > 0
    covered_units = sum(1 for v in covered_map.values() if v)
    total_selected = len(selected_map)
    uncovered_units = sum(1 for v in covered_map.values() if not v)
    return {
        "granularity": _clean(scope.get("granularity"), 20) or "atomic",
        "per_unit": per_unit,
        "counts": {
            "selected_units": total_selected,
            "covered_units": covered_units,
            "uncovered_units": uncovered_units,
            "planned_exercises": len(exercise_plan or []),
            "degraded_parents": degraded,
        },
        "complete": total_selected > 0 and covered_units == total_selected,
    }


def _source_type(value: Any) -> str:
    text = _clean(value, 100)
    for allowed in ALLOWED_TYPES:
        if allowed in text:
            return allowed
    if "证明" in text:
        return "综合题"
    return "综合题"


def _strategy_plan(
    payload: dict[str, Any],
    *,
    selected_source_questions: list[dict[str, Any]],
    selected_types: list[str],
) -> tuple[str, int, list[str], list[str]]:
    if not selected_source_questions:
        count = max(1, min(8, int(payload.get("count") or 5)))
        return "single", count, _type_plan(selected_types, count), []

    strategy = _clean(payload.get("generation_strategy"), 40)
    if strategy not in {"targeted_set", "parallel_exam", "per_question"}:
        raise ValueError("请选择整套专项补强、平行试卷或逐题变式。")

    if strategy == "targeted_set":
        count = max(1, min(20, int(payload.get("strategy_count") or 10)))
        return strategy, count, _type_plan(selected_types, count), []

    if strategy == "parallel_exam":
        questions = selected_source_questions[:30]
        types = [_source_type(item.get("question_type")) for item in questions]
        source_ids = [_clean(item.get("source_question_id"), 80) for item in questions]
        return strategy, len(questions), types, source_ids

    variants = max(1, min(3, int(payload.get("variants_per_question") or 1)))
    rows = [
        (item, copy_index)
        for item in selected_source_questions
        for copy_index in range(variants)
    ][:30]
    if selected_types:
        types = _type_plan(selected_types, len(rows))
    else:
        types = [_source_type(item.get("question_type")) for item, _ in rows]
    source_ids = [_clean(item.get("source_question_id"), 80) for item, _ in rows]
    return strategy, len(rows), types, source_ids


def analyze_practice_source(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze source material only; configuration and blueprint come later."""
    ensure_practice_generation_active(payload)
    sources = parse_practice_sources(payload)
    # Text-first DOCX parsing deliberately keeps embedded media out of
    # ``images`` so plain documents do not force vision. Source analysis is
    # different: it must still see bounded reference media or it cannot tell
    # which extracted questions depend on a diagram, chart, or micrograph.
    analysis_images = sources.get("reference_images") or sources["images"]
    source_mode = _clean(payload.get("source_mode"), 30)
    is_knowledge_mode = source_mode == "knowledge"
    knowledge_title = _clean(payload.get("knowledge_title"), 300)
    provider, model = _model_runtime(payload, bool(analysis_images))
    item_label = "知识单元" if is_knowledge_mode else "原题"

    def analysis_chunks() -> list[tuple[str, list[str]]]:
        text = str(sources["text"] or "").strip()
        if len(text) <= 18000 and len(analysis_images) <= 8:
            return [(text, list(analysis_images))]
        paragraphs: list[str] = []
        for paragraph in text.split("\n\n"):
            paragraph = paragraph.strip()
            if len(paragraph) <= 11000:
                if paragraph:
                    paragraphs.append(paragraph)
                continue
            lines = paragraph.splitlines()
            current = ""
            for line in lines:
                if current and len(current) + len(line) + 1 > 11000:
                    paragraphs.append(current)
                    current = line
                else:
                    current = f"{current}\n{line}".strip()
            if current:
                paragraphs.append(current)
        chunks: list[tuple[str, list[str]]] = []
        current_parts: list[str] = []
        current_refs: set[int] = set()
        current_length = 0
        for paragraph in paragraphs:
            paragraph_refs = {
                int(value)
                for value in re.findall(r"⟦IMAGE_REF:(\d+);", paragraph)
                if 1 <= int(value) <= len(analysis_images)
            }
            if current_parts and (
                current_length + len(paragraph) > 11000
                or len(current_refs | paragraph_refs) > 8
            ):
                ordered = sorted(current_refs)
                chunks.append(("\n\n".join(current_parts), [analysis_images[index - 1] for index in ordered]))
                current_parts = []
                current_refs = set()
                current_length = 0
            current_parts.append(paragraph)
            current_refs.update(paragraph_refs)
            current_length += len(paragraph) + 2
        if current_parts:
            ordered = sorted(current_refs)
            chunks.append(("\n\n".join(current_parts), [analysis_images[index - 1] for index in ordered[:8]]))
        return chunks or [(text, list(analysis_images[:8]))]

    chunks = analysis_chunks()

    def build_task(material: str, chunk_index: int) -> str:
        chunk_note = (
            f"这是长材料的第 {chunk_index}/{len(chunks)} 段；只分析本段实际出现的内容，系统会合并各段结果。"
            if len(chunks) > 1
            else ""
        )
        return f"""# 任务

只分析用户提交的{'知识材料' if is_knowledge_mode else '题目材料'}，识别范围与考查内容。不要设计蓝图，不要生成新题。

{chunk_note}

## 材料

{material or '请读取附带的图片、PDF 或 Word 内容。'}

## 规则

- 将材料拆成可独立选择的{item_label}；即使只有一项，也必须在 questions 中返回一项
- source_scope.mode：多项返回 question_set，单项返回 single
- questions 中每项给出稳定 ID、编号、短标题、摘要、类型和实际涉及的全部核心知识点；不得为了简化只保留一个知识点
- questions 中每项给出 source_content，保留该项可供正式生题参考的完整原文、公式和表格内容；不要用 stem_excerpt 替代
- questions 中每项还要给出与该项知识点对应的 required_constraints，分别列出必要定义、公式/参数关系、适用边界；不得只把这些约束放在全局 source_analysis
- 知识材料按章节、概念或能力单元拆分，不要伪装成真题
- source_analysis 总结学科、整体难度、知识点、能力点、常见错误和不确定处
- 只输出合法 JSON

## 输出

{{
  "source_scope": {{"mode": "single/question_set", "title": "材料标题", "questions": [{{
    "source_question_id": "source_01", "number": "1", "title": "短标题",
    "stem_excerpt": "可辨认的内容摘要", "source_content": "该原题/知识单元的完整可用原文、公式和表格内容", "question_type": "题型或知识单元类型",
    "source_difficulty": "基础/进阶/挑战；无法判断时填空字符串",
    "knowledge_points": ["知识点"],
    "required_constraints": {{"essential_definitions": ["必要定义"], "essential_formulas": ["必要公式或参数关系"], "applicable_boundaries": ["适用条件、边界或限制"]}}
  }}]}},
  "source_analysis": {{
    "subject": "学科", "question_type": "材料类型", "knowledge_points": ["知识点"],
    "skills": ["能力点"], "difficulty": "材料本身的认知层级判断",
    "solution_strategy": [], "essential_definitions": ["正式生题可用的必要定义"],
    "essential_formulas": ["正式生题可用的必要公式或参数关系"],
    "applicable_boundaries": ["适用条件、边界或限制"], "common_errors": ["常见错误"], "uncertainties": []
  }}
}}
"""
    raw_responses: list[dict[str, Any]] = []
    for chunk_index, (chunk_text, chunk_images) in enumerate(chunks, start=1):
        raw_responses.append(_call_practice_json(
        _practice_generation_client(provider, model),
            [
                {"role": "system", "content": "你是研究生教育内容分析专家。只做材料结构与范围识别，只输出合法 JSON 对象。"},
                {"role": "user", "content": _user_content(build_task(chunk_text, chunk_index), chunk_images)},
            ],
            model=model,
            temperature=0.1,
            thinking=_clean(payload.get("thinking"), 20) or None,
            ensure_active=lambda: ensure_practice_generation_active(payload),
        ))
        job_id = _clean(payload.get("_job_id"), 100)
        if job_id and len(chunks) > 1:
            try:
                from .practice_jobs import update_practice_job
                update_practice_job(
                    job_id,
                    expected_status="running",
                    progress_current=chunk_index,
                    progress_total=len(chunks),
                    progress_message=f"长材料分段分析：已完成 {chunk_index}/{len(chunks)} 段。",
                )
            except Exception:
                pass
    if len(raw_responses) == 1:
        raw = raw_responses[0]
    else:
        merged_questions: list[dict[str, Any]] = []
        merged_analysis: dict[str, Any] = {}
        list_fields = (
            "knowledge_points", "skills", "solution_strategy", "essential_definitions",
            "essential_formulas", "applicable_boundaries", "common_errors", "uncertainties",
        )
        for response in raw_responses:
            chunk_scope = _normalize_source_scope(response.get("source_scope"))
            local_id_map: dict[str, str] = {}
            chunk_questions = [item for item in chunk_scope.get("questions", []) if isinstance(item, dict)]
            for item in chunk_questions:
                old_id = _clean(item.get("source_question_id"), 80)
                local_id_map[old_id] = f"source_{len(merged_questions) + len(local_id_map) + 1:02d}"
            for item in chunk_questions:
                old_id = _clean(item.get("source_question_id"), 80)
                parent_id = _clean(item.get("parent_id"), 80)
                merged_questions.append({
                    **item,
                    "source_question_id": local_id_map.get(old_id, f"source_{len(merged_questions) + 1:02d}"),
                    "parent_id": local_id_map.get(parent_id, "") if parent_id else "",
                })
            chunk_analysis = response.get("source_analysis") if isinstance(response.get("source_analysis"), dict) else {}
            for field in list_fields:
                merged_analysis[field] = _unique_strings(
                    [*(merged_analysis.get(field) or []), *(chunk_analysis.get(field) or [])],
                    limit=60,
                    item_limit=1200,
                )
            for field in ("subject", "question_type", "difficulty"):
                if not merged_analysis.get(field) and chunk_analysis.get(field):
                    merged_analysis[field] = chunk_analysis[field]
        raw = {
            "source_scope": {
                "mode": "question_set" if len(merged_questions) > 1 else "single",
                "title": knowledge_title or "长材料分段分析",
                "questions": merged_questions,
            },
            "source_analysis": merged_analysis,
        }
    ensure_practice_generation_active(payload)
    source_analysis = raw.get("source_analysis") if isinstance(raw.get("source_analysis"), dict) else {}
    model_scope = raw.get("source_scope") if isinstance(raw.get("source_scope"), dict) else {}
    model_scope_had_questions = any(isinstance(item, dict) for item in (model_scope.get("questions") or []))
    source_scope = _normalize_source_scope(raw.get("source_scope"))
    if not source_scope["questions"]:
        fallback_title = knowledge_title or source_scope.get("title") or ("知识材料" if is_knowledge_mode else "原题")
        source_scope = {
            "mode": "single",
            "title": fallback_title,
            "questions": [{
                "source_question_id": "source_01",
                "number": "1",
                "title": fallback_title,
                "stem_excerpt": _clean(sources["text"], 1200),
                "source_content": sources["text"],
                "question_type": _clean(source_analysis.get("question_type"), 100) or ("知识单元" if is_knowledge_mode else "综合题"),
                "knowledge_points": _string_list(source_analysis.get("knowledge_points"), limit=8),
                "required_constraints": _required_constraints_for_refs([], [], source_analysis),
            }],
        }
    constraint_repair_attempted = False
    constraint_repair_error = ""
    missing_constraint_ids = [
        _clean(item.get("source_question_id"), 80)
        for item in source_scope["questions"]
        if not any(_required_constraints_for_refs([], [], item).values())
    ] if model_scope_had_questions else []
    if missing_constraint_ids:
        constraint_repair_attempted = True
        repair_catalog = [
            {
                "source_question_id": _clean(item.get("source_question_id"), 80),
                "title": _clean(item.get("title"), 300),
                "source_content": _clean(item.get("source_content"), 18000),
                "knowledge_points": _string_list(item.get("knowledge_points"), limit=60),
            }
            for item in source_scope["questions"]
            if _clean(item.get("source_question_id"), 80) in missing_constraint_ids
        ]
        repair_task = f"""# 任务

上一轮材料分析已识别题目，但以下题目的逐题生成约束为空。只补齐这些题目的约束，不要修改题目、编号、知识点或其它题目。

## 待补题目

{json.dumps(repair_catalog, ensure_ascii=False, indent=2)}

## 全局分析（仅供理解学科，不得把其它题目的约束混入当前题）

{json.dumps(source_analysis, ensure_ascii=False, indent=2)}

## 规则

- 每项只填写该 source_question_id 自身生题时必须保持的定义、公式/参数关系和适用边界
- 不需要公式或特殊边界时对应数组可以为空，但三类约束不能全部为空
- 不得把整份材料的全局约束复制给每一道题
- 只输出合法 JSON

## 输出

{{"constraints": [{{"source_question_id": "source_01", "required_constraints": {{"essential_definitions": [], "essential_formulas": [], "applicable_boundaries": []}}}}]}}
"""
        try:
            repair_provider, repair_model = _model_runtime(payload, False)
            repaired_raw = _call_practice_json(
                _practice_generation_client(repair_provider, repair_model),
                [
                    {"role": "system", "content": "你是研究生教育内容约束审查专家。只补逐题约束，只输出合法 JSON。"},
                    {"role": "user", "content": repair_task},
                ],
                model=repair_model,
                temperature=0.1,
                thinking=_clean(payload.get("thinking"), 20) or None,
                ensure_active=lambda: ensure_practice_generation_active(payload),
            )
            repaired_by_id = {
                _clean(item.get("source_question_id"), 80): _required_constraints_for_refs(
                    [], [], item.get("required_constraints")
                )
                for item in (repaired_raw.get("constraints") or [])
                if isinstance(item, dict) and _clean(item.get("source_question_id"), 80)
            }
            for item in source_scope["questions"]:
                source_id = _clean(item.get("source_question_id"), 80)
                repaired_constraints = repaired_by_id.get(source_id)
                if repaired_constraints and any(repaired_constraints.values()):
                    item["required_constraints"] = repaired_constraints
        except PracticeGenerationStopped:
            raise
        except Exception as exc:
            constraint_repair_error = _generation_error_detail(exc)["message"]
    remaining_constraint_ids = [
        _clean(item.get("source_question_id"), 80)
        for item in source_scope["questions"]
        if not any(_required_constraints_for_refs([], [], item).values())
    ] if model_scope_had_questions else []
    for item in source_scope["questions"]:
        item["constraint_status"] = (
            "incomplete"
            if _clean(item.get("source_question_id"), 80) in remaining_constraint_ids
            else "complete"
        )
    if remaining_constraint_ids:
        source_analysis["uncertainties"] = _unique_strings(
            [
                *(source_analysis.get("uncertainties") or []),
                "以下题目的逐题生成约束不完整，确认前不得进入生题：" + "、".join(remaining_constraint_ids),
            ],
            limit=30,
            item_limit=1000,
        )
    return {
        "schema_version": "answer_book.practice_scope.v2",
        "requires_scope_confirmation": True,
        "source_mode": "knowledge" if is_knowledge_mode else "exam",
        "knowledge_title": knowledge_title,
        "source_scope": source_scope,
        "source_analysis": source_analysis,
        "source_files": sources["file_names"],
        "source_file_diagnostics": sources.get("file_diagnostics", []),
        "source_constraint_gate": {
            "status": "blocked" if remaining_constraint_ids else "passed",
            "repair_attempted": constraint_repair_attempted,
            "incomplete_source_question_ids": remaining_constraint_ids,
            "repair_error": constraint_repair_error,
        },
        "include_source_content_in_generation": _include_source_content_in_generation(payload),
        "generation": {
            "provider": provider.name,
            "model": model,
            "stage": "source_analysis",
            "input_mode": "mixed" if sources["text"] and analysis_images else ("vision" if analysis_images else "text"),
            "reference_image_count": len(analysis_images),
            "analysis_chunk_count": len(chunks),
            "chunked_analysis": len(chunks) > 1,
            "model_route": _model_route(payload, bool(analysis_images), provider, model),
        },
    }


def plan_practice_set(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_practice_generation_active(payload)
    sources = parse_practice_sources(payload)
    source_mode = _clean(payload.get("source_mode"), 30)
    is_knowledge_mode = source_mode == "knowledge"
    include_source_content = _include_source_content_in_generation(payload)
    knowledge_title = _clean(payload.get("knowledge_title"), 300)
    difficulty = _clean(payload.get("difficulty"), 100) or "基础到进阶"
    focus = _clean(payload.get("focus"), 1000) or (
        "围绕知识点形成概念辨析、原理理解、计算应用和综合迁移的渐进式练习"
        if is_knowledge_mode
        else "围绕原题核心考点形成由浅入深的专项练习"
    )
    selected_types = [
        _clean(item, 20)
        for item in (payload.get("question_types") or [])
        if _clean(item, 20) in ALLOWED_TYPES
    ]
    selected_source_questions = [
        {
            "source_question_id": _clean(item.get("source_question_id"), 80),
            "number": _clean(item.get("number"), 50),
            "title": _clean(item.get("title"), 300),
            "stem_excerpt": _clean(item.get("stem_excerpt"), 1200),
            "source_content": _clean(item.get("source_content") or item.get("source_text"), 18000),
            "question_type": _clean(item.get("question_type"), 100),
            "source_difficulty": _clean(item.get("source_difficulty") or item.get("difficulty"), 20),
            "knowledge_points": _string_list(item.get("knowledge_points"), limit=60),
            "required_constraints": _required_constraints_for_refs([], [], item),
            "constraint_status": _clean(item.get("constraint_status"), 20),
        }
        for item in (payload.get("selected_source_questions") or [])
        if isinstance(item, dict) and _clean(item.get("source_question_id"), 80)
    ]
    incomplete_selected_ids = [
        item["source_question_id"]
        for item in selected_source_questions
        if item.get("constraint_status") == "incomplete"
    ]
    if incomplete_selected_ids:
        raise ValueError(
            "所选题目的逐题生成约束尚未补齐，不能进入蓝图设计："
            + "、".join(incomplete_selected_ids)
        )
    if is_knowledge_mode:
        strategy = _clean(payload.get("generation_strategy"), 40)
        if strategy in {"knowledge_item_wise", "per_question"} and selected_source_questions:
            variants = max(1, min(3, int(payload.get("variants_per_question") or 1)))
            rows = [(item, copy_index) for item in selected_source_questions for copy_index in range(variants)][:30]
            count = len(rows)
            generation_strategy = "knowledge_item_wise"
            planned_types = _type_plan(selected_types, count)
            planned_source_ids = [_clean(item.get("source_question_id"), 80) for item, _ in rows]
        elif strategy == "knowledge_overall" or isinstance(payload.get("source_scope"), dict):
            count = max(1, min(20, int(payload.get("strategy_count") or payload.get("count") or 5)))
            generation_strategy = "knowledge_overall"
            planned_types = _type_plan(selected_types, count)
            planned_source_ids = []
        else:
            count = max(1, min(20, int(payload.get("count") or 5)))
            generation_strategy = "knowledge_targeted"
            planned_types = _type_plan(selected_types, count)
            planned_source_ids = []
    else:
        generation_strategy, count, planned_types, planned_source_ids = _strategy_plan(
            payload,
            selected_source_questions=selected_source_questions,
            selected_types=selected_types,
        )
    difficulty_counts = normalize_difficulty_counts(payload, count)
    planned_difficulties = _difficulty_slots(difficulty_counts)
    if not isinstance(payload.get("difficulty_counts"), dict) and difficulty == "同等难度":
        planned_difficulties = _source_difficulty_plan(selected_source_questions, count) or planned_difficulties
    prior_source_scope = _normalize_source_scope(payload.get("source_scope"))
    confirmed_analysis = payload.get("source_analysis") if isinstance(payload.get("source_analysis"), dict) else {}
    # Small plans are faster and more coherent as one call. Larger plans use
    # one compact global allocation followed by bounded, concurrent detail
    # calls owned by source/knowledge units. The opt-out keeps rollback simple
    # for integrations that need the legacy one-call behavior.
    adaptive_blueprint = (
        count > 5
        and payload.get("adaptive_blueprint") is not False
        and (is_knowledge_mode or bool(selected_source_questions) or bool(confirmed_analysis))
    )
    if len(selected_source_questions) == 1 and not selected_source_questions[0].get("source_content"):
        selected_source_questions[0]["source_content"] = sources["text"]
    planning_units = selected_source_questions or [item for item in (prior_source_scope.get("questions") or []) if isinstance(item, dict)]
    prompt_source_catalog = [
        {
            "source_ref": f"S{index}",
            "number": _clean(item.get("number"), 50),
            "title": _clean(item.get("title"), 300),
            "source_excerpt": _clean(item.get("stem_excerpt") or item.get("excerpt"), 1200),
            "source_content": _clean(item.get("source_content") or item.get("source_text"), 18000),
            "question_type": _clean(item.get("question_type"), 100),
            "source_difficulty": _clean(item.get("source_difficulty") or item.get("difficulty"), 20),
            "knowledge_points": _string_list(item.get("knowledge_points"), limit=20),
            "required_constraints": _required_constraints_for_refs([], [], item),
        }
        for index, item in enumerate(planning_units, start=1)
    ]
    prompt_catalog_reference = (
        f"来源目录中的 {'、'.join(str(item.get('source_ref') or '') for item in prompt_source_catalog if item.get('source_ref'))}（共 {len(prompt_source_catalog)} 项）"
        if prompt_source_catalog
        else "整份材料"
    )
    catalog_id_to_alias = {
        _clean(item.get("source_question_id"), 80): f"S{index}"
        for index, item in enumerate(planning_units, start=1)
        if _clean(item.get("source_question_id"), 80)
    }
    prompt_planned_source_refs = [catalog_id_to_alias.get(source_id, "") for source_id in planned_source_ids]
    strategy_requirement = _strategy_prompt_requirement(
        generation_strategy,
        knowledge_mode=is_knowledge_mode,
        source_count=len(prompt_source_catalog),
        exercise_count=count,
    )
    underprovisioned_comprehensive = (
        generation_strategy in COMPREHENSIVE_STRATEGIES
        and len(prompt_source_catalog) > 0
        and count < len(prompt_source_catalog)
    )
    if generation_strategy == "knowledge_item_wise":
        required_knowledge_points_requirement = (
            "同一知识来源只分配一题时，本题完整保留该来源的知识点组合；"
            "同一来源分配多题时，每题只列出题干将实际考查的相关子集，且同源整组题合计覆盖全部确认知识点；"
            "required_constraints 同样只保留与本题子主题相关的定义、公式和边界"
        )
    else:
        required_knowledge_points_requirement = (
            "逐项模式完整保留所绑定来源的知识点组合；综合模式按 source_refs 合理分配或组合，"
            "题量少于已选来源数时优先覆盖核心知识点，不得声称已覆盖全部范围"
            if underprovisioned_comprehensive
            else "逐项模式完整保留所绑定来源的知识点组合；综合模式按 source_refs 合理分配或组合，并在整套蓝图中覆盖所有已选知识点"
        )
    compact_material = "" if confirmed_analysis and prompt_source_catalog else sources["text"]
    # A confirmed source snapshot is already text/JSON. Do not let the mere
    # presence of the original attachment replace the user's selected model.
    # Vision fallback remains available only when this call itself still has
    # to decode an unanalysed image.
    has_confirmed_source_snapshot = bool(
        confirmed_analysis or prior_source_scope.get("questions") or selected_source_questions
    )
    planning_images = [] if has_confirmed_source_snapshot else (sources.get("reference_images") or sources["images"])
    provider, model = (
        _primary_model_runtime(payload)
        if has_confirmed_source_snapshot
        else _model_runtime(payload, bool(planning_images))
    )
    blueprint_item_contract = {
        "number": 1,
        "question_type": "题型",
        "difficulty": "基础/进阶/挑战",
        "target_skill": "能力",
        "variation_type": "变式类型",
        "source_refs": ["仅使用上方目录中的临时来源序号 S1、S2，最多 3 个"],
        "coverage_role": "变式/铺垫/连接/综合/迁移",
        "required_knowledge_points": ["本题正式生成时必须完整考查的知识点组合"],
        "stem_figure_required": False,
        "figure_design": {
            "kind": "line/bar/scatter/diagram；仅 stem_figure_required=true 时填写",
            "required_elements": ["题干配图必须出现的元素"],
            "relationship_constraints": ["题干配图必须保持的关系"],
            "question_dependency": "学生需要从题干配图读取的信息",
        },
    }
    if not adaptive_blueprint:
        blueprint_item_contract.update({
            "difficulty_levers": ["选择一种主要难度方向，必要时再选一种辅助方向；不全部堆叠"],
            "difficulty_rationale": "说明预期学生的主要认知瓶颈，不使用固定步数或纯计算量规则",
            "structural_change": "改变未知量/改变求解路径/增加边界条件/逆向求解/比较与优化/跨情境迁移/增加多步约束/改变子问结构",
            "design_intent": "本题为何这样设计",
            "required_constraints": {"essential_definitions": ["本题允许使用的必要定义"], "essential_formulas": ["本题允许使用的公式或参数关系"], "applicable_boundaries": ["本题适用条件、边界或限制"]},
        })
    blueprint_contract = {
        "training_goal": "明确训练目标",
        "progression": ["逐题梯度"],
        "design_notes": ["设计原则"],
        "exercise_plan": [blueprint_item_contract],
    }
    contract = {
        "source_scope": {
            "mode": "single/question_set",
            "title": "试卷或题目标题",
            "questions": [
                {
                    "source_question_id": "source_01",
                    "number": "原题编号",
                    "title": "便于选择的短标题",
                    "stem_excerpt": "足以区分题目的题干摘要",
                    "source_content": "该原题完整可用文本、公式和表格内容",
                    "question_type": "原题题型",
                    "knowledge_points": ["知识点"],
                    "required_constraints": {"essential_definitions": ["必要定义"], "essential_formulas": ["必要公式或参数关系"], "applicable_boundaries": ["适用条件、边界或限制"]},
                }
            ],
        },
        "source_analysis": {
            "subject": "自动识别学科",
            "question_type": "原题题型",
            "knowledge_points": ["知识点"],
            "skills": ["能力点"],
            "difficulty": "研究生层级难度判断",
            "solution_strategy": ["正确解题路径"],
            "essential_definitions": ["正式生题可用的必要定义"],
            "essential_formulas": ["正式生题可用的必要公式或参数关系"],
            "applicable_boundaries": ["适用条件、边界或限制"],
            "common_errors": ["常见错误"],
            "uncertainties": [],
        },
        "blueprint": blueprint_contract,
    }
    output_contract = {"blueprint": blueprint_contract} if confirmed_analysis and prior_source_scope.get("questions") else contract
    if is_knowledge_mode:
        task = f"""# 任务

根据用户提供的知识点名称、简介、教材原文、图片或文档，先建立准确的知识范围，再设计研究生层级的针对性模拟题蓝图。本阶段不要生成具体题目。

## 知识点名称

{knowledge_title or "请从材料中识别"}

## 知识材料

{compact_material or "已使用下方压缩来源目录；无需重复整份材料。"}

## 压缩来源目录（临时序号只用于本次规划）

{json.dumps(prompt_source_catalog, ensure_ascii=False) if prompt_source_catalog else "尚未形成，需先从材料识别"}

## 出题要求

{focus}

## 参数

- 题量：{count}
- 生成方式：{generation_strategy}
- {"本题难度" if count == 1 else "整套题难度分布"}：{difficulty}
- 程序指定的逐题难度：{"、".join(planned_difficulties)}
- 程序指定的逐题题型：{"、".join(planned_types)}
- 用户确认的知识范围：{prompt_catalog_reference}
- 已确认的材料分析：{json.dumps(confirmed_analysis, ensure_ascii=False) if confirmed_analysis else "尚未形成"}

## 约束

- 材料可能只是一个简短知识点名称，也可能是含文字、公式、表格和配图的教材原文
- 不得把知识材料误判成真题，不需要寻找或复刻原题结构
- 当前生成策略：{strategy_requirement}
- 每个蓝图项都必须输出 required_knowledge_points：{required_knowledge_points_requirement}
- 每个蓝图项输出 difficulty_levers 和 difficulty_rationale，但它们是设计方向而非硬模板：自主选择一种最合适的主要机制，最多再加一种辅助机制；基础优先直接条件或支架，进阶优先条件转换、方法选择或知识组合，挑战优先真实的迁移、判断、逆向、评价、建模或纠错瓶颈。不得全部堆叠、不得用固定步数或纯计算量代替难度。
- 已有材料分析时直接作为事实使用，不要重新分析或改写；尚无分析时才补充 source_analysis
- exercise_plan 必须恰好 {count} 项，并严格使用程序指定的逐题题型和逐题难度
- 仅当学生必须读取题干所附图片才能作答时，stem_figure_required 才为 true 并填写 figure_design；“作图题”要求学生绘图，不自动代表题干需要配图
- 题目应覆盖概念辨析、原理理解、公式或参数应用、图表理解和综合迁移中适合该知识点的层次
- {"本次只做全局槽位分配：先给出每题的来源、目标能力、变式方式和覆盖角色；系统随后会按知识点/来源分组并发补全设计意图与难度细节。" if adaptive_blueprint else "每题说明目标能力、出题方式和设计意图；不得只是对教材原句做机械填空"}
- 只输出合法 JSON，不输出具体题干

## 输出结构

{json.dumps(output_contract, ensure_ascii=False, indent=2)}
"""
    else:
        task = f"""# 任务

只解析原题并设计研究生专项练习蓝图，本阶段不要生成具体题目。

## 原题材料

{compact_material or "已使用下方压缩来源目录；无需重复整份材料。"}

## 压缩来源目录（临时序号只用于本次规划）

{json.dumps(prompt_source_catalog, ensure_ascii=False) if prompt_source_catalog else "尚未形成，需先从材料识别"}

## 专项要求

{focus}

## 参数

- 题量：{count}
- 难度梯度：{difficulty}
- 生成策略：{generation_strategy}
- 程序指定的逐题题型：{"、".join(planned_types)}
- 程序指定的逐题临时来源：{"、".join(prompt_planned_source_refs) if any(prompt_planned_source_refs) else "由蓝图按整套试卷考点分布合理分配"}
- 用户已选择的原题：{prompt_catalog_reference if prompt_source_catalog else "尚未选择"}
- 已确认的原题分析：{json.dumps(confirmed_analysis, ensure_ascii=False) if confirmed_analysis else "尚未形成"}

## 约束

- 全部内容保持研究生层级
- 已有确认范围和分析时直接作为事实使用，不得重新拆题或改变用户确认结果
- 仅在尚无确认范围时，判断材料是一道独立题还是题目集
- 如果是题目集且用户尚未选择原题：完整列出可辨认的原题，source_scope.mode 返回 question_set，exercise_plan 必须为空，不得把整套试卷混成一个训练目标
- 如果用户已经选择原题：只围绕选中的原题设计蓝图，exercise_plan 每项用 source_refs 的临时序号标明来源
- 每个蓝图项都必须输出 required_knowledge_points：parallel_exam 和 per_question 完整保留所绑定原题的全部核心知识点组合；{required_knowledge_points_requirement}
- required_constraints 是来源允许的硬边界。变式可以增加更具体的条件，但不得把适用边界替换成来源未覆盖的过程、理论或状态，也不得引入缺少必要参数而无法唯一作答的新任务。
- 用户专项要求中的“保留读图、原图依赖、作图条件、公式或表格”等要求属于硬约束；不能为了降低生成难度改成纯文字题。
- 每个蓝图项输出 difficulty_levers 和 difficulty_rationale，但只选择一种最合适的主要机制，最多再加一种辅助机制；基础优先直接条件或支架，进阶优先条件转换、方法选择或知识组合，挑战优先迁移、边界判断、逆向、评价、建模或纠错。这些是设计方向而非检查表，不得全部堆叠，也不得靠删除必考知识点或纯增加计算量改变难度。
- 对参考计算题生成计算题时，structural_change 必须选择一种实质结构变化（改变未知量、求解路径、边界条件、子问结构、逆向求解、比较优化或跨情境迁移）；不得只写“换数字”“改参数”“平行换数据”。
- 当前生成策略：{strategy_requirement}
- 如果确实只有一道题：source_scope.mode 返回 single，并直接设计蓝图
- 只有单题或用户已经选择原题时，exercise_plan 才必须恰好 {count} 项，并严格使用上面的逐题题型
- 仅当学生必须读取题干所附图片才能作答时，stem_figure_required 才为 true 并填写 figure_design；“作图题”要求学生绘图，不自动代表题干需要配图
- {"本次只做全局槽位分配：先给出每题的来源、目标能力、变式方式和覆盖角色；系统随后会按知识点/原题分组并发补全设计意图与难度细节。" if adaptive_blueprint else "每题说明目标能力、变式方式和设计意图"}
- variation_type 与 structural_change 必须具体说明考查方式或解题结构如何变化，不能把数字变化当作唯一变化。
- 只输出合法 JSON，不输出具体题干

## 输出结构

{json.dumps(output_contract, ensure_ascii=False, indent=2)}
"""
    messages = [
        {
            "role": "system",
            "content": (
                (
                    "你是研究生教育教研专家。准确梳理知识边界并制定可审查的模拟题蓝图。"
                    if is_knowledge_mode
                    else "你是研究生教育教研专家。先准确识别原题，再制定可审查的训练蓝图。"
                )
                + "不要在本阶段生成练习题正文。只输出一个合法 JSON 对象。"
            ),
        },
        {"role": "user", "content": _user_content(task, planning_images)},
    ]
    raw = _call_practice_json(
        _practice_generation_client(provider, model),
        messages,
        model=model,
        temperature=0.2,
        thinking=_clean(payload.get("thinking"), 20) or None,
        ensure_active=lambda: ensure_practice_generation_active(payload),
    )
    ensure_practice_generation_active(payload)
    if confirmed_analysis:
        raw["source_analysis"] = confirmed_analysis
    if prior_source_scope.get("questions"):
        raw["source_scope"] = prior_source_scope
    source_scope = _normalize_source_scope(raw.get("source_scope"))
    if is_knowledge_mode:
        if not isinstance(payload.get("source_scope"), dict):
            source_scope = {
                "mode": "single",
                "title": knowledge_title or source_scope.get("title") or "知识点模拟题",
                "questions": [],
            }
        elif not source_scope.get("title"):
            source_scope["title"] = knowledge_title or "知识点模拟题"
    if prior_source_scope.get("mode") == "question_set" and selected_source_questions:
        source_scope = prior_source_scope
    if not is_knowledge_mode and source_scope["mode"] == "question_set" and not selected_source_questions:
        return {
            "schema_version": "answer_book.practice_source_selection.v1",
            "requires_source_selection": True,
            "source_scope": source_scope,
            "source_analysis": raw.get("source_analysis") if isinstance(raw.get("source_analysis"), dict) else {},
            "source_files": sources["file_names"],
            "source_file_diagnostics": sources.get("file_diagnostics", []),
            "generation": {
                "provider": provider.name,
                "model": model,
                "stage": "source_detection",
                "model_route": _model_route(payload, bool(planning_images), provider, model),
            },
        }
    plan = _normalize_plan(
        raw,
        count=count,
        planned_types=planned_types,
        difficulty=difficulty,
        planned_difficulties=planned_difficulties,
        selected_types=selected_types,
        source_files=sources["file_names"],
        source_scope=source_scope,
        selected_source_questions=selected_source_questions,
        planned_source_ids=planned_source_ids,
        generation_strategy=generation_strategy,
        include_source_content_in_generation=include_source_content,
    )
    plan["blueprint"]["exercise_plan"] = _ensure_selected_source_coverage(
        plan["blueprint"].get("exercise_plan") or [],
        selected_source_questions,
    )
    plan = ensure_practice_blueprint_defaults(plan)
    blueprint_refinement = None
    if adaptive_blueprint:
        blueprint_refinement = refine_blueprint_units(plan, payload)
        plan = ensure_practice_blueprint_defaults(plan)
        plan["blueprint_generation_units"] = blueprint_refinement.get("units") or []
        plan["blueprint_refinement"] = blueprint_refinement
    plan["scope_cover"] = scope_cover_summary(
        source_scope,
        selected_source_questions,
        plan["blueprint"]["exercise_plan"],
    )
    plan["mode_contract"] = validate_practice_mode_contract(plan)
    plan["source_mode"] = "knowledge" if is_knowledge_mode else "exam"
    plan["include_source_content_in_generation"] = include_source_content
    plan["blueprint"]["include_source_content_in_generation"] = include_source_content
    if plan["mode_contract"].get("errors"):
        _raise_plan_gate_error(
            "生成蓝图未满足模式约束：" + "；".join(plan["mode_contract"]["errors"]),
            plan,
            "mode_contract",
        )
    plan["blueprint_audit"] = audit_practice_blueprint(plan)
    blueprint_audit_repair = None
    has_repairable_findings = any(
        isinstance(finding, dict) and finding.get("code") == "cross_source_design_leak"
        for finding in (plan["blueprint_audit"].get("findings") or [])
    )
    if plan["blueprint_audit"]["status"] == "blocked" and adaptive_blueprint and has_repairable_findings:
        blueprint_audit_repair = repair_blueprint_audit_findings(
            plan,
            payload,
            plan["blueprint_audit"],
        )
        plan = ensure_practice_blueprint_defaults(plan)
        plan["blueprint_audit_repair"] = blueprint_audit_repair
        if blueprint_refinement is not None:
            blueprint_refinement["audit_repair"] = blueprint_audit_repair
            blueprint_refinement["call_count"] = (
                int(blueprint_refinement.get("call_count") or 0)
                + int(blueprint_audit_repair.get("call_count") or 0)
            )
            plan["blueprint_refinement"] = blueprint_refinement
        plan["blueprint_audit"] = audit_practice_blueprint(plan)
    if plan["blueprint_audit"]["status"] == "blocked":
        _raise_plan_gate_error(
            "生成蓝图未通过确认门禁：" + "；".join(plan["blueprint_audit"]["errors"]),
            plan,
            "blueprint_audit",
        )
    plan["planning_evidence"] = {
        "default_call_count": 1 + int((blueprint_refinement or {}).get("call_count") or 0),
        "global_allocation_call_count": 1,
        "adaptive_blueprint": adaptive_blueprint,
        "blueprint_unit_count": int((blueprint_refinement or {}).get("unit_count") or 0),
        "blueprint_refinement_failures": (blueprint_refinement or {}).get("failures") or [],
        "blueprint_audit_repair": blueprint_audit_repair or {},
        "optional_json_repair_only_on_invalid_output": True,
        "prompt_char_count": len(task),
        "material_char_count": len(compact_material),
        "source_catalog_count": len(prompt_source_catalog),
        "uses_compact_catalog": bool(confirmed_analysis and prompt_source_catalog),
        "generation_batches_use_referenced_sources_only": True,
    }
    plan["source_mode"] = "knowledge" if is_knowledge_mode else "exam"
    plan["difficulty_counts"] = {
        level: planned_difficulties.count(level) for level in DIFFICULTY_LEVELS
    }
    plan["source_file_diagnostics"] = sources.get("file_diagnostics", [])
    if is_knowledge_mode:
        plan["knowledge_title"] = knowledge_title or source_scope.get("title") or "知识点模拟题"
    plan["generation"] = {
        "provider": provider.name,
        "model": model,
        "stage": "planning",
        "model_route": "selected_primary" if has_confirmed_source_snapshot else _model_route(payload, bool(planning_images), provider, model),
        "include_source_content_in_generation": include_source_content,
    }
    return plan


def validate_generation_plan_identity(payload: dict[str, Any], blueprint: dict[str, Any]) -> None:
    """Reject a resumed generation when its requested strategy differs from its plan.

    A generation retry must reuse the exact confirmed plan.  This check keeps a
    stale or concurrently-created plan from silently being used for a different
    acceptance scenario.
    """
    requested = _clean(payload.get("generation_strategy"), 40)
    planned = _clean(blueprint.get("generation_strategy"), 40)
    if requested and planned and requested != planned:
        raise ValueError(
            "恢复生成的 strategy 与已确认 plan 不一致；请重新选择对应 plan 后再发起。"
        )


def _selectively_repair_practice_diversity(
    practice: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Repair only deterministic set-level collisions, with a strict call cap."""
    try:
        repair_limit = max(0, min(8, int(payload.get("diversity_auto_repair_limit", 4))))
    except (TypeError, ValueError):
        repair_limit = 4
    initial_issues = [issue for issue in practice_diversity_issues(practice) if issue.get("blocking") is True]
    report: dict[str, Any] = {
        "gate": "deterministic_set_diversity",
        "initial_issue_count": len(initial_issues),
        "repair_limit": repair_limit,
        "attempts": [],
    }
    if not initial_issues or repair_limit == 0:
        report["status"] = "passed" if not initial_issues else "repair_disabled"
        report["remaining_issue_count"] = len(initial_issues)
        return report, {}

    attempted_indexes: set[int] = set()
    for issue in initial_issues:
        ensure_practice_generation_active(payload)
        if len(attempted_indexes) >= repair_limit:
            break
        try:
            index = int(issue.get("exercise_index"))
        except (TypeError, ValueError):
            continue
        if index in attempted_indexes or not (0 <= index < len(practice.get("exercises") or [])):
            continue
        attempted_indexes.add(index)
        attempt = {
            "exercise_index": index,
            "issue_code": _clean(issue.get("code"), 100),
            "peer_index": issue.get("peer_index"),
        }
        try:
            response = regenerate_practice_exercise({
                **payload,
                "practice": practice,
                "exercise_index": index,
                "instruction": (
                    f"整套差异门禁发现：{_clean(issue.get('message'), 700)}"
                    "请重新设计该题，与来源题及同来源已有题至少在情境、主要未知量、"
                    "认知操作、核心公式链中改变两项。"
                ),
            })
            ensure_practice_generation_active(payload)
            practice["exercises"][index] = response["exercise"]
            attempt["status"] = "repaired"
        except PracticeGenerationStopped:
            raise
        except Exception as exc:
            error = _generation_error_detail(exc)
            attempt.update({
                "status": "repair_request_failed",
                "error_code": error["code"],
                "error_message": error["message"],
            })
        report["attempts"].append(attempt)

    remaining = [issue for issue in practice_diversity_issues(practice) if issue.get("blocking") is True]
    unresolved_by_index: dict[int, list[dict[str, Any]]] = {}
    for issue in remaining:
        try:
            index = int(issue.get("exercise_index"))
        except (TypeError, ValueError):
            continue
        unresolved_by_index.setdefault(index, []).append(issue)
    failures: dict[str, dict[str, Any]] = {}
    for index, issues in unresolved_by_index.items():
        if not (0 <= index < len(practice.get("exercises") or [])):
            continue
        exercise = practice["exercises"][index]
        if not isinstance(exercise, dict):
            continue
        plan_item_id = _clean(exercise.get("plan_item_id"), 80) or f"plan_item_{index + 1:02d}"
        error = {
            "code": "generation_diversity_gate_failed",
            "message": "题目重设计后仍与来源题或套内其它题实质近似。",
            "retryable": True,
            "detail": "；".join(_clean(issue.get("message"), 500) for issue in issues),
        }
        exercise["generation_status"] = "failed"
        exercise["generation_error"] = error
        failures[plan_item_id] = error
    report["remaining_issue_count"] = len(remaining)
    report["status"] = "passed_after_repair" if not remaining else "partial_failure"
    return report, failures


def generate_practice_from_plan(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    plan = ensure_practice_blueprint_defaults(plan)
    include_source_content = _include_source_content_in_generation(payload, plan)
    plan["include_source_content_in_generation"] = include_source_content
    is_knowledge_mode = _clean(payload.get("source_mode") or plan.get("source_mode"), 30) == "knowledge"
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    blueprint["include_source_content_in_generation"] = include_source_content
    # Older knowledge-flow pages could submit the generic exam default
    # `targeted_set` even though the confirmed blueprint had already normalized
    # it to a knowledge strategy. The confirmed blueprint is authoritative, so
    # repair that legacy request server-side; this lets an already-open page
    # continue without a refresh or rebuilding its task.
    requested_strategy = _clean(payload.get("generation_strategy"), 40)
    planned_strategy = _clean(blueprint.get("generation_strategy"), 40)
    if is_knowledge_mode and requested_strategy == "targeted_set" and planned_strategy.startswith("knowledge_"):
        payload = {**payload, "generation_strategy": planned_strategy}
    validate_generation_plan_identity(payload, blueprint)
    plan_audit = (
        {"status": "passed", "errors": [], "warnings": [], "metrics": {"plan_count": len(blueprint.get("exercise_plan") or [])}}
        if payload.get("blueprint_review_enabled") is False
        else audit_practice_blueprint(plan)
    )
    if plan_audit["status"] == "blocked":
        raise ValueError("确认后的蓝图未通过生成门禁：" + "；".join(plan_audit["errors"]))
    reviewed_blueprint = copy.deepcopy(blueprint)
    reviewed_exercise_plan = [
        copy.deepcopy(item)
        for item in (reviewed_blueprint.get("exercise_plan") or [])
        if isinstance(item, dict)
    ]
    if not reviewed_exercise_plan:
        raise ValueError("缺少已确认的训练蓝图。")
    multi_question_config = _blueprint_multi_question_config(payload, reviewed_blueprint)
    reviewed_blueprint["multi_question"] = multi_question_config
    base_count = len(reviewed_exercise_plan)
    if isinstance(payload.get("difficulty_counts"), dict):
        expected_difficulty_counts = normalize_difficulty_counts(payload, base_count)
        actual_difficulty_counts = {
            level: sum(1 for item in reviewed_exercise_plan if item.get("difficulty") == level)
            for level in DIFFICULTY_LEVELS
        }
        if actual_difficulty_counts != expected_difficulty_counts:
            raise ValueError(
                "蓝图难度题数与范围确认不一致："
                f"确认值 {expected_difficulty_counts}，当前蓝图 {actual_difficulty_counts}。"
            )
    exercise_plan = _expand_blueprint_items_for_generation(
        reviewed_exercise_plan if multi_question_config.get("enabled") else reviewed_exercise_plan[:30],
        multi_question_config,
    )
    count = len(exercise_plan)
    blueprint = {**copy.deepcopy(reviewed_blueprint), "exercise_plan": exercise_plan}
    plan = {**plan, "blueprint": blueprint}
    planned_types = [
        _clean(item.get("question_type"), 20) if isinstance(item, dict) else "综合题"
        for item in exercise_plan[:count]
    ]
    planned_types = [item if item in ALLOWED_TYPES else "综合题" for item in planned_types]
    planned_source_ids = [
        _clean(item.get("source_question_id"), 80) if isinstance(item, dict) else ""
        for item in exercise_plan[:count]
    ]
    planned_difficulties = [
        _clean(item.get("difficulty"), 20) if isinstance(item, dict) else "进阶"
        for item in exercise_plan[:count]
    ]
    sources = (
        parse_practice_sources(payload)
        if include_source_content
        else {"text": "", "images": [], "reference_images": [], "file_names": [], "analysis_mode": "not_used_for_generation"}
    )
    if include_source_content:
        _hydrate_single_source_content(plan, sources.get("text") or "")
    reference_images = sources.get("reference_images") or sources["images"]
    provider, model = _primary_model_runtime(payload)
    generation_reference_images = (
        reference_images if _provider_model_supports_vision(provider, model) else []
    )
    job_id = _clean(payload.get("_job_id"), 100)
    resume_from_job_id = _clean(payload.get("resume_from_job_id"), 100)
    all_exercises: list[dict[str, Any]] = []
    if job_id or resume_from_job_id:
        checkpoint = load_practice_generation_checkpoint(
            payload,
            expected_plan_item_ids=[
                str(item.get("plan_item_id") or f"plan_item_{index + 1:02d}")
                for index, item in enumerate(exercise_plan[:count])
                if isinstance(item, dict)
            ],
        )
        all_exercises = [dict(item) for item in checkpoint.exercises]
    if job_id:
        try:
            from .practice_jobs import update_practice_job
            update_practice_job(
                job_id,
                partial_exercises=all_exercises,
                generated_count=len({str(item.get("plan_item_id") or "") for item in all_exercises}),
                total_count=count,
            )
        except Exception:
            pass

    # 采用中的蓝图草案：按 plan_item_id 替换对应计划项，直接进入正式结果，不再调用模型
    plan_drafts = payload.get("plan_drafts") if isinstance(payload.get("plan_drafts"), dict) else {}
    if plan_drafts:
        injected_ids = {str(item.get("plan_item_id") or "") for item in all_exercises}
        for idx, item in enumerate(exercise_plan[:count]):
            pid = str(item.get("plan_item_id") or f"plan_item_{idx + 1:02d}")
            parent_pid = _clean(item.get("parent_plan_item_id"), 80)
            draft = plan_drafts.get(pid) or (
                plan_drafts.get(parent_pid)
                if parent_pid and _nonnegative_int(item.get("variant_index")) == 1
                else None
            )
            if not isinstance(draft, dict) or pid in injected_ids:
                continue
            try:
                draft_for_normalization = {
                    **draft,
                    "plan_item_id": pid,
                    "parent_plan_item_id": parent_pid,
                    "variant_id": item.get("variant_id"),
                    "variant_index": item.get("variant_index"),
                    "variant_count": item.get("variant_count"),
                    "variant_mode": item.get("variant_mode"),
                    "variant_role": item.get("variant_role"),
                }
                normalized = normalize_practice_set(
                    {"source_analysis": plan.get("source_analysis") or {}, "blueprint": blueprint, "exercises": [draft_for_normalization]},
                    requested_count=1,
                    subject="",
                    planned_types=[_clean(item.get("question_type"), 20) if _clean(item.get("question_type"), 20) in ALLOWED_TYPES else "综合题"],
                    planned_source_ids=[_clean(item.get("source_question_id"), 80)] if _clean(item.get("source_question_id"), 80) else [],
                    planned_plan_ids=[pid],
                )
                injected = normalized["exercises"][0]
                injected["plan_item_id"] = pid
                injected["source_question_id"] = _clean(item.get("source_question_id"), 80)
                injected["number"] = idx + 1
                all_exercises.append(injected)
            except Exception:
                # 草案规范化失败则忽略，交由模型正常生成该项
                continue
    # A small batch keeps each response robust; callers can lower the parallel
    # request ceiling for providers with stricter throughput limits.
    batch_size = (
        int(multi_question_config["variants_per_item"])
        if multi_question_config.get("enabled")
        else max(1, min(5, int(payload.get("generation_batch_size") or 3)))
    )
    max_concurrency = practice_generation_concurrency(payload)

    def generate_batch(
        batch_start: int,
        batch_plan: list[dict[str, Any]],
    ) -> tuple[int, list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
        ensure_practice_generation_active(payload)
        batch_count = len(batch_plan)
        semantic_plan = _semantic_batch_plan(batch_plan, exercise_plan)
        semantic_sources = _semantic_batch_context(
            plan,
            batch_plan,
            knowledge_mode=is_knowledge_mode,
            include_source_content=include_source_content,
        )
        source_context = semantic_sources if include_source_content else _abstract_generation_context(
            plan,
            knowledge_mode=is_knowledge_mode,
        )
        material_heading = (
            "知识材料" if is_knowledge_mode else "原题语义约束"
        ) if include_source_content else "抽象知识与边界约束（不含来源原文、题面、图片和表格）"
        analysis = plan.get("source_analysis") if isinstance(plan.get("source_analysis"), dict) else {}
        common_context = {
            "subject": _clean(analysis.get("subject"), 100),
            "user_focus": _clean(payload.get("focus"), 1000),
            "generation_strategy": _clean(blueprint.get("generation_strategy"), 40),
            "blueprint_multi_question": multi_question_config,
        }
        diversity_context = _semantic_diversity_context(exercise_plan, batch_plan)
        subject_requirements = _subject_format_requirements(plan, batch_plan)
        output_format_requirements = _practice_output_format_requirements()
        figure_requirements = []
        if any(_plan_requires_stem_figure(item) for item in batch_plan):
            figure_requirements = [
                "仅 stem_figure_required=true 的题返回 figures；它是题干供学生读取的内容，不是学生应绘制的答案图。",
                "真实曲线至少提供 5 个有序数据点；水平/垂直辅助线至少提供 2 个点；结构示意图必须提供至少两个 nodes 及必要 edges，只有 description 的假图无效。",
                "图表中的 nodes 必须与 series 使用同一数据坐标并落在对应端点或交点；只有无 series 的纯示意图才使用 0 到 1 的画布坐标。",
                "figure_design.required_elements 括号中的曲线、水平、垂直和先后关系必须落实为实际几何结构，不要机械复制成长图例。",
                "题干、figure_design、figures.semantic_contract 和实际数据/节点必须表达同一组事实。",
            ]
        task = f"""# 任务

根据已经确认的研究生{"知识点模拟题" if is_knowledge_mode else "专项训练"}蓝图，生成完整练习题。

## {material_heading}

{json.dumps(source_context, ensure_ascii=False, indent=2)}

## 批次公共上下文

{json.dumps(common_context, ensure_ascii=False, indent=2)}

## 已确认的训练蓝图

{json.dumps({"training_goal": blueprint.get("training_goal"), "progression": blueprint.get("progression"), "design_notes": blueprint.get("design_notes"), "exercise_plan": semantic_plan}, ensure_ascii=False, indent=2)}

## 整套差异合同

{json.dumps(diversity_context, ensure_ascii=False, indent=2)}

## 内容要求

- 这是总任务第 {batch_start + 1} 至 {batch_start + batch_count} 题
- 必须生成恰好 {batch_count} 道题，逐题严格遵循本批 exercise_plan 的 batch_index
- batch_index 只是本次调用的临时序号；不要在题干中展示它，也不要编造或输出任何内部 ID
- 文字、术语和推导深度保持研究生层级
- 仅生成题目正文；不得输出答案、解析、解题步骤、评分依据或自我验证过程
- verification_note 仅记录题干条件充分性检查，不得包含答案、结论、推导或解题过程
- 每题独立、条件充分、可作答；严格执行 change_contract.required_difference
- diversity_signature 只用于系统去重，必须忠实概括本题，不得写入 stem，也不得伪造差异
- difficulty_intent 是难度方向和防退化边界，不是检查表；每题自主选择一种最自然的主要机制，最多再加一种辅助机制，不得把候选项全部堆叠
- difficulty_evidence 只记录实际使用的主要机制和学生瓶颈，不得为迎合难度标签伪造，也不得包含答案或推导
- {"题目必须由知识范围出发独立设计，不得假装存在一份未提供的原题" if is_knowledge_mode else "题目必须保持与已确认训练目标的一致性"}
- 每题 knowledge_points 必须与本批蓝图项的 required_knowledge_points 完整一致，且题干必须实际要求学生使用每个列出的知识点；不得只在元数据中声称覆盖、不得额外添加其它知识点
- 不得用固定推理步数、统一计算量或删除必考知识点替代难度设计
- {"只参考同一 batch_index 绑定来源的 source_content、图片和约束，不得引用本批未绑定来源。" if include_source_content else "本次已关闭来源材料参考：不得假设、复述或引用原题题面、教材原文、图片和表格；仅按本项蓝图的必考知识点、required_constraints、题型、难度和变式要求独立设计。"}
{chr(10).join(f'- {requirement}' for requirement in subject_requirements)}
{chr(10).join(f'- {requirement}' for requirement in figure_requirements)}
{chr(10).join(f'- {requirement}' for requirement in output_format_requirements)}
- 只输出合法 JSON

## 输出结构

{json.dumps(_batch_prompt_contract(batch_plan), ensure_ascii=False, indent=2)}
"""
        messages = [
            {
                "role": "system",
                "content": (
                    "你是严谨的研究生教研专家。严格执行已确认蓝图生成练习正文。"
                    "不得改变题型方案，不得降低到中学或普通本科入门层级。只输出合法 JSON。"
                ),
            },
            {"role": "user", "content": _user_content(task, generation_reference_images if include_source_content and _batch_needs_visual_reference(semantic_sources, batch_plan) else [])},
        ]

        batch_diagnostic: dict[str, Any] = {
            "batch_start": batch_start + 1,
            "batch_end": batch_start + batch_count,
            "expected_indexes": list(range(1, batch_count + 1)),
            "prompt_char_count": len(task),
            "source_context_char_count": len(json.dumps(source_context, ensure_ascii=False)),
            "control_contract_char_count": sum(len(json.dumps(value, ensure_ascii=False)) for value in (
                semantic_plan,
                diversity_context,
                _batch_prompt_contract(batch_plan),
            )),
            "response_attempts": [],
            "recovery_attempt_count": 0,
            "transport_attempts": [],
        }

        def _partition_batch_rows(raw: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
            return partition_practice_batch_rows(
                raw,
                expected_count=batch_count,
                clean_value=_clean,
            )

        def _recover_missing_batch_item(
            target_index: int,
            *,
            prior_raw: dict[str, Any],
            phase: str,
        ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
            item_contract = _exercise_output_contract_for_plan_item(batch_plan[target_index - 1])
            recovery_prompt = f"""上一批响应缺少或重复了 batch_index={target_index}。

只补生成这一项，不要返回其它 batch_index，不要修改已成功返回的题目。
严格遵循下面的单题蓝图和输出结构；仍不得输出答案、解析或内部 ID。

单题蓝图：
{json.dumps(semantic_plan[target_index - 1], ensure_ascii=False, indent=2)}

输出结构：
{json.dumps({"exercises": [{"batch_index": target_index, **item_contract}]}, ensure_ascii=False, indent=2)}
"""
            attempt_report: dict[str, Any] = {
                "phase": phase,
                "batch_index": target_index,
                "attempts": [],
            }
            for recovery_attempt in range(1, 3):
                ensure_practice_generation_active(payload)
                batch_diagnostic["recovery_attempt_count"] += 1
                try:
                    recovered_raw = _call_practice_json(
        _practice_generation_client(provider, model),
                        [
                            *messages,
                            {"role": "assistant", "content": json.dumps(prior_raw, ensure_ascii=False)},
                            {"role": "user", "content": recovery_prompt},
                        ],
                        model=model,
                        temperature=0.3 if recovery_attempt == 1 else 0.15,
                        thinking=_clean(payload.get("thinking"), 20) or None,
                        timeout_seconds=600,
                        ensure_active=lambda: ensure_practice_generation_active(payload),
                    )
                    recovered_rows, recovered_shape = _partition_batch_rows(recovered_raw)
                    attempt_report["attempts"].append({
                        "attempt": recovery_attempt,
                        "actual_indexes": recovered_shape["actual_indexes"],
                        "accepted_target": target_index in recovered_rows,
                    })
                    if target_index in recovered_rows:
                        attempt_report["status"] = "recovered"
                        return recovered_rows[target_index], attempt_report
                except PracticeGenerationStopped:
                    raise
                except Exception as exc:
                    error = _generation_error_detail(exc)
                    attempt_report["attempts"].append({
                        "attempt": recovery_attempt,
                        "error_code": error["code"],
                        "error_message": error["message"],
                    })
            attempt_report["status"] = "failed"
            return None, attempt_report

        def _complete_partial_batch(
            raw: dict[str, Any],
            *,
            phase: str,
        ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
            rows_by_index, shape = _partition_batch_rows(raw)
            response_report = {"phase": phase, **shape, "recoveries": []}
            missing_before_recovery = list(shape["missing_indexes"])
            structural_failures: dict[int, dict[str, Any]] = {}
            for target_index in missing_before_recovery:
                recovered, recovery_report = _recover_missing_batch_item(
                    target_index,
                    prior_raw=raw,
                    phase=phase,
                )
                response_report["recoveries"].append(recovery_report)
                if recovered is not None:
                    rows_by_index[target_index] = recovered
                    continue
                total_number = batch_start + target_index
                structural_failures[target_index - 1] = {
                    "code": "generation_response_invalid",
                    "message": "模型未完整返回本题，逐题补生后仍未成功。",
                    "retryable": True,
                    "detail": (
                        f"第 {batch_start + 1}-{batch_start + batch_count} 题批次期望临时序号 "
                        f"{list(range(1, batch_count + 1))}，首次实际返回 {shape['actual_indexes']}；"
                        f"第 {total_number} 题对应的临时序号 {target_index} 经 2 次独立补生仍未返回。"
                    ),
                }
            response_report["final_indexes"] = sorted(rows_by_index)
            response_report["failed_indexes"] = [index + 1 for index in sorted(structural_failures)]
            batch_diagnostic["response_attempts"].append(response_report)
            return [rows_by_index[index] for index in sorted(rows_by_index)], structural_failures

        try:
            ensure_practice_generation_active(payload)
            raw_batch = _call_practice_json_with_transport_retry(
        _practice_generation_client(provider, model),
                messages,
                model=model,
                temperature=0.35,
                thinking=_clean(payload.get("thinking"), 20) or None,
                timeout_seconds=600,
                attempts=max(1, min(3, _nonnegative_int(payload.get("generation_transport_attempts"), 2))),
                backoff_seconds=float(payload.get("generation_retry_backoff_seconds") or 0.5),
                attempt_log=batch_diagnostic["transport_attempts"],
                ensure_active=lambda: ensure_practice_generation_active(payload),
            )
        except Exception as exc:
            if batch_count <= 1 or not _is_transport_generation_error(exc):
                raise
            # A dead batch stream must not erase every question in that batch.
            # Retry each slot independently so one unstable response has a
            # one-question blast radius at most.
            split_restored: list[dict[str, Any]] = []
            split_failures: dict[str, dict[str, Any]] = {}
            split_reports: list[dict[str, Any]] = []
            for local_index, planned_item in enumerate(batch_plan):
                try:
                    _, restored, failures, diagnostic = generate_batch(
                        batch_start + local_index,
                        [planned_item],
                    )
                    split_restored.extend(restored)
                    split_failures.update(failures)
                    split_reports.append(diagnostic)
                except PracticeGenerationStopped:
                    raise
                except Exception as split_exc:
                    plan_item_id = _clean(planned_item.get("plan_item_id"), 80) or f"plan_item_{batch_start + local_index + 1:02d}"
                    split_failures[plan_item_id] = _generation_error_detail(split_exc)
                    split_reports.append({
                        "batch_start": batch_start + local_index + 1,
                        "batch_end": batch_start + local_index + 1,
                        "status": "split_item_failed",
                        "error_code": split_failures[plan_item_id]["code"],
                    })
            batch_diagnostic.update({
                "status": "recovered_by_single_item_split" if not split_failures else "partial_success_after_single_item_split",
                "split_recovery": split_reports,
                "final_accepted_indexes": [
                    local_index + 1
                    for local_index, planned_item in enumerate(batch_plan)
                    if _clean(planned_item.get("plan_item_id"), 80) not in split_failures
                ],
                "failed_plan_item_ids": sorted(split_failures),
            })
            return batch_start, split_restored, split_failures, batch_diagnostic
        batch_exercises, structural_failures = _complete_partial_batch(raw_batch, phase="initial")
        def batch_content_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            structure_issues: list[dict[str, Any]] = []
            for raw_item in rows:
                if not isinstance(raw_item, dict):
                    continue
                try:
                    local_index = int(raw_item.get("batch_index")) - 1
                except (TypeError, ValueError):
                    continue
                if local_index < 0 or local_index >= len(batch_plan):
                    continue
                issue = _question_structure_issue(
                    raw_item,
                    question_type=_effective_question_type(raw_item, batch_plan[local_index]),
                )
                if issue:
                    structure_issues.append({"batch_index": raw_item.get("batch_index"), **issue})
            return [
                *structure_issues,
                *_batch_variation_issues(rows, batch_plan, plan),
                *_batch_sibling_variant_issues(rows, batch_plan),
                *_batch_required_knowledge_point_issues(rows, batch_plan),
            ]

        initial_issues = batch_content_issues(batch_exercises)
        if initial_issues:
            issue_indexes: set[int] = set()
            has_unscoped_issue = False
            for issue in initial_issues:
                try:
                    issue_index = int(issue.get("batch_index"))
                except (TypeError, ValueError):
                    has_unscoped_issue = True
                    continue
                if 1 <= issue_index <= batch_count:
                    issue_indexes.add(issue_index)
                else:
                    has_unscoped_issue = True
            # An unscoped batch issue cannot safely identify a healthy sibling.
            # In that rare case every slot is implicated, but each is still
            # repaired independently so one bad response cannot replace another.
            target_indexes = list(range(1, batch_count + 1)) if has_unscoped_issue else sorted(issue_indexes)
            batch_diagnostic["content_gate_retry_targets"] = target_indexes
            batch_diagnostic["content_gate_retries"] = []
            rows_by_index = {
                int(item.get("batch_index")): item
                for item in batch_exercises
                if isinstance(item, dict) and str(item.get("batch_index", "")).isdigit()
            }
            for target_index in target_indexes:
                target_issues = [
                    issue for issue in initial_issues
                    if has_unscoped_issue or str(issue.get("batch_index")) == str(target_index)
                ]
                item_contract = _exercise_output_contract_for_plan_item(batch_plan[target_index - 1])
                repair_prompt = f"""上一版的 batch_index={target_index} 未通过生成门禁。

只修复这一题，不要返回其它 batch_index，不要修改任何已通过的题目。
严格保留该题的 required_knowledge_points、题型、难度和既定输出结构；不得输出答案、解析或内部 ID。
若有结构变化问题，重写题干结构；若题干需要配图，必须返回可渲染的数据点或节点关系，并让题干明确引用该图。

本题问题：
{json.dumps(target_issues, ensure_ascii=False, indent=2)}

单题蓝图：
{json.dumps(semantic_plan[target_index - 1], ensure_ascii=False, indent=2)}

输出结构：
{json.dumps({"exercises": [{"batch_index": target_index, **item_contract}]}, ensure_ascii=False, indent=2)}
"""
                transport_attempts: list[dict[str, Any]] = []
                retry_report: dict[str, Any] = {
                    "batch_index": target_index,
                    "issues": target_issues,
                    "transport_attempts": transport_attempts,
                }
                try:
                    ensure_practice_generation_active(payload)
                    repaired_raw = _call_practice_json_with_transport_retry(
        _practice_generation_client(provider, model),
                        [
                            *messages,
                            {"role": "assistant", "content": json.dumps(raw_batch, ensure_ascii=False)},
                            {"role": "user", "content": repair_prompt},
                        ],
                        model=model,
                        temperature=0.45,
                        thinking=_clean(payload.get("thinking"), 20) or None,
                        timeout_seconds=600,
                        attempts=2,
                        backoff_seconds=float(payload.get("generation_retry_backoff_seconds") or 0.5),
                        attempt_log=transport_attempts,
                        ensure_active=lambda: ensure_practice_generation_active(payload),
                    )
                    repaired_rows, repaired_shape = _partition_batch_rows(repaired_raw)
                    repaired_item = repaired_rows.get(target_index)
                    retry_report["actual_indexes"] = repaired_shape["actual_indexes"]
                    retry_report["status"] = "repaired" if repaired_item is not None else "invalid_response"
                    if repaired_item is not None:
                        rows_by_index[target_index] = repaired_item
                except PracticeGenerationStopped:
                    raise
                except Exception as exc:
                    if not _is_transport_generation_error(exc):
                        raise
                    retry_report["status"] = "transport_failed"
                    retry_report["error"] = _generation_error_detail(exc)
                batch_diagnostic["content_gate_retries"].append(retry_report)
            # Rebuild in ordinal order. Healthy initial rows are kept byte-for-byte;
            # only explicitly failing slots may be replaced by repair responses.
            batch_exercises = [rows_by_index[index] for index in sorted(rows_by_index)]

        # Figures have their own repair lifecycle. A figure-only defect must
        # never regenerate healthy question text or the rest of its batch.
        for raw_item in batch_exercises:
            if not isinstance(raw_item, dict):
                continue
            try:
                local_index = int(raw_item.get("batch_index")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= local_index < batch_count:
                _complete_generated_figure(raw_item, batch_plan[local_index])
        figure_issues = _batch_figure_issues(batch_exercises, batch_plan)
        figure_issues_by_index: dict[int, list[dict[str, Any]]] = {}
        for issue in figure_issues:
            try:
                local_index = int(issue.get("batch_index")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= local_index < batch_count:
                figure_issues_by_index.setdefault(local_index, []).append(issue)
        for raw_item in batch_exercises:
            if not isinstance(raw_item, dict):
                continue
            try:
                local_index = int(raw_item.get("batch_index")) - 1
            except (TypeError, ValueError):
                continue
            item_issues = figure_issues_by_index.get(local_index)
            if 0 <= local_index < batch_count and _plan_requires_stem_figure(batch_plan[local_index]):
                raw_item["figure_generation"] = {
                    "required": True,
                    "repair_attempted": bool(item_issues),
                    "status": "repairing" if item_issues else "passed_without_repair",
                    "initial_issue_codes": _unique_strings(
                        [issue.get("code") for issue in (item_issues or [])],
                        limit=20,
                        item_limit=100,
                    ),
                }
            if not item_issues:
                continue
            try:
                ensure_practice_generation_active(payload)
                repaired_figures = _repair_exercise_figures(
                    raw_item,
                    batch_plan[local_index],
                    item_issues,
                    provider=provider,
                    model=model,
                    payload=payload,
                    ensure_active=lambda: ensure_practice_generation_active(payload),
                )
                if repaired_figures:
                    ensure_practice_generation_active(payload)
                    raw_item["figures"] = repaired_figures
                    _complete_generated_figure(raw_item, batch_plan[local_index])
            except PracticeGenerationStopped:
                raise
            except Exception:
                # Preserve the original candidate and its deterministic gate
                # findings; the isolated failure remains retryable in the UI.
                raw_item["figure_generation"]["repair_error"] = "题图独立修复请求失败。"

        remaining_issues = [
            *batch_content_issues(batch_exercises),
            *_batch_figure_issues(batch_exercises, batch_plan),
        ]
        final_figure_issues_by_index: dict[int, list[dict[str, Any]]] = {}
        for issue in _batch_figure_issues(batch_exercises, batch_plan):
            try:
                local_index = int(issue.get("batch_index")) - 1
            except (TypeError, ValueError):
                continue
            final_figure_issues_by_index.setdefault(local_index, []).append(issue)
        for raw_item in batch_exercises:
            if not isinstance(raw_item, dict) or not isinstance(raw_item.get("figure_generation"), dict):
                continue
            local_index = int(raw_item.get("batch_index")) - 1
            final_figure_issues = final_figure_issues_by_index.get(local_index, [])
            raw_item["figure_generation"]["status"] = "failed" if final_figure_issues else (
                "repaired" if raw_item["figure_generation"].get("repair_attempted") else "passed_without_repair"
            )
            raw_item["figure_generation"]["final_issue_codes"] = _unique_strings(
                [issue.get("code") for issue in final_figure_issues],
                limit=20,
                item_limit=100,
            )
        issues_by_index: dict[int, list[dict[str, Any]]] = {}
        for issue in remaining_issues:
            try:
                local_index = int(issue.get("batch_index")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= local_index < batch_count:
                issues_by_index.setdefault(local_index, []).append(issue)
        restored: list[dict[str, Any]] = []
        for raw_item in batch_exercises:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            local_index = int(item.pop("batch_index")) - 1
            if local_index in issues_by_index:
                continue
            planned_item = batch_plan[local_index]
            item["plan_item_id"] = str(planned_item.get("plan_item_id") or f"plan_item_{batch_start + local_index + 1:02d}")
            item["parent_plan_item_id"] = _clean(planned_item.get("parent_plan_item_id"), 80)
            item["variant_id"] = _clean(planned_item.get("variant_id"), 100)
            item["variant_index"] = _nonnegative_int(planned_item.get("variant_index"))
            item["variant_count"] = _nonnegative_int(planned_item.get("variant_count"))
            item["variant_mode"] = _clean(planned_item.get("variant_mode"), 30)
            item["variant_role"] = _clean(planned_item.get("variant_role"), 100)
            item["source_question_id"] = _clean(planned_item.get("source_question_id"), 80)
            restored.append(item)
        failures = {
            str(batch_plan[local_index].get("plan_item_id") or f"plan_item_{batch_start + local_index + 1:02d}"): error
            for local_index, error in structural_failures.items()
        }
        failures.update({
            str(batch_plan[local_index].get("plan_item_id") or f"plan_item_{batch_start + local_index + 1:02d}"):
            _generation_gate_error(issues)
            for local_index, issues in issues_by_index.items()
        })
        batch_diagnostic["final_accepted_indexes"] = sorted(
            int(item.get("batch_index")) for item in batch_exercises if isinstance(item, dict)
        )
        batch_diagnostic["failed_plan_item_ids"] = sorted(failures)
        batch_diagnostic["status"] = "partial_success" if failures else "completed"
        return batch_start, restored, failures, batch_diagnostic

    pending_batches: list[tuple[int, list[dict[str, Any]]]] = []
    batch_failures: dict[str, dict[str, Any]] = {}
    generation_batch_diagnostics: list[dict[str, Any]] = []
    completed_ids = {str(item.get("plan_item_id") or "") for item in all_exercises}
    for batch_start in range(0, count, batch_size):
        batch_plan = [item for item in exercise_plan[batch_start : batch_start + batch_size] if isinstance(item, dict)]
        expected_ids = [str(item.get("plan_item_id") or f"plan_item_{batch_start + index + 1:02d}") for index, item in enumerate(batch_plan)]
        if batch_plan and not all(item in completed_ids for item in expected_ids):
            pending_batches.append((batch_start, batch_plan))

    def run_pending_batch(
        pending: tuple[int, list[dict[str, Any]]],
    ) -> tuple[int, list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
        return generate_batch(*pending)

    for (batch_start, batch_plan), future in iter_bounded_futures(
        pending_batches,
        run_pending_batch,
        max_workers=max_concurrency,
        thread_name_prefix="practice-batch",
        ensure_active=lambda: ensure_practice_generation_active(payload),
    ):
        try:
            _batch_start, restored, isolated_failures, batch_diagnostic = future.result()
            ensure_practice_generation_active(payload)
            already_completed_ids = {
                str(item.get("plan_item_id") or "")
                for item in all_exercises
                if isinstance(item, dict)
            }
            restored = [
                item for item in restored
                if str(item.get("plan_item_id") or "") not in already_completed_ids
            ]
            isolated_failures = {
                plan_item_id: error
                for plan_item_id, error in isolated_failures.items()
                if plan_item_id not in already_completed_ids
            }
            all_exercises.extend(restored)
            batch_failures.update(isolated_failures)
            generation_batch_diagnostics.append(batch_diagnostic)
        except PracticeGenerationStopped:
            raise
        except Exception as exc:
            error = _generation_error_detail(exc)
            failed_ids: list[str] = []
            for local_index, planned_item in enumerate(batch_plan):
                plan_item_id = str(
                    planned_item.get("plan_item_id")
                    or f"plan_item_{batch_start + local_index + 1:02d}"
                )
                batch_failures[plan_item_id] = error
                failed_ids.append(plan_item_id)
            generation_batch_diagnostics.append({
                "batch_start": batch_start + 1,
                "batch_end": batch_start + len(batch_plan),
                "expected_indexes": list(range(1, len(batch_plan) + 1)),
                "response_attempts": [],
                "recovery_attempt_count": 0,
                "final_accepted_indexes": [],
                "failed_plan_item_ids": failed_ids,
                "status": "batch_request_failed",
                "error_code": error["code"],
                "error_message": error["message"],
            })
        if job_id:
            try:
                from .practice_jobs import update_practice_job
                completed = len({str(item.get("plan_item_id") or "") for item in all_exercises})
                update_practice_job(
                    job_id,
                    partial_exercises=all_exercises,
                    generated_count=completed,
                    failed_count=len(batch_failures),
                    total_count=count,
                    batch_errors=[
                        {"plan_item_id": plan_item_id, **error}
                        for plan_item_id, error in sorted(batch_failures.items())
                    ],
                    batch_diagnostics=sorted(
                        generation_batch_diagnostics,
                        key=lambda row: int(row.get("batch_start") or 0),
                    ),
                    progress_message=(
                        f"已成功生成 {completed}/{count} 道题，另有 {len(batch_failures)} 道题生成失败；"
                        "正在继续处理其余批次。可离开当前页面，任务会继续。"
                        if batch_failures
                        else f"已完成 {completed}/{count} 道题，正在继续生成并检查后续内容。可离开当前页面，任务会继续。"
                    ),
                )
            except Exception:
                pass
        ensure_practice_generation_active(payload)
    all_exercises = complete_practice_slots(
        all_exercises,
        exercise_plan[:count],
        failed_placeholder=lambda planned_item, index, error: _failed_exercise_placeholder(
            planned_item,
            index=index,
            error=error,
        ),
        failures=batch_failures,
    )
    raw = {"exercises": all_exercises}
    result = normalize_practice_set(
        raw,
        requested_count=count,
        subject="",
        planned_types=planned_types,
        planned_source_ids=planned_source_ids,
        planned_plan_ids=[str(item.get("plan_item_id") or f"plan_item_{index + 1:02d}") for index, item in enumerate(exercise_plan[:count])],
        planned_difficulties=planned_difficulties,
    )
    result["source_analysis"] = plan.get("source_analysis") or result["source_analysis"]
    result["blueprint"] = {**result["blueprint"], **reviewed_blueprint}
    result["blueprint_multi_question"] = multi_question_config
    result["source_scope"] = plan.get("source_scope") or {"mode": "single", "questions": []}
    result["selected_source_questions"] = plan.get("selected_source_questions") or []
    result["generation_strategy"] = reviewed_blueprint.get("generation_strategy") or "single"
    result["include_source_content_in_generation"] = include_source_content
    result["blueprint_audit"] = plan_audit
    result["source_mode"] = "knowledge" if is_knowledge_mode else "exam"
    result_ids = [str(item.get("plan_item_id") or "").strip() for item in all_exercises]
    expected_all_ids = [str(item.get("plan_item_id") or "").strip() for item in exercise_plan[:count]]
    if result_ids != expected_all_ids:
        raise ValueError(f"合并后的 plan_item_id 未完整覆盖蓝图：期望 {expected_all_ids}，实际 {result_ids}。")
    if is_knowledge_mode:
        result["knowledge_title"] = _clean(plan.get("knowledge_title"), 300) or "知识点模拟题"
    diversity_repair, diversity_failures = _selectively_repair_practice_diversity(result, payload)
    result["diversity_repair"] = diversity_repair
    batch_failures.update(diversity_failures)
    result["quality"] = recompute_practice_quality(result)
    groups = build_practice_result_groups(
        result["exercises"],
        selected_source_questions=[
            item for item in (plan.get("selected_source_questions") or []) if isinstance(item, dict)
        ],
        reviewed_exercise_plan=reviewed_exercise_plan,
        clean_value=_clean,
    )
    result.update(groups)
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    result["generation"] = build_practice_generation_metadata(
        quality,
        PracticeGenerationMetadataContext(
            provider_name=provider.name,
            model=model,
            expected_count=count,
            batch_failures=batch_failures,
            batch_diagnostics=generation_batch_diagnostics,
            diversity_repair=diversity_repair,
            generation_run_id=_clean(payload.get("generation_run_id"), 100),
            include_source_content=include_source_content,
            reference_images_attached=bool(generation_reference_images),
            blueprint_multi_question=multi_question_config,
        ),
    )
    result["blueprint_review_enabled"] = bool(payload.get("blueprint_review_enabled", True))
    result["quality"] = recompute_practice_quality(result)
    if _practice_semantic_review_should_run(result, payload):
        try:
            result["semantic_review"] = review_practice_semantics(result, payload)
        except PracticeGenerationStopped:
            raise
        except Exception as exc:
            # A reviewer outage must not erase a usable generated set. Surface
            # the missing review so the user can keep or edit the result.
            result["semantic_review"] = {
                "status": "failed",
                "triggered": True,
                "review_scope": "complete_set",
                "items": [],
                "error": _clean(str(exc), 800),
            }
    elif payload.get("semantic_review_enabled") is True or payload.get("formal_quality_review") is True:
        result["semantic_review"] = {
            "status": "skipped",
            "triggered": False,
            "reason": "structural_issues_or_low_risk_simple_set",
            "items": [],
        }
    result["quality"] = recompute_practice_quality(result)
    return result


def generate_practice_from_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate directly from program-owned constraints without model planning."""
    supplied = payload.get("generation_contract")
    contract = supplied if isinstance(supplied, dict) else build_generation_contract(payload)
    include_source_content = _include_source_content_in_generation(payload, {
        "include_source_content_in_generation": contract.get("include_source_content_in_generation"),
    })
    audit = audit_generation_contract(contract)
    slots = [dict(item) for item in contract.get("slots") or [] if isinstance(item, dict)]
    adapter_plan = {
        "schema_version": "answer_book.practice_plan.v1",
        "source_mode": contract.get("source_mode") or payload.get("source_mode") or "exam",
        "source_scope": contract.get("source_scope") or payload.get("source_scope") or {},
        "source_analysis": contract.get("source_analysis") or payload.get("source_analysis") or {},
        "selected_source_questions": contract.get("selected_source_questions") or payload.get("selected_source_questions") or [],
        "include_source_content_in_generation": include_source_content,
        "blueprint": {
            "training_goal": contract.get("focus") or "按确认范围生成练习",
            "progression": ["严格执行已确认题量、题型、难度和来源约束"],
            "design_notes": ["本任务跳过蓝图审查，程序按稳定生成槽位直接生题"],
            "generation_strategy": contract.get("generation_strategy"),
            "include_source_content_in_generation": include_source_content,
            "exercise_plan": slots,
        },
        "scope_cover": contract.get("source_coverage") or {},
    }
    result = generate_practice_from_plan({
        **payload,
        "generation_strategy": contract.get("generation_strategy"),
        "include_source_content_in_generation": include_source_content,
        "plan": adapter_plan,
    })
    result["generation_contract"] = contract
    result["generation_contract_audit"] = audit
    result["blueprint_review_enabled"] = False
    result["include_source_content_in_generation"] = include_source_content
    result["generation"] = {
        **(result.get("generation") or {}),
        "path": "direct_from_confirmed_scope",
        "generation_run_id": _clean(payload.get("generation_run_id"), 100),
    }
    return result


def regenerate_practice_exercise(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_practice_generation_active(payload)
    practice = payload.get("practice") if isinstance(payload.get("practice"), dict) else {}
    exercises = practice.get("exercises") if isinstance(practice.get("exercises"), list) else []
    index = int(payload.get("exercise_index") or 0)
    if index < 0 or index >= len(exercises):
        raise ValueError("需要重新生成的题目序号无效。")
    current = exercises[index] if isinstance(exercises[index], dict) else {}
    include_source_content = _include_source_content_in_generation(payload, practice)
    source_files = payload.get("source_files") if isinstance(payload.get("source_files"), list) else []
    source_text = str(payload.get("question_text") or "").strip()
    if include_source_content and not source_text and not source_files and not (practice.get("source_scope") or practice.get("selected_source_questions")):
        raise ValueError("单题重生缺少原始题目/教材材料或来源快照，无法可靠重生。")
    sources = (
        parse_practice_sources({"question_text": source_text, "source_files": source_files})
        if include_source_content and (source_text or source_files)
        else {"text": "", "images": [], "reference_images": []}
    )
    reference_images = sources.get("reference_images") or sources.get("images") or []
    provider, model = _primary_model_runtime(payload)
    generation_reference_images = (
        reference_images if _provider_model_supports_vision(provider, model) else []
    )
    target_type = _clean(current.get("question_type"), 20)
    is_knowledge_mode = _clean(payload.get("source_mode") or practice.get("source_mode"), 30) == "knowledge"
    blueprint = practice.get("blueprint") if isinstance(practice.get("blueprint"), dict) else {}
    parent_plan_item_id = _clean(current.get("parent_plan_item_id"), 80)
    current_plan_item_id = _clean(current.get("plan_item_id"), 80)
    planned_item = next(
        (row for row in blueprint.get("exercise_plan") or []
         if isinstance(row, dict) and _clean(row.get("plan_item_id"), 80) == (parent_plan_item_id or current_plan_item_id)),
        {},
    )
    required_knowledge_points = _unique_strings(
        planned_item.get("required_knowledge_points") or current.get("knowledge_points"),
        limit=60,
        item_limit=500,
    )
    source_catalog = [
        row for row in (practice.get("selected_source_questions") or (practice.get("source_scope") or {}).get("questions") or [])
        if isinstance(row, dict)
    ]
    source_refs = _unique_strings(
        planned_item.get("source_refs") or [current.get("source_question_id")],
        limit=3,
        item_limit=80,
    )
    peer_patterns = []
    for peer_index, peer in enumerate(exercises):
        if peer_index == index or not isinstance(peer, dict) or peer.get("generation_status") == "failed":
            continue
        peer_source = _clean(peer.get("source_question_id"), 80)
        if source_refs and peer_source and peer_source not in source_refs:
            continue
        peer_patterns.append({
            "question_number": peer.get("number") or peer_index + 1,
            "stem": _clean(peer.get("stem"), 500),
            "diversity_signature": _normalized_diversity_signature(peer.get("diversity_signature")),
            "primary_difficulty_mechanism": _normalized_difficulty_evidence(peer.get("difficulty_evidence"))["primary_mechanism"],
        })
        if len(peer_patterns) >= 6:
            break
    required_constraints = _required_constraints_for_plan_item(
        source_refs,
        source_catalog,
        planned_item.get("required_constraints"),
        _clean(blueprint.get("generation_strategy") or practice.get("generation_strategy"), 40),
        practice.get("source_analysis") if len(source_catalog) <= 1 else None,
        allow_partition=(
            is_knowledge_mode
            and _clean(blueprint.get("generation_strategy") or practice.get("generation_strategy"), 40) == "knowledge_item_wise"
            and bool(source_refs)
            and sum(
                1
                for row in (blueprint.get("exercise_plan") or [])
                if isinstance(row, dict)
                and _clean((row.get("source_refs") or [row.get("source_question_id")])[0], 80) == source_refs[0]
            ) > 1
        ),
    )
    item_for_generation = dict(planned_item) if isinstance(planned_item, dict) and planned_item else {
        "plan_item_id": _clean(current.get("plan_item_id"), 80),
        "source_question_id": _clean(current.get("source_question_id"), 80),
        "source_refs": source_refs,
        "question_type": target_type,
        "difficulty": _clean(payload.get("difficulty") or current.get("difficulty"), 20),
        "target_skill": _clean(current.get("target_skill"), 500),
        "variation_type": _clean(current.get("variation_type"), 200),
        "design_intent": "保持已确认训练目标并重设计本题。",
        "required_knowledge_points": required_knowledge_points,
        "required_constraints": required_constraints,
    }
    item_for_generation["required_knowledge_points"] = required_knowledge_points
    item_for_generation["required_constraints"] = required_constraints
    item_for_generation.update({
        "plan_item_id": current_plan_item_id or f"plan_item_{index + 1:02d}",
        "parent_plan_item_id": parent_plan_item_id,
        "variant_id": _clean(current.get("variant_id"), 100),
        "variant_index": _nonnegative_int(current.get("variant_index")),
        "variant_count": _nonnegative_int(current.get("variant_count")),
        "variant_mode": _clean(current.get("variant_mode"), 30),
        "variant_role": _clean(current.get("variant_role"), 100),
        "difficulty": _clean(current.get("difficulty"), 20) or item_for_generation.get("difficulty"),
        "variation_type": _clean(current.get("variation_type"), 200) or item_for_generation.get("variation_type"),
    })
    context_plan = ensure_practice_blueprint_defaults({
        "source_mode": practice.get("source_mode"),
        "source_analysis": practice.get("source_analysis") or {},
        "source_scope": practice.get("source_scope") or {},
        "selected_source_questions": practice.get("selected_source_questions") or [],
        "blueprint": {
            "generation_strategy": blueprint.get("generation_strategy"),
            "expected_source_counts": blueprint.get("expected_source_counts") or {},
            "exercise_plan": [item_for_generation],
        },
    })
    item_for_generation = context_plan["blueprint"]["exercise_plan"][0]
    if include_source_content:
        _hydrate_single_source_content(context_plan, sources.get("text") or "")
    semantic_sources = _semantic_batch_context(
        context_plan,
        [item_for_generation],
        knowledge_mode=is_knowledge_mode,
        include_source_content=include_source_content,
    )
    source_context = semantic_sources if include_source_content else _abstract_generation_context(
        context_plan,
        knowledge_mode=is_knowledge_mode,
    )
    generation_context = {
        "source_mode": _clean(payload.get("source_mode") or practice.get("source_mode"), 30) or "exam",
        "generation_strategy": _clean(payload.get("generation_strategy") or practice.get("generation_strategy"), 60),
        "required_knowledge_points": required_knowledge_points,
        "required_constraints": required_constraints,
        "difficulty": item_for_generation.get("difficulty"),
        "difficulty_intent": _difficulty_intent(item_for_generation, set_position=index),
        "question_type_requirements": _question_type_generation_requirements(item_for_generation),
        "change_contract": _plan_change_contract(item_for_generation),
        "forbidden_peer_patterns": peer_patterns,
        "stem_figure_required": _plan_requires_stem_figure(item_for_generation),
        "figure_design": _figure_design(item_for_generation.get("figure_design"), required=True) if _plan_requires_stem_figure(item_for_generation) else None,
    }
    subject_requirements = _subject_format_requirements(context_plan, [item_for_generation])
    output_format_requirements = _practice_output_format_requirements()
    task = f"""# 任务

只重新生成{"知识点模拟题" if is_knowledge_mode else "专项练习"}中的第 {index + 1} 题，其他题目不会传给你修改。

## {"本题绑定来源" if include_source_content else "任务级元信息"}

{json.dumps(source_context, ensure_ascii=False, indent=2)}

## 当前题目

{json.dumps(_without_internal_ids(current), ensure_ascii=False, indent=2)}

## 本题生成上下文（必须保留）

{json.dumps(_without_internal_ids(generation_context), ensure_ascii=False, indent=2)}

## 用户补充要求

{_clean(payload.get("instruction"), 1000) or "保持训练目标，换一种有效变式。"}

## 约束

- 保持题型为 {target_type or "综合题"}
- 保持研究生层级
- knowledge_points 必须与 required_knowledge_points 完整一致
- required_constraints 只适用于本题，必须落实到题干条件或考查要求中
- difficulty_intent 是防退化边界而非硬模板；选择一种最适合本题的主要机制，最多再加一种辅助机制，并返回不含答案的 difficulty_evidence
- 优先避免与 forbidden_peer_patterns 重复主要难度机制，但如果重复机制对本题最自然，允许保留，不得为了形式差异硬堆高阶任务
- 当前题目和 forbidden_peer_patterns 都是反例；新题必须执行 change_contract，并改变情境、主要未知量、认知操作或核心公式链中的至少两项
- 必须返回忠实的 diversity_signature 供系统去重；不得把同一道题换数字、单位、题型外壳或同义措辞
- {"仅参考 batch_index 1 绑定来源的 source_content、图片和约束。" if include_source_content else "不得复述、引用或假设原题题面、教材原文、图片和表格。"}
{('- stem_figure_required=true：必须返回可渲染的题干图；图表至少两个数据点，示意图至少两个节点及必要关系，不能只写 description。' if _plan_requires_stem_figure(item_for_generation) else '- stem_figure_required=false：不要返回 figures；作图题要求学生绘图不等于题干需要配图。')}
{chr(10).join(f'- {requirement}' for requirement in subject_requirements)}
{chr(10).join(f'- {requirement}' for requirement in output_format_requirements)}
- 只输出包含 exercises 数组的合法 JSON，数组中恰好一题

## 输出结构

{json.dumps({"exercises": [_exercise_output_contract_for_plan_item(item_for_generation)]}, ensure_ascii=False, indent=2)}
"""
    messages = [
        {"role": "system", "content": "你是研究生教研专家，只重写指定的一道练习题。只输出合法 JSON。"},
            {"role": "user", "content": _user_content(task, generation_reference_images if include_source_content and _batch_needs_visual_reference(semantic_sources, [item_for_generation]) else [])},
    ]
    raw = _call_practice_json(
        _practice_generation_client(provider, model),
        messages,
        model=model,
        temperature=0.45,
        thinking=_clean(payload.get("thinking"), 20) or None,
        ensure_active=lambda: ensure_practice_generation_active(payload),
    )
    first_candidate = next((item for item in (raw.get("exercises") or []) if isinstance(item, dict)), {})
    if first_candidate and not _regenerated_exercise_substantively_changed(current, first_candidate):
        retry_messages = [
            *messages,
            {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
            {
                "role": "user",
                "content": (
                    "上一次返回与当前题目实质相同，未完成重新生成。请严格执行 change_contract 和用户要求，"
                    "改变情境、认知操作、核心判断链或选项逻辑中至少两项；"
                    "保留题型、难度、必考知识点和边界。只输出合法 JSON。"
                ),
            },
        ]
        raw = _call_practice_json(
        _practice_generation_client(provider, model),
            retry_messages,
            model=model,
            temperature=0.5,
            thinking=_clean(payload.get("thinking"), 20) or None,
            ensure_active=lambda: ensure_practice_generation_active(payload),
        )
        first_candidate = next((item for item in (raw.get("exercises") or []) if isinstance(item, dict)), {})
        if not first_candidate or not _regenerated_exercise_substantively_changed(current, first_candidate):
            raise ValueError("模型未生成与当前题目有实质差异的新题；原题已安全保留，请调整要求后重试本题。")
    ensure_practice_generation_active(payload)
    raw_exercises = []
    for raw_item in raw.get("exercises") or []:
        if isinstance(raw_item, dict):
            restored = dict(raw_item)
            restored["plan_item_id"] = _clean(current.get("plan_item_id"), 80) or f"plan_item_{index + 1:02d}"
            restored["source_question_id"] = _clean(current.get("source_question_id"), 80)
            _complete_generated_figure(restored, item_for_generation)
            raw_exercises.append(restored)
    normalized = normalize_practice_set(
        {
            "source_analysis": practice.get("source_analysis") or {},
            "blueprint": practice.get("blueprint") or {},
            "exercises": raw_exercises,
        },
        requested_count=1,
        subject="",
        planned_types=[target_type] if target_type in ALLOWED_TYPES else ["综合题"],
        planned_source_ids=[_clean(current.get("source_question_id"), 80)],
        planned_plan_ids=[_clean(current.get("plan_item_id"), 80) or f"plan_item_{index + 1:02d}"],
        planned_difficulties=[_clean(current.get("difficulty"), 20) or "进阶"],
    )
    exercise = normalized["exercises"][0]
    for field in (
        "parent_plan_item_id",
        "variant_id",
        "variant_index",
        "variant_count",
        "variant_mode",
        "variant_role",
    ):
        exercise[field] = current.get(field)
    structure_issue = _question_structure_issue(
        exercise,
        question_type=_effective_question_type(exercise, item_for_generation),
    )
    if structure_issue:
        raise ValueError(f"单题重生结构不完整：{structure_issue['message']}")
    issue = _required_knowledge_point_issue(exercise, {"required_knowledge_points": required_knowledge_points})
    if issue:
        raise ValueError(
            "单题重生未完整匹配蓝图必考知识点："
            f"缺少 {'、'.join(issue['missing_knowledge_points']) or '无'}；"
            f"额外 {'、'.join(issue['extra_knowledge_points']) or '无'}。"
        )
    figure_issues = _exercise_figure_issues(exercise, item_for_generation)
    if _plan_requires_stem_figure(item_for_generation):
        exercise["figure_generation"] = {
            "required": True,
            "repair_attempted": bool(figure_issues),
            "status": "repairing" if figure_issues else "passed_without_repair",
            "initial_issue_codes": _unique_strings([item.get("code") for item in figure_issues], limit=20, item_limit=100),
            "final_issue_codes": [],
            "repair_error": "",
        }
    if figure_issues:
        try:
            repaired_figures = _repair_exercise_figures(
                exercise,
                item_for_generation,
                figure_issues,
                provider=provider,
                model=model,
                payload=payload,
            )
        except Exception as exc:
            exercise["figure_generation"]["status"] = "failed"
            exercise["figure_generation"]["repair_error"] = _clean(str(exc), 500)
            if _is_transport_generation_error(exc):
                raise ValueError(
                    "题目正文已生成，但题图自动修复超时或网络异常；"
                    "原题已安全保留，请稍后只重试本题。"
                ) from exc
            raise ValueError(
                "题目正文已生成，但题图自动修复未完成；"
                "原题已安全保留，请只重试本题。"
            ) from exc
        exercise["figures"] = _normalize_figures(repaired_figures)
        _complete_generated_figure(exercise, item_for_generation)
        figure_issues = _exercise_figure_issues(exercise, item_for_generation)
        exercise["figure_generation"]["status"] = "failed" if figure_issues else "repaired"
        exercise["figure_generation"]["final_issue_codes"] = _unique_strings(
            [item.get("code") for item in figure_issues], limit=20, item_limit=100
        )
        if figure_issues:
            detail = _generation_gate_error(figure_issues).get("detail") or "；".join(
                item["message"] for item in figure_issues
            )
            raise ValueError("单题重生未通过题干配图门禁：" + detail)
    exercise["exercise_id"] = _clean(current.get("exercise_id"), 100) or f"practice_{index + 1:02d}"
    exercise["number"] = index + 1
    merged = list(exercises)
    merged[index] = exercise
    ensure_unique_figure_ids(merged)
    exercise = merged[index]
    updated_practice = {**practice, "exercises": merged, "quality": {}}
    semantic_review = practice.get("semantic_review") if isinstance(practice.get("semantic_review"), dict) else {}
    review_enabled = payload.get("semantic_review_enabled") is True or payload.get("formal_quality_review") is True
    review_required = (
        _effective_question_type(exercise, item_for_generation) in {"综合题", "作图题"}
        or _clean(exercise.get("difficulty"), 20) == "挑战"
        or _plan_requires_stem_figure(item_for_generation)
    )
    if review_enabled and review_required:
        single_review_practice = {
            **updated_practice,
            "requested_count": 1,
            "exercises": [exercise],
            "blueprint": {**blueprint, "exercise_plan": [item_for_generation]},
            "semantic_review": {},
        }
        try:
            replacement_review = review_practice_semantics(single_review_practice, payload)
        except PracticeGenerationStopped:
            raise
        except Exception as exc:
            replacement_review = {
                "status": "failed",
                "triggered": True,
                "review_scope": "single_question",
                "items": [],
                "error": _clean(str(exc), 800),
            }
        semantic_review = _merge_incremental_semantic_review(
            {**updated_practice, "semantic_review": semantic_review},
            replacement_review,
            target_number=index + 1,
        )
    elif review_required:
        # Never retain a green semantic verdict for content that has changed
        # without being re-reviewed. The usable question remains available.
        semantic_review = {
            **semantic_review,
            "status": "failed",
            "review_scope": "stale_after_regeneration",
            "items": [
                ({"number": index + 1, "status": "not_reviewed", "risks": []}
                 if str(item.get("number") or "") == str(index + 1) else item)
                for item in (semantic_review.get("items") or [])
                if isinstance(item, dict)
            ],
            "error": "本题已重生成，原语义复核结论已失效。",
        }
    updated_practice["semantic_review"] = semantic_review
    quality = recompute_practice_quality(updated_practice)
    return {
        "exercise": exercise,
        "quality": quality,
        "semantic_review": semantic_review,
        "include_source_content_in_generation": include_source_content,
        "generation": {
            "provider": provider.name,
            "model": model,
            "model_route": "selected_primary",
            "reference_images_attached": bool(generation_reference_images),
            "include_source_content_in_generation": include_source_content,
        },
        "regeneration": {"exercise_index": index, "context": generation_context},
    }


def generate_practice_set(payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry point for callers that still expect one request."""
    plan = plan_practice_set(payload)
    return generate_practice_from_plan({**payload, "plan": plan})


def regenerate_plan_item(payload: dict[str, Any]) -> dict[str, Any]:
    """Redesign exactly one confirmed blueprint item while retaining all others."""
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    items = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []
    index = int(payload.get("plan_index") or 0)
    if index < 0 or index >= len(items) or not isinstance(items[index], dict):
        raise ValueError("需要重新设计的蓝图项无效。")
    original = items[index]
    raw_spec = payload.get("revision_spec") if isinstance(payload.get("revision_spec"), dict) else {}
    allowed_change_fields = {"question_type", "difficulty", "target_skill", "variation_type", "design_intent"}
    must_change = [item for item in _unique_strings(raw_spec.get("must_change"), limit=5, item_limit=40) if item in allowed_change_fields]
    revision_spec = {
        "must_change": must_change,
        "must_preserve": ["source_binding", "graduate_level"],
        "forbid": _unique_strings(raw_spec.get("forbid"), limit=8, item_limit=120),
        "note": _clean(raw_spec.get("note") or payload.get("instruction"), 1200),
    }
    if not revision_spec["must_change"] and not revision_spec["note"]:
        raise ValueError("请至少选择一项必须改变的内容，或填写具体调整意见。")
    scope = plan.get("source_scope") if isinstance(plan.get("source_scope"), dict) else {}
    catalog = [row for row in (plan.get("selected_source_questions") or scope.get("questions") or []) if isinstance(row, dict)]
    source_by_id = {_clean(row.get("source_question_id"), 80): row for row in catalog if _clean(row.get("source_question_id"), 80)}
    source_refs = _unique_strings(original.get("source_refs") or [original.get("source_question_id")], limit=3, item_limit=80)
    source_semantics = {
        "sources": [
            {
                "source_index": source_index,
                "title": _clean(source.get("title"), 300),
                "source_excerpt": _clean(source.get("stem_excerpt") or source.get("excerpt"), 1800),
                "question_type": _clean(source.get("question_type"), 100),
                "knowledge_points": _string_list(source.get("knowledge_points"), limit=20),
            }
            for source_index, source_id in enumerate(source_refs, start=1)
            for source in [source_by_id.get(source_id, {})]
        ],
        "coverage_role": _clean(original.get("coverage_role"), 20),
    }
    occupied = [
        {
            "question_type": _clean(row.get("question_type"), 20),
            "target_skill": _clean(row.get("target_skill"), 120),
            "variation_type": _clean(row.get("variation_type"), 120),
        }
        for row_index, row in enumerate(items)
        if row_index != index and isinstance(row, dict)
    ][:10]
    task = f"""# 任务

只重新设计一个研究生训练蓝图项；不得改动来源绑定或其它蓝图项。

## 修改约束（最高优先级）

{json.dumps(revision_spec, ensure_ascii=False, indent=2)}

## 当前来源语义（仅绑定来源片段）

{json.dumps(source_semantics, ensure_ascii=False, indent=2)}

## 当前蓝图项（待修改对象）

{json.dumps(_without_internal_ids(original), ensure_ascii=False, indent=2)}

## 其它项已占用摘要（仅用于避免重复，最多 10 条）

{json.dumps(occupied, ensure_ascii=False, indent=2)}

## 输出要求

- must_change 中列出的字段必须与当前项产生实质变化
- must_preserve 始终优先，禁止改变来源和研究生层级
- forbid 中的内容不得出现在新蓝图项中
- applied_changes 只是给用户看的变更说明，不是质量证明
- 只输出 JSON：{{"plan_item":{{"question_type":"...","difficulty":"基础/进阶/挑战","target_skill":"...","variation_type":"...","design_intent":"..."}},"applied_changes":[{{"constraint_id":"must_change/1","evidence":"..."}}]}}
- 不得输出任何内部 ID
"""
    provider, model = _model_runtime(payload, False)
    messages = [
        {"role": "system", "content": "你是研究生教研专家。用户的新修改约束优先于旧蓝图；只输出合法 JSON。"},
        {"role": "user", "content": task},
    ]
    # Use the prompt-only path deliberately: unlike chat_json(), chat_text()
    # never performs a response_format compatibility retry, so this action is
    # exactly one provider request even on providers without JSON mode.
    revision_client = _practice_generation_client(provider, model)
    result = revision_client.chat_text(
        messages,
        model=model,
        temperature=0.25,
        max_tokens=max(DEFAULT_MODEL_MAX_TOKENS, 10000),
        thinking=_clean(payload.get("thinking"), 20) or None,
        timeout=_practice_stage_timeout("blueprint_revision", 300),
    )
    raw = _parse_safe_practice_json(result.content)
    redesigned = raw.get("plan_item") if isinstance(raw.get("plan_item"), dict) else {}
    if not _clean(redesigned.get("target_skill"), 500):
        raise ValueError("模型未返回可用的蓝图候选项。")
    if _clean(redesigned.get("question_type"), 20) not in ALLOWED_TYPES:
        raise ValueError("模型返回的题型不在允许范围内。")
    if _clean(redesigned.get("difficulty"), 20) not in ALLOWED_DIFFICULTIES:
        raise ValueError("模型返回的难度不在允许范围内。")
    field_labels = {
        "question_type": "题型",
        "difficulty": "难度",
        "target_skill": "目标能力",
        "variation_type": "变化方式",
        "design_intent": "情境/条件/设计意图",
    }
    hard_errors = [
        f"要求改变“{field_labels[field]}”，但模型返回值与原蓝图相同。"
        for field in revision_spec["must_change"]
        if _clean(redesigned.get(field), 800) == _clean(original.get(field), 800)
    ]
    candidate_text = json.dumps(redesigned, ensure_ascii=False).casefold()
    hard_errors.extend(f"候选仍包含禁止内容：{term}" for term in revision_spec["forbid"] if term.casefold() in candidate_text)
    if hard_errors:
        raise ValueError("候选未通过用户约束门禁：" + "；".join(hard_errors))
    redesigned = {**original, **redesigned}
    if _clean(redesigned.get("difficulty"), 20) != _clean(original.get("difficulty"), 20):
        redesigned["difficulty_levers"], redesigned["difficulty_rationale"] = _difficulty_design(
            _clean(redesigned.get("difficulty"), 20),
            _clean(redesigned.get("question_type"), 20),
            structural_change=redesigned.get("structural_change"),
            target_skill=redesigned.get("target_skill"),
        )
    redesigned["difficulty_design_level"] = _clean(redesigned.get("difficulty"), 20)
    for key in ("plan_item_id", "source_question_id", "number"):
        redesigned[key] = original.get(key)
    redesigned["source_refs"] = source_refs
    redesigned["coverage_role"] = _clean(original.get("coverage_role"), 20) or "变式"
    return {
        "plan_index": index,
        "plan_item": redesigned,
        "applied_changes": raw.get("applied_changes") or [],
        "hard_checks": {"status": "passed", "checked_fields": revision_spec["must_change"], "forbid_count": len(revision_spec["forbid"])},
        "request_evidence": {
            "call_count": 1,
            "prompt_char_count": len(task),
            "source_char_count": len(json.dumps(source_semantics, ensure_ascii=False)),
            "occupied_summary_count": len(occupied),
            "revision_spec": revision_spec,
            "prompt_sections": ["revision_spec", "source_semantics", "current_plan_item", "occupied_summary"],
            "prompt_snapshot": {
                "revision_spec": revision_spec,
                "source_semantics": source_semantics,
                "current_plan_item": _without_internal_ids(original),
                "occupied_summary": occupied,
            },
        },
    }


def _plan_item_by_id_or_index(plan: dict[str, Any], plan_item_id: str | None, index: int) -> tuple[dict[str, Any], int]:
    """
    取蓝图计划项：优先按全局唯一 plan_item_id 定位，其次按下标。

    返回 (计划项, 实际下标)。找不到时抛错。
    """
    blueprint = plan.get("blueprint") if isinstance(plan, dict) else {}
    exercise_plan = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []
    if not exercise_plan:
        raise ValueError("缺少已确认的蓝图计划项。")
    target_id = _clean(plan_item_id, 100)
    if target_id:
        for i, item in enumerate(exercise_plan):
            if isinstance(item, dict) and _clean(item.get("plan_item_id"), 100) == target_id:
                return item, i
        raise ValueError(f"蓝图计划项不存在: {target_id}")
    index = int(index)
    if index < 0 or index >= len(exercise_plan):
        raise ValueError("蓝图计划项下标无效。")
    item = exercise_plan[index] if isinstance(exercise_plan[index], dict) else {}
    return item, index


def _is_single_item_mode(generation_strategy: str) -> bool:
    """单项模式只带当前来源片段；整套模式才带全套资料与蓝图。"""
    strategy = _clean(generation_strategy, 40)
    return strategy in {"knowledge_item_wise", "per_question", "parallel_exam", "knowledge_targeted"}


def generate_plan_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """
    蓝图页「生成本题草案」：按单一计划项生成一题，作为草案预览。

    按场景裁剪给模型的内容（@Jayden 要求）：
    - 单项模式（knowledge_item_wise / per_question / parallel_exam 单项）：只带当前项来源片段。
    - 整套模式（knowledge_overall / targeted_set）：带全部资料 + 全套蓝图 + 用户反馈。

    保留 plan_item_id / 题型 / 难度 / 来源；返回的草案挂在该计划项下，不污染正式结果。
    """
    plan = ensure_practice_blueprint_defaults(
        payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    )
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    item, index = _plan_item_by_id_or_index(plan, _clean(payload.get("plan_item_id"), 100), int(payload.get("plan_index") or 0))

    is_knowledge_mode = _clean(payload.get("source_mode") or plan.get("source_mode") or payload.get("plan", {}).get("source_mode", ""), 30) == "knowledge"
    strategy = _clean(payload.get("generation_strategy") or blueprint.get("generation_strategy") or (plan.get("blueprint") or {}).get("generation_strategy") or item.get("generation_strategy"), 40)
    target_type = _clean(item.get("question_type"), 20) or "综合题"
    target_diff = _clean(item.get("difficulty"), 20) or "基础"
    source_question_id = _clean(item.get("source_question_id"), 80)
    source_scope = _normalize_source_scope(payload.get("source_scope") or {})
    include_source_content = _include_source_content_in_generation(payload, plan)

    # 单项模式：只取当前项来源片段（来自 #9 的 stem_excerpt/source_ref）
    single_item_mode = _is_single_item_mode(strategy)
    clipped_scope = source_scope
    if single_item_mode and source_question_id:
        for q in source_scope.get("questions") or []:
            if _clean(q.get("source_question_id"), 80) == source_question_id:
                clipped_scope = {"mode": "single", "title": q.get("title", ""), "questions": [q]}
                break

    # 草案会被用户采用进入正式生成，因此与正式生成使用同一份来源材料开关。
    source_text = ""
    images: list[str] = []
    if include_source_content:
        sources = parse_practice_sources({"question_text": payload.get("question_text") or "", "source_files": payload.get("source_files") or []})
        source_text = sources.get("text") or ""
        images = sources.get("reference_images") or sources.get("images") or []
    else:
        clipped_scope = {"mode": "abstract_constraints", "questions": []}

    provider, model = _primary_model_runtime(payload)
    generation_images = images if _provider_model_supports_vision(provider, model) else []
    context_plan = {
        **plan,
        "source_scope": clipped_scope,
        "selected_source_questions": plan.get("selected_source_questions") or clipped_scope.get("questions") or [],
    }
    if include_source_content:
        _hydrate_single_source_content(context_plan, source_text)
    semantic_sources = _semantic_batch_context(
        context_plan,
        [item],
        knowledge_mode=is_knowledge_mode,
        include_source_content=include_source_content,
    )
    source_context = semantic_sources if include_source_content else _abstract_generation_context(
        context_plan,
        knowledge_mode=is_knowledge_mode,
    )
    source_heading = "本项绑定来源" if include_source_content else "抽象知识与边界约束（不含来源原文、题面、图片和表格）"
    subject_requirements = _subject_format_requirements(context_plan, [item])
    output_format_requirements = _practice_output_format_requirements()
    task = f"""# 任务

只生成蓝图中的一个计划项对应的题目**草案**（第 {index + 1} 项）。不要生成整套，不要改动其他计划项。

## 模式

{"知识点模拟题" if is_knowledge_mode else "专项练习"} · 生成策略：{strategy}

## {source_heading}

{json.dumps(source_context, ensure_ascii=False, indent=2)}

## 本计划项（必须保留的字段）

{json.dumps(_without_internal_ids(item), ensure_ascii=False, indent=2)}

## 软难度意图（方向而非硬模板）

{json.dumps(_difficulty_intent(item, set_position=index), ensure_ascii=False, indent=2)}

## 用户调整要求

{_clean(payload.get("instruction"), 2000) or "保持训练目标，换一种有效变式。"}

## 约束

- 保持题型为 {target_type}，难度为 {target_diff}
- 保持研究生层级；条件充分、可作答
- 软难度意图只规定防退化边界；自主选择一种最自然的主要机制，最多再加一种辅助机制，并返回不含答案的 difficulty_evidence
- 不得为了迎合难度标签堆叠全部候选机制，也不得用固定推理步数、纯计算量或删减必考知识点替代难度
- {"只参考 batch_index 1 绑定来源的 source_content、图片和约束。" if include_source_content else "不得复述、引用或假设原题题面、教材原文、图片和表格；仅按本计划项的必考知识点与必要约束独立设计。"}
{chr(10).join(f'- {requirement}' for requirement in subject_requirements)}
{chr(10).join(f'- {requirement}' for requirement in output_format_requirements)}
- 只输出包含 exercises 数组的合法 JSON，数组中恰好一题；不要输出任何内部 ID
- 这是草案预览：允许返回待人工确认的题目

## 输出结构

{json.dumps({"exercises": [_exercise_output_contract_for_plan_item(item)]}, ensure_ascii=False, indent=2)}
"""
    messages = [
        {"role": "system", "content": "你是研究生教研专家，只生成蓝图某一计划项的题目草案。只输出合法 JSON。"},
        {"role": "user", "content": _user_content(task, generation_images if include_source_content and _batch_needs_visual_reference(semantic_sources, [item]) else [])},
    ]
    raw = _call_practice_json(
        _practice_generation_client(provider, model),
        messages,
        model=model,
        temperature=0.45,
        thinking=_clean(payload.get("thinking"), 20) or None,
    )
    raw_exercises = []
    for raw_item in raw.get("exercises") or []:
        if isinstance(raw_item, dict):
            restored = dict(raw_item)
            restored["plan_item_id"] = _clean(item.get("plan_item_id"), 100) or f"plan_item_{index + 1:02d}"
            restored["source_question_id"] = source_question_id
            raw_exercises.append(restored)
    normalized = normalize_practice_set(
        {
            "source_analysis": plan.get("source_analysis") or {},
            "blueprint": blueprint,
            "exercises": raw_exercises,
        },
        requested_count=1,
        subject="",
        planned_types=[target_type] if target_type in ALLOWED_TYPES else ["综合题"],
        planned_source_ids=[source_question_id] if source_question_id else [],
        planned_plan_ids=[_clean(item.get("plan_item_id"), 100) or f"plan_item_{index + 1:02d}"],
    )
    draft = normalized["exercises"][0]
    draft.pop("exercise_id", None)
    draft["plan_item_id"] = _clean(item.get("plan_item_id"), 100) or f"plan_item_{index + 1:02d}"
    draft["source_question_id"] = source_question_id
    draft["number"] = index + 1
    quality = recompute_practice_quality({"exercises": [draft]})
    return {
        "draft": draft,
        "plan_index": index,
        "quality": quality,
        "generation": {
            "provider": provider.name,
            "model": model,
            "model_route": "selected_primary",
            "reference_images_attached": bool(generation_images),
            "mode": "single" if single_item_mode else "full",
            "include_source_content_in_generation": include_source_content,
        },
    }
