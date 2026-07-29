#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docx import Document
from lxml import etree

from app.docx_audit import audit_docx_v4
from app.docx_v4 import add_formula_paragraph
from app.omml import omml_from_latex


SAMPLES = [
    r"\Delta_r G_m^\theta=-RT\ln K_p^\theta",
    r"\frac{\partial E}{\partial T}_p",
    r"t_{1/2}=\frac{\ln 2}{k}",
    r"\Delta S=nR\ln\frac{V_2}{V_1}",
    r"\Delta S_{\text{系统}} < 0",
    r"\mathrmH2O + \mathrmH2SO4",
    r"K^\ominus=1",
    r"n = 5\,\mathrm{mol}",
    r"\mathrm{N_2 + 3H_2 \rightleftharpoons 2NH_3}",
]


def main() -> int:
    failed = 0
    for sample in SAMPLES:
        node = omml_from_latex(sample)
        xml = etree.tostring(node, encoding="unicode")
        text = "".join(node.xpath(".//*[local-name()='t']/text()"))
        print("SOURCE:", sample)
        print("TEXT:", text)
        print("HAS_STRUCT:", any(tag in xml for tag in ["m:f", "m:sSub", "m:sSup", "m:sSubSup"]))
        print()
        for run in node.xpath(".//*[local-name()='r' and ./*[local-name()='t']]"):
            styles = run.xpath("./*[local-name()='rPr']/*[local-name()='sty']/@*[local-name()='val']")
            normal_text = run.xpath("./*[local-name()='rPr']/*[local-name()='nor']")
            if normal_text or not any(style in {"i", "bi"} for style in styles):
                failed += 1
        if "\\" in text or "{" in text or "}" in text:
            failed += 1
        if "leftharpoons" in text or "harpoons" in text:
            failed += 1
        if "rightleftharpoons" in sample and "⇌" not in text:
            failed += 1
    grouped_structure_factor = r"F = f \left\{ 1 + \exp[\pi i (h+k)] + \exp[\pi i (h+l)] + \exp[\pi i (k+l)] \right\}"
    with tempfile.TemporaryDirectory() as tmp:
        docx_path = Path(tmp) / "grouped_structure_factor.docx"
        doc = Document()
        add_formula_paragraph(doc, grouped_structure_factor)
        doc.save(docx_path)
        issues = audit_docx_v4(docx_path, min_formulas=1)
        print("GROUPED_STRUCTURE_FACTOR_ISSUES:", issues)
        if issues:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
