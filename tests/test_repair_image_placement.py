import copy

import pytest

from app.audit_model_repair import _preserve_accepted_generated_images


def original_fragment():
    return {
        "question_id": "q1",
        "generated_images": [{"asset_id": "accepted", "caption": "曲线"}],
        "_meta": {"image_tool_loop": {"generated_artifacts": [{"asset_id": "accepted", "path": "original.png"}]}},
        "blocks": [{"label": "图示", "segments": [
            {"type": "image_ref", "image_id": "q1_agent_img_01", "path": "figures/q1_agent_img_01.png", "role": "answer_generated_figure"},
            {"type": "text", "text": "曲线", "figure_caption_for": "q1_agent_img_01"},
            {"type": "formula_ref", "formula_id": "bad_formula"},
        ]}],
    }


@pytest.mark.parametrize("echo_binding", [False, True])
def test_scoped_repair_preserves_placement_and_caption_without_stale_formula(echo_binding):
    original = original_fragment()
    before = copy.deepcopy(original)
    repaired = {"generated_images": copy.deepcopy(original["generated_images"]) if echo_binding else [],
                "_draft": {}, "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "已修复"}]}]}
    _preserve_accepted_generated_images(original, repaired)
    _preserve_accepted_generated_images(original, repaired)
    assert original == before
    assert repaired["blocks"][0]["segments"] == original["blocks"][0]["segments"][:2]
    assert len(repaired["blocks"]) == 2
    assert repaired["_draft"]["generated_images"] == original["generated_images"]
    assert repaired["_meta"]["image_tool_loop"]["generated_artifacts"] == original["_meta"]["image_tool_loop"]["generated_artifacts"]


def test_replacement_asset_does_not_restore_superseded_image():
    repaired = {"generated_images": [{"asset_id": "replacement"}], "blocks": [],
                "_meta": {"image_tool_loop": {"generated_artifacts": [{"asset_id": "replacement"}]}}}
    before = copy.deepcopy(repaired)
    _preserve_accepted_generated_images(original_fragment(), repaired)
    assert repaired == before


def test_unproven_asset_does_not_restore_image_blocks():
    original = original_fragment()
    original["_meta"] = {}
    repaired = {"generated_images": [], "blocks": []}
    _preserve_accepted_generated_images(original, repaired)
    assert repaired == {"generated_images": [], "blocks": []}
