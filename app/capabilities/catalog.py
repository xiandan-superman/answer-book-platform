from __future__ import annotations

from typing import Any, Iterable

from .builtin.core_expressions import CORE_EXPRESSIONS_CAPABILITY
from .builtin.core_figures import CORE_FIGURES_CAPABILITY
from .builtin.materials import MATERIALS_CAPABILITY
from .registry import CapabilityMatch, CapabilityRegistry

DEFAULT_CAPABILITY_REGISTRY = CapabilityRegistry(
    (CORE_EXPRESSIONS_CAPABILITY, CORE_FIGURES_CAPABILITY, MATERIALS_CAPABILITY)
)
CORE_CAPABILITY_IDS = ("core.academic_expressions", "core.figures")


def capability_ids_for_text(text: str) -> tuple[str, ...]:
    """Resolve the smallest prompt-visible capability set for one question."""

    selected = list(CORE_CAPABILITY_IDS)
    match = DEFAULT_CAPABILITY_REGISTRY.match_text(text)
    if match and match.capability_id not in selected:
        selected.append(match.capability_id)
    return tuple(selected)


def get_schema(kind: str) -> dict[str, Any] | None:
    return DEFAULT_CAPABILITY_REGISTRY.get_schema(kind)


def registry_snapshot(capability_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    return DEFAULT_CAPABILITY_REGISTRY.schema_snapshot(capability_ids)


def schema_prompt_catalog(capability_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    return DEFAULT_CAPABILITY_REGISTRY.prompt_catalog(capability_ids)


def match_schema_for_text(text: str) -> CapabilityMatch | None:
    return DEFAULT_CAPABILITY_REGISTRY.match_text(text)


def planner_system_context(capability_ids: Iterable[str] | None = None) -> str:
    contexts = DEFAULT_CAPABILITY_REGISTRY.prompt_contexts(capability_ids)
    return "\n".join(contexts)


def capability_policy_contributions(
    hook_name: str,
    context: dict[str, Any],
    *,
    text: str = "",
    capability_ids: Iterable[str] | None = None,
) -> list[Any]:
    selected = tuple(capability_ids) if capability_ids is not None else capability_ids_for_text(text)
    return DEFAULT_CAPABILITY_REGISTRY.policy_contributions(hook_name, context, selected)


def apply_capability_policy_transforms(
    hook_name: str,
    value: Any,
    context: dict[str, Any],
    *,
    text: str = "",
    capability_ids: Iterable[str] | None = None,
) -> Any:
    selected = tuple(capability_ids) if capability_ids is not None else capability_ids_for_text(text)
    return DEFAULT_CAPABILITY_REGISTRY.apply_policy_transforms(hook_name, value, context, selected)
