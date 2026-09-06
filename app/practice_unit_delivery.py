"""The same per-unit Word boundary for source- and knowledge-based practice."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .document_contracts import DOCUMENT_CONTRACT_VERSION
from .output_checkpoints import content_sha256, file_dependencies
from .pandoc_word import PANDOC_CONTRACT
from .practice_document_contracts import PRACTICE_DOCUMENT_CONTRACT_VERSION
from .practice_export import (
    build_practice_question_docx,
    practice_export_exercise_id,
    resolve_practice_export_payload,
    validate_docx_output,
    validate_practice_export,
)
from .unit_delivery import dependency_units, publish_manifest, read_manifest, store_unit, unit_artifact


def preserve_practice_units(root: Path, data: dict[str, Any], *, on_update: Callable[[dict[str, Any]], None] = lambda _: None) -> dict[str, Any]:
    items = [{**item, "exercise_id": item.get("exercise_id") or practice_export_exercise_id(item, index)}
             for index, item in enumerate(data.get("exercises") or [])]
    groups = dependency_units(items, id_field="exercise_id")
    expected = [{"object_id": str(item["exercise_id"]), "number": str(item.get("number") or index)}
                for index, item in enumerate(items, start=1)]
    units: list[dict[str, Any]] = []
    previous = read_manifest(root)
    manifest = previous
    reusable = {unit.get("revision"): unit for unit in previous.get("units", [])
                if unit.get("status") == "deliverable"}
    for group in groups:
        ids = group["object_ids"]
        unit: dict[str, Any] = {"object_ids": ids, "status": "not_deliverable", "issues": []}
        try:
            if not group["dependency_complete"]:
                raise ValueError("依赖题目缺失，不能拆开交付。")
            for item in group["items"]:
                expected_variants = int(item.get("variant_count") or 0)
                if item.get("parent_plan_item_id") and expected_variants > len(group["items"]):
                    raise ValueError("变式组尚未完整，不能作为完整题组交付。")
            # Dependency/manifest IDs belong to generated objects, whereas the
            # export API selects plan IDs when both identities are present.
            selection = [practice_export_exercise_id(item) for item in group["items"]]
            scoped = resolve_practice_export_payload(
                {"export_scope": "selected", "selected_exercise_ids": selection}, data,
            )
            scoped["title"] = "分题练习成果（非完整试卷）"
            revision = content_sha256({"data": scoped, "assets": file_dependencies(scoped),
                                      "contracts": [DOCUMENT_CONTRACT_VERSION, PRACTICE_DOCUMENT_CONTRACT_VERSION,
                                      PANDOC_CONTRACT, "practice-units-v2"]})
            report = validate_practice_export(scoped)
            if not report["ok"]:
                unit["issues"] = report["blocking_issues"]
            else:
                cached = reusable.get(revision)
                if cached:
                    try:
                        unit_artifact(root, previous, cached["sha256"])
                    except (OSError, ValueError):
                        cached = None
                if cached:
                    unit = cached
                else:
                    content = build_practice_question_docx(scoped)
                    validation = validate_docx_output(content, scoped)
                    if not validation["ok"]:
                        unit["issues"] = validation["issues"]
                    else:
                        unit = store_unit(root, content, revision=revision, object_ids=ids,
                                          warnings=report["warning_issues"])
        except Exception as exc:
            unit["issues"] = [f"{type(exc).__name__}: {exc}"]
        units.append(unit)
        manifest = publish_manifest(root, expected=expected, units=units)
        on_update(manifest)
    return manifest if groups else publish_manifest(root, expected=expected, units=units)
