from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _fragment(*, formulas: list[str], text: str = "") -> dict[str, object]:
    return {
        "question_id": "q1",
        "answer": text,
        "formulas": [
            {"formula_id": f"f{index}", "latex": latex, "role": "relation", "display": True}
            for index, latex in enumerate(formulas, start=1)
        ],
        "blocks": [],
    }


class AcademicExpressionTests(unittest.TestCase):
    def test_shared_text_scanner_covers_cross_discipline_explicit_notation(self) -> None:
        from app.capabilities.academic_expressions import extract_text_expressions

        expressions = extract_text_expressions(
            "速度为 12.5 m/s，浓度为 2.0 mmol/L；E°=0.268 V，电极为（Hg₂Cl₂(s)|Hg(l)|KCl(aq)）。",
            question_id="q1",
            location="answer",
            context="物理化学与工程测量",
        )

        kinds = {item.kind for item in expressions}
        rules = {item.rule_id for item in expressions}
        self.assertIn("quantity", kinds)
        self.assertIn("equation", kinds)
        self.assertIn("chemical_notation", kinds)
        self.assertIn("core.text_electrode_notation", rules)
        self.assertTrue(all(item.capability_id == "core.academic_expressions" for item in expressions))

    def test_shared_registry_captures_the_complete_reaction_not_only_the_arrow(self) -> None:
        from app.capabilities.academic_expressions import extract_text_expressions

        expressions = extract_text_expressions(
            "包晶转变为 L+δ→γ，共晶转变为 L→γ+Fe₃C。",
            question_id="q1",
            location="answer",
            context="相变反应",
        )

        reactions = [item for item in expressions if item.kind == "reaction"]
        self.assertEqual(["L+δ→γ", "L→γ+Fe₃C"], [item.raw for item in reactions])
        self.assertEqual({"core.text_reaction"}, {item.rule_id for item in reactions})

        from app.capabilities.text_expression_rendering import build_text_expression_render_plans

        plans = build_text_expression_render_plans("包晶转变为 L+δ→γ。")
        self.assertEqual(r"L+\delta\to\gamma", plans[0].render_latex)
        self.assertEqual("reaction", plans[0].expression_kind)

    def test_shared_reaction_contract_supports_stoichiometric_coefficients(self) -> None:
        from app.capabilities.academic_expressions import extract_text_expressions
        from app.capabilities.text_expression_rendering import build_text_expression_render_plans

        source = "反应式为 2H₂+O₂→2H₂O。"
        expressions = extract_text_expressions(
            source,
            question_id="q1",
            location="answer",
            context="化学反应计量",
        )
        plans = build_text_expression_render_plans(source, context="化学反应计量")

        self.assertEqual(["2H₂+O₂→2H₂O"], [item.raw for item in expressions if item.kind == "reaction"])
        self.assertEqual(
            r"2\mathrm{H}_{2}+\mathrm{O}_{2}\to2\mathrm{H}_{2}\mathrm{O}",
            plans[0].render_latex,
        )

    def test_shared_text_render_plan_normalizes_standard_state_and_electrode_notation(self) -> None:
        from app.capabilities.text_expression_rendering import build_text_expression_render_plans

        plans = build_text_expression_render_plans(
            "已知 E°(Hg₂Cl₂/Hg)=0.268 V，参比电极为（Hg₂Cl₂(s)|Hg(l)|KCl(aq)）。"
        )

        rendered = "|".join(plan.render_latex for plan in plans)
        self.assertIn(r"E^{\theta}(Hg_{2}Cl_{2}/Hg)=0.268 V", rendered)
        self.assertIn(r"\mathrm{Hg}_{2}\mathrm{Cl}_{2}(\mathrm{s})\vert", rendered)
        electrode = next(plan for plan in plans if plan.rule_id == "core.text_electrode_notation")
        self.assertTrue(electrode.preserve_parentheses)

    def test_shared_render_plan_supports_compact_physical_chemistry_notation(self) -> None:
        from app.capabilities.text_expression_rendering import build_text_expression_render_plans

        plans = build_text_expression_render_plans("由ΔrCp,m与(∂ΔrGmθ/∂T)p=-ΔrHmθ/T²判断，并检查pV项。")
        rendered = "|".join(plan.render_latex for plan in plans)

        self.assertIn(r"\Delta_{\mathrm{r}} C_{p,\mathrm{m}}", rendered)
        self.assertIn(r"\partial", rendered)
        self.assertIn("pV", rendered)

    def test_material_text_notation_is_audited_but_not_promoted_by_generic_renderer(self) -> None:
        from app.capabilities.academic_expressions import extract_text_expressions
        from app.capabilities.text_expression_rendering import build_text_expression_render_plans

        text = "晶向为 [1 1 0]。"
        expressions = extract_text_expressions(
            text,
            question_id="q1",
            location="answer",
            context="标出晶向与密勒指数",
        )
        plans = build_text_expression_render_plans(text, context="标出晶向与密勒指数")

        self.assertEqual("materials.figures", expressions[0].capability_id)
        self.assertEqual([], plans)

    def test_render_plan_classifies_chemistry_typography_and_preflights_word(self) -> None:
        from app.capabilities.expression_rendering import (
            build_expression_render_plan,
            preflight_expression_render,
        )

        plan = build_expression_render_plan(
            r"\ce{2H2 + O2 -> 2H2O}",
            question_id="q1",
            location="formulas[0].latex",
        )

        self.assertEqual("chemical_notation", plan.expression_kind)
        self.assertEqual("all_italic", plan.typography.value)
        self.assertEqual("", preflight_expression_render(plan))

    def test_rendered_chemistry_and_math_are_entirely_italic(self) -> None:
        from lxml import etree

        from app.capabilities.expression_rendering import render_expression_omml

        chemistry = render_expression_omml(r"\ce{H2 + O2 -> H2O}")
        math = render_expression_omml(r"v=12.5\,\mathrm{m\,s^{-1}}")
        chemistry_xml = etree.tostring(chemistry, encoding="unicode")
        math_xml = etree.tostring(math, encoding="unicode")

        self.assertNotIn("<m:nor", chemistry_xml)
        self.assertIn('m:val="i"', math_xml)
        self.assertNotIn('m:val="p"', chemistry_xml)
        self.assertNotIn('m:val="p"', math_xml)
        self.assertTrue(all(
            run.xpath("./m:rPr/m:sty[@m:val='i']", namespaces={"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"})
            for run in chemistry.xpath(".//m:r[m:t]", namespaces={"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"})
        ))

    def test_domain_label_le_is_not_rendered_as_less_equal(self) -> None:
        from lxml import etree

        from app.capabilities.expression_rendering import render_expression_omml

        rendered = render_expression_omml(r"w(P)+w(Le)=1")
        text = "".join(
            rendered.xpath(
                ".//m:t/text()",
                namespaces={"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"},
            )
        )
        xml = etree.tostring(rendered, encoding="unicode")

        self.assertIn("Le", text)
        self.assertNotIn("≤", text)
        self.assertIn('m:val="i"', xml)
        self.assertNotIn('m:val="p"', xml)

    def test_cross_discipline_formulas_use_one_intermediate_contract(self) -> None:
        from app.capabilities.academic_expressions import audit_academic_expressions

        fragments = {
            "fragments": [
                {
                    **_fragment(
                        formulas=[
                            r"\begin{bmatrix}1 & 0 \\ 0 & 1\end{bmatrix}",
                            r"\vec{F}=m\vec{a}",
                            r"\ce{2H2 + O2 -> 2H2O}",
                        ],
                        text="测量长度为 12.5 cm。",
                    )
                }
            ]
        }
        report = audit_academic_expressions(fragments)

        self.assertTrue(report["ok"])
        self.assertEqual(0, report["remote_model_calls"])
        self.assertFalse(report["mutates_source_content"])
        self.assertEqual(
            {"chemical_notation", "matrix", "quantity", "vector"},
            {item["kind"] for item in report["expressions"]},
        )

    def test_material_notation_requires_material_context(self) -> None:
        from app.capabilities.academic_expressions import audit_academic_expressions

        fragments = {"fragments": [_fragment(formulas=[], text="方向为 [1 1 0]。")]}
        unrelated = audit_academic_expressions(
            fragments,
            structured_exam={"items": [{"question_id": "q1", "stem": "计算数组索引。"}]},
        )
        materials = audit_academic_expressions(
            fragments,
            structured_exam={"items": [{"question_id": "q1", "stem": "标出晶向与晶带轴。"}]},
        )

        self.assertEqual(0, unrelated["expression_count"])
        self.assertEqual("domain_notation", materials["expressions"][0]["kind"])
        self.assertEqual("materials.figures", materials["expressions"][0]["capability_id"])

    def test_normalization_is_non_mutating_and_stable(self) -> None:
        from app.capabilities.academic_expressions import audit_academic_expressions

        fragments = {"fragments": [_fragment(formulas=[r"\dfrac{a}{b}  =  1"])]}
        before = json.dumps(fragments, ensure_ascii=False, sort_keys=True)
        first = audit_academic_expressions(fragments)
        second = audit_academic_expressions(fragments)

        expression = first["expressions"][0]
        self.assertEqual(r"\frac{a}{b} = 1", expression["normalized"])
        self.assertEqual(expression["expression_id"], second["expressions"][0]["expression_id"])
        self.assertEqual(before, json.dumps(fragments, ensure_ascii=False, sort_keys=True))

    def test_invalid_latex_structure_is_deterministic_issue(self) -> None:
        from app.capabilities.academic_expressions import audit_academic_expressions

        report = audit_academic_expressions(
            {"fragments": [_fragment(formulas=[r"\frac{a}{b", r"\begin{matrix}1&0\end{bmatrix}"])]}
        )

        self.assertFalse(report["ok"])
        self.assertEqual(2, report["issue_count"])
        self.assertEqual({"invalid_latex_structure"}, {item["code"] for item in report["issues"]})

    def test_expression_report_feeds_unattended_shadow_governance(self) -> None:
        from app.capabilities.academic_expressions import audit_academic_expressions
        from app.capabilities.shadow_quality import build_shadow_quality_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            audit_academic_expressions(
                {"fragments": [_fragment(formulas=[r"\frac{a}{b"])]},
                output_json=stage / "academic_expression_audit.json",
            )
            shadow = build_shadow_quality_report(stage)

        self.assertEqual(1, shadow["finding_count"])
        finding = shadow["findings"][0]
        self.assertEqual("academic_expression.invalid_latex_structure", finding["code"])
        self.assertEqual("repair_then_block", finding["governance"]["action_ceiling"])
        self.assertEqual("block", finding["action"])

    def test_expression_report_writes_json_without_remote_work(self) -> None:
        from app.capabilities.academic_expressions import audit_academic_expressions

        with tempfile.TemporaryDirectory() as raw_tmp:
            output = Path(raw_tmp) / "academic_expression_audit.json"
            report = audit_academic_expressions({"fragments": [_fragment(formulas=[r"E=mc^2"])]}, output_json=output)
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(report["schema_version"], saved["schema_version"])
        self.assertEqual("local_only", saved["mode"])
        self.assertEqual(0, saved["remote_model_calls"])
        self.assertEqual(1, saved["render_plan_count"])
        self.assertEqual(0, saved["render_preflight_failure_count"])

    def test_expression_render_preflight_failure_is_machine_governed(self) -> None:
        from unittest.mock import patch

        from app.capabilities.academic_expressions import audit_academic_expressions
        from app.capabilities.quality_governance import governance_for

        with patch(
            "app.capabilities.expression_rendering.preflight_expression_render",
            return_value="renderer failed",
        ):
            report = audit_academic_expressions(
                {"fragments": [_fragment(formulas=[r"E=mc^2"])]}
            )

        self.assertFalse(report["ok"])
        self.assertEqual("render_preflight_failed", report["issues"][0]["code"])
        governance = governance_for("academic_expression.render_preflight_failed")
        self.assertEqual("deterministic", governance.evidence_class.value)
        self.assertEqual("block", governance.action_ceiling.value)

    def test_cloud_structure_audit_defers_word_render_preflight(self) -> None:
        from unittest.mock import patch

        from app.capabilities.academic_expressions import audit_academic_expressions

        with patch(
            "app.capabilities.expression_rendering.preflight_expression_render",
            return_value="Word renderer is unavailable",
        ) as preflight:
            report = audit_academic_expressions(
                {"fragments": [_fragment(formulas=[r"E=mc^2"])]},
                render_preflight=False,
            )

        self.assertTrue(report["ok"])
        self.assertEqual("cloud_structure_only", report["mode"])
        self.assertTrue(report["render_preflight_deferred"])
        self.assertIsNone(report["render_plans"][0]["preflight_ok"])
        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
