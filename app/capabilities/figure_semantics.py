from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable


class SourceImagePolicy(str, Enum):
    NONE = "none"
    REFERENCE_ONLY = "reference_only"
    PRESERVE_AND_OVERLAY = "preserve_and_overlay"


class RenderStrategy(str, Enum):
    PROGRAMMATIC_RENDERER = "programmatic_renderer"
    SOURCE_IMAGE_OVERLAY = "source_image_overlay"
    MODEL_CODE_RENDERER = "model_code_renderer"
    IMAGE_MODEL_FALLBACK = "image_model_fallback"
    UNAVAILABLE = "unavailable"


def _strings(value: Any, *, limit: int = 20, item_limit: int = 300) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    rows: list[str] = []
    for raw in value:
        text = str(raw or "").strip()[:item_limit]
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= limit:
            break
    return tuple(rows)


@dataclass(frozen=True)
class FigureSemanticContract:
    contract_id: str
    figure_role: str
    source_image_policy: SourceImagePolicy
    required_elements: tuple[str, ...]
    required_labels: tuple[str, ...]
    relationship_constraints: tuple[str, ...]
    forbidden_assumptions: tuple[str, ...]
    original_image_available: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_image_policy"] = self.source_image_policy.value
        for key in (
            "required_elements",
            "required_labels",
            "relationship_constraints",
            "forbidden_assumptions",
        ):
            value[key] = list(value[key])
        return value


@dataclass(frozen=True)
class FigureRenderDecision:
    strategy: RenderStrategy
    reason: str
    semantic_contract_id: str
    schema_kind: str = ""
    renderer: str = ""
    fallback_allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "strategy": self.strategy.value}


def _contract_id(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"figure_contract_{digest}"


def build_figure_semantic_contract(
    *,
    source_image_policy: str,
    required_elements: Iterable[str] = (),
    required_labels: Iterable[str] = (),
    relationship_constraints: Iterable[str] = (),
    forbidden_assumptions: Iterable[str] = (),
    original_image_available: bool,
    figure_role: str = "answer_required",
) -> FigureSemanticContract:
    try:
        policy = SourceImagePolicy(source_image_policy)
    except ValueError:
        policy = SourceImagePolicy.REFERENCE_ONLY if original_image_available else SourceImagePolicy.NONE
    normalized_role = str(figure_role or "answer_required").strip() or "answer_required"
    normalized_elements = _strings(list(required_elements))
    normalized_labels = _strings(list(required_labels))
    normalized_relationships = _strings(list(relationship_constraints))
    normalized_forbidden = _strings(list(forbidden_assumptions))
    available = bool(original_image_available)
    payload: dict[str, Any] = {
        "figure_role": normalized_role,
        "source_image_policy": policy.value,
        "required_elements": list(normalized_elements),
        "required_labels": list(normalized_labels),
        "relationship_constraints": list(normalized_relationships),
        "forbidden_assumptions": list(normalized_forbidden),
        "original_image_available": available,
    }
    return FigureSemanticContract(
        contract_id=_contract_id(payload),
        figure_role=normalized_role,
        source_image_policy=policy,
        required_elements=normalized_elements,
        required_labels=normalized_labels,
        relationship_constraints=normalized_relationships,
        forbidden_assumptions=normalized_forbidden,
        original_image_available=available,
    )


def semantic_contract_from_mapping(value: dict[str, Any]) -> FigureSemanticContract:
    return build_figure_semantic_contract(
        figure_role=str(value.get("figure_role") or "answer_required"),
        source_image_policy=str(value.get("source_image_policy") or "none"),
        required_elements=value.get("required_elements") or (),
        required_labels=value.get("required_labels") or (),
        relationship_constraints=value.get("relationship_constraints") or (),
        forbidden_assumptions=value.get("forbidden_assumptions") or (),
        original_image_available=bool(value.get("original_image_available")),
    )


def validate_figure_semantic_contract(contract: FigureSemanticContract) -> list[str]:
    issues: list[str] = []
    if contract.figure_role != "answer_required":
        issues.append("unsupported_figure_role")
    if (
        contract.source_image_policy is SourceImagePolicy.PRESERVE_AND_OVERLAY
        and not contract.original_image_available
    ):
        issues.append("overlay_requires_original_image")
    return issues


def choose_figure_render_strategy(
    contract: FigureSemanticContract,
    *,
    schema_status: str,
    schema_kind: str = "",
    renderer: str = "",
    drawing_mode: str = "figure_specs",
    image_model_available: bool = False,
) -> FigureRenderDecision:
    if (
        contract.source_image_policy is SourceImagePolicy.PRESERVE_AND_OVERLAY
        and contract.original_image_available
    ):
        return FigureRenderDecision(
            strategy=RenderStrategy.SOURCE_IMAGE_OVERLAY,
            reason="preserve the verified source image and add bounded vector annotations",
            semantic_contract_id=contract.contract_id,
            schema_kind="source_image_overlay",
            renderer="draw_source_image_overlay",
            fallback_allowed=False,
        )
    if schema_status == "schema_found" and renderer:
        return FigureRenderDecision(
            strategy=RenderStrategy.PROGRAMMATIC_RENDERER,
            reason="registered schema has a deterministic renderer",
            semantic_contract_id=contract.contract_id,
            schema_kind=schema_kind,
            renderer=renderer,
        )
    if drawing_mode == "code":
        return FigureRenderDecision(
            strategy=RenderStrategy.MODEL_CODE_RENDERER,
            reason="unregistered semantics use sandboxed drawing code",
            semantic_contract_id=contract.contract_id,
            schema_kind=schema_kind,
        )
    if image_model_available:
        return FigureRenderDecision(
            strategy=RenderStrategy.IMAGE_MODEL_FALLBACK,
            reason="no deterministic renderer is available",
            semantic_contract_id=contract.contract_id,
            schema_kind=schema_kind,
        )
    return FigureRenderDecision(
        strategy=RenderStrategy.UNAVAILABLE,
        reason="no compatible renderer is configured",
        semantic_contract_id=contract.contract_id,
        schema_kind=schema_kind,
    )


def audit_figure_render_outcome(
    contract: FigureSemanticContract,
    decision: FigureRenderDecision,
    *,
    actual_kind: str,
    generation_method: str,
) -> list[str]:
    """Check routing/binding facts without claiming visual semantic correctness."""

    issues: list[str] = []
    if decision.semantic_contract_id != contract.contract_id:
        issues.append("semantic_contract_id_mismatch")
    if (
        decision.strategy in {RenderStrategy.PROGRAMMATIC_RENDERER, RenderStrategy.SOURCE_IMAGE_OVERLAY}
        and (generation_method == "programmatic_renderer" or not decision.fallback_allowed)
        and decision.schema_kind
        and actual_kind != decision.schema_kind
    ):
        issues.append("actual_schema_kind_differs_from_plan")
    expected_methods = {
        RenderStrategy.PROGRAMMATIC_RENDERER: "programmatic_renderer",
        RenderStrategy.SOURCE_IMAGE_OVERLAY: "source_image_overlay",
        RenderStrategy.MODEL_CODE_RENDERER: "model_code_renderer",
        RenderStrategy.IMAGE_MODEL_FALLBACK: "image_model",
        RenderStrategy.UNAVAILABLE: "none",
    }
    expected = expected_methods[decision.strategy]
    if generation_method != expected and not decision.fallback_allowed:
        issues.append("actual_generation_method_differs_from_plan")
    if not decision.fallback_allowed and generation_method != "none":
        issues.append("forbidden_fallback_was_used")
    return issues
