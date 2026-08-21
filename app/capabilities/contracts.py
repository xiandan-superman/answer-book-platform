from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class KeywordRule:
    """One evidence-producing rule used for local capability resolution."""

    schema_kind: str
    keywords: tuple[str, ...]
    confidence: float = 0.9

    def __post_init__(self) -> None:
        if not self.schema_kind.strip():
            raise ValueError("schema_kind must not be empty")
        if not self.keywords or any(not keyword.strip() for keyword in self.keywords):
            raise ValueError(f"keywords must not be empty for {self.schema_kind}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class ExpressionRule:
    """One local recognizer contributed by a capability pack.

    Rules classify explicit notation only. They do not assert that the
    expression is scientifically correct.
    """

    rule_id: str
    expression_kind: str
    pattern: str
    source_format: str = "any"
    context_keywords: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.9
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or not self.expression_kind.strip() or not self.pattern:
            raise ValueError("expression rule id, kind and pattern must not be empty")
        if self.source_format not in {"any", "text", "latex"}:
            raise ValueError(f"invalid expression source format: {self.source_format}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("expression rule confidence must be between 0 and 1")
        try:
            re.compile(self.pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid expression rule regex {self.rule_id}: {exc}") from exc


@dataclass(frozen=True)
class CapabilityManifest:
    """Stable declaration supplied by one built-in or optional capability pack.

    The core knows only this contract. Discipline vocabulary, schema metadata and
    local routing hints remain inside the capability pack that owns them.
    """

    capability_id: str
    version: str
    name: str
    description: str
    schemas: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    keyword_rules: tuple[KeywordRule, ...] = field(default_factory=tuple)
    expression_rules: tuple[ExpressionRule, ...] = field(default_factory=tuple)
    prompt_context: str = ""
    optional_dependencies: tuple[str, ...] = field(default_factory=tuple)
    policy_hooks: Mapping[str, Callable[[Mapping[str, Any]], Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be empty")
        if not self.version.strip():
            raise ValueError(f"version must not be empty for {self.capability_id}")
        if not self.name.strip():
            raise ValueError(f"name must not be empty for {self.capability_id}")
        schema_kinds = {str(schema.get("kind") or "").strip() for schema in self.schemas}
        if "" in schema_kinds:
            raise ValueError(f"schema kind must not be empty for {self.capability_id}")
        if len(schema_kinds) != len(self.schemas):
            raise ValueError(f"duplicate schema kind in {self.capability_id}")
        unknown_rule_kinds = {rule.schema_kind for rule in self.keyword_rules} - schema_kinds
        if unknown_rule_kinds:
            raise ValueError(
                f"keyword rules reference unknown schemas in {self.capability_id}: "
                f"{sorted(unknown_rule_kinds)}"
            )
        expression_rule_ids = [rule.rule_id for rule in self.expression_rules]
        if len(set(expression_rule_ids)) != len(expression_rule_ids):
            raise ValueError(f"duplicate expression rule id in {self.capability_id}")
        for hook_name, hook in self.policy_hooks.items():
            if not str(hook_name).strip() or not callable(hook):
                raise ValueError(f"invalid policy hook in {self.capability_id}: {hook_name!r}")
