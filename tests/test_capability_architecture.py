from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CapabilityArchitectureTests(unittest.TestCase):
    def test_default_registry_separates_core_and_material_schema_ownership(self) -> None:
        from app.capabilities.catalog import DEFAULT_CAPABILITY_REGISTRY, get_schema

        manifests = {item["capability_id"]: item for item in DEFAULT_CAPABILITY_REGISTRY.manifest_snapshot()}
        self.assertEqual(
            {"core.academic_expressions", "core.figures", "materials.figures"},
            set(manifests),
        )
        self.assertEqual(0, manifests["core.academic_expressions"]["schema_count"])
        self.assertGreater(manifests["core.academic_expressions"]["expression_rule_count"], 0)
        generic_curve = get_schema("generic_axis_curve")
        process_flow = get_schema("process_flow_diagram")
        diffraction = get_schema("zone_axis_diffraction")
        self.assertIsNotNone(generic_curve)
        self.assertIsNotNone(process_flow)
        self.assertIsNotNone(diffraction)
        assert generic_curve is not None and process_flow is not None and diffraction is not None
        self.assertEqual("core.figures", generic_curve["capability_id"])
        self.assertEqual("core.figures", process_flow["capability_id"])
        self.assertEqual("materials.figures", diffraction["capability_id"])

    def test_duplicate_capabilities_and_schema_kinds_are_rejected(self) -> None:
        from app.capabilities import CapabilityManifest, CapabilityRegistry

        schema = {
            "schema_id": "example.v1",
            "kind": "example",
            "name": "Example",
            "description": "Example schema",
            "required_fields": ["kind"],
            "renderer": "draw_example",
        }
        first = CapabilityManifest("example.one", "1", "One", "First", schemas=(schema,))
        registry = CapabilityRegistry((first,))
        with self.assertRaisesRegex(ValueError, "capability already registered"):
            registry.register(first)
        second = CapabilityManifest("example.two", "1", "Two", "Second", schemas=(schema,))
        with self.assertRaisesRegex(ValueError, "schema kinds already registered"):
            registry.register(second)

    def test_unknown_local_drawing_is_not_forced_into_axis_curve(self) -> None:
        from app.figure_schema_planning import infer_schema_kind_locally

        kind, reason = infer_schema_kind_locally({"stem": "请画出题目要求的专用空间结构示意。"})
        self.assertEqual("unregistered_diagram", kind)
        self.assertIn("匹配证据", reason)

    def test_specific_discipline_match_beats_generic_keyword(self) -> None:
        from app.capabilities.catalog import match_schema_for_text

        match = match_schema_for_text("请画出材料的 S-N 疲劳曲线，并标出疲劳极限。")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual("fatigue_sn_curve", match.schema_kind)
        self.assertEqual("materials.figures", match.capability_id)

    def test_prompt_catalog_only_loads_materials_for_matching_question(self) -> None:
        from app.capabilities.catalog import capability_ids_for_text, schema_prompt_catalog

        generic_ids = capability_ids_for_text("请画出速度随时间变化的坐标曲线。")
        generic_kinds = {item["kind"] for item in schema_prompt_catalog(generic_ids)}
        self.assertNotIn("materials.figures", generic_ids)
        self.assertIn("generic_axis_curve", generic_kinds)
        self.assertNotIn("zone_axis_diffraction", generic_kinds)

        material_ids = capability_ids_for_text("请画出晶面和晶向并标注密勒指数。")
        material_kinds = {item["kind"] for item in schema_prompt_catalog(material_ids)}
        self.assertIn("materials.figures", material_ids)
        self.assertIn("crystal_plane_direction", material_kinds)

    def test_figure_planner_does_not_send_material_registry_for_generic_question(self) -> None:
        import json

        from app.figure_schema_planning import _model_plan_prompt

        messages = _model_plan_prompt(
            {"question_id": "q1", "question_type": "作图题", "stem": "画出速度-时间曲线。"}
        )
        payload = json.loads(messages[1]["content"])
        kinds = {item["kind"] for item in payload["available_schema_registry"]}

        self.assertNotIn("materials.figures", payload["active_capability_ids"])
        self.assertNotIn("binary_phase_diagram", kinds)

    def test_generic_visual_understanding_prompt_does_not_load_material_policy(self) -> None:
        import json

        from app.question_understanding import _vision_prompt

        messages = _vision_prompt(
            {"question_id": "q1", "stem": "根据速度-时间曲线回答问题。"},
            {"question_id": "q1", "text": "根据速度-时间曲线回答问题。", "images": [], "tables": []},
        )
        payload = json.loads(messages[1]["content"][0]["text"])
        image_schema = payload["output_schema"]["images"][0]
        hard_rules = "\n".join(payload["hard_rules"])

        self.assertNotIn("invariant_horizontal_lines", image_schema)
        self.assertNotIn("unit_cell_site_families", image_schema)
        self.assertNotIn("phase diagram", hard_rules)
        self.assertNotIn("crystal unit-cell", hard_rules)

    def test_answer_prompt_only_loads_crystallographic_policy_for_matching_question(self) -> None:
        import json

        from app.prompts import build_answer_draft_prompt

        generic_messages = build_answer_draft_prompt(
            {"question_id": "q1", "question_type": "简答题", "stem": "说明速度与时间的关系。"},
            [],
        )
        material_messages = build_answer_draft_prompt(
            {"question_id": "q2", "question_type": "简答题", "stem": "写出晶面族{10-10}的规范表示。"},
            [],
        )
        generic_payload = json.loads(generic_messages[-1]["content"])
        material_payload = json.loads(material_messages[-1]["content"])

        self.assertNotIn("crystallographic plane indices", "\n".join(generic_payload["hard_rules"]))
        self.assertIn("crystallographic plane indices", "\n".join(material_payload["hard_rules"]))

    def test_quality_finding_is_separate_from_policy_action(self) -> None:
        from app.capabilities import FindingSeverity, PolicyAction, QualityFinding, QualityPolicy

        finding = QualityFinding(
            code="figure.missing_required_label",
            message="图中缺少题目要求的标签。",
            source="core.figure_contract",
            severity=FindingSeverity.ERROR,
            confidence=0.97,
            evidence={"label": "A"},
        )
        strict = QualityPolicy(
            blocking_codes=frozenset({finding.code}),
            minimum_block_confidence=0.98,
        )
        self.assertEqual(PolicyAction.WARN, strict.action_for(finding))
        high_confidence = QualityFinding(
            code=finding.code,
            message=finding.message,
            source=finding.source,
            severity=finding.severity,
            confidence=0.99,
        )
        self.assertEqual(PolicyAction.BLOCK, strict.action_for(high_confidence))
        self.assertEqual("error", high_confidence.to_dict()["severity"])

    def test_renderer_registry_dispatches_without_kind_branch(self) -> None:
        from app.capabilities import RendererRegistry

        calls: list[tuple[str, Path]] = []

        def render(spec: dict[str, object], output: Path) -> None:
            calls.append((str(spec["kind"]), output))

        registry = RendererRegistry({"example": render})
        with tempfile.TemporaryDirectory() as raw_tmp:
            output = Path(raw_tmp) / "example.png"
            self.assertTrue(registry.render("example", {"kind": "example"}, output))
            self.assertFalse(registry.render("missing", {"kind": "missing"}, output))
        self.assertEqual("example", calls[0][0])

    def test_schema_renderers_are_assembled_from_capability_declarations(self) -> None:
        from app.capabilities.catalog import registry_snapshot
        from app.figures import _figure_renderer_registry

        registry = _figure_renderer_registry()
        declared = {str(schema["kind"]) for schema in registry_snapshot()}
        self.assertTrue(declared)
        self.assertTrue(declared.issubset(set(registry.kinds())))

    def test_renderer_assembly_rejects_missing_implementation_before_execution(self) -> None:
        from app.capabilities import assemble_renderer_registry, renderer_binding_issues

        schemas = ({"kind": "example", "renderer": "draw_missing"},)
        issues = renderer_binding_issues(schemas, {})
        self.assertEqual(
            ("renderer implementation not found: example -> draw_missing",),
            issues,
        )
        with self.assertRaisesRegex(ValueError, "example -> draw_missing"):
            assemble_renderer_registry(schemas, {})

    def test_legacy_schema_facade_stays_compatible(self) -> None:
        from app.figure_schema_registry import MATERIAL_SCHEMA_KINDS, get_schema, registry_snapshot

        self.assertTrue(set(MATERIAL_SCHEMA_KINDS).issubset({item["kind"] for item in registry_snapshot()}))
        diffraction = get_schema("zone_axis_diffraction")
        self.assertIsNotNone(diffraction)
        assert diffraction is not None
        self.assertEqual("zone_axis_diffraction.v1", diffraction["schema_id"])


if __name__ == "__main__":
    unittest.main()
