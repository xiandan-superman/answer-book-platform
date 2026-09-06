"""Offline delivery regression against saved tasks; never invokes generation.

Inputs are read only. All derived files go to a new, explicitly supplied output
directory so historical downloads and their manifests remain untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.docx_v4 import build_docx_from_fragments  # noqa: E402
from app.exam_unit_delivery import preserve_exam_units  # noqa: E402
from app.pipeline_checkpoints import normalize_answer_checkpoint  # noqa: E402
from app.practice_export import (  # noqa: E402
    build_practice_question_docx,
    practice_export_exercise_id,
    resolve_practice_export_payload,
    validate_docx_output,
    validate_practice_export,
)
from app.practice_unit_delivery import preserve_practice_units  # noqa: E402


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ReplayAssets:
    """Localize asset references, including missing ones, before any renderer runs.

    A missing source remains missing in the sandbox; it must not accidentally
    resolve against the process directory or a historical output. One canonical
    mapping is shared by question, draft and rendered-fragment references.
    """

    PATH_FIELDS = {"path", "image_path", "asset_path", "figure_path", "image_refs"}

    def __init__(self, source_base: Path, output: Path):
        self.source_base = source_base.resolve()
        self.output = output.resolve() / "replay_assets"
        self.copied: dict[Path, Path] = {}

    def localize(self, value, field: str = ""):
        if isinstance(value, dict):
            return {key: self.localize(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self.localize(item, field) for item in value]
        if not isinstance(value, str) or field not in self.PATH_FIELDS or not value.strip():
            return value
        if "://" in value or value.startswith("data:"):
            raise ValueError("Offline replay requires local assets; remote asset reference found")
        source = Path(value)
        if not source.is_absolute():
            source = self.source_base / source
        source = source.resolve()
        if source not in self.copied:
            identity = hashlib.sha256(str(source).encode()).hexdigest()
            target = self.output / identity / source.name
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            self.copied[source] = target
        return str(self.copied[source])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--practice", action="append", default=[])
    parser.add_argument("--exam", action="append", default=[])
    args = parser.parse_args()
    source = args.data_root.resolve()
    output = args.output.resolve()
    if output == source or source in output.parents:
        parser.error("output must be outside saved input data")
    output.mkdir(parents=True, exist_ok=False)
    reports = []
    for history_id in args.practice:
        data = read(source / "practice_history" / f"{history_id}.json")["data"]
        root = output / history_id
        data = ReplayAssets(source, root).localize(data)
        manifest = preserve_practice_units(root / "units", data)
        selection = [practice_export_exercise_id(item, index)
                     for index, item in enumerate(data["exercises"])
                     if item.get("generation_status") != "failed"]
        scoped = resolve_practice_export_payload({"export_scope": "selected", "selected_exercise_ids": selection}, data)
        validation = validate_practice_export(scoped)
        docx_validation = None
        if validation["ok"]:
            content = build_practice_question_docx(scoped)
            docx_validation = validate_docx_output(content, scoped)
            (root / "selected.docx").write_bytes(content)
        reports.append({"id": history_id, "whole_gate": validate_practice_export(data),
                        "selected_gate": validation, "selected_docx": docx_validation,
                        "available": manifest["available_count"], "missing": manifest["missing"],
                        "unit_issues": [unit for unit in manifest["units"] if unit["status"] != "deliverable"]})
        print(history_id, manifest["available_count"], "units", flush=True)
    for task_id in args.exam:
        stage = source / "tasks" / task_id / "stage_outputs"
        root = output / task_id
        root.mkdir()
        assets = ReplayAssets(stage, root)
        for name in ("answer_fragments.json", "answer_drafts.json"):
            (root / name).write_text(json.dumps(assets.localize(read(stage / name)), ensure_ascii=False), encoding="utf-8")
        exam = assets.localize(read(stage / "structured_exam.json"))
        selection_data = assets.localize(read(stage / "evidence_selection.json"))
        normalized_ids = normalize_answer_checkpoint(root, exam)
        manifest = preserve_exam_units(
            root, structured_exam=exam,
            fragments_json=root / "answer_fragments.json", selection_data=selection_data,
        )
        build_docx_from_fragments(root / "answer_fragments.json", root / "replayed_candidate.docx")
        reports.append({"id": task_id, "normalized_ids": normalized_ids,
                        "available": manifest["available_count"], "missing": manifest["missing"],
                        "unit_issues": [unit for unit in manifest["units"] if unit["status"] != "deliverable"]})
        print(task_id, manifest["available_count"], "units", flush=True)
    (output / "report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
