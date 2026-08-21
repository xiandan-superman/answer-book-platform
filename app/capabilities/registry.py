from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import CapabilityManifest, ExpressionRule


@dataclass(frozen=True)
class CapabilityMatch:
    schema_kind: str
    capability_id: str
    evidence: str
    confidence: float


@dataclass(frozen=True)
class ExpressionMatch:
    rule_id: str
    expression_kind: str
    capability_id: str
    value: str
    start: int
    end: int
    confidence: float
    priority: int


class CapabilityRegistry:
    """Conflict-checking registry for schemas and local routing evidence."""

    def __init__(self, manifests: Iterable[CapabilityManifest] = ()) -> None:
        self._manifests: dict[str, CapabilityManifest] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._expression_rules: dict[str, tuple[str, ExpressionRule]] = {}
        for manifest in manifests:
            self.register(manifest)

    def register(self, manifest: CapabilityManifest) -> None:
        if manifest.capability_id in self._manifests:
            raise ValueError(f"capability already registered: {manifest.capability_id}")
        collisions = sorted(
            str(schema.get("kind") or "").strip()
            for schema in manifest.schemas
            if str(schema.get("kind") or "").strip() in self._schemas
        )
        if collisions:
            raise ValueError(f"schema kinds already registered: {collisions}")
        expression_collisions = sorted(
            rule.rule_id for rule in manifest.expression_rules if rule.rule_id in self._expression_rules
        )
        if expression_collisions:
            raise ValueError(f"expression rules already registered: {expression_collisions}")
        for raw_schema in manifest.schemas:
            schema = deepcopy(dict(raw_schema))
            schema["capability_id"] = manifest.capability_id
            schema["capability_version"] = manifest.version
            self._schemas[schema["kind"]] = schema
        self._manifests[manifest.capability_id] = manifest
        for rule in manifest.expression_rules:
            self._expression_rules[rule.rule_id] = (manifest.capability_id, rule)

    def get_schema(self, kind: str) -> dict[str, Any] | None:
        schema = self._schemas.get(str(kind or "").strip())
        return deepcopy(schema) if schema else None

    def schema_snapshot(self, capability_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        selected = set(capability_ids) if capability_ids is not None else None
        return [
            deepcopy(schema)
            for schema in self._schemas.values()
            if selected is None or schema["capability_id"] in selected
        ]

    def manifest_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "capability_id": manifest.capability_id,
                "version": manifest.version,
                "name": manifest.name,
                "description": manifest.description,
                "schema_count": len(manifest.schemas),
                "optional_dependencies": list(manifest.optional_dependencies),
                "expression_rule_count": len(manifest.expression_rules),
            }
            for manifest in self._manifests.values()
        ]

    def prompt_catalog(self, capability_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "schema_id": entry["schema_id"],
                "kind": entry["kind"],
                "name": entry["name"],
                "description": entry["description"],
                "required_fields": entry["required_fields"],
                "optional_fields": entry.get("optional_fields", []),
                "capability_id": entry["capability_id"],
            }
            for entry in self.schema_snapshot(capability_ids)
        ]

    def prompt_contexts(self, capability_ids: Iterable[str] | None = None) -> list[str]:
        selected = set(capability_ids) if capability_ids is not None else None
        return [
            manifest.prompt_context
            for manifest in self._manifests.values()
            if manifest.prompt_context and (selected is None or manifest.capability_id in selected)
        ]

    def policy_contributions(
        self,
        hook_name: str,
        context: dict[str, Any],
        capability_ids: Iterable[str] | None = None,
    ) -> list[Any]:
        """Collect optional domain policy without teaching the core any discipline vocabulary."""

        selected = set(capability_ids) if capability_ids is not None else None
        contributions: list[Any] = []
        for manifest in self._manifests.values():
            if selected is not None and manifest.capability_id not in selected:
                continue
            hook = manifest.policy_hooks.get(hook_name)
            if hook is None:
                continue
            contribution = hook(deepcopy(context))
            if contribution is not None:
                contributions.append(contribution)
        return contributions

    def apply_policy_transforms(
        self,
        hook_name: str,
        value: Any,
        context: dict[str, Any],
        capability_ids: Iterable[str] | None = None,
    ) -> Any:
        """Apply selected capability transforms in stable registration order."""

        selected = set(capability_ids) if capability_ids is not None else None
        transformed = deepcopy(value)
        for manifest in self._manifests.values():
            if selected is not None and manifest.capability_id not in selected:
                continue
            hook = manifest.policy_hooks.get(hook_name)
            if hook is None:
                continue
            hook_context = deepcopy(context)
            hook_context["value"] = transformed
            candidate = hook(hook_context)
            if candidate is not None:
                transformed = candidate
        return transformed

    def expression_rule_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "rule_id": rule.rule_id,
                "expression_kind": rule.expression_kind,
                "source_format": rule.source_format,
                "context_keywords": list(rule.context_keywords),
                "confidence": rule.confidence,
                "priority": rule.priority,
                "capability_id": capability_id,
            }
            for capability_id, rule in self._expression_rules.values()
        ]

    def match_expressions(
        self,
        value: str,
        *,
        source_format: str,
        context: str = "",
    ) -> list[ExpressionMatch]:
        import re

        source = str(value or "")
        context_lower = str(context or "").lower()
        candidates: list[ExpressionMatch] = []
        for capability_id, rule in self._expression_rules.values():
            if rule.source_format not in {"any", source_format}:
                continue
            if rule.context_keywords and not any(keyword.lower() in context_lower for keyword in rule.context_keywords):
                continue
            for match in re.finditer(rule.pattern, source, re.IGNORECASE):
                candidates.append(
                    ExpressionMatch(
                        rule_id=rule.rule_id,
                        expression_kind=rule.expression_kind,
                        capability_id=capability_id,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        confidence=rule.confidence,
                        priority=rule.priority,
                    )
                )
        selected: list[ExpressionMatch] = []
        for candidate in sorted(
            candidates,
            key=lambda item: (-item.priority, -item.confidence, -(item.end - item.start), item.start),
        ):
            if any(candidate.start < item.end and item.start < candidate.end for item in selected):
                continue
            selected.append(candidate)
        return sorted(selected, key=lambda item: (item.start, item.end))

    def match_text(self, text: str) -> CapabilityMatch | None:
        lowered = str(text or "").lower()
        matches: list[tuple[float, int, CapabilityMatch]] = []
        for manifest in self._manifests.values():
            for rule in manifest.keyword_rules:
                for keyword in rule.keywords:
                    if keyword.lower() in lowered:
                        matches.append(
                            (
                                rule.confidence,
                                len(keyword),
                                CapabilityMatch(
                                    schema_kind=rule.schema_kind,
                                    capability_id=manifest.capability_id,
                                    evidence=f"题面包含“{keyword}”",
                                    confidence=rule.confidence,
                                ),
                            )
                        )
        if not matches:
            return None
        return max(matches, key=lambda item: (item[0], item[1]))[2]
