from pathlib import Path
from types import SimpleNamespace

from app import exercise_generation as generation


def _exercise() -> dict:
    return {
        "batch_index": 1,
        "figures": [{
            "figure_id": "g1",
            "nodes": [
                {"id": "a", "label": "", "x": 0.1, "y": 0.1},
                {"id": "b", "label": "", "x": 0.9, "y": 0.9},
            ],
            "edges": [{"from": "a", "to": "b"}],
        }],
    }


def _plan() -> dict:
    return {
        "plan_item_id": "plan_01",
        "stem_figure_required": True,
        "figure_design": {
            "role": "blank_template",
            "kind": "diagram",
            "template_elements": ["空白晶胞立方体框架"],
            "forbidden_answer_elements": ["目标晶面", "目标晶向"],
        },
    }


def test_configured_image_model_attaches_only_a_blank_template(monkeypatch, tmp_path: Path) -> None:
    prompts: list[str] = []

    class FakeClient:
        def __init__(self, _provider):
            pass

        def generate_image(self, prompt, output, **_kwargs):
            prompts.append(prompt)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"png")

    monkeypatch.setattr(generation, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation, "get_provider", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(generation, "OpenAICompatibleClient", FakeClient)
    exercise = _exercise()

    generation._attach_model_generated_blank_template(
        exercise,
        _plan(),
        {"image_provider": "lingsuan_image", "image_model": "gpt-image-2"},
    )

    figure = exercise["figures"][0]
    assert Path(figure["image_path"]).is_file()
    assert figure["figure_purpose"] == "blank_template"
    assert exercise["figure_generation"]["answer_image_forbidden"] is True
    assert "Do not solve the problem" in prompts[0]
    assert "目标晶面" in prompts[0]


def test_image_model_is_not_called_without_explicit_forbidden_answer_elements(monkeypatch) -> None:
    called = False

    class FakeClient:
        def __init__(self, _provider):
            nonlocal called
            called = True

    monkeypatch.setattr(generation, "OpenAICompatibleClient", FakeClient)
    plan = _plan()
    plan["figure_design"].pop("forbidden_answer_elements")
    exercise = _exercise()

    generation._attach_model_generated_blank_template(
        exercise,
        plan,
        {"image_provider": "lingsuan_image", "image_model": "gpt-image-2"},
    )

    assert called is False
    assert "image_path" not in exercise["figures"][0]


def test_model_cannot_inject_an_arbitrary_local_image_path(monkeypatch, tmp_path: Path) -> None:
    outside = tmp_path / "private.png"
    outside.write_bytes(b"secret")
    monkeypatch.setattr(generation, "OUTPUTS_DIR", tmp_path / "outputs")

    normalized = generation._normalize_figures([{
        "figure_id": "g1",
        "image_path": str(outside),
        "nodes": [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 1, "y": 1}],
    }])

    assert normalized[0]["image_path"] == ""
