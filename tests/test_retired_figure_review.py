from __future__ import annotations

import ast
import inspect
import json

import pytest
from PIL import Image

import app.pipeline as pipeline
from app.figure_artifact_audit import audit_figure_artifacts
from app.final_acceptance import AUDIT_FILES, build_final_acceptance_report


def test_pipeline_has_no_independent_visual_review_or_repair_entrypoint():
    """Guard both initial generation and the content-repair branch."""
    tree = ast.parse(inspect.getsource(pipeline._run_pipeline_impl))
    calls = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls.intersection({
        "audit_figures_with_vision", "repair_figures_with_model_for_visual_qa",
    })
    assert "audit_figure_artifacts" in calls


@pytest.mark.parametrize("condition", ["valid", "missing", "corrupt"])
def test_current_image_files_override_retired_visual_verdict(tmp_path, condition):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    (stage / "figures").mkdir(parents=True)
    output.mkdir()
    for name, filename in AUDIT_FILES.items():
        data = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            data["formula_conversion"] = {"preferred_chain_ready": True}
        (stage / filename).write_text(json.dumps(data))
    (stage / "acceptance_report.json").write_text('{"status":"passed"}')
    (output / "answer_book.docx").write_bytes(b"candidate")
    (stage / "figure_specs.json").write_text(json.dumps({
        "figures": [{"question_id": "q1", "figure_id": "f1"}],
    }))
    legacy = '{"enabled":true,"items":[{"qa":{"ok":false,"summary":"old opinion"}}]}'
    (stage / "figure_visual_qa.json").write_text(legacy)
    image = stage / "figures" / "f1.png"
    if condition == "valid":
        Image.new("RGB", (80, 60), "white").save(image)
    elif condition == "corrupt":
        image.write_bytes(b"not an image")
    artifacts = audit_figure_artifacts(stage)
    final = build_final_acceptance_report(stage, output, require_render=False)
    assert artifacts["ok"] == (condition == "valid")
    assert final["delivery_ready"] == (condition == "valid")
    assert not final["figure_visual_qa_summary"]["enabled"]
    assert not any("old opinion" in item for item in final["warnings"])
    assert (stage / "figure_visual_qa.json").read_text() == legacy


def test_missing_fragment_image_without_specs_remains_blocking(tmp_path):
    (tmp_path / "answer_fragments.json").write_text(json.dumps({
        "fragments": [{"question_id": "q1", "blocks": [{"segments": [
            {"type": "image_ref", "path": "figures/missing.png"},
        ]}]}],
    }))
    assert not audit_figure_artifacts(tmp_path)["ok"]
