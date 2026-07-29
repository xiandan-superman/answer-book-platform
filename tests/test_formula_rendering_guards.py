from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docx import Document
from lxml import etree

from app.docx_audit import NS, audit_docx_v4, math_node_has_empty_delimiter_slots
from app.docx_v4 import build_docx_from_fragments
from app.final_acceptance import build_final_acceptance_report
from app.omml import normalize_latex, omml_from_latex


class FormulaRenderingGuardTests(unittest.TestCase):
    def test_vector_norm_product_does_not_create_empty_omml_slots(self) -> None:
        latex = r"\cos\varphi=\frac{\mathbf{g}_1\cdot\mathbf{g}_2}{\left|\mathbf{g}_1\right|\left|\mathbf{g}_2\right|}"

        normalized = normalize_latex(latex)
        omml = omml_from_latex(latex)
        xml = etree.tostring(omml, encoding="unicode")

        self.assertIn(r"\vert", normalized)
        self.assertNotIn("<m:e/>", xml)
        self.assertFalse(math_node_has_empty_delimiter_slots(omml))

    def test_docx_audit_reports_empty_formula_delimiter_slots(self) -> None:
        xml = etree.fromstring(
            b"""
            <m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
              <m:d>
                <m:dPr><m:begChr m:val="|"/><m:endChr m:val="|"/></m:dPr>
                <m:e/><m:e/><m:e/>
              </m:d>
            </m:oMath>
            """
        )

        self.assertTrue(math_node_has_empty_delimiter_slots(xml))

    def test_docx_audit_passes_generated_vector_norm_formula(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            docx_path = Path(raw_tmp) / "formula.docx"
            doc = Document()
            p = doc.add_paragraph()
            p._p.append(
                omml_from_latex(
                    r"\cos\varphi=\frac{\mathbf{g}_1\cdot\mathbf{g}_2}{\left|\mathbf{g}_1\right|\left|\mathbf{g}_2\right|}"
                )
            )
            doc.save(docx_path)

            issues = audit_docx_v4(docx_path, min_formulas=1)

        self.assertFalse(any("empty delimiter slots" in issue for issue in issues), issues)

    def test_short_answer_answer_field_renders_dollar_latex_as_omml(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            fragments_json = root / "answer_fragments.json"
            output_docx = root / "answer_book.docx"
            fragments_json.write_text(
                json.dumps(
                    {
                        "schema_version": "answer_book.answer_fragments.v4",
                        "fragments": [
                            {
                                "schema_version": "answer_book.answer_fragment.v4",
                                "question_id": "q_diffusion",
                                "section": "六、简答题",
                                "question_type": "简答题",
                                "number": "1",
                                "answer": "见解析",
                                "answer_summary": r"稳态扩散流量 $J = \frac{D_1 D_2 c_0}{L(D_2 + 2D_1)}$",
                                "evidence_ids": [],
                                "blocks": [],
                                "formulas": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            build_docx_from_fragments(fragments_json, output_docx)
            issues = audit_docx_v4(output_docx, min_formulas=1)

        self.assertFalse(any("raw latex marker in normal text" in issue for issue in issues), issues)

    def test_fill_blank_answer_field_renders_dollar_latex_as_omml(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            fragments_json = root / "answer_fragments.json"
            output_docx = root / "answer_book.docx"
            fragments_json.write_text(
                json.dumps(
                    {
                        "schema_version": "answer_book.answer_fragments.v4",
                        "fragments": [
                            {
                                "schema_version": "answer_book.answer_fragment.v4",
                                "question_id": "fill_formula",
                                "section": "二、填空题",
                                "question_type": "填空题",
                                "number": "1",
                                "answer": r"$\\Delta G < 0$",
                                "answer_summary": "",
                                "evidence_ids": [],
                                "blocks": [],
                                "formulas": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            build_docx_from_fragments(fragments_json, output_docx)
            issues = audit_docx_v4(output_docx, min_formulas=1)

        self.assertFalse(any("raw latex marker in normal text" in issue for issue in issues), issues)

    def test_final_acceptance_fails_demo_fragments_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            stage_dir = root / "stage_outputs"
            output_dir = root / "outputs"
            stage_dir.mkdir()
            (output_dir / "word_rendered").mkdir(parents=True)
            (output_dir / "answer_book.docx").write_bytes(b"placeholder")
            (output_dir / "word_rendered" / "answer_book.pdf").write_bytes(b"placeholder")
            for name in (
                "environment_check.json",
                "exam_structure_audit.json",
                "retrieval_audit.json",
                "answer_coverage_audit.json",
                "content_quality_audit.json",
                "docx_audit.json",
                "figure_size_audit.json",
                "render_audit.json",
            ):
                payload = {"ok": True, "issues": [], "warnings": []}
                if name == "environment_check.json":
                    payload["formula_conversion"] = {"preferred_chain_ready": True}
                (stage_dir / name).write_text(json.dumps(payload), encoding="utf-8")
            (stage_dir / "acceptance_report.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            (stage_dir / "pipeline_status.json").write_text(json.dumps({"stages": []}), encoding="utf-8")
            (stage_dir / "answer_fragments.json").write_text(
                json.dumps({"provider": "demo", "fragments": [{"question_id": "q1", "answer": "待复核"}]}, ensure_ascii=False),
                encoding="utf-8",
            )

            report = build_final_acceptance_report(stage_dir, output_dir)

        self.assertFalse(report["ok"])
        self.assertEqual("failed", report["status"])
        self.assertTrue(any("demo 占位流程" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
