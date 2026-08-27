from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from ..calculation_consistency import evaluate_simple_numeric_expression
from ..concurrency import model_request_slot, run_limited_concurrent
from ..llm_client import OpenAICompatibleClient

SELECTIVE_REVIEW_VERSION = "answer_book.selective_quality_review.v27"
SELECTIVE_CONTENT_WARNING_CODES = frozenset(
    {"noncalculation_unintegrated_formulas", "high_risk_correctness"}
)
_WRITE_LOCK = threading.RLock()


def _json_list(value: Any) -> list[Any]:
    """Narrow a nullable/untrusted JSON field to a list."""

    return value if isinstance(value, list) else []


def _float_or_nan(value: Any) -> float:
    """Parse an untrusted JSON number without raising into the review path."""

    if value is None:
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _candidate_id(value: dict[str, Any]) -> str:
    existing = str(value.get("candidate_id") or "").strip()
    if existing:
        return existing
    identity = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f"review_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def _normalized_candidate_id(value: dict[str, Any]) -> str:
    return _candidate_id({key: item for key, item in value.items() if key != "candidate_id"})


def collect_selective_review_candidates(
    academic_report: dict[str, Any] | None,
    content_quality_report: dict[str, Any] | None,
    *,
    max_candidates: int,
) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    """Collect only explicitly governed high-risk candidates; never scan everything."""

    candidates: list[dict[str, Any]] = []
    for raw in (academic_report or {}).get("review_candidates", []) or []:
        if not isinstance(raw, dict):
            continue
        candidates.append(
            {
                **raw,
                "candidate_id": _normalized_candidate_id(raw),
                "source": "academic_expression",
                "priority": 100,
            }
        )
    for raw in (content_quality_report or {}).get("warnings", []) or []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip()
        if code not in SELECTIVE_CONTENT_WARNING_CODES:
            continue
        candidate = {
            "question_id": str(raw.get("question_id") or "").strip(),
            "code": code,
            "reason": str(raw.get("message") or code),
            "source": "content_quality",
            "priority": 80,
        }
        candidate["candidate_id"] = _candidate_id(candidate)
        candidates.append(candidate)
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        unique.setdefault(str(candidate["candidate_id"]), candidate)
    ordered = sorted(
        unique.values(),
        key=lambda item: (-int(item.get("priority", 0)), str(item.get("question_id") or ""), str(item["candidate_id"])),
    )
    limit = max(0, int(max_candidates))
    selected = ordered[:limit]
    return selected, {
        "eligible_count": len(ordered),
        "selected_count": len(selected),
        "truncated": len(ordered) > len(selected),
    }


def _question_context(structured_exam: dict[str, Any], qids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for question in structured_exam.get("items", []) or []:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        if qid not in qids:
            continue
        required_answer_units: list[dict[str, str]] = []
        for subquestion in question.get("subquestions", []) or []:
            if not isinstance(subquestion, dict):
                continue
            requirements = [
                item
                for item in subquestion.get("requirements", []) or []
                if isinstance(item, dict)
            ]
            leaves = requirements or [subquestion]
            for leaf in leaves:
                number = str(leaf.get("number") or "").strip()
                stem = str(leaf.get("stem") or leaf.get("raw") or "").strip()
                if number and stem:
                    required_answer_units.append({"number": number, "stem": stem})
        if not required_answer_units:
            required_answer_units.append(
                {
                    "number": str(question.get("number") or "").strip(),
                    "stem": str(question.get("stem") or "").strip(),
                }
            )
        raw_understanding = question.get("question_understanding")
        understanding: dict[str, Any] = raw_understanding if isinstance(raw_understanding, dict) else {}
        visual_facts: list[dict[str, Any]] = []
        for image in _json_list(understanding.get("images")):
            if not isinstance(image, dict):
                continue
            visual_facts.append(
                {
                    "image_id": str(image.get("image_id") or ""),
                    "visual_description": str(image.get("visual_description") or "")[:1500],
                    "detected_labels": [str(value)[:120] for value in (image.get("detected_labels") or [])[:30]],
                    "axes": image.get("axes") if isinstance(image.get("axes"), dict) else {},
                    "data_points": [str(value)[:300] for value in (image.get("data_points") or [])[:20]],
                    "invariant_horizontal_lines": [
                        value for value in (image.get("invariant_horizontal_lines") or [])[:20]
                        if isinstance(value, dict)
                    ],
                    "unit_cell_site_families": [
                        value for value in (image.get("unit_cell_site_families") or [])[:20]
                        if isinstance(value, dict)
                    ],
                    "fixed_condition_phase_paths": [
                        value for value in (image.get("fixed_condition_phase_paths") or [])[:10]
                        if isinstance(value, dict)
                    ],
                    "answer_relevant_observations": [
                        str(value)[:500] for value in (image.get("answer_relevant_observations") or [])[:20]
                    ],
                    "uncertainties": [str(value)[:300] for value in (image.get("uncertainties") or [])[:10]],
                }
            )
        rows.append(
            {
                "question_id": qid,
                "stem": question.get("stem") or "",
                "question_type": question.get("question_type") or "",
                # This is the only scope checklist the reviewer may use.  It is
                # derived from the confirmed exam structure, never textbooks.
                "required_answer_units": required_answer_units,
                # Calculations can depend on values read from an attached phase
                # diagram, graph, table, circuit, map, or geometry figure. The
                # reviewer must receive those already-confirmed structured facts
                # rather than trying to infer them from answer text.
                "confirmed_visual_facts": visual_facts,
            }
        )
    return rows


def _fragment_context(fragments_data: dict[str, Any], qids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fragment in fragments_data.get("fragments", []) or []:
        if not isinstance(fragment, dict):
            continue
        qid = str(fragment.get("question_id") or "").strip()
        if qid not in qids:
            continue
        coverage_manifest = []
        formulas_by_id = {
            str(item.get("formula_id") or ""): str(item.get("latex") or "")
            for item in fragment.get("formulas", []) or []
            if isinstance(item, dict)
        }
        visible_blocks: list[dict[str, Any]] = []
        visible_budget = 8000
        for block in fragment.get("blocks", []) or []:
            if not isinstance(block, dict) or visible_budget <= 0:
                continue
            pieces: list[str] = []
            for segment in block.get("segments", []) or []:
                if not isinstance(segment, dict):
                    continue
                if segment.get("type") == "text":
                    pieces.append(str(segment.get("text") or ""))
                elif segment.get("type") == "formula_ref":
                    pieces.append(formulas_by_id.get(str(segment.get("formula_id") or ""), ""))
            text = "".join(pieces).strip()[:visible_budget]
            if text:
                visible_blocks.append({"label": str(block.get("label") or ""), "text": text})
                visible_budget -= len(text)
        for unit in fragment.get("answer_units", []) or []:
            if not isinstance(unit, dict):
                continue
            coverage_manifest.append(
                {
                    "number": str(unit.get("number") or "").strip(),
                    "answer": str(unit.get("answer") or ""),
                    "analysis_texts": [
                        str(item.get("text") or "")
                        for item in unit.get("analysis_segments", []) or []
                        if isinstance(item, dict)
                    ],
                    "step_texts": [
                        {
                            "text": str(item.get("text") or ""),
                            "result_text": str(item.get("result_text") or ""),
                        }
                        for item in unit.get("steps", []) or []
                        if isinstance(item, dict)
                    ],
                    "has_figure_spec": bool(unit.get("figure_specs") or unit.get("drawing_code_specs")),
                }
            )
        rows.append(
            {
                "question_id": qid,
                "answer": fragment.get("answer") or "",
                "answer_summary": fragment.get("answer_summary") or "",
                "formulas": fragment.get("formulas") or [],
                "calculation_contract": fragment.get("calculation_contract") or {},
                "coverage_manifest": coverage_manifest,
                # Single-part short-answer questions often carry their actual
                # causal argument in blocks rather than answer_units.  The
                # correctness reviewer must see that argument, not only the
                # one-line conclusion.
                "visible_blocks": visible_blocks,
            }
        )
    return rows


def _balanced_evidence_rows(items: list[dict[str, Any]], *, max_items: int = 16) -> list[dict[str, Any]]:
    """Preserve coverage across knowledge points under a bounded review prompt.

    Retrieval candidates are grouped by knowledge point.  Taking the first N
    rows globally lets an early broad topic consume the entire review budget and
    can hide direct evidence for a later numerical conclusion.  Round-robin
    selection keeps the prompt bounded while guaranteeing topical diversity.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        group = str(item.get("knowledge_point") or "未分组证据").strip()
        grouped.setdefault(group, []).append(item)
    for group_items in grouped.values():
        group_items.sort(
            key=lambda item: (
                str(item.get("source_type") or "").lower() not in {"text", "text_block", "equation", "equation_block"},
                not bool(item.get("verified_page")),
                -float(item.get("score") or 0.0),
            )
        )
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < max(0, int(max_items)):
        added = False
        for group_items in grouped.values():
            if depth < len(group_items):
                selected.append(group_items[depth])
                added = True
                if len(selected) >= max_items:
                    break
        if not added:
            break
        depth += 1
    return selected


def _fingerprint(
    *,
    candidates: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    fragments: list[dict[str, Any]],
    evidence: dict[str, list[dict[str, Any]]],
    provider_name: str,
    model: str,
) -> str:
    payload = {
        "version": SELECTIVE_REVIEW_VERSION,
        "candidates": candidates,
        "questions": questions,
        "fragments": fragments,
        "evidence": evidence,
        "provider": provider_name,
        "model": model,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _read_cached(path: Path, fingerprint: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("input_fingerprint") != fingerprint:
        return None
    # Service outages, quota errors, and protocol failures are transient runtime
    # states.  Reusing them would turn one failed request into a permanent loss
    # of semantic review for every identical rerun.
    if str(value.get("status") or "") in {"degraded", "shadow_only"}:
        return None
    cached = dict(value)
    cached["cache"] = {"hit": True, "content_addressed": True}
    cached["remote_model_calls_this_run"] = 0
    return cached


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _WRITE_LOCK:
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _compact_text(value: Any) -> str:
    text = "".join(str(value or "").split()).lower()
    return (
        text.replace(r"\%", "%")
        .replace("₃", "3")
        .replace("₂", "2")
        .replace("₁", "1")
        .replace("（", "(")
        .replace("）", ")")
        .replace("：", ":")
    )


def _compact_formula_quote(value: Any) -> str:
    """Canonicalize equivalent Unicode/LaTeX notation for quote grounding."""

    # The answer corpus is JSON-serialized, so LaTeX backslashes are escaped.
    text = str(value or "").replace(r"\\", "\\")
    replacements = {
        r"\Delta": "Δ",
        r"\times": "×",
        r"\cdot": "·",
        r"\mathrm": "",
        r"\text": "",
        r"\operatorname": "",
        r"\left": "",
        r"\right": "",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\\(?:,|;|!|quad|qquad)\s*", "", text)
    text = text.replace("{", "").replace("}", "").replace("$", "")
    return _compact_text(text)


_NUMERIC_REPAIR_RE = re.compile(
    r"(?:[-+]?\d+\.\d+|[-+]?\d+\s*%|=\s*[-+]?\d+|(?:应为|改为|结果为)\s*[-+]?\d+|"
    r"分数|百分比|组成|比例|质量比|数值|守恒|杠杆|计算错误|fraction|percentage|ratio|numeric|conservation)",
    re.IGNORECASE,
)
_PARTITION_REPAIR_RE = re.compile(
    r"(?:组成|分配|概率分布|总体|加和|守恒|composition|allocation|distribution|conservation)",
    re.IGNORECASE,
)
_LINEAGE_REPAIR_RE = re.compile(
    r"(?:析出|沉淀|剩余|余量|剩余量|父项|前驱|转移|损失|分解|"
    r"precipitat|remain|parent|precursor|transfer|loss|split|decompos)",
    re.IGNORECASE,
)


def _numeric_patch_issues(
    value: Any,
    *,
    require_partition: bool = False,
    require_transition: bool = False,
    source_corpus: str = "",
    current_contract: Any = None,
) -> list[str]:
    """Validate a reviewer's proposed numerical replacement before it can mutate an answer."""

    if not isinstance(value, dict):
        return ["numeric_patch_missing"]
    issues: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for key in ("result_quantities", "intermediate_quantities"):
        rows = _json_list(value.get(key))
        for row in rows:
            if not isinstance(row, dict):
                issues.append(f"numeric_patch_invalid_{key}")
                continue
            quantity_id = str(row.get("quantity_id") or "").strip()
            amount = _float_or_nan(row.get("value"))
            if not quantity_id or quantity_id in by_id or not math.isfinite(amount):
                issues.append(f"numeric_patch_invalid_quantity:{quantity_id or 'missing'}")
                continue
            by_id[quantity_id] = {**row, "value": amount}
    if not by_id:
        issues.append("numeric_patch_missing_quantities")

    def close(left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=0.012, abs_tol=0.0015)

    partitions = _json_list(value.get("partitions"))
    if require_partition and not partitions:
        issues.append("numeric_patch_missing_partition")
    for index, partition in enumerate(partitions, start=1):
        if not isinstance(partition, dict):
            issues.append(f"numeric_patch_invalid_partition:{index}")
            continue
        ids = [str(item or "").strip() for item in partition.get("component_quantity_ids", []) or []]
        rows = [by_id.get(quantity_id) for quantity_id in ids]
        if len(ids) < 2 or any(row is None for row in rows):
            issues.append(f"numeric_patch_invalid_partition_components:{index}")
            continue
        bases = {str(row.get("basis") or "").strip() for row in rows if row is not None}
        if len(bases) > 1:
            issues.append(f"numeric_patch_mixed_partition_basis:{index}")
        expected = _float_or_nan(partition.get("expected_total", 1.0))
        total = sum(float(row["value"]) for row in rows if row is not None)
        if not math.isfinite(expected) or not close(total, expected):
            issues.append(f"numeric_patch_partition_sum_mismatch:{index}")

    transitions = _json_list(value.get("transitions"))
    if require_transition and not transitions:
        issues.append("numeric_patch_missing_transition_lineage")
    for index, transition in enumerate(transitions, start=1):
        if not isinstance(transition, dict):
            issues.append(f"numeric_patch_invalid_transition:{index}")
            continue
        parent = by_id.get(str(transition.get("parent_quantity_id") or "").strip())
        product_ids = [str(item or "").strip() for item in transition.get("product_quantity_ids", []) or []]
        products = [by_id.get(quantity_id) for quantity_id in product_ids]
        if parent is None or len(product_ids) < 1 or any(row is None for row in products):
            issues.append(f"numeric_patch_invalid_transition_lineage:{index}")
            continue
        bases = {str(row.get("basis") or "").strip() for row in [parent, *products] if row is not None}
        if len(bases) > 1:
            issues.append(f"numeric_patch_mixed_transition_basis:{index}")
        if not close(sum(float(row["value"]) for row in products if row is not None), float(parent["value"])):
            issues.append(f"numeric_patch_transition_conservation_mismatch:{index}")
        derived_id = str(transition.get("derived_quantity_id") or "").strip()
        if derived_id or transition.get("local_fraction") is not None:
            fraction = _float_or_nan(transition.get("local_fraction"))
            derived = by_id.get(derived_id)
            if derived_id not in product_ids or derived is None or not math.isfinite(fraction):
                issues.append(f"numeric_patch_invalid_derived_quantity:{index}")
            else:
                fraction = fraction / 100.0 if fraction > 1.0 else fraction
                if not close(float(derived["value"]), float(parent["value"]) * fraction):
                    issues.append(f"numeric_patch_transition_derivation_mismatch:{index}")

    current_rows = (
        [item for item in current_contract.get("result_quantities", []) or [] if isinstance(item, dict)]
        if isinstance(current_contract, dict)
        else []
    )
    changed_ids: set[str] = set()
    for item in _json_list(value.get("result_quantities")):
        if not isinstance(item, dict):
            continue
        proposed_value = _float_or_nan(item.get("value"))
        if not math.isfinite(proposed_value):
            continue
        proposed_name = _compact_text(item.get("name"))
        same = any(
            proposed_name == _compact_text(current.get("name"))
            and close(proposed_value, _float_or_nan(current.get("value")))
            for current in current_rows
            if current.get("value") is not None
        )
        if not same and abs(proposed_value) > 1e-9:
            changed_ids.add(str(item.get("quantity_id") or "").strip())

    derivations = _json_list(value.get("derivations"))
    derivation_by_id = {
        str(item.get("quantity_id") or "").strip(): item
        for item in derivations
        if isinstance(item, dict) and str(item.get("quantity_id") or "").strip()
    }
    for quantity_id in sorted(changed_ids):
        derivation = derivation_by_id.get(quantity_id)
        if not isinstance(derivation, dict):
            issues.append(f"numeric_patch_missing_derivation:{quantity_id}")
            continue
        expression = str(derivation.get("expression") or "").strip()
        computed = evaluate_simple_numeric_expression(expression)
        if computed is None:
            issues.append(f"numeric_patch_invalid_derivation_expression:{quantity_id}")
            continue
        proposed_value = float(by_id[quantity_id]["value"])
        if not (
            close(computed, proposed_value)
            or close(computed * 100.0, proposed_value)
            or close(computed, proposed_value * 100.0)
        ):
            issues.append(f"numeric_patch_derivation_result_mismatch:{quantity_id}")
        source_quotes = [
            str(quote or "").strip()
            for quote in derivation.get("source_quotes", []) or []
            if str(quote or "").strip()
        ]
        if not source_quotes:
            issues.append(f"numeric_patch_missing_derivation_source:{quantity_id}")
            continue
        if any(_compact_text(quote) not in _compact_text(source_corpus) for quote in source_quotes):
            issues.append(f"numeric_patch_unverified_derivation_source:{quantity_id}")
            continue
        quoted_numbers = {
            token.lstrip("+-")
            for quote in source_quotes
            for token in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", quote)
        }
        expression_numbers = {
            token.lstrip("+-")
            for token in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", expression)
        }
        unsupported_numbers = {
            token
            for token in expression_numbers
            if token not in quoted_numbers and token not in {"1", "100"}
        }
        if unsupported_numbers:
            issues.append(f"numeric_patch_unanchored_derivation_inputs:{quantity_id}")
    return list(dict.fromkeys(issues))


def _numeric_patch_changes_results(value: Any, current_contract: Any) -> bool:
    """Return whether a validated proposal actually changes a positive result.

    Reviewers sometimes re-submit the current values, add a zero-valued
    component, and still call the answer incorrect.  Such a proposal contains
    no executable correction and must not trigger another semantic repair.
    """

    if not isinstance(value, dict) or not isinstance(current_contract, dict):
        return True
    proposed = [item for item in _json_list(value.get("result_quantities")) if isinstance(item, dict)]
    current = [item for item in _json_list(current_contract.get("result_quantities")) if isinstance(item, dict)]
    comparable_proposed: list[tuple[str, float]] = []
    comparable_current: list[tuple[str, float]] = []
    for target, rows in ((comparable_proposed, proposed), (comparable_current, current)):
        for item in rows:
            amount = _float_or_nan(item.get("value"))
            if not math.isfinite(amount):
                continue
            # A newly invented 0% bucket neither repairs a stated positive
            # result nor changes the rendered answer.
            if math.isfinite(amount) and abs(amount) > 1e-9:
                target.append((_compact_text(item.get("name")), amount))
    if not comparable_proposed:
        return False
    for proposed_name, proposed_value in comparable_proposed:
        if not any(
            proposed_name == current_name
            and math.isclose(proposed_value, current_value, rel_tol=0.012, abs_tol=0.0015)
            for current_name, current_value in comparable_current
        ):
            return True
    return False


def _normalized_decisions(
    response: dict[str, Any],
    candidate_ids: set[str],
    validation_context: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in response.get("decisions", []) if isinstance(response.get("decisions"), list) else []:
        if not isinstance(raw, dict):
            continue
        candidate_id = str(raw.get("candidate_id") or "").strip()
        decision = str(raw.get("decision") or "").strip().lower()
        if candidate_id not in candidate_ids or candidate_id in seen or decision not in {"pass", "warn", "repair"}:
            continue
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0) or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        defects = []
        rejected_defects: list[dict[str, Any]] = []
        for defect in raw.get("defects", []) if isinstance(raw.get("defects"), list) else []:
            if not isinstance(defect, dict):
                continue
            answer_unit_number = str(defect.get("answer_unit_number") or "").strip()
            defect_kind = str(defect.get("defect_kind") or "").strip().lower()
            requirement_quote = str(defect.get("requirement_quote") or "").strip()
            current_answer_quote = str(defect.get("current_answer_quote") or "").strip()
            evidence_quote = str(defect.get("evidence_quote") or "").strip()
            missing_output_quote = str(defect.get("missing_output_quote") or "").strip()
            raw_context = (validation_context or {}).get(candidate_id)
            context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
            raw_required_units = context.get("required_units")
            required_units: dict[str, Any] = raw_required_units if isinstance(raw_required_units, dict) else {}
            requirement_stem = str(required_units.get(answer_unit_number) or "")
            answer_corpus = str(context.get("answer_corpus") or "")
            evidence_corpus = str(context.get("evidence_corpus") or "")
            if not requirement_stem and requirement_quote and validation_context:
                matched_units = [
                    (number, stem)
                    for number, stem in required_units.items()
                    if _compact_text(requirement_quote) in _compact_text(stem)
                    or _compact_text(stem) in _compact_text(requirement_quote)
                ]
                if len(matched_units) == 1:
                    answer_unit_number, requirement_stem = matched_units[0]
            if requirement_stem and requirement_quote and (
                _compact_text(requirement_quote) not in _compact_text(requirement_stem)
            ):
                # The model identified a valid leaf but paraphrased its stem.
                # Store the authoritative stem instead of throwing away an
                # otherwise concrete defect.
                requirement_quote = requirement_stem
            valid = bool(
                answer_unit_number
                and defect_kind in {"incorrect", "missing", "contradictory"}
                and requirement_quote
                and (not validation_context or (
                    requirement_stem
                    and _compact_text(requirement_quote) in _compact_text(requirement_stem)
                ))
            )
            if defect_kind == "missing":
                valid = valid and bool(missing_output_quote)
                if validation_context:
                    valid = valid and (
                        _compact_text(missing_output_quote) in _compact_text(requirement_stem)
                        and _compact_text(missing_output_quote) not in _compact_text(answer_corpus)
                    )
                    if answer_unit_number in set(context.get("figure_units") or set()) and any(
                        token in requirement_stem for token in ("画", "图", "绘制", "示意")
                    ):
                        valid = False
            else:
                valid = valid and bool(current_answer_quote)
                if validation_context:
                    valid = valid and _compact_formula_quote(current_answer_quote) in _compact_formula_quote(answer_corpus)
            # Absence of an evidence quote is not evidence verification. This
            # distinction matters when a reviewer proposes overturning a
            # machine-consistent numeric ledger.
            evidence_quote_verified = False
            if valid and evidence_quote and validation_context:
                evidence_quote_verified = _compact_text(evidence_quote) in _compact_text(evidence_corpus)
                if not evidence_quote_verified:
                    evidence_quote = ""
            if valid:
                defects.append(
                    {
                        "answer_unit_number": answer_unit_number,
                        "defect_kind": defect_kind,
                        "requirement_quote": requirement_quote[:500],
                        "current_answer_quote": current_answer_quote[:800],
                        "evidence_quote": evidence_quote[:800],
                        "missing_output_quote": missing_output_quote[:300],
                        "evidence_quote_verified": evidence_quote_verified,
                    }
                )
            else:
                rejected_defects.append(
                    {
                        "answer_unit_number": answer_unit_number,
                        "defect_kind": defect_kind,
                        "requirement_quote": requirement_quote[:500],
                        "current_answer_quote": current_answer_quote[:800],
                        "reason": "atomic_defect_not_grounded_in_confirmed_question_or_current_answer",
                    }
                )
        reason = str(raw.get("reason") or "").strip()[:1000]
        suggested_fix = str(raw.get("suggested_fix") or "").strip()[:1000]
        numeric_patch = raw.get("proposed_calculation_contract")
        compact_reason = _compact_text(reason)
        reviewer_admits_answer_is_correct = any(
            marker in compact_reason
            for marker in (
                "answerisactuallycorrect",
                "answeriscorrect",
                "finalnumbersarecorrect",
                "currentansweriscorrect",
                "答案实际正确",
                "答案是正确的",
                "当前答案正确",
                "最终数值正确",
            )
        )
        if decision == "repair" and reviewer_admits_answer_is_correct:
            rejected_defects.extend(
                {
                    **defect,
                    "reason": "reviewer_reason_admits_current_answer_is_correct",
                }
                for defect in defects
            )
            decision = "pass"
            defects = []
            suggested_fix = ""
        context = (validation_context or {}).get(candidate_id, {})
        numeric_repair = bool(
            context.get("has_numeric_calculation_contract", context.get("has_calculation_contract"))
            and _NUMERIC_REPAIR_RE.search(
                " ".join(
                    [
                        reason,
                        suggested_fix,
                        *[str(item.get("current_answer_quote") or "") for item in defects],
                    ]
                )
            )
        )
        repair_claim_text = " ".join(
            [
                reason,
                suggested_fix,
                *[str(item.get("requirement_quote") or "") for item in defects],
                *[str(item.get("current_answer_quote") or "") for item in defects],
            ]
        )
        numeric_patch_validation_issues = (
            _numeric_patch_issues(
                numeric_patch,
                require_partition=bool(_PARTITION_REPAIR_RE.search(repair_claim_text)),
                require_transition=bool(_LINEAGE_REPAIR_RE.search(repair_claim_text)),
                source_corpus=str(context.get("source_corpus") or ""),
                current_contract=context.get("calculation_contract"),
            )
            if numeric_repair
            else []
        )
        if (
            numeric_repair
            and not numeric_patch_validation_issues
            and not _numeric_patch_changes_results(numeric_patch, context.get("calculation_contract"))
        ):
            numeric_patch_validation_issues.append("numeric_patch_no_effective_result_change")
        if decision == "repair" and numeric_patch_validation_issues:
            # Reject the numerical proposal, not a separately grounded atomic
            # defect. The repairer must re-derive a complete ledger and pass
            # deterministic postconditions plus the independent recheck.
            confidence = 0.0
            reason = "reviewer_numeric_patch_failed_machine_validation:" + ",".join(numeric_patch_validation_issues)
            has_verified_evidence = any(
                bool(defect.get("evidence_quote")) and defect.get("evidence_quote_verified") is True
                for defect in defects
            )
            if not has_verified_evidence:
                # An unsupported reviewer opinion with an invalid numeric
                # contract has no authority to overturn a complete,
                # machine-consistent answer. Preserve it as rejected review
                # diagnostics, but do not trigger repeated repair calls or
                # downgrade the user's deliverable.
                rejected_defects.extend(
                    {
                        **defect,
                        "reason": "numeric_repair_lacks_verified_evidence_and_machine_valid_contract",
                    }
                    for defect in defects
                )
                decision = "pass"
                defects = []
                suggested_fix = ""
        if decision == "repair" and not defects:
            decision = "warn"
            confidence = 0.0
            reason = "reviewer_repair_missing_atomic_defect_evidence"
            suggested_fix = ""
        decisions.append(
            {
                "candidate_id": candidate_id,
                "decision": decision,
                "confidence": confidence,
                "reason": reason,
                "suggested_fix": suggested_fix,
                "defects": defects,
                "rejected_defects": rejected_defects,
                "proposed_calculation_contract": numeric_patch if numeric_repair and not numeric_patch_validation_issues else {},
                "numeric_patch_validation_issues": numeric_patch_validation_issues,
                "reviewer_repair_rejected": bool(
                    numeric_patch_validation_issues and decision == "pass"
                ),
            }
        )
        seen.add(candidate_id)
    for missing in sorted(candidate_ids - seen):
        decisions.append(
            {
                "candidate_id": missing,
                "decision": "warn",
                "confidence": 0.0,
                "reason": "reviewer_did_not_return_a_valid_decision",
                "suggested_fix": "",
                "defects": [],
            }
        )
    return decisions


def _decision_validation_context(
    candidates: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    fragments: list[dict[str, Any]],
    evidence: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    question_by_id = {str(item.get("question_id") or ""): item for item in questions}
    fragment_by_id = {str(item.get("question_id") or ""): item for item in fragments}
    contexts: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        qid = str(candidate.get("question_id") or "")
        question = question_by_id.get(qid, {})
        fragment = fragment_by_id.get(qid, {})
        contexts[candidate_id] = {
            "required_units": {
                str(item.get("number") or ""): str(item.get("stem") or "")
                for item in question.get("required_answer_units", []) or []
                if isinstance(item, dict)
            },
            "answer_corpus": json.dumps(fragment, ensure_ascii=False),
            "figure_units": {
                str(unit.get("number") or "")
                for unit in fragment.get("coverage_manifest", []) or []
                if isinstance(unit, dict) and unit.get("has_figure_spec")
            },
            "evidence_corpus": "\n".join(
                str(item.get("evidence_text") or "")
                for item in evidence.get(qid, []) or []
                if isinstance(item, dict)
            ),
            "has_calculation_contract": bool(fragment.get("calculation_contract")),
            "has_numeric_calculation_contract": any(
                math.isfinite(_float_or_nan(item.get("value")))
                for item in (fragment.get("calculation_contract") or {}).get("result_quantities", []) or []
                if isinstance(item, dict)
            ),
            "calculation_contract": fragment.get("calculation_contract") or {},
            "source_corpus": json.dumps(question, ensure_ascii=False)
            + "\n"
            + "\n".join(
                str(item.get("evidence_text") or "")
                for item in evidence.get(qid, []) or []
                if isinstance(item, dict)
            ),
        }
    return contexts


def _warning_rows(candidates: list[dict[str, Any]], decisions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(candidate["candidate_id"]): candidate for candidate in candidates}
    warnings: list[dict[str, Any]] = []
    for decision in decisions:
        if decision["decision"] == "pass":
            continue
        candidate = by_id.get(str(decision["candidate_id"]), {})
        warnings.append(
            {
                "question_id": str(candidate.get("question_id") or ""),
                "code": "repair_suggested" if decision["decision"] == "repair" else "semantic_risk",
                "message": decision["reason"] or str(candidate.get("reason") or "选择性 AI 复核发现风险。"),
                "candidate_id": decision["candidate_id"],
                "confidence": decision["confidence"],
                "suggested_fix": decision["suggested_fix"],
            }
        )
    return warnings


def review_selective_quality(
    *,
    academic_report: dict[str, Any],
    content_quality_report: dict[str, Any],
    structured_exam: dict[str, Any],
    fragments_data: dict[str, Any],
    report_json: Path,
    provider: Any | None,
    model: str,
    max_candidates: int = 8,
    max_batches: int = 1,
    max_attempts_per_batch: int = 1,
    enabled: bool = True,
    shadow_only: bool = False,
    client: Any | None = None,
    evidence_context: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    candidates, selection = collect_selective_review_candidates(
        academic_report,
        content_quality_report,
        max_candidates=max_candidates,
    )
    qids = {str(candidate.get("question_id") or "") for candidate in candidates}
    questions = _question_context(structured_exam, qids)
    fragments = _fragment_context(fragments_data, qids)
    evidence = {
        qid: [
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "knowledge_point": str(item.get("knowledge_point") or ""),
                "source_type": str(item.get("source_type") or ""),
                "citation_textbook": str(item.get("citation_textbook") or item.get("textbook") or ""),
                "printed_page": str(item.get("printed_page") or ""),
                "evidence_text": str(item.get("evidence_text") or "")[:1200],
            }
            for item in _balanced_evidence_rows((evidence_context or {}).get(qid, []), max_items=12)
            if isinstance(item, dict)
        ]
        for qid in qids
    }
    provider_name = str(getattr(provider, "name", "") or "")
    selected_model = str(model or getattr(provider, "default_model", "") or "")
    fingerprint = _fingerprint(
        candidates=candidates,
        questions=questions,
        fragments=fragments,
        evidence=evidence,
        provider_name=provider_name,
        model=selected_model,
    )
    cached = _read_cached(report_json, fingerprint)
    if cached is not None:
        return cached
    base: dict[str, Any] = {
        "schema_version": SELECTIVE_REVIEW_VERSION,
        "mode": "selective_batched_ai",
        "human_review_required": False,
        "input_fingerprint": fingerprint,
        "provider": provider_name,
        "model": selected_model,
        "selection": selection,
        "candidates": candidates,
        "batch_count": 0,
        "remote_model_calls": 0,
        "remote_model_calls_this_run": 0,
        "request_budget": {
            "max_batches": max(0, int(max_batches)),
            "max_attempts_per_batch": max(1, min(2, int(max_attempts_per_batch))),
        },
        "cache": {"hit": False, "content_addressed": True},
        "issues": [],
        "warnings": [],
        "decisions": [],
    }
    if not candidates:
        base.update({"status": "not_needed", "ok": True})
        _write_json_atomic(report_json, base)
        return base
    if shadow_only:
        base.update(
            {
                "status": "shadow_only",
                "ok": True,
                "shadow_only": True,
                "warnings": [],
            }
        )
        _write_json_atomic(report_json, base)
        return base
    configured = bool(provider is not None and getattr(provider, "api_key", "") and selected_model)
    if not enabled or max_batches <= 0 or not configured:
        reason = "disabled_or_budget_zero" if not enabled or max_batches <= 0 else "provider_not_configured"
        base.update(
            {
                "status": "degraded",
                "ok": True,
                "degraded_reason": reason,
                "warnings": [
                    {
                        "question_id": str(candidate.get("question_id") or ""),
                        "code": "reviewer_unavailable",
                        "message": f"选择性 AI 复核未执行：{reason}；已保留本地风险标记。",
                        "candidate_id": candidate["candidate_id"],
                    }
                    for candidate in candidates
                ],
            }
        )
        _write_json_atomic(report_json, base)
        return base
    payload = {
        "task": "review_selected_academic_quality_risks",
        "candidates": candidates,
        "questions": questions,
        "current_answers": fragments,
        "confirmed_textbook_evidence_by_question": evidence,
        "output_schema": {
            "decisions": [
                {
                    "candidate_id": "must match input",
                    "decision": "pass|warn|repair",
                    "confidence": 0.0,
                    "reason": "concise evidence-based reason",
                    "suggested_fix": "only when repair is selected",
                    "defects": [
                        {
                            "answer_unit_number": "must match required_answer_units.number",
                            "defect_kind": "incorrect|missing|contradictory",
                            "requirement_quote": "exact quote from that required answer unit stem",
                            "current_answer_quote": "exact quote from coverage_manifest when incorrect or contradictory; empty only for missing",
                            "evidence_quote": "short exact quote from confirmed evidence, or empty when the defect is directly checkable from the question/answer",
                            "missing_output_quote": "for missing only: the shortest exact phrase in requirement_quote that names the allegedly missing output; empty otherwise",
                        }
                    ],
                    "proposed_calculation_contract": {
                        "result_quantities": [{"quantity_id": "q1", "name": "final result", "value": 0.5, "basis": "one explicit global basis"}],
                        "intermediate_quantities": [{"quantity_id": "i1", "name": "pre-change parent", "value": 0.6, "basis": "same global basis"}],
                        "partitions": [{"component_quantity_ids": ["q1", "q2"], "expected_total": 1.0}],
                        "transitions": [{"transition_id": "t1", "parent_quantity_id": "i1", "product_quantity_ids": ["q1", "q2"], "derived_quantity_id": "q2", "local_fraction": 0.2}],
                        "derivations": [{"quantity_id": "q1", "expression": "(60-40)/(60-30)", "source_quotes": ["exact question/visual/evidence text containing 60, 40, and 30"]}],
                    },
                }
            ]
        },
        "hard_rules": [
            "Return exactly one JSON object.",
            "Return exactly one decision for every candidate_id and do not invent ids.",
            "Judge only the selected risk against the question and current answer.",
            "For high_risk_correctness candidates, check every requested answer unit, numerical result, physical/disciplinary conclusion, and drawing requirement against the confirmed textbook evidence. Use pass only when the answer is materially correct and complete; use repair for a concrete defect; use warn only when the supplied evidence is genuinely insufficient.",
            "Build the requirement checklist only from the question stem and its confirmed answer units. Evidence supports or contradicts an answer; it never expands what the question asks. Do not demand a related output merely because the textbook discusses it. For example, a request for structure composition is not also a request for phase composition unless the question explicitly asks for both.",
            "Use required_answer_units as the authoritative scope and coverage_manifest as the authoritative record of what the current answer already contains. Search answer, analysis_texts, step_texts/result_texts, formulas, and calculation_contract before claiming anything is missing.",
            "For a question asking whether a proposition is necessary, inevitable, or possible and why, verify both the yes/no conclusion and the causal explanation in visible_blocks. A correct one-line conclusion does not pass when its explanation reverses the physical, mathematical, or disciplinary condition.",
            "This review runs before figure rendering. For a drawing answer unit, has_figure_spec=true means the drawing requirement is structurally covered; do not demand an already rendered image at this stage. You may still repair a concrete semantic defect in the specification.",
            "For any claimed exhaustive composition, fraction, allocation, probability distribution, or ratio converted from such a partition, verify that all components use the same basis and that their fractions sum to the declared whole. A set of quantities from different nested bases must not be presented as one partition.",
            "For every multi-stage calculation, verify the semantic identity and basis of each multiplier, not only its arithmetic. A fraction of a phase inside a precursor is not automatically the fraction of the whole transformed constituent. If confirmed evidence states that all of a precursor transforms into a named product, do not replace that whole-product fraction with a nested phase fraction.",
            "For every numerical or symbolic calculation, independently reconstruct the governing equations from the question's stated conditions before comparing with the current answer. Check every boundary, equilibrium, conservation, sign, and endpoint condition; do not assume the current formula chain chose the correct physical or disciplinary model merely because its arithmetic is internally consistent.",
            "Recompute the requested final quantities from that independent equation set. Explicitly test whether phrases such as final equilibrium, constant external condition, reversible/irreversible, open/closed/isolated, steady state, or limiting state impose an additional equation. A missing governing condition is an incorrect derivation, not a rounding difference.",
            "For every final composition partition, trace each precursor through every explicitly stated split, precipitation, reaction, transfer, loss, or transformation in the confirmed evidence and current derivation. A precursor fraction may be carried forward unchanged only when it transforms wholly into one final constituent. If it first produces a nonzero constituent and only the remainder transforms, the final partition must retain both products (or explicitly justify grouping them on the same declared basis). A numerically normalized partition is still incorrect when it silently drops such an intermediate product by renaming the whole precursor as the remainder product.",
            "For every derived constituent, verify the identity of its parent precursor in the formula: the local fraction must multiply the global amount of that exact precursor, not a different coexisting constituent. Then verify that the reported remainder of the parent equals parent minus derived amount. A partition can sum to 100% and still fail this lineage check.",
            "When a question depends on an attached diagram, graph, table, map, circuit, or geometric figure, treat questions[].confirmed_visual_facts as confirmed question input. Use its data_points and answer_relevant_observations in calculations; respect any listed uncertainty. Do not ignore visual input merely because the prose stem omits its numbers.",
            "If the current answer names a phase, region, object, component, or state that directly contradicts a confirmed fixed-condition visual fact (for example a phase outside the stated isotherm), return repair, not warn. Uncertainty in an axis spelling or approximate tick value does not excuse a categorical phase/region contradiction.",
            "For crystal unit cells, derive lattice claims from unit_cell_site_families plus stated coordinates. Do not infer face-center sites from projected overlap, and distinguish a Bravais lattice from a conventional-cell drawing with a multi-atom basis.",
            "Distinguish phases from microstructural constituents. A named composite constituent may remain one morphological constituent even when its internal phase fractions change. If a new constituent precipitates from a precursor outside that composite, compute it from that exact precursor and subtract it from the precursor remainder; do not relabel the composite's entire mass as one of its internal phases.",
            "Do not infer taxonomic membership, parentage, source, or ledger ownership from a purely spatial/observational relation. Evidence that X is attached to, coated on, embedded in, adjacent to, or hard to distinguish from Y does not by itself mean X belongs inside Y. If evidence states that X forms from multiple precursors, preserve those origins; when X is not separately counted at the requested top level, say only that it is not independently resolved/countable at that level unless the evidence explicitly assigns it to one named constituent.",
            "Generic conservation template for organization composition: if the pre-change organization is precursor A with global fraction p plus composite constituent E with global fraction e, and a new constituent X precipitates only from A with global amount x, the final organization partition is remaining A=(p-x), X=x, and composite E=e. Do not replace E by one of its internal phases, and do not compute x from e. Separately, a phase-composition partition may split every constituent into internal phases on its own declared basis.",
            "When calculation_contract contains intermediate_quantities and transitions, verify their semantic parent identities against the question/evidence, then treat the program-checkable conservation and multiplication relations as the numeric source of truth. Do not propose a replacement that violates a valid transition ledger or silently changes its global basis.",
            "Use standard product terminology: describe a product as 'product P transformed from precursor A', not as 'precursor P' when that phrase would falsely classify P as a pro-primary/pro-eutectic constituent. Terminology corrections must not change an already correct mass ledger.",
            "Protocol for a silently omitted partition component: classify the existing claimed exhaustive partition as defect_kind=incorrect, not missing. Put the exact current partition sentence in current_answer_quote and the question's composition/partition request in requirement_quote. The omitted component need not be named verbatim in the question, because it is a defect in the submitted partition rather than a new requested output. Reserve defect_kind=missing for an entire output explicitly named in the requirement but absent from the answer.",
            "A repair decision must contain at least one atomic defect with an exact requirement_quote. Do not label an item incorrect and then state that the same calculation or requested content is correct. If all proposed fixes are already present anywhere in the coverage manifest, formulas, or calculation contract, return pass.",
            "Every repair that changes a number, fraction, ratio, composition, allocation, or calculation must include proposed_calculation_contract. Include all final quantities needed to check every affected partition, any pre-change parent as an intermediate quantity, and every parent-to-products transition. Use one explicit global basis per partition/transition. The program rejects the repair proposal unless partitions and parent splits conserve exactly; prose-only numeric suggestions have no repair authority.",
            "For every proposed result that differs from the current calculation_contract, proposed_calculation_contract.derivations must contain its quantity_id, a numeric-only expression that evaluates to the proposed value, and short exact source_quotes from the supplied question, confirmed visual facts, or confirmed evidence containing every nontrivial input number. A result formula without grounded inputs has no repair authority. Keep phase/stage-specific endpoints in their stated context; do not reuse endpoints from another temperature, time, population, or boundary.",
            "Use repair only for a concrete correctness or notation problem; style preference alone is warn at most.",
            "Do not request human review and do not add textbook citations or page numbers.",
            "Keep reason under 500 characters and suggested_fix under 700 characters. Return only concrete defects; do not narrate your checking process.",
        ],
    }
    batches: list[list[dict[str, Any]]] = []
    try:
        review_client = client or OpenAICompatibleClient(provider)
        # Integrated correctness reviews are isolated per question so one
        # verbose response cannot exhaust the JSON budget for every candidate.
        high_risk = [candidate for candidate in candidates if str(candidate.get("code") or "") == "high_risk_correctness"]
        ordinary = [candidate for candidate in candidates if candidate not in high_risk]
        desired_batches = [[candidate] for candidate in high_risk]
        if ordinary:
            desired_batches.append(ordinary)
        batch_limit = max(1, int(max_batches))
        if len(desired_batches) <= batch_limit:
            batches = desired_batches
        elif batch_limit == 1:
            batches = [[candidate for batch in desired_batches for candidate in batch]]
        else:
            batches = desired_batches[: batch_limit - 1]
            batches.append([candidate for batch in desired_batches[batch_limit - 1 :] for candidate in batch])

        def review_batch(batch: list[dict[str, Any]]) -> dict[str, Any]:
            batch_qids = {str(candidate.get("question_id") or "") for candidate in batch}
            batch_questions = [item for item in questions if str(item.get("question_id") or "") in batch_qids]
            batch_fragments = [item for item in fragments if str(item.get("question_id") or "") in batch_qids]
            batch_evidence = {qid: rows for qid, rows in evidence.items() if qid in batch_qids}
            batch_payload = {
                **payload,
                "candidates": batch,
                "questions": batch_questions,
                "current_answers": batch_fragments,
                "confirmed_textbook_evidence_by_question": batch_evidence,
            }
            try:
                with model_request_slot(provider):
                    response = review_client.chat_json_object(
                        [
                            {"role": "system", "content": "你是跨学科学术符号和表达质量复核器，只输出 JSON。结论必须简洁。"},
                            {"role": "user", "content": json.dumps(batch_payload, ensure_ascii=False)},
                        ],
                        model=selected_model,
                        max_tokens=8192,
                        attempts=1,
                        thinking="disabled",
                        timeout=90,
                        task_stage="review",
                        item_ids=sorted(batch_qids),
                        enforce_context_budget=True,
                    )
                return {
                    "decisions": _normalized_decisions(
                        response,
                        {str(candidate["candidate_id"]) for candidate in batch},
                        _decision_validation_context(batch, batch_questions, batch_fragments, batch_evidence),
                    ),
                    "error": "",
                    "remote_model_calls": 1,
                }
            except Exception as exc:
                # Some models become verbose on a rich integrated question and
                # exhaust the JSON budget. Retry that batch once with only the
                # authoritative scope, final answer ledger, visual facts, and
                # the most relevant evidence. This is a degraded input shape,
                # not a relaxed correctness standard.
                if max(1, min(2, int(max_attempts_per_batch))) < 2:
                    return {
                        "decisions": [],
                        "error": str(exc)[:500],
                        "candidate_ids": [str(candidate.get("candidate_id") or "") for candidate in batch],
                        "remote_model_calls": 1,
                    }
                compact_answers = []
                for item in batch_fragments:
                    compact_answers.append(
                        {
                            "question_id": item.get("question_id"),
                            "answer": item.get("answer"),
                            "answer_summary": item.get("answer_summary"),
                            "calculation_contract": item.get("calculation_contract"),
                            "coverage_manifest": [
                                {
                                    "number": unit.get("number"),
                                    "answer": unit.get("answer"),
                                    "step_texts": unit.get("step_texts"),
                                    "has_figure_spec": unit.get("has_figure_spec"),
                                }
                                for unit in item.get("coverage_manifest", []) or []
                            ],
                        }
                    )
                compact_payload = {
                    "task": "compact_retry_review_selected_academic_quality_risks",
                    "candidates": batch,
                    "questions": batch_questions,
                    "current_answers": compact_answers,
                    "confirmed_textbook_evidence_by_question": {
                        qid: rows[:6] for qid, rows in batch_evidence.items()
                    },
                    "output_schema": payload["output_schema"],
                    "hard_rules": payload["hard_rules"],
                }
                try:
                    with model_request_slot(provider):
                        response = review_client.chat_json_object(
                            [
                                {"role": "system", "content": "你是跨学科学术正确性复核器。只输出最短合法 JSON，不写检查过程。"},
                                {"role": "user", "content": json.dumps(compact_payload, ensure_ascii=False)},
                            ],
                            model=selected_model,
                            max_tokens=4096,
                            attempts=1,
                            thinking="disabled",
                            timeout=90,
                            task_stage="review",
                            item_ids=sorted(batch_qids),
                            enforce_context_budget=True,
                        )
                    return {
                        "decisions": _normalized_decisions(
                            response,
                            {str(candidate["candidate_id"]) for candidate in batch},
                            _decision_validation_context(batch, batch_questions, batch_fragments, batch_evidence),
                        ),
                        "error": "",
                        "compact_retry": True,
                        "initial_error": str(exc)[:500],
                        "remote_model_calls": 2,
                    }
                except Exception as retry_exc:
                    return {
                        "decisions": [],
                        "error": str(retry_exc)[:500],
                        "initial_error": str(exc)[:500],
                        "candidate_ids": [str(candidate.get("candidate_id") or "") for candidate in batch],
                        "remote_model_calls": 2,
                    }

        decision_groups = run_limited_concurrent(batches, review_batch, max_workers=min(2, len(batches)))
        decisions = [decision for group in decision_groups for decision in group.get("decisions", [])]
        batch_failures = [group for group in decision_groups if group.get("error")]
        unavailable_warnings = []
        candidate_by_id = {str(candidate.get("candidate_id") or ""): candidate for candidate in candidates}
        for failure in batch_failures:
            for candidate_id in failure.get("candidate_ids", []):
                candidate = candidate_by_id.get(str(candidate_id), {})
                unavailable_warnings.append(
                    {
                        "question_id": str(candidate.get("question_id") or ""),
                        "code": "reviewer_unavailable",
                        "message": "选择性 AI 复核服务请求失败；不将调用故障计为答案语义错误。",
                        "candidate_id": str(candidate_id),
                    }
                )
        actual_remote_calls = sum(int(group.get("remote_model_calls") or 0) for group in decision_groups)
        base.update(
            {
                "status": "degraded" if batch_failures else "completed",
                "ok": True,
                "batch_count": len(batches),
                "remote_model_calls": actual_remote_calls,
                "remote_model_calls_this_run": actual_remote_calls,
                "decisions": decisions,
                "warnings": [*_warning_rows(candidates, decisions), *unavailable_warnings],
                "batch_failures": batch_failures,
            }
        )
    except Exception as exc:
        base.update(
            {
                "status": "degraded",
                "ok": True,
                "degraded_reason": "review_request_failed",
                "error": str(exc)[:500],
                "batch_count": len(batches) or 1,
                "remote_model_calls": 0,
                "remote_model_calls_this_run": 0,
                "warnings": [
                    {
                        "question_id": str(candidate.get("question_id") or ""),
                        "code": "reviewer_unavailable",
                        "message": "选择性 AI 复核请求失败；已自动降级为本地风险标记。",
                        "candidate_id": candidate["candidate_id"],
                    }
                    for candidate in candidates
                ],
            }
        )
    _write_json_atomic(report_json, base)
    return base
