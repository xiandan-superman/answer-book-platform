from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FigureSemanticsTests(unittest.TestCase):
    def test_contract_id_is_stable_and_semantics_are_deduplicated(self) -> None:
        from app.capabilities.figure_semantics import build_figure_semantic_contract

        first = build_figure_semantic_contract(
            source_image_policy="none",
            required_elements=["曲线", "曲线"],
            required_labels=["T", "T"],
            relationship_constraints=["随温度升高而上升"],
            forbidden_assumptions=["不得虚构数值"],
            original_image_available=False,
        )
        second = build_figure_semantic_contract(
            source_image_policy="none",
            required_elements=["曲线"],
            required_labels=["T"],
            relationship_constraints=["随温度升高而上升"],
            forbidden_assumptions=["不得虚构数值"],
            original_image_available=False,
        )

        self.assertEqual(first.contract_id, second.contract_id)
        self.assertEqual(("曲线",), first.required_elements)
        self.assertEqual(("T",), first.required_labels)

    def test_registered_renderer_is_selected_independently_of_semantics(self) -> None:
        from app.capabilities.figure_semantics import (
            build_figure_semantic_contract,
            choose_figure_render_strategy,
        )

        contract = build_figure_semantic_contract(
            source_image_policy="none",
            required_elements=["坐标轴", "曲线"],
            original_image_available=False,
        )
        decision = choose_figure_render_strategy(
            contract,
            schema_status="schema_found",
            schema_kind="generic_axis_curve",
            renderer="draw_generic_axis_curve",
            drawing_mode="code",
        )

        self.assertEqual("programmatic_renderer", decision.strategy.value)
        self.assertEqual(contract.contract_id, decision.semantic_contract_id)

    def test_unknown_semantics_use_bounded_code_or_image_fallback(self) -> None:
        from app.capabilities.figure_semantics import (
            build_figure_semantic_contract,
            choose_figure_render_strategy,
        )

        contract = build_figure_semantic_contract(
            source_image_policy="none",
            required_elements=["未注册的专业结构"],
            original_image_available=False,
        )
        code = choose_figure_render_strategy(
            contract,
            schema_status="schema_proposed",
            drawing_mode="code",
        )
        image = choose_figure_render_strategy(
            contract,
            schema_status="schema_proposed",
            drawing_mode="figure_specs",
            image_model_available=True,
        )

        self.assertEqual("model_code_renderer", code.strategy.value)
        self.assertEqual("image_model_fallback", image.strategy.value)

    def test_overlay_requirement_selects_verified_overlay_without_replacement_fallback(self) -> None:
        from app.capabilities.figure_semantics import (
            build_figure_semantic_contract,
            choose_figure_render_strategy,
        )

        contract = build_figure_semantic_contract(
            source_image_policy="preserve_and_overlay",
            required_elements=["在原图标出方向"],
            original_image_available=True,
        )
        decision = choose_figure_render_strategy(
            contract,
            schema_status="schema_found",
            schema_kind="generic_axis_curve",
            renderer="draw_generic_axis_curve",
            drawing_mode="figure_specs",
            image_model_available=True,
        )

        self.assertEqual("source_image_overlay", decision.strategy.value)
        self.assertEqual("source_image_overlay", decision.schema_kind)
        self.assertEqual("draw_source_image_overlay", decision.renderer)
        self.assertFalse(decision.fallback_allowed)
        self.assertIn("preserve", decision.reason)

    def test_invalid_overlay_contract_is_machine_detectable(self) -> None:
        from app.capabilities.figure_semantics import (
            build_figure_semantic_contract,
            validate_figure_semantic_contract,
        )

        contract = build_figure_semantic_contract(
            source_image_policy="preserve_and_overlay",
            original_image_available=False,
        )

        self.assertEqual(["overlay_requires_original_image"], validate_figure_semantic_contract(contract))

    def test_planned_renderer_overrides_legacy_default_code_mode(self) -> None:
        from app.drawing_code import question_drawing_mode

        question = {
            "drawing_generation_mode": "code",
            "figure_schema_plan": {
                "render_decision": {"strategy": "programmatic_renderer"},
            },
        }

        self.assertEqual("figure_specs", question_drawing_mode(question))

    def test_planned_programmatic_schema_blocks_conflicting_legacy_heuristic(self) -> None:
        from app.figures import _figure_spec_for_question

        question = {
            "question_id": "q1",
            "question_type": "作图题",
            "stem": "画出面心立方晶胞。",
            "figure_schema_plan": {
                "schema_resolution": {"status": "schema_found", "kind": "crystal_unit_cell"},
                "render_decision": {"strategy": "programmatic_renderer"},
            },
        }

        self.assertIsNone(_figure_spec_for_question(question))

    def test_legacy_keyword_heuristic_is_disabled_without_a_semantic_plan(self) -> None:
        from app.figures import _figure_spec_for_question

        spec = _figure_spec_for_question(
            {
                "question_id": "q1",
                "question_type": "作图题",
                "stem": "画出面心立方晶胞。",
            }
        )

        self.assertIsNone(spec)

    def test_source_image_overlay_preserves_base_pixels_outside_annotations(self) -> None:
        import hashlib
        import tempfile

        from PIL import Image

        from app.figures import draw_source_image_overlay, validate_source_image_overlay_spec

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.png"
            output = root / "overlay.png"
            Image.new("RGB", (120, 80), (240, 241, 242)).save(source)
            spec = {
                "kind": "source_image_overlay",
                "source_image": str(source),
                "source_image_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "annotations": [{"type": "arrow", "start": [0.2, 0.2], "end": [0.8, 0.8], "label": "方向"}],
                "required_labels": ["方向"],
            }

            self.assertEqual([], validate_source_image_overlay_spec(spec))
            draw_source_image_overlay(spec, output)

            with Image.open(source) as base, Image.open(output) as rendered:
                self.assertEqual(base.getpixel((0, 0)), rendered.getpixel((0, 0)))
                self.assertNotEqual(base.tobytes(), rendered.tobytes())

    def test_source_image_overlay_rejects_changed_base_image(self) -> None:
        import tempfile

        from PIL import Image

        from app.figures import validate_source_image_overlay_spec

        with tempfile.TemporaryDirectory() as raw_tmp:
            source = Path(raw_tmp) / "source.png"
            Image.new("RGB", (40, 40), "white").save(source)
            issues = validate_source_image_overlay_spec(
                {
                    "kind": "source_image_overlay",
                    "source_image": str(source),
                    "source_image_sha256": "0" * 64,
                    "annotations": [{"type": "point", "xy": [0.5, 0.5]}],
                }
            )

        self.assertIn("source_image_overlay: source image hash mismatch", issues)

    def test_multiple_source_images_require_explicit_overlay_binding(self) -> None:
        import tempfile

        from PIL import Image

        from app.figures import _explicit_figure_specs

        with tempfile.TemporaryDirectory() as raw_tmp:
            first = Path(raw_tmp) / "first.png"
            second = Path(raw_tmp) / "second.png"
            Image.new("RGB", (20, 20), "white").save(first)
            Image.new("RGB", (20, 20), "black").save(second)
            specs = _explicit_figure_specs(
                {"_draft": {"figure_specs": [{"kind": "source_image_overlay", "annotations": [{"type": "point", "xy": [0.5, 0.5]}]}]}},
                "q1",
                {
                    "image_refs": [str(first), str(second)],
                    "figure_schema_plan": {"render_decision": {"strategy": "source_image_overlay"}},
                },
            )

        self.assertEqual("multiple source images require an explicit source_image_index", specs[0]["overlay_binding_issue"])

    def test_overlay_specs_render_without_invoking_image_model_fallback(self) -> None:
        import hashlib
        import json
        import tempfile

        from PIL import Image

        from app.figures import prepare_figures_for_fragments

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.png"
            Image.new("RGB", (160, 100), "white").save(source)
            expected_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            contract = {
                "contract_id": "ignored_and_rebuilt_from_content",
                "figure_role": "answer_required",
                "source_image_policy": "preserve_and_overlay",
                "required_elements": ["箭头"],
                "required_labels": ["力"],
                "relationship_constraints": [],
                "forbidden_assumptions": [],
                "original_image_available": True,
            }
            from app.capabilities.figure_semantics import semantic_contract_from_mapping

            bound_contract = semantic_contract_from_mapping(contract).to_dict()
            decision = {
                "strategy": "source_image_overlay",
                "reason": "test",
                "semantic_contract_id": bound_contract["contract_id"],
                "schema_kind": "source_image_overlay",
                "renderer": "draw_source_image_overlay",
                "fallback_allowed": False,
            }
            question = {
                "question_id": "q1",
                "question_type": "作图题",
                "stem": "在原图中标出力的方向。",
                "image_refs": [str(source)],
                "figure_schema_plan": {
                    "figure_semantic_contract": bound_contract,
                    "render_decision": decision,
                    "schema_resolution": {"status": "schema_proposed", "proposed_kind": "original_annotation"},
                },
            }
            fragments_json = root / "answer_fragments.json"
            fragments_json.write_text(
                json.dumps(
                    {
                        "fragments": [
                            {
                                "question_id": "q1",
                                "answer": "见图。",
                                "blocks": [],
                                "_draft": {
                                    "figure_specs": [
                                        {
                                            "kind": "source_image_overlay",
                                            "caption": "原图标注",
                                            "required_labels": ["力"],
                                            "annotations": [
                                                {"type": "arrow", "start": [0.2, 0.5], "end": [0.8, 0.5], "label": "力"}
                                            ],
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            specs_json = root / "figure_specs.json"
            generated = prepare_figures_for_fragments(
                {"items": [question]},
                fragments_json,
                specs_json,
                root / "figures",
            )
            audit = json.loads((root / "figure_generation_audit.json").read_text(encoding="utf-8"))
            saved_specs = json.loads(specs_json.read_text(encoding="utf-8"))["figures"]

        self.assertEqual(1, len(generated))
        self.assertEqual(expected_source_hash, saved_specs[0]["source_image_sha256"])
        self.assertEqual("source_image_overlay", audit["items"][0]["generation_method"])
        self.assertEqual([], audit["direct_model_generation"]["generated"])

    def test_outcome_audit_detects_binding_schema_and_forbidden_fallback(self) -> None:
        from app.capabilities.figure_semantics import (
            FigureRenderDecision,
            RenderStrategy,
            audit_figure_render_outcome,
            build_figure_semantic_contract,
        )

        contract = build_figure_semantic_contract(
            source_image_policy="none",
            required_elements=["曲线"],
            original_image_available=False,
        )
        wrong_binding = FigureRenderDecision(
            strategy=RenderStrategy.PROGRAMMATIC_RENDERER,
            reason="test",
            semantic_contract_id="wrong",
            schema_kind="generic_axis_curve",
            renderer="draw_generic_axis_curve",
            fallback_allowed=False,
        )

        issues = audit_figure_render_outcome(
            contract,
            wrong_binding,
            actual_kind="custom_diagram",
            generation_method="image_model",
        )

        self.assertEqual(
            {
                "semantic_contract_id_mismatch",
                "actual_schema_kind_differs_from_plan",
                "actual_generation_method_differs_from_plan",
                "forbidden_fallback_was_used",
            },
            set(issues),
        )

    def test_outcome_audit_allows_registered_renderer_to_fallback_when_policy_allows(self) -> None:
        from app.capabilities.figure_semantics import (
            audit_figure_render_outcome,
            build_figure_semantic_contract,
            choose_figure_render_strategy,
        )

        contract = build_figure_semantic_contract(
            source_image_policy="none",
            required_elements=["曲线"],
            original_image_available=False,
        )
        decision = choose_figure_render_strategy(
            contract,
            schema_status="schema_found",
            schema_kind="generic_axis_curve",
            renderer="draw_generic_axis_curve",
        )

        issues = audit_figure_render_outcome(
            contract,
            decision,
            actual_kind="model_generated_image",
            generation_method="image_model",
        )

        self.assertEqual([], issues)


if __name__ == "__main__":
    unittest.main()
