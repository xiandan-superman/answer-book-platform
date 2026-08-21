from __future__ import annotations

from types import SimpleNamespace

from app.question_understanding import _merge_visual_result


def test_phase_diagram_visual_merge_removes_model_solved_reactions() -> None:
    base = {"text": "根据二元相图回答问题", "images": [{"image_id": "i1"}]}
    visual = {
        "images": [
            {
                "image_id": "i1",
                "invariant_horizontal_lines": [{"y_value": "1200 K", "x_start": "0.5", "x_end": "0.8"}],
                "answer_relevant_observations": [
                    "1200 K处有一条从0.5延伸到0.8的水平线",
                    "约1200 K发生共晶反应 L→A+B",
                ],
                "uncertainties": [],
            }
        ]
    }

    merged = _merge_visual_result(base, visual, SimpleNamespace(name="vision"), "model")

    image = merged["images"][0]
    assert image["answer_relevant_observations"] == ["1200 K处有一条从0.5延伸到0.8的水平线"]
    assert image["invariant_horizontal_lines"][0]["x_end"] == "0.8"
    assert any("仅保留可见几何事实" in item for item in image["uncertainties"])
