from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

FigureRenderer = Callable[[dict[str, Any], Path], None]


def renderer_binding_issues(
    schemas: Iterable[Mapping[str, Any]],
    implementations: Mapping[str, FigureRenderer],
) -> tuple[str, ...]:
    """Return deterministic schema-to-renderer wiring problems.

    Capability packs declare renderer names, while the application supplies the
    implementation. Keeping the check here avoids a second hand-maintained
    schema-kind dispatch table and makes a broken capability fail at assembly
    time instead of halfway through a user task.
    """

    issues: list[str] = []
    seen_kinds: set[str] = set()
    for schema in schemas:
        kind = str(schema.get("kind") or "").strip()
        renderer_name = str(schema.get("renderer") or "").strip()
        if not kind:
            issues.append("schema kind must not be empty")
            continue
        if kind in seen_kinds:
            issues.append(f"schema kind declared more than once: {kind}")
        seen_kinds.add(kind)
        if not renderer_name:
            issues.append(f"schema renderer must not be empty: {kind}")
            continue
        renderer = implementations.get(renderer_name)
        if renderer is None:
            issues.append(f"renderer implementation not found: {kind} -> {renderer_name}")
        elif not callable(renderer):
            issues.append(f"renderer implementation is not callable: {kind} -> {renderer_name}")
    return tuple(issues)


def assemble_renderer_registry(
    schemas: Iterable[Mapping[str, Any]],
    implementations: Mapping[str, FigureRenderer],
    *,
    compatibility_renderers: Mapping[str, FigureRenderer] | None = None,
) -> "RendererRegistry":
    """Assemble schema renderers by declared implementation name."""

    schema_list = tuple(schemas)
    issues = renderer_binding_issues(schema_list, implementations)
    if issues:
        raise ValueError("invalid renderer bindings: " + "; ".join(issues))
    registry = RendererRegistry()
    for schema in schema_list:
        kind = str(schema["kind"]).strip()
        renderer_name = str(schema["renderer"]).strip()
        registry.register(kind, implementations[renderer_name])
    for kind, renderer in (compatibility_renderers or {}).items():
        if registry.get(kind) is None:
            registry.register(kind, renderer)
    return registry


class RendererRegistry:
    """Renderer dispatch without core-level kind conditionals."""

    def __init__(self, renderers: Mapping[str, FigureRenderer] | None = None) -> None:
        self._renderers: dict[str, FigureRenderer] = {}
        for kind, renderer in (renderers or {}).items():
            self.register(kind, renderer)

    def register(self, kind: str, renderer: FigureRenderer) -> None:
        normalized = str(kind or "").strip()
        if not normalized:
            raise ValueError("renderer kind must not be empty")
        if normalized in self._renderers:
            raise ValueError(f"renderer already registered: {normalized}")
        if not callable(renderer):
            raise TypeError(f"renderer must be callable: {normalized}")
        self._renderers[normalized] = renderer

    def get(self, kind: str) -> FigureRenderer | None:
        return self._renderers.get(str(kind or "").strip())

    def render(self, kind: str, spec: dict[str, Any], output: Path) -> bool:
        renderer = self.get(kind)
        if renderer is None:
            return False
        renderer(spec, output)
        return True

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._renderers)
