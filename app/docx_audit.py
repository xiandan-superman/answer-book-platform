from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from .document_contracts import (
    FOOTER_TEXT,
    HEADER_FOOTER_CONTRACT,
    HEADER_TEXT,
    PAGE_CONTRACT,
    TEXT_CONTRACT,
)

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
INTERNAL_REVIEW_LANGUAGE = (
    "待复核公式",
    "以下公式未能自然融入解析，请复核",
    "需人工复核",
)


def _twips(cm: float) -> int:
    return round(float(cm) * 1440 / 2.54)


def _half_points(points: float) -> str:
    return str(round(float(points) * 2))


def _text(node) -> str:
    return "".join(node.xpath(".//w:t/text()", namespaces=NS))


def _contract_attribute(node, name: str) -> str:
    if node is None:
        return ""
    return str(node.get(f"{{{NS['w']}}}{name}") or "")


def _twips_match(value: str, expected: float, tolerance: int = 2) -> bool:
    try:
        return abs(int(value) - _twips(expected)) <= tolerance
    except (TypeError, ValueError):
        return False


def _run_contract_issues(run, *, expected_size: float, location: str) -> list[str]:
    issues: list[str] = []
    fonts = run.find("w:rPr/w:rFonts", namespaces=NS)
    east = _contract_attribute(fonts, "eastAsia")
    ascii_font = _contract_attribute(fonts, "ascii")
    hansi_font = _contract_attribute(fonts, "hAnsi")
    if east != TEXT_CONTRACT.east_asia_font:
        issues.append(f"document contract font mismatch at {location}: eastAsia={east or 'missing'}")
    if ascii_font != TEXT_CONTRACT.latin_font or hansi_font != TEXT_CONTRACT.latin_font:
        issues.append(
            f"document contract font mismatch at {location}: ascii={ascii_font or 'missing'}, hAnsi={hansi_font or 'missing'}"
        )
    size = run.find("w:rPr/w:sz", namespaces=NS)
    actual_size = _contract_attribute(size, "val")
    if actual_size != _half_points(expected_size):
        issues.append(
            f"document contract size mismatch at {location}: half_points={actual_size or 'missing'}, expected={_half_points(expected_size)}"
        )
    color = run.find("w:rPr/w:color", namespaces=NS)
    actual_color = _contract_attribute(color, "val").upper()
    if actual_color != "000000":
        issues.append(f"document contract color mismatch at {location}: {actual_color or 'missing'}")
    return issues


def _answer_book_contract_issues(zf: zipfile.ZipFile, root) -> list[str]:
    """Audit the current V4 answer-book compatibility contract.

    This check activates only for documents carrying the V4 title. It therefore
    does not impose answer-book typography on standalone formula fixtures or
    other DOCX products that share the low-level formula auditor.
    """

    paragraphs = root.xpath("//w:body/w:p", namespaces=NS)
    if not any(_text(paragraph).strip() == "真题答案解析" for paragraph in paragraphs):
        return []
    issues: list[str] = []
    section = root.xpath("//w:sectPr", namespaces=NS)
    section = section[-1] if section else None
    page_size = section.find("w:pgSz", namespaces=NS) if section is not None else None
    page_margin = section.find("w:pgMar", namespaces=NS) if section is not None else None
    if not _twips_match(_contract_attribute(page_size, "w"), PAGE_CONTRACT.width_cm):
        issues.append("document contract page width mismatch")
    if not _twips_match(_contract_attribute(page_size, "h"), PAGE_CONTRACT.height_cm):
        issues.append("document contract page height mismatch")
    for side in ("top", "right", "bottom", "left"):
        if not _twips_match(_contract_attribute(page_margin, side), PAGE_CONTRACT.margin_cm):
            issues.append(f"document contract {side} margin mismatch")
    if not _twips_match(_contract_attribute(page_margin, "header"), PAGE_CONTRACT.header_distance_cm):
        issues.append("document contract header distance mismatch")
    if not _twips_match(_contract_attribute(page_margin, "footer"), PAGE_CONTRACT.footer_distance_cm):
        issues.append("document contract footer distance mismatch")

    for paragraph_index, paragraph in enumerate(paragraphs, 1):
        paragraph_text = _text(paragraph).strip()
        has_payload = bool(paragraph_text or paragraph.xpath(".//m:oMath | .//w:drawing", namespaces=NS))
        if not has_payload:
            continue
        spacing = paragraph.find("w:pPr/w:spacing", namespaces=NS)
        expected_line = (
            round(TEXT_CONTRACT.note_line_spacing * 240)
            if paragraph_text.startswith("易错点及注意事项：")
            else round(TEXT_CONTRACT.line_spacing * 240)
        )
        if _contract_attribute(spacing, "line") != str(expected_line):
            issues.append(f"document contract line spacing mismatch at paragraph {paragraph_index}")
        if _contract_attribute(spacing, "before") != "0" or _contract_attribute(spacing, "after") != "0":
            issues.append(f"document contract paragraph spacing mismatch at paragraph {paragraph_index}")
        is_title = paragraph_text == "真题答案解析"
        if is_title:
            alignment = paragraph.find("w:pPr/w:jc", namespaces=NS)
            if _contract_attribute(alignment, "val") != "center":
                issues.append("document contract title alignment mismatch")
        for run_index, run in enumerate(paragraph.xpath("./w:r[w:t]", namespaces=NS), 1):
            if not _text(run).strip():
                continue
            issues.extend(
                _run_contract_issues(
                    run,
                    expected_size=TEXT_CONTRACT.title_size_pt if is_title else TEXT_CONTRACT.body_size_pt,
                    location=f"paragraph {paragraph_index} run {run_index}",
                )
            )

    header_names = sorted(name for name in zf.namelist() if re.fullmatch(r"word/header\d+\.xml", name))
    footer_names = sorted(name for name in zf.namelist() if re.fullmatch(r"word/footer\d+\.xml", name))
    if not header_names:
        issues.append("document contract header missing")
    else:
        header = etree.fromstring(zf.read(header_names[0]))
        if _text(header).strip() != HEADER_TEXT:
            issues.append("document contract header text mismatch")
        header_run = next(iter(header.xpath("//w:r[w:t]", namespaces=NS)), None)
        if header_run is None:
            issues.append("document contract header run missing")
        else:
            fonts = header_run.find("w:rPr/w:rFonts", namespaces=NS)
            size = header_run.find("w:rPr/w:sz", namespaces=NS)
            if _contract_attribute(fonts, "eastAsia") != HEADER_FOOTER_CONTRACT.header_font:
                issues.append("document contract header font mismatch")
            if _contract_attribute(size, "val") != _half_points(HEADER_FOOTER_CONTRACT.header_size_pt):
                issues.append("document contract header size mismatch")
            if header_run.find("w:rPr/w:b", namespaces=NS) is None:
                issues.append("document contract header bold missing")
    if not footer_names:
        issues.append("document contract footer missing")
    else:
        footer = etree.fromstring(zf.read(footer_names[0]))
        footer_paragraphs = footer.xpath("//w:p", namespaces=NS)
        if not footer_paragraphs or _text(footer_paragraphs[0]).strip() != FOOTER_TEXT:
            issues.append("document contract footer text mismatch")
        else:
            footer_run = next(iter(footer_paragraphs[0].xpath("./w:r[w:t]", namespaces=NS)), None)
            fonts = footer_run.find("w:rPr/w:rFonts", namespaces=NS) if footer_run is not None else None
            size = footer_run.find("w:rPr/w:sz", namespaces=NS) if footer_run is not None else None
            if _contract_attribute(fonts, "eastAsia") != HEADER_FOOTER_CONTRACT.footer_font:
                issues.append("document contract footer font mismatch")
            if _contract_attribute(size, "val") != _half_points(HEADER_FOOTER_CONTRACT.footer_size_pt):
                issues.append("document contract footer size mismatch")
        page_fields = footer.xpath("//w:p/w:fldSimple[@w:instr=' PAGE ']", namespaces=NS)
        if len(page_fields) != 1:
            issues.append("document contract page field missing or invalid")
    return issues


def math_node_has_empty_delimiter_slots(node) -> bool:
    for delimiter in node.xpath(".//m:d", namespaces=NS):
        slots = delimiter.xpath("./m:e", namespaces=NS)
        if not slots:
            continue
        if all(not "".join(slot.xpath(".//m:t/text()", namespaces=NS)).strip() and len(slot) == 0 for slot in slots):
            return True
    return False


def math_node_has_empty_delimiter_character(node) -> bool:
    for delimiter in node.xpath(".//m:d", namespaces=NS):
        beginnings = delimiter.xpath("./m:dPr/m:begChr", namespaces=NS)
        begin_value = (
            beginnings[0].get(f"{{{NS['m']}}}val")
            if beginnings
            else None
        )
        for name in ("begChr", "endChr"):
            for character in delimiter.xpath(f"./m:dPr/m:{name}", namespaces=NS):
                value = character.get(f"{{{NS['m']}}}val")
                if value is None or str(value).strip():
                    continue
                # Word represents a TeX cases environment as a left brace,
                # matrix body and intentionally invisible right delimiter.
                if (
                    name == "endChr"
                    and begin_value == "{"
                    and delimiter.xpath("./m:e/m:m", namespaces=NS)
                ):
                    continue
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
    if RAW_LATEX_WORD_RE.search(value):
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
        issues.extend(_answer_book_contract_issues(zf, root))
    math_nodes = root.xpath("//m:oMath", namespaces=NS)
    if len(math_nodes) < min_formulas:
        issues.append(f"OMML formula count {len(math_nodes)} below expected minimum {min_formulas}")
    for idx, node in enumerate(math_nodes, 1):
        text = "".join(node.xpath(".//m:t/text()", namespaces=NS))
        if not text.strip():
            issues.append(f"math object {idx} is empty")
        if math_node_has_empty_delimiter_slots(node):
            issues.append(f"math object {idx} contains empty delimiter slots; Word may render formula boxes")
        if math_node_has_empty_delimiter_character(node):
            issues.append(f"math object {idx} contains an empty delimiter character; Word may render formula boxes")
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
            leaked_phrase = next((phrase for phrase in INTERNAL_REVIEW_LANGUAGE if phrase in text), "")
            if leaked_phrase:
                issues.append(
                    f"paragraph {idx} contains internal review language in formal delivery: {leaked_phrase}"
                )
            issue = dangerous_normal_text_issue(text)
            if issue:
                issues.append(f"paragraph {idx} {issue}")
    return issues
