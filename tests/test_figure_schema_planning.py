from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


ALL_MATERIAL_SCHEMA_KINDS = {
    "generic_axis_curve",
    "multi_curve_axis_plot",
    "binary_phase_diagram",
    "ternary_phase_diagram",
    "crystal_unit_cell",
    "crystal_plane_direction",
    "zone_axis_diffraction",
    "xrd_pattern",
    "microstructure_schematic",
    "defect_structure_schematic",
    "process_flow_diagram",
    "fe_c_phase_diagram",
    "ttt_diagram",
    "cct_diagram",
    "heat_treatment_curve",
    "stress_strain_curve",
    "creep_curve",
    "fatigue_sn_curve",
    "dislocation_schematic",
    "slip_system_schematic",
    "precipitation_aging_curve",
    "recrystallization_grain_growth",
    "corrosion_polarization_curve",
    "welding_thermal_cycle",
    "dsc_curve",
    "polymer_chain_structure",
    "polymer_configuration_conformation",
    "polymer_crystalline_morphology",
    "spherulite_schematic",
    "tga_curve",
    "dma_curve",
    "viscoelastic_creep_curve",
    "stress_relaxation_curve",
    "time_temperature_superposition",
    "polymer_stress_strain_curve",
    "molecular_weight_distribution",
    "polymer_blend_phase_diagram",
    "rheology_flow_curve",
    "ceramic_crystal_structure",
    "silicate_structure_schematic",
    "glass_network_structure",
    "ceramic_phase_diagram",
    "sintering_densification_curve",
    "sintering_microstructure_evolution",
    "porous_ceramic_microstructure",
    "defect_chemistry_diagram",
    "ionic_conductivity_arrhenius",
    "dielectric_temperature_curve",
    "ferroelectric_hysteresis_loop",
    "magnetic_hysteresis_loop",
    "fracture_toughness_schematic",
}


class FigureSchemaPlanningTests(unittest.TestCase):
    def test_xrd_output_intent_outranks_body_centered_lattice_keyword(self) -> None:
        from app.figure_schema_planning import infer_schema_kind_locally

        question = {"stem": "体心立方固溶体发生有序化后，画出 X 射线粉末衍射峰的相对位置。"}
        kind, _ = infer_schema_kind_locally(question)
        self.assertEqual("xrd_pattern", kind)

    def test_bcc_ordering_xrd_fallback_is_machine_derived(self) -> None:
        from app.figures import _figure_spec_for_question, program_check_figure_spec

        question = {
            "question_id": "q-xrd",
            "stem": "体心立方固溶体有序化后衍射峰如何变化",
            "figure_schema_plan": {"schema_resolution": {"kind": "xrd_pattern"}},
        }
        spec = _figure_spec_for_question(question)
        self.assertIsNotNone(spec)
        self.assertEqual("xrd_pattern", spec["kind"])
        self.assertEqual("materials.bcc_cscl_extinction_contract", spec["generation_basis"])
        self.assertEqual("materials.figures", spec["capability_id"])
        self.assertTrue(any(peak["style"] == "--" for peak in spec["peaks"]))
        labels = {peak["label"] for peak in spec["peaks"]}
        self.assertTrue({"321", "400", "311", "320"}.issubset(labels))
        self.assertEqual(16, max(float(peak["two_theta"]) for peak in spec["peaks"]))
        self.assertEqual([], program_check_figure_spec(spec))

    def test_negative_diffraction_index_uses_crystallographic_overbar(self) -> None:
        from app.figures import _format_hkl_plot_label

        self.assertEqual(r"$(1\ \overline{1}\ 0)$", _format_hkl_plot_label(1, -1, 0))

    def test_explicit_figure_specs_collapse_legacy_question_mirror(self) -> None:
        from app.figures import _explicit_figure_specs

        specs = _explicit_figure_specs(
            {
                "figure_specs": [
                    {"kind": "zone_axis_diffraction", "zone_axis": "[110]", "lattice": "bcc"},
                    {"kind": "zone_axis_diffraction", "zone_axis": "[110]", "lattice": "bcc", "answer_unit_number": "1"},
                ]
            },
            "q1",
        )

        self.assertEqual(1, len(specs))
        self.assertEqual("1", specs[0]["answer_unit_number"])

    def test_explicit_figure_specs_deduplicate_same_semantic_contract(self) -> None:
        from app.figures import _explicit_figure_specs

        specs = _explicit_figure_specs(
            {
                "figure_specs": [
                    {
                        "kind": "microstructure_schematic",
                        "answer_unit_number": "2.2",
                        "semantic_contract_id": "contract_1",
                        "features": [{"label": "A"}],
                        "source": "answer_unit",
                    },
                    {
                        "kind": "microstructure_schematic",
                        "answer_unit_number": "2.2",
                        "semantic_contract_id": "contract_1",
                        "features": [{"label": "A"}, {"label": "B"}],
                        "source": "visual_qa_vision_reviewer_candidate",
                    },
                ]
            },
            "q1",
        )

        self.assertEqual(1, len(specs))
        self.assertEqual("visual_qa_vision_reviewer_candidate", specs[0]["source"])

    def test_attached_question_figure_is_reference_only_when_answer_draws_new_figures(self) -> None:
        from app.figure_schema_planning import _semantic_contract

        question = {
            "question_id": "q-reference",
            "question_type": "作图题",
            "stem": "图为合金相图。画出该合金的冷却曲线和室温组织示意图。",
            "image_refs": ["source.png"],
        }
        contract = _semantic_contract(
            question,
            {"source_image_policy": "preserve_and_overlay"},
        )

        self.assertEqual("reference_only", contract["source_image_policy"])

    def test_explicit_instruction_to_mark_source_image_requires_overlay(self) -> None:
        from app.figure_schema_planning import _semantic_contract

        question = {
            "question_id": "q-overlay",
            "question_type": "作图题",
            "stem": "在原图中标出三个恒温转变位置。",
            "image_refs": ["source.png"],
        }

        self.assertEqual(
            "preserve_and_overlay",
            _semantic_contract(question)["source_image_policy"],
        )

    def test_planning_scope_excludes_non_drawing_sibling_units(self) -> None:
        from app.figure_schema_planning import _drawing_scope

        question = {
            "question_id": "q-mixed",
            "question_type": "计算题",
            "stem": "综合题",
            "subquestions": [
                {"number": "1", "question_type": "简答题", "stem": "说明三个反应。"},
                {
                    "number": "2",
                    "question_type": "计算题",
                    "stem": "画图并计算。",
                    "requirements": [
                        {"number": "2.1", "question_type": "作图题", "stem": "画出冷却曲线。"},
                        {"number": "2.2", "question_type": "作图题", "stem": "画出组织示意图。"},
                        {"number": "2.3", "question_type": "计算题", "stem": "计算组成。"},
                    ],
                },
                {"number": "3", "question_type": "简答题", "stem": "比较拉伸强度。"},
            ],
        }

        scoped = _drawing_scope(question)

        self.assertIn("画出冷却曲线", scoped["stem"])
        self.assertIn("画出组织示意图", scoped["stem"])
        self.assertNotIn("计算组成", scoped["stem"])
        self.assertNotIn("拉伸强度", scoped["stem"])

    def test_semantic_contract_cannot_invent_panels_from_reference_image(self) -> None:
        from app.figure_schema_planning import _drawing_scope, _semantic_contract

        question = {
            "question_id": "q-mixed",
            "question_type": "计算题",
            "stem": "根据所给相图完成综合题。",
            "image_refs": ["phase_diagram.png"],
            "subquestions": [
                {
                    "number": "2",
                    "question_type": "计算题",
                    "stem": "画图并计算。",
                    "requirements": [
                        {"number": "2.1", "question_type": "作图题", "stem": "画出冷却曲线。"},
                        {"number": "2.2", "question_type": "作图题", "stem": "画出组织示意图。"},
                        {"number": "2.3", "question_type": "计算题", "stem": "计算组成。"},
                    ],
                }
            ],
        }

        contract = _semantic_contract(
            _drawing_scope(question),
            {
                "required_elements": [
                    "重画相图并叠加冷却路径",
                    "画出水淬与室冷组织",
                    "画出应力应变曲线",
                ]
            },
        )

        self.assertEqual(["画出冷却曲线。", "画出组织示意图。"], contract["required_elements"])

    def test_first_batch_registry_contains_all_material_schema_kinds(self) -> None:
        from app.figure_schema_registry import registry_snapshot

        registry = registry_snapshot()
        registered_kinds = {entry["kind"] for entry in registry}
        self.assertTrue(ALL_MATERIAL_SCHEMA_KINDS.issubset(registered_kinds))
        overlay = next(entry for entry in registry if entry["kind"] == "source_image_overlay")
        self.assertEqual("core.figures", overlay["capability_id"])
        for entry in registry:
            self.assertTrue(entry["schema_id"].endswith(".v1"), entry)
            self.assertTrue(entry["renderer"], entry)
            self.assertTrue(entry["required_fields"], entry)

    def test_planning_resolves_drawing_question_to_registry_schema(self) -> None:
        from app.figure_schema_planning import plan_figure_schemas

        exam = {
            "items": [
                {
                    "question_id": "q1",
                    "question_type": "作图题",
                    "stem": "画出[110]带轴电子衍射花样，并标出主要衍射斑指数。",
                },
                {
                    "question_id": "q2",
                    "question_type": "计算题",
                    "stem": "画出曲线后计算弹性模量。",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            output = Path(raw_tmp) / "figure_schema_plan.json"
            report = plan_figure_schemas(exam, output)

        self.assertEqual("answer_book.figure_schema_plan.v1", report["schema_version"])
        # R2 treats an explicit drawing command as authoritative even when the
        # parent question is confirmed as a calculation question.
        self.assertEqual(2, report["planned_count"])
        plan = report["items"][0]
        self.assertEqual("q1", plan["question_id"])
        self.assertEqual("zone_axis_diffraction", plan["schema_resolution"]["kind"])
        self.assertEqual("schema_found", plan["schema_resolution"]["status"])
        self.assertTrue(plan["diagram_intent"]["needs_figure"])
        self.assertEqual("answer_required", plan["figure_semantic_contract"]["figure_role"])
        self.assertEqual("none", plan["figure_semantic_contract"]["source_image_policy"])
        self.assertTrue(plan["figure_semantic_contract"]["contract_id"].startswith("figure_contract_"))
        self.assertEqual("programmatic_renderer", plan["render_decision"]["strategy"])
        self.assertEqual(
            plan["figure_semantic_contract"]["contract_id"],
            plan["render_decision"]["semantic_contract_id"],
        )
        self.assertEqual(1, len([item for item in report["items"] if item["question_id"] == "q2"]))

    def test_parallel_schema_planning_returns_question_order(self) -> None:
        from app.figure_schema_planning import plan_figure_schemas

        thread_ids: set[int] = set()

        def fake_plan(question, **_kwargs):
            thread_ids.add(threading.get_ident())
            time.sleep(0.04 if question["question_id"] != "q02" else 0.01)
            return {"question_id": question["question_id"], "schema_resolution": {"status": "schema_found"}}

        exam = {"items": [
            {"question_id": "q01", "question_type": "作图题"},
            {"question_id": "q02", "question_type": "作图题"},
            {"question_id": "q03", "question_type": "作图题"},
        ]}
        with tempfile.TemporaryDirectory() as raw_tmp, patch("app.figure_schema_planning._plan_one", side_effect=fake_plan):
            report = plan_figure_schemas(exam, Path(raw_tmp) / "figure_schema_plan.json")

        self.assertEqual(["q01", "q02", "q03"], [item["question_id"] for item in report["items"]])
        self.assertTrue(report["concurrency"]["parallel_enabled"])
        self.assertGreaterEqual(len(thread_ids), 2)

    def test_figure_generation_writes_schema_audit_for_programmatic_and_fallback_paths(self) -> None:
        from app.figures import prepare_figures_for_fragments

        structured_exam = {
            "items": [
                {"question_id": "q1", "question_type": "作图题", "drawing_generation_mode": "figure_specs", "stem": "画出[110]带轴电子衍射花样。"},
                {"question_id": "q2", "question_type": "作图题", "drawing_generation_mode": "figure_specs", "stem": "画出一种暂未支持的新型复杂图。"},
            ]
        }
        fragments = {
            "fragments": [
                {
                    "question_id": "q1",
                    "answer": "见图",
                    "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "按带轴定律作图。"}]}],
                    "_draft": {
                        "figure_specs": [
                            {
                                "kind": "zone_axis_diffraction",
                                "caption": "[110]带轴电子衍射花样",
                                "zone_axis": [1, 1, 0],
                                "lattice": "generic_cubic",
                                "max_index": 2,
                                "label_indices": [[0, 0, 0], [1, -1, 0], [0, 0, 1]],
                            }
                        ]
                    },
                },
                {
                    "question_id": "q2",
                    "answer": "见图",
                    "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "模型未给出可渲染规格。"}]}],
                    "_draft": {"figure_specs": [{"kind": "unknown_professional_diagram", "caption": "复杂图"}]},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            fragments_json = tmp / "answer_fragments.json"
            specs_json = tmp / "figure_specs.json"
            fragments_json.write_text(json.dumps(fragments, ensure_ascii=False), encoding="utf-8")
            generated = prepare_figures_for_fragments(structured_exam, fragments_json, specs_json, tmp / "figures")
            report = json.loads((tmp / "figure_generation_audit.json").read_text(encoding="utf-8"))

        self.assertEqual(1, len(generated))
        self.assertEqual("answer_book.figure_generation_audit.v1", report["schema_version"])
        by_qid = {item["question_id"]: item for item in report["items"]}
        self.assertEqual("programmatic_renderer", by_qid["q1"]["generation_method"])
        self.assertEqual("schema_found", by_qid["q1"]["schema_status"])
        self.assertFalse(by_qid["q1"]["needs_manual_review"])
        self.assertEqual("render_failed", by_qid["q2"]["schema_status"])
        self.assertEqual("none", by_qid["q2"]["generation_method"])
        self.assertTrue(by_qid["q2"]["needs_manual_review"])

    def test_default_drawing_generation_uses_model_code_specs(self) -> None:
        from app.figures import prepare_figures_for_fragments

        drawing_code = """
import matplotlib.pyplot as plt

def draw(output_path: str) -> None:
    fig, ax = plt.subplots(figsize=(4, 3), dpi=120)
    ax.scatter([0, 1, 2], [0, 1, 0], c="black", marker="o", s=70)
    ax.plot([0, 1, 2], [0, 1, 0], color="black", linestyle="--", linewidth=1.5)
    ax.text(0, -0.2, "起点", ha="center", va="top")
    ax.text(1, 1.1, "峰位", ha="center", va="bottom")
    ax.set_xlabel("横坐标")
    ax.set_ylabel("纵坐标")
    ax.set_title("题目图示")
    ax.set_xlim(-0.4, 2.4)
    ax.set_ylim(-0.5, 1.4)
    ax.grid(True, color="0.85", linestyle=":")
    fig.tight_layout()
    fig.savefig(output_path, facecolor="white")
    plt.close(fig)
""".strip()
        structured_exam = {
            "items": [
                {"question_id": "q1", "question_type": "作图题", "stem": "画出示意图。"},
            ]
        }
        fragments = {
            "fragments": [
                {
                    "question_id": "q1",
                    "answer": "见图",
                    "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "按题意作图。"}]}],
                    "_draft": {"drawing_code_specs": [{"caption": "示意图", "code": drawing_code}]},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            fragments_json = tmp / "answer_fragments.json"
            specs_json = tmp / "figure_specs.json"
            fragments_json.write_text(json.dumps(fragments, ensure_ascii=False), encoding="utf-8")
            generated = prepare_figures_for_fragments(structured_exam, fragments_json, specs_json, tmp / "figures")
            specs_data = json.loads(specs_json.read_text(encoding="utf-8"))
            report = json.loads((tmp / "figure_generation_audit.json").read_text(encoding="utf-8"))
            updated_fragments = json.loads(fragments_json.read_text(encoding="utf-8"))
            self.assertTrue(Path(specs_data["figures"][0]["code_path"]).exists())

        self.assertEqual(1, len(generated))
        self.assertEqual("model_drawing_code", specs_data["figures"][0]["kind"])
        self.assertEqual("code", specs_data["figures"][0]["drawing_generation_mode"])
        self.assertEqual("model_code_renderer", report["items"][0]["generation_method"])
        self.assertFalse(report["items"][0]["needs_manual_review"])
        figure_segments = [
            segment
            for block in updated_fragments["fragments"][0]["blocks"]
            if block.get("label") == "图示"
            for segment in block.get("segments", [])
            if segment.get("type") == "image_ref"
        ]
        self.assertEqual(1, len(figure_segments))

    def test_code_mode_prefers_independent_drawing_generator_over_answer_draft(self) -> None:
        from app.figures import prepare_figures_for_fragments
        from app.settings import ProviderConfig

        answer_draft_code = """
import matplotlib.pyplot as plt

def draw(output_path: str) -> None:
    fig, ax = plt.subplots()
    ax.scatter([0], [0], c='black')
    fig.savefig(output_path)
    plt.close(fig)
""".strip()
        independent_code = """
import matplotlib.pyplot as plt

def draw(output_path: str) -> None:
    fig, ax = plt.subplots(figsize=(4, 3), dpi=120)
    ax.plot([0, 1], [0, 1], color='black', linestyle='-', linewidth=2)
    ax.text(0.5, 0.5, '独立作图器', ha='center')
    ax.set_xlabel('横坐标')
    ax.set_ylabel('纵坐标')
    fig.tight_layout()
    fig.savefig(output_path, facecolor='white')
    plt.close(fig)
""".strip()
        structured_exam = {"items": [{"question_id": "q1", "question_type": "作图题", "drawing_generation_mode": "code", "stem": "画出示意图。"}]}
        fragments = {
            "fragments": [
                {
                    "question_id": "q1",
                    "answer": "见图",
                    "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "按题意作图。"}]}],
                    "_draft": {"drawing_code_specs": [{"figure_id": "draft_fig", "caption": "草稿图", "code": answer_draft_code}]},
                }
            ]
        }
        provider = ProviderConfig(
            name="deepseek",
            type="openai_compatible",
            base_url="http://unused",
            api_key="key",
            default_model="deepseek-v4-pro",
            model_options=("deepseek-v4-pro",),
            allow_custom_model=True,
            model_hint="",
            temperature=0.2,
            max_tokens=12288,
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            fragments_json = tmp / "answer_fragments.json"
            specs_json = tmp / "figure_specs.json"
            fragments_json.write_text(json.dumps(fragments, ensure_ascii=False), encoding="utf-8")
            with patch(
                "app.figures.generate_drawing_code_spec",
                return_value={
                    "figure_id": "independent_fig",
                    "question_id": "q1",
                    "kind": "model_drawing_code",
                    "caption": "独立作图",
                    "code": independent_code,
                    "notes": "",
                },
            ) as mocked:
                generated = prepare_figures_for_fragments(
                    structured_exam,
                    fragments_json,
                    specs_json,
                    tmp / "figures",
                    code_provider=provider,
                    code_model="deepseek-v4-pro",
                )
            specs_data = json.loads(specs_json.read_text(encoding="utf-8"))

        mocked.assert_called_once()
        self.assertEqual(1, len(generated))
        self.assertEqual("independent_fig", specs_data["figures"][0]["figure_id"])
        self.assertEqual("independent_code_generator", specs_data["figures"][0]["source"])
        self.assertIn("独立作图器", specs_data["figures"][0]["code"])
        self.assertNotIn("draft_fig", json.dumps(specs_data, ensure_ascii=False))

    def test_drawing_domain_quality_rules_cover_material_figures(self) -> None:
        from app.drawing_code import drawing_domain_quality_rules

        zone_rules = drawing_domain_quality_rules({"stem": "对于体心立方点阵画出[110]带轴电子衍射花样。"})
        xrd_rules = drawing_domain_quality_rules({"stem": "A:B=1:1体心立方固溶体有序化前后X射线粉末衍射峰相对位置。"})

        self.assertTrue(any("spot array" in rule for rule in zone_rules), zone_rules)
        self.assertTrue(any("Do not invent lattice constants" in rule for rule in xrd_rules), xrd_rules)

    def test_drawing_code_validation_rejects_color_dependent_output(self) -> None:
        from app.drawing_code import validate_drawing_code

        issues = validate_drawing_code(
            """
import matplotlib.pyplot as plt

def draw(output_path: str) -> None:
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], color="blue", label="blue peak")
    fig.savefig(output_path)
""".strip()
        )

        self.assertTrue(any("non-monochrome color" in issue for issue in issues), issues)
        self.assertTrue(any("Chinese or standard notation" in issue for issue in issues), issues)

    def test_drawing_code_runtime_rejects_missing_glyph_warnings(self) -> None:
        from app.drawing_code import _runtime_stderr_issues

        issues = _runtime_stderr_issues("UserWarning: Glyph 24378 missing from font(s) DejaVu Sans.")

        self.assertTrue(any("glyphs" in issue for issue in issues), issues)

    def test_drawing_runner_allows_platforms_without_resource_module(self) -> None:
        from app.drawing_code import RUNNER_TEMPLATE

        self.assertIn("try:\n    import resource", RUNNER_TEMPLATE)
        self.assertIn("except ImportError:\n    resource = None", RUNNER_TEMPLATE)
        self.assertIn("if resource is not None:", RUNNER_TEMPLATE)

    def test_drawing_code_forces_cjk_font_before_tight_layout(self) -> None:
        from matplotlib import font_manager

        available = {font.name for font in font_manager.fontManager.ttflist}
        if not available.intersection({"Arial Unicode MS", "Songti SC", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", "SimHei"}):
            self.skipTest("No CJK font available in this environment")

        from app.drawing_code import run_drawing_code

        code = """
import matplotlib.pyplot as plt

def draw(output_path: str) -> None:
    plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], color='black')
    ax.set_title('中文标题')
    fig.tight_layout()
    fig.savefig(output_path)
""".strip()
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            result = run_drawing_code(code, tmp / "out.png", tmp / "code.py")

        self.assertTrue(result.ok, result.issues + [result.stderr])
        self.assertNotIn("Glyph", result.stderr)
        self.assertIn("SimHei", result.stdout)

    def test_drawing_code_detects_requested_font_families(self) -> None:
        from app.drawing_code import _requested_font_families

        code = """
import matplotlib.pyplot as plt

def draw(output_path: str) -> None:
    plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
    plt.rcParams.update({'font.family': ['Microsoft YaHei', 'sans-serif']})
""".strip()

        self.assertEqual(["SimHei", "DejaVu Sans", "Microsoft YaHei"], _requested_font_families(code))

    def test_drawing_code_font_source_config_supports_checksums_and_limits(self) -> None:
        from app.drawing_code import _font_download_urls, _font_max_bytes, _font_sha256_config

        old_env = {key: os.environ.get(key) for key in ("ANSWER_BOOK_FONT_URLS", "ANSWER_BOOK_FONT_SHA256", "ANSWER_BOOK_FONT_MAX_BYTES")}
        try:
            os.environ["ANSWER_BOOK_FONT_URLS"] = "SimHei=https://mirror.example/fonts/simhei.ttf"
            os.environ["ANSWER_BOOK_FONT_SHA256"] = "SimHei=sha256:" + ("a" * 64)
            os.environ["ANSWER_BOOK_FONT_MAX_BYTES"] = str(1024)

            self.assertEqual({"SimHei": "https://mirror.example/fonts/simhei.ttf"}, _font_download_urls())
            self.assertEqual({"SimHei": "a" * 64}, _font_sha256_config())
            self.assertEqual(1024 * 1024, _font_max_bytes())
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_drawing_code_validation_reports_missing_allowed_import(self) -> None:
        from app.drawing_code import ALLOWED_IMPORT_ROOTS, validate_drawing_code

        ALLOWED_IMPORT_ROOTS.add("definitely_missing_pkg_for_answer_book_tests")
        try:
            issues = validate_drawing_code(
                """
import definitely_missing_pkg_for_answer_book_tests

def draw(output_path: str) -> None:
    pass
""".strip()
            )
        finally:
            ALLOWED_IMPORT_ROOTS.discard("definitely_missing_pkg_for_answer_book_tests")

        self.assertTrue(any("not installed" in issue for issue in issues), issues)

    def test_all_material_schema_kinds_have_programmatic_renderer_binding(self) -> None:
        from app.figures import _figure_renderer_registry

        registered = set(_figure_renderer_registry().kinds())

        self.assertEqual([], sorted(set(ALL_MATERIAL_SCHEMA_KINDS) - registered))

    def test_numeric_professional_renderer_rejects_missing_semantic_data(self) -> None:
        from app.figures import generate_figures

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            specs_json = tmp / "specs.json"
            output_dir = tmp / "figures"
            specs_json.write_text(
                json.dumps(
                    {
                        "figures": [
                            {
                                "figure_id": "missing_curve",
                                "question_id": "q1",
                                "kind": "creep_curve",
                                "caption": "蠕变曲线",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            generated = generate_figures(specs_json, output_dir)
            stored = json.loads(specs_json.read_text(encoding="utf-8"))["figures"][0]

        self.assertEqual([], generated)
        self.assertIn("creep_curve: required field points is missing", stored["validation_issues"])

    def test_material_figure_specs_are_normalized_before_rendering(self) -> None:
        from app.figures import normalize_figure_spec, program_check_figure_spec

        zone = normalize_figure_spec(
            {
                "kind": "zone_axis_diffraction",
                "caption": "bcc [110]",
                "zone_axis": "[110]",
                "lattice": "体心立方",
                "max_index": 2,
                "label_indices": ["(002)", "(1-10)"],
                "spot_size": 0.1,
            }
        )
        self.assertEqual([1, 1, 0], zone["zone_axis"])
        self.assertEqual("bcc", zone["lattice"])
        self.assertEqual([[0, 0, 2], [1, -1, 0]], zone["label_indices"])
        self.assertGreaterEqual(zone["spot_size"], 30)
        self.assertEqual([], program_check_figure_spec(zone))

        xrd = normalize_figure_spec(
            {
                "kind": "xrd_pattern",
                "caption": "有序化峰",
                "peaks": [
                    {"position": 29.1, "label": "100", "phase": 1},
                    {"position": 44.7, "label": "110", "phase": 0},
                    {"position": 52.6, "label": "111", "phase": 1},
                ],
                "phase_labels": ["原有峰", "新增超结构峰"],
            }
        )
        self.assertEqual([29.1, 44.7, 52.6], [peak["two_theta"] for peak in xrd["peaks"]])
        self.assertEqual(["--", "-", "--"], [peak["style"] for peak in xrd["peaks"]])
        self.assertEqual(["(100)", "(110)", "(111)"], [peak["label"] for peak in xrd["peaks"]])
        self.assertEqual([], program_check_figure_spec(xrd))

        ordered = normalize_figure_spec(
            {
                "kind": "xrd_pattern",
                "caption": "有序化转变前后衍射峰对比",
                "peaks": [
                    {"position": 31.2, "label": "(100)★", "phase_index": 1, "phase_label": "原有峰"},
                    {"position": 44.7, "label": "(110)", "phase_index": 0, "phase_label": "原有峰"},
                    {"position": 65.0, "label": "(200)", "phase_index": 1, "phase_label": "有序保留峰"},
                ],
            }
        )
        self.assertEqual(["--", "-", "-"], [peak["style"] for peak in ordered["peaks"]])
        self.assertEqual("新增超结构峰", ordered["peaks"][0]["phase_label"])
        self.assertEqual([], program_check_figure_spec(ordered))

    def test_xrd_program_check_rejects_collapsed_peak_positions(self) -> None:
        from app.figures import normalize_figure_spec, program_check_figure_spec

        spec = normalize_figure_spec(
            {
                "kind": "xrd_pattern",
                "caption": "错误 XRD",
                "peaks": [
                    {"label": "110"},
                    {"label": "200"},
                ],
            }
        )
        issues = program_check_figure_spec(spec)
        self.assertTrue(any("peak positions" in issue for issue in issues), issues)

    def test_figure_repair_payload_is_scoped_to_failed_figure(self) -> None:
        from app.figures import build_figure_spec_repair_payload

        structured_exam = {
            "items": [
                {
                    "question_id": "q1",
                    "question_type": "作图题",
                    "stem": "第一问：画出[110]带轴电子衍射花样。",
                },
                {
                    "question_id": "q2",
                    "question_type": "作图题",
                    "stem": "第二问：画出XRD峰位变化。这个题干不应进入第一问修复请求。",
                },
            ]
        }
        spec = {
            "figure_id": "qa_s01_01_01_fig_01",
            "question_id": "q1",
            "kind": "zone_axis_diffraction",
            "caption": "[110]带轴电子衍射花样",
            "zone_axis": [1, 1, 0],
            "lattice": "generic_cubic",
            "max_index": 3,
            "label_indices": [[0, 0, 0]],
        }
        qa_item = {
            "question_id": "q1",
            "figure_id": "qa_s01_01_01_fig_01",
            "qa": {
                "ok": False,
                "summary": "斑点太小，且非中心斑点未标注。",
                "missing_requirements": ["至少标出两个非中心衍射斑点指数"],
            },
        }

        payload = build_figure_spec_repair_payload(structured_exam, spec, qa_item)
        payload_text = json.dumps(payload, ensure_ascii=False)

        self.assertEqual("q1", payload["question"]["question_id"])
        self.assertEqual("qa_s01_01_01_fig_01", payload["failed_figure"]["figure_id"])
        self.assertIn("只修复", payload_text)
        self.assertIn("斑点太小", payload_text)
        self.assertNotIn("第二问：画出XRD峰位变化", payload_text)
        self.assertNotIn("q2", payload_text)

    def test_drawing_code_repair_payload_is_scoped_to_failed_figure(self) -> None:
        from app.figures import build_drawing_code_repair_payload

        structured_exam = {
            "items": [
                {"question_id": "q1", "question_type": "作图题", "stem": "第一问：画出[110]带轴电子衍射花样。"},
                {"question_id": "q2", "question_type": "作图题", "stem": "第二问：画出XRD峰位变化。这个题干不应进入第一问修复请求。"},
            ]
        }
        spec = {
            "figure_id": "q1_code_fig_01",
            "question_id": "q1",
            "kind": "model_drawing_code",
            "caption": "[110]带轴电子衍射花样",
            "code": "import matplotlib.pyplot as plt\n\ndef draw(output_path: str) -> None:\n    pass\n",
            "run_result": {"ok": False, "issues": ["drawing code did not write output image"], "stderr": "missing output"},
        }
        qa_item = {
            "question_id": "q1",
            "figure_id": "q1_code_fig_01",
            "qa": {"ok": False, "summary": "没有生成有效图片。", "missing_requirements": ["figure image missing"]},
        }

        payload = build_drawing_code_repair_payload(structured_exam, spec, qa_item)
        payload_text = json.dumps(payload, ensure_ascii=False)

        self.assertEqual("repair_one_failed_drawing_code", payload["task"])
        self.assertEqual("q1_code_fig_01", payload["failed_figure"]["figure_id"])
        self.assertIn("draw(output_path", payload_text)
        self.assertIn("没有生成有效图片", payload_text)
        self.assertNotIn("第二问：画出XRD峰位变化", payload_text)
        self.assertNotIn("q2", payload_text)

    def test_visual_qa_records_missing_image_as_skipped_item(self) -> None:
        from app.figures import audit_figures_with_vision
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="vision",
            type="openai_compatible",
            base_url="http://unused",
            api_key="key",
            default_model="text",
            model_options=("text",),
            allow_custom_model=True,
            model_hint="",
            temperature=0.2,
            max_tokens=12288,
            vision_model="vision-model",
            supports_vision=True,
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            specs_json = tmp / "figure_specs.json"
            specs_json.write_text(
                json.dumps(
                    {
                        "figures": [
                            {
                                "figure_id": "missing_fig",
                                "question_id": "q1",
                                "kind": "xrd_pattern",
                                "caption": "XRD",
                                "peaks": [{"two_theta": 30, "label": "(110)"}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report = audit_figures_with_vision(
                {"items": [{"question_id": "q1", "stem": "画XRD"}]},
                specs_json,
                tmp / "figures",
                tmp / "figure_visual_qa.json",
                provider=provider,
                model="vision-model",
            )

        self.assertEqual([], report["items"])
        self.assertEqual(1, len(report["skipped"]))
        self.assertEqual("figure image missing", report["skipped"][0]["reason"])

    def test_same_question_can_keep_multiple_figure_blocks(self) -> None:
        from app.figures import _insert_figure_block

        fragment = {
            "question_id": "q2",
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "见图。"}]}],
        }
        _insert_figure_block(fragment, {"figure_id": "fig_01", "caption": "图一"})
        _insert_figure_block(fragment, {"figure_id": "fig_02", "caption": "图二"})
        _insert_figure_block(fragment, {"figure_id": "fig_01", "caption": "图一修正"})

        figure_blocks = [block for block in fragment["blocks"] if block.get("label") == "图示"]
        self.assertEqual(1, len(figure_blocks))
        image_ids = [segment.get("image_id") for segment in figure_blocks[0]["segments"] if segment.get("type") == "image_ref"]
        captions = [segment.get("text") for segment in figure_blocks[0]["segments"] if segment.get("type") == "text"]
        self.assertEqual(["fig_02", "fig_01"], image_ids)
        self.assertEqual(["图二", "图一修正"], captions)

    def test_final_acceptance_warns_for_model_judgment_after_bounded_repair(self) -> None:
        from app.final_acceptance import build_final_acceptance_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp) / "stage"
            out = Path(raw_tmp) / "out"
            stage.mkdir()
            out.mkdir()
            (out / "answer_book.docx").write_text("docx", encoding="utf-8")
            (out / "word_rendered").mkdir()
            (out / "word_rendered" / "answer_book.pdf").write_text("pdf", encoding="utf-8")
            base_ok = {"ok": True, "issues": [], "warnings": []}
            for filename in [
                "exam_structure_audit.json",
                "retrieval_audit.json",
                "answer_coverage_audit.json",
                "content_quality_audit.json",
                "docx_audit.json",
                "render_audit.json",
            ]:
                (stage / filename).write_text(json.dumps(base_ok, ensure_ascii=False), encoding="utf-8")
            (stage / "environment_check.json").write_text(json.dumps({"formula_conversion": {"preferred_chain_ready": True}}, ensure_ascii=False), encoding="utf-8")
            (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed", "docx": str(out / "answer_book.docx")}, ensure_ascii=False), encoding="utf-8")
            (stage / "pipeline_status.json").write_text(
                json.dumps({"stages": [{"stage": "figures", "status": "failed"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stage / "answer_fragments.json").write_text(json.dumps({"fragments": []}, ensure_ascii=False), encoding="utf-8")
            (stage / "figure_visual_qa.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "items": [
                            {"question_id": "q1", "figure_id": "fig1", "qa": {"ok": False, "summary": "标签缺失"}}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_final_acceptance_report(stage, out, require_render=True)

        self.assertTrue(report["ok"])
        self.assertTrue(report["delivery_ready"])
        self.assertFalse(report["formal_acceptance_passed"])
        self.assertEqual("completed_with_issues", report["status"])
        self.assertTrue(any("figure_visual_qa" in warning for warning in report["warnings"]), report["warnings"])
        self.assertFalse(any("failed stage: figures" in issue for issue in report["issues"]), report["issues"])

    def test_final_acceptance_ignores_unreferenced_failed_figure_when_fallback_is_used(self) -> None:
        from app.final_acceptance import build_final_acceptance_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp) / "stage"
            out = Path(raw_tmp) / "out"
            stage.mkdir()
            out.mkdir()
            (out / "answer_book.docx").write_text("docx", encoding="utf-8")
            (out / "word_rendered").mkdir()
            (out / "word_rendered" / "answer_book.pdf").write_text("pdf", encoding="utf-8")
            base_ok = {"ok": True, "issues": [], "warnings": []}
            for filename in [
                "exam_structure_audit.json",
                "retrieval_audit.json",
                "answer_coverage_audit.json",
                "content_quality_audit.json",
                "docx_audit.json",
                "render_audit.json",
            ]:
                (stage / filename).write_text(json.dumps(base_ok, ensure_ascii=False), encoding="utf-8")
            (stage / "environment_check.json").write_text(json.dumps({"formula_conversion": {"preferred_chain_ready": True}}, ensure_ascii=False), encoding="utf-8")
            (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed", "docx": str(out / "answer_book.docx")}, ensure_ascii=False), encoding="utf-8")
            (stage / "pipeline_status.json").write_text(
                json.dumps({"stages": [{"stage": "figures", "status": "failed"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {
                                "question_id": "q1",
                                "answer": "见图示。",
                                "blocks": [
                                    {
                                        "label": "图示",
                                        "segments": [
                                            {
                                                "type": "image_ref",
                                                "image_id": "q1_model_fig_01",
                                                "path": "figures/q1_model_fig_01.png",
                                            }
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
            (stage / "figure_visual_qa.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "items": [
                            {"question_id": "q1", "figure_id": "q1_code_fig_01", "qa": {"ok": False, "summary": "标签乱码"}},
                            {"question_id": "q1", "figure_id": "q1_model_fig_01", "qa": {"ok": True, "summary": "可用"}},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_final_acceptance_report(stage, out, require_render=True)

        self.assertTrue(report["ok"], report["issues"])
        self.assertFalse(any("figure_visual_qa" in issue for issue in report["issues"]), report["issues"])
        self.assertFalse(any("failed stage: figures" in issue for issue in report["issues"]), report["issues"])
        self.assertEqual(1, report["figure_visual_qa_summary"]["ignored_unreferenced_failed_count"])
        self.assertTrue(any("ignored unreferenced failed figure" in warning for warning in report["warnings"]), report["warnings"])


if __name__ == "__main__":
    unittest.main()
