from __future__ import annotations

import json

from PIL import Image, ImageDraw

from app.figures import _sync_generated_figure_blocks


def test_superseded_generated_figure_is_removed_but_source_image_is_preserved(tmp_path) -> None:
    fragments_json = tmp_path / "answer_fragments.json"
    output_dir = tmp_path / "figures"
    output_dir.mkdir()
    Image.new("RGB", (120, 120), "white").save(output_dir / "active.png")
    fragments_json.write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "blocks": [
                            {
                                "label": "图示",
                                "segments": [
                                    {
                                        "type": "image_ref",
                                        "image_id": "source-1",
                                        "path": "/exam/source.png",
                                        "role": "source_question_image",
                                    },
                                    {
                                        "type": "image_ref",
                                        "image_id": "failed_fallback",
                                        "path": "figures/failed_fallback.png",
                                        "role": "answer_generated_figure",
                                    },
                                    {
                                        "type": "text",
                                        "text": "已淘汰候选图",
                                    },
                                ],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    specs = [{"question_id": "q1", "figure_id": "active", "caption": "最终采用图"}]

    _sync_generated_figure_blocks(fragments_json, specs, output_dir)

    segments = json.loads(fragments_json.read_text(encoding="utf-8"))["fragments"][0]["blocks"][0]["segments"]
    image_ids = [segment.get("image_id") for segment in segments if segment.get("type") == "image_ref"]
    assert image_ids == ["source-1", "active"]
    assert all(segment.get("text") != "已淘汰候选图" for segment in segments)


def test_xrd_hkl_and_style_aliases_are_normalized_generically() -> None:
    from app.figures import normalize_figure_spec, program_check_figure_spec

    spec = normalize_figure_spec(
        {
            "kind": "xrd_pattern",
            "caption": "有序相与基本峰对比",
            "peaks": [
                {"position": 1, "hkl": "100", "style": "dashed", "phase_label": "新增超结构峰"},
                {"position": 2, "hkl": "110", "style": "solid", "phase_label": "原有峰"},
            ],
        }
    )

    assert [peak["label"] for peak in spec["peaks"]] == ["(100)", "(110)"]
    assert [peak["style"] for peak in spec["peaks"]] == ["--", "-"]
    assert program_check_figure_spec(spec) == []


def test_successful_fallback_prunes_failed_primary_spec(tmp_path) -> None:
    from app.figures import _prune_failed_primary_specs_with_generated_fallback

    Image.new("RGB", (120, 120), "white").save(tmp_path / "q1_model_fig_01.png")
    data = {
        "figures": [
            {"question_id": "q1", "figure_id": "q1_primary", "kind": "crystal_unit_cell"},
            {"question_id": "q1", "figure_id": "q1_model_fig_01", "kind": "model_generated_image"},
        ]
    }

    assert _prune_failed_primary_specs_with_generated_fallback(data, tmp_path) is True
    assert [spec["figure_id"] for spec in data["figures"]] == ["q1_model_fig_01"]


def test_materials_capability_hydrates_explicit_fcc_unit_cell_without_model_field() -> None:
    from app.capabilities.builtin.materials import materials_deterministic_figure_spec
    from app.figures import program_check_figure_spec

    spec = materials_deterministic_figure_spec(
        {
            "question": {
                "question_id": "q_fcc",
                "stem": "画出铝的面心立方晶胞，并说明其结构特征。",
            },
            "planned_kinds": ["crystal_unit_cell"],
            "purpose": "hydrate_explicit_spec",
        }
    )

    assert spec is not None
    assert spec["kind"] == "crystal_unit_cell"
    assert spec["structure"] == "fcc"
    assert spec["generation_basis"] == "materials.explicit_lattice_type_contract"
    assert program_check_figure_spec(spec) == []


def test_materials_capability_does_not_guess_unnamed_unit_cell_structure() -> None:
    from app.capabilities.builtin.materials import materials_deterministic_figure_spec

    spec = materials_deterministic_figure_spec(
        {
            "question": {"question_id": "q_unknown", "stem": "画出该材料的晶胞。"},
            "planned_kinds": ["crystal_unit_cell"],
            "purpose": "hydrate_explicit_spec",
        }
    )

    assert spec is None


def test_explicit_fcc_spec_is_hydrated_without_overwriting_model_fields() -> None:
    from app.figures import _hydrate_explicit_figure_spec, program_check_figure_spec

    question = {
        "question_id": "q-fcc",
        "stem": "请画出面心立方晶胞，并标出原子位置。",
        "figure_schema_plan": {
            "schema_resolution": {"status": "schema_found", "kind": "crystal_unit_cell"},
            "render_decision": {"strategy": "programmatic_renderer", "schema_kind": "crystal_unit_cell"},
        },
    }
    model_spec = {
        "question_id": "q-fcc",
        "figure_id": "model-owned-id",
        "kind": "crystal_unit_cell",
        "caption": "模型给出的标题",
    }

    hydrated = _hydrate_explicit_figure_spec(question, model_spec)

    assert hydrated["figure_id"] == "model-owned-id"
    assert hydrated["caption"] == "模型给出的标题"
    assert hydrated["structure"] == "fcc"
    assert hydrated["deterministic_hydration"]["filled_fields"] == ["structure"]
    assert program_check_figure_spec(hydrated) == []


def test_prepare_figures_does_not_reuse_image_fallback_for_programmatic_contract(tmp_path, monkeypatch) -> None:
    from app.figures import prepare_figures_for_fragments

    class Provider:
        name = "test"
        api_key = "secret"
        image_model = "image-model"
        image_size = "1024x1024"

    structured_exam = {
        "items": [
            {
                "question_id": "q1",
                "stem": "画出一个通用示意图",
                "answer_figure_required": True,
                "drawing_generation_mode": "figure_specs",
                "figure_schema_plan": {
                    "render_decision": {
                        "strategy": "programmatic_renderer",
                        "fallback_allowed": True,
                        "schema_kind": "custom_diagram",
                    },
                },
            }
        ]
    }
    fragments_json = tmp_path / "answer_fragments.json"
    fragments_json.write_text(
        json.dumps({"fragments": [{"question_id": "q1", "blocks": [], "figure_specs": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    specs_json = tmp_path / "figure_specs.json"
    output_dir = tmp_path / "figures"
    output_dir.mkdir()

    import app.figures as figures

    prompt = figures._direct_figure_prompt(
        structured_exam["items"][0],
        {"question_id": "q1", "blocks": [], "figure_specs": []},
        [],
    )
    fallback_image = Image.new("RGB", (600, 600), "white")
    fallback_draw = ImageDraw.Draw(fallback_image)
    fallback_draw.rectangle((80, 80, 520, 520), outline="black", width=12)
    fallback_draw.line((100, 500, 500, 100), fill="black", width=10)
    fallback_image.save(output_dir / "q1_model_fig_01.png")
    specs_json.write_text(
        json.dumps(
            {
                "figures": [
                    {
                        "question_id": "q1",
                        "figure_id": "q1_model_fig_01",
                        "kind": "model_generated_image",
                        "prompt": prompt,
                        "provider": "test",
                        "model": "image-model",
                        "image_size": "1024x1024",
                        "path": str(output_dir / "q1_model_fig_01.png"),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(figures, "provider_supports_image_generation", lambda _provider: True)

    class Client:
        def __init__(self, _provider):
            pass

        def generate_image(self, *_args, **_kwargs):
            raise AssertionError("matching fallback must be reused")

    monkeypatch.setattr(figures, "OpenAICompatibleClient", Client)

    generated = prepare_figures_for_fragments(
        structured_exam,
        fragments_json,
        specs_json,
        output_dir,
        provider=Provider(),
    )

    assert generated == []
    report = json.loads((tmp_path / "direct_model_figures.json").read_text(encoding="utf-8"))
    assert report["reused"] == []
    assert report["generated"] == []
