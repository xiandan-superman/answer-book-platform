from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from .catalog import DEFAULT_CAPABILITY_REGISTRY
from .registry import CapabilityRegistry, ExpressionMatch

ACADEMIC_EXPRESSION_REPORT_VERSION = "answer_book.academic_expression_audit.v1"


class ExpressionKind(str, Enum):
    FORMULA = "formula"
    EQUATION = "equation"
    QUANTITY = "quantity"
    VECTOR = "vector"
    MATRIX = "matrix"
    CHEMICAL_NOTATION = "chemical_notation"
    REACTION = "reaction"
    DOMAIN_NOTATION = "domain_notation"


@dataclass(frozen=True)
class AcademicExpression:
    expression_id: str
    question_id: str
    kind: str
    source_format: str
    raw: str
    normalized: str
    location: str
    capability_id: str
    rule_id: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_expression(value: str, *, source_format: str) -> str:
    """Create a comparison form without changing author-visible content."""

    normalized = str(value or "").strip().replace("−", "-").replace("–", "-")
    normalized = normalized.replace("＋", "+").replace("＝", "=")
    if source_format == "latex":
        normalized = normalized.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
        normalized = re.sub(r"\s+", " ", normalized)
    else:
        normalized = re.sub(r"[\t\r\n ]+", " ", normalized)
    return normalized


def _expression_id(question_id: str, location: str, raw: str) -> str:
    digest = hashlib.sha256(f"{question_id}\0{location}\0{raw}".encode()).hexdigest()[:16]
    return f"expr_{digest}"


def _best_match(matches: Iterable[ExpressionMatch]) -> ExpressionMatch | None:
    values = list(matches)
    if not values:
        return None
    return max(values, key=lambda item: (item.priority, item.confidence, item.end - item.start))


def classify_formula(
    raw: str,
    *,
    question_id: str,
    location: str,
    context: str,
    registry: CapabilityRegistry = DEFAULT_CAPABILITY_REGISTRY,
) -> AcademicExpression:
    matches = registry.match_expressions(raw, source_format="latex", context=context)
    best = _best_match(matches)
    kind = best.expression_kind if best else ExpressionKind.FORMULA.value
    return AcademicExpression(
        expression_id=_expression_id(question_id, location, raw),
        question_id=question_id,
        kind=kind,
        source_format="latex",
        raw=raw,
        normalized=normalize_expression(raw, source_format="latex"),
        location=location,
        capability_id=best.capability_id if best else "core.academic_expressions",
        rule_id=best.rule_id if best else "core.formula_fallback",
        confidence=best.confidence if best else 0.8,
    )


def extract_text_expressions(
    text: str,
    *,
    question_id: str,
    location: str,
    context: str,
    registry: CapabilityRegistry = DEFAULT_CAPABILITY_REGISTRY,
) -> list[AcademicExpression]:
    expressions: list[AcademicExpression] = []
    for match in registry.match_expressions(text, source_format="text", context=context):
        expression_location = f"{location}[{match.start}:{match.end}]"
        expressions.append(
            AcademicExpression(
                expression_id=_expression_id(question_id, expression_location, match.value),
                question_id=question_id,
                kind=match.expression_kind,
                source_format="text",
                raw=match.value,
                normalized=normalize_expression(match.value, source_format="text"),
                location=expression_location,
                capability_id=match.capability_id,
                rule_id=match.rule_id,
                confidence=match.confidence,
            )
        )
    return expressions


def _latex_structure_issues(value: str) -> list[str]:
    issues: list[str] = []
    depth = 0
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                issues.append("closing_brace_without_opening")
                break
    if depth > 0:
        issues.append("unclosed_brace")
    environment_stack: list[str] = []
    for match in re.finditer(r"\\(begin|end)\s*\{([^{}]+)\}", value):
        operation, name = match.groups()
        if operation == "begin":
            environment_stack.append(name)
        elif not environment_stack or environment_stack.pop() != name:
            issues.append("unbalanced_environment")
            break
    if environment_stack and "unbalanced_environment" not in issues:
        issues.append("unbalanced_environment")
    return issues


def _semantic_review_reason(expression: AcademicExpression) -> str:
    """Identify rare, high-risk notation that local structure checks cannot judge."""

    raw = expression.raw
    if expression.kind in {ExpressionKind.CHEMICAL_NOTATION.value, ExpressionKind.REACTION.value}:
        return "chemical_or_reaction_semantics_require_context"
    command_count = len(re.findall(r"\\[A-Za-z]+", raw))
    complex_construct = re.search(
        r"\\(?:overset|underset|substack|operatorname|text)\b|"
        r"\\begin\s*\{(?:cases|aligned|array)\}",
        raw,
    )
    if expression.kind == ExpressionKind.FORMULA.value and (
        len(raw) >= 120 or command_count >= 8 or complex_construct
    ):
        return "complex_unclassified_latex_requires_context"
    return ""


def _question_context(question: dict[str, Any] | None) -> str:
    if not isinstance(question, dict):
        return ""
    values = [
        question.get("stem"),
        question.get("title"),
        question.get("question_type"),
        question.get("section"),
    ]
    return "\n".join(str(value) for value in values if value)


def _text_segments(fragment: dict[str, Any]) -> Iterable[tuple[str, str]]:
    answer = str(fragment.get("answer") or "").strip()
    if answer:
        yield "answer", answer
    raw_blocks = fragment.get("blocks")
    blocks: list[Any] = raw_blocks if isinstance(raw_blocks, list) else []
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        for segment_index, segment in enumerate(block.get("segments", []) or []):
            if isinstance(segment, dict) and segment.get("type") == "text":
                text = str(segment.get("text") or "").strip()
                if text:
                    yield f"blocks[{block_index}].segments[{segment_index}].text", text


def audit_academic_expressions(
    fragments_data: dict[str, Any],
    *,
    structured_exam: dict[str, Any] | None = None,
    output_json: Path | None = None,
    registry: CapabilityRegistry = DEFAULT_CAPABILITY_REGISTRY,
) -> dict[str, Any]:
    questions = {
        str(question.get("question_id") or "").strip(): question
        for question in (structured_exam or {}).get("items", []) or []
        if isinstance(question, dict) and str(question.get("question_id") or "").strip()
    }
    expressions: list[AcademicExpression] = []
    render_plans: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    review_candidates: list[dict[str, Any]] = []
    raw_fragments = fragments_data.get("fragments")
    fragments: list[Any] = raw_fragments if isinstance(raw_fragments, list) else []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        qid = str(fragment.get("question_id") or "").strip()
        context = _question_context(questions.get(qid))
        raw_formulas = fragment.get("formulas")
        formulas: list[Any] = raw_formulas if isinstance(raw_formulas, list) else []
        for index, formula in enumerate(formulas):
            if not isinstance(formula, dict):
                continue
            raw = str(formula.get("latex") or "").strip()
            location = f"formulas[{index}].latex"
            if not raw:
                issues.append(
                    {
                        "question_id": qid,
                        "code": "empty_expression",
                        "message": f"{location} 没有可渲染的 LaTeX 表达式。",
                        "location": location,
                    }
                )
                continue
            expression = classify_formula(
                raw,
                question_id=qid,
                location=location,
                context=context,
                registry=registry,
            )
            expressions.append(expression)
            structure_issues = _latex_structure_issues(raw)
            if structure_issues:
                issues.append(
                    {
                        "question_id": qid,
                        "code": "invalid_latex_structure",
                        "message": f"{location} 存在可机器确认的 LaTeX 结构错误：{', '.join(structure_issues)}。",
                        "location": location,
                        "expression_id": expression.expression_id,
                        "evidence": structure_issues,
                    }
                )
            else:
                from .expression_rendering import build_expression_render_plan, preflight_expression_render

                plan = build_expression_render_plan(
                    raw,
                    question_id=qid,
                    location=location,
                    context=context,
                    role=str(formula.get("role") or "relation"),
                    display=bool(formula.get("display", True)),
                )
                preflight_error = preflight_expression_render(plan)
                plan_data = {**plan.to_dict(), "preflight_ok": not preflight_error}
                if preflight_error:
                    plan_data["preflight_error"] = preflight_error
                    issues.append(
                        {
                            "question_id": qid,
                            "code": "render_preflight_failed",
                            "message": f"{location} 无法生成有效 Word 公式对象：{preflight_error}",
                            "location": location,
                            "expression_id": expression.expression_id,
                        }
                    )
                render_plans.append(plan_data)
            if not structure_issues and (reason := _semantic_review_reason(expression)):
                review_candidates.append(
                    {
                        "candidate_id": expression.expression_id,
                        "question_id": qid,
                        "code": "semantic_context_risk",
                        "reason": reason,
                        "expression_id": expression.expression_id,
                        "kind": expression.kind,
                        "raw": expression.raw,
                        "normalized": expression.normalized,
                        "location": expression.location,
                        "confidence": expression.confidence,
                    }
                )
        for location, text in _text_segments(fragment):
            expressions.extend(
                extract_text_expressions(
                    text,
                    question_id=qid,
                    location=location,
                    context=context,
                    registry=registry,
                )
            )
    kind_counts: dict[str, int] = {}
    capability_counts: dict[str, int] = {}
    for expression in expressions:
        kind_counts[expression.kind] = kind_counts.get(expression.kind, 0) + 1
        capability_counts[expression.capability_id] = capability_counts.get(expression.capability_id, 0) + 1
    report = {
        "schema_version": ACADEMIC_EXPRESSION_REPORT_VERSION,
        "mode": "local_only",
        "remote_model_calls": 0,
        "mutates_source_content": False,
        "ok": not issues,
        "expression_count": len(expressions),
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "kind_counts": dict(sorted(kind_counts.items())),
        "capability_counts": dict(sorted(capability_counts.items())),
        "issues": issues,
        "warnings": warnings,
        "review_candidate_count": len(review_candidates),
        "review_candidates": review_candidates,
        "render_plan_count": len(render_plans),
        "render_preflight_failure_count": sum(1 for plan in render_plans if not plan["preflight_ok"]),
        "render_plans": render_plans,
        "expressions": [expression.to_dict() for expression in expressions],
        "registered_expression_rules": registry.expression_rule_snapshot(),
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
