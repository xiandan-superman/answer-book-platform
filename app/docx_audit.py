from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}


RAW_LATEX_COMMAND_RE = re.compile(
    r"(\\[A-Za-z]+|"
    r"\b(?:frac|sqrt|mathrm|mathbf|mathit|text|ce|begin|end|left|right|overline|underline|bar|hat|tilde)\s*\{)"
)
RAW_LATEX_SCRIPT_BRACE_RE = re.compile(r"[_^]\s*\{")
FORMULA_PLACEHOLDER_RE = re.compile(r"\{f\d+\}")
RAW_LATEX_WORD_RE = re.compile(
    r"\b(?:"
    r"frac|sqrt|leftharpoons|rightarrow|leftarrow|Rightarrow|Leftarrow|"
    r"alpha|beta|gamma|delta|Delta|theta|lambda|mu|nu|sigma|pi|"
    r"mathrm|mathbf|mathit|ce"
    r")\b"
)
RAW_SQRT_RE = re.compile(r"√\s*(?:\(|[A-Za-z0-9])")
RAW_SUBSCRIPT_RE = re.compile(r"(?<!\w)[A-Za-zαβγδνΔ∆]_[A-Za-z0-9αβγδνΔ∆]+")


def math_node_has_empty_delimiter_slots(node) -> bool:
    for delimiter in node.xpath(".//m:d", namespaces=NS):
        slots = delimiter.xpath("./m:e", namespaces=NS)
        if not slots:
            continue
        if all(not "".join(slot.xpath(".//m:t/text()", namespaces=NS)).strip() and len(slot) == 0 for slot in slots):
            return True
    return False


def math_text_has_raw_latex_marker(text: str) -> bool:
    value = str(text or "")
    if "\\" in value:
        return True
    if value.count("{") != value.count("}"):
        return True
    if RAW_LATEX_COMMAND_RE.search(value):
        return True
    return bool(RAW_LATEX_SCRIPT_BRACE_RE.search(value))


def dangerous_normal_text_issue(text: str) -> str:
    value = str(text or "")
    if FORMULA_PLACEHOLDER_RE.search(value):
        return f"unresolved formula placeholder in normal text: {value[:120]}"
    if "\\" in value or RAW_LATEX_COMMAND_RE.search(value) or RAW_LATEX_SCRIPT_BRACE_RE.search(value):
        return f"raw latex marker in normal text: {value[:120]}"
    if RAW_LATEX_WORD_RE.search(value):
        return f"raw latex command word in normal text: {value[:120]}"
    if RAW_SQRT_RE.search(value):
        return f"raw radical in normal text: {value[:120]}"
    if RAW_SUBSCRIPT_RE.search(value):
        return f"raw subscript marker in normal text: {value[:120]}"
    return ""


def audit_docx_v4(docx: Path, min_formulas: int = 0) -> list[str]:
    issues: list[str] = []
    with zipfile.ZipFile(docx) as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    math_nodes = root.xpath("//m:oMath", namespaces=NS)
    if len(math_nodes) < min_formulas:
        issues.append(f"OMML formula count {len(math_nodes)} below expected minimum {min_formulas}")
    for idx, node in enumerate(math_nodes, 1):
        text = "".join(node.xpath(".//m:t/text()", namespaces=NS))
        if not text.strip():
            issues.append(f"math object {idx} is empty")
        if math_node_has_empty_delimiter_slots(node):
            issues.append(f"math object {idx} contains empty delimiter slots; Word may render formula boxes")
        if math_text_has_raw_latex_marker(text):
            issues.append(f"math object {idx} contains raw latex marker: {text[:100]}")
        for run_idx, run in enumerate(node.xpath(".//m:r[m:t]", namespaces=NS), 1):
            styles = run.xpath("./m:rPr/m:sty/@m:val", namespaces=NS)
            normal_text = run.xpath("./m:rPr/m:nor", namespaces=NS)
            if normal_text or not any(style in {"i", "bi"} for style in styles):
                run_text = "".join(run.xpath("./m:t/text()", namespaces=NS))
                issues.append(f"math object {idx} run {run_idx} is not italic: {run_text[:40]}")
    for idx, p in enumerate(root.xpath("//w:body/w:p", namespaces=NS), 1):
        text = "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip()
        if text.startswith("教材依据："):
            continue
        if text:
            issue = dangerous_normal_text_issue(text)
            if issue:
                issues.append(f"paragraph {idx} {issue}")
    return issues
