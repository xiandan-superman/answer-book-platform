from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

from .formula_audit import looks_like_formula, looks_like_symbolic_formula
from .omml import omml_from_latex
from .question_types import (
    is_calculation_question,
    is_choice_question,
    is_short_answer_question,
    is_term_explanation_question,
    question_has_type,
)


HEADER_TEXT = "航研学考研 丨 专注北航考研 丨 材料考研 954467835"
FOOTER_TEXT = "愿每一位考研学子，以梦为马，不负韶华；披荆斩棘，终达彼岸！"
MIN_WORD_FIGURE_HEIGHT_CM = 3.8
WIDE_WORD_FIGURE_ASPECT_RATIO = 2.4
MIN_WIDE_WORD_FIGURE_HEIGHT_CM = 4.4
SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
GREEK_LATEX = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "θ": r"\theta",
    "λ": r"\lambda",
    "μ": r"\mu",
    "π": r"\pi",
    "σ": r"\sigma",
    "ν": r"\nu",
    "Δ": r"\Delta",
    "∆": r"\Delta",
}
GREEK_CHARS = "αβγδθλμπσνΔ∆"
SYMBOL_CHARS = rf"A-Za-z{GREEK_CHARS}"
SCRIPTED_ATOM_RE = rf"(?:[{SYMBOL_CHARS}])_[A-Za-z0-9{GREEK_CHARS}]+"
CHEM_SUBSCRIPT_RE = r"(?:[A-Za-z]{1,3}[₀₁₂₃₄₅₆₇₈₉]+[A-Za-z]?)"
REACTION_SUMMARY_RE = re.compile(
    rf"(?:{SCRIPTED_ATOM_RE}|{CHEM_SUBSCRIPT_RE})(?:\s*(?:[+→⇌])\s*(?:{SCRIPTED_ATOM_RE}|{CHEM_SUBSCRIPT_RE}))+"
)
SCRIPTED_SUMMARY_RE = re.compile(SCRIPTED_ATOM_RE)
SQRT_SUMMARY_RE = re.compile(r"√\s*(?:\(([^)]+)\)|([A-Za-z0-9]+))(?:\s*≈\s*[-+]?\d+(?:\.\d+)?)?")
FORMULA_TOKEN_RE = (
    rf"(?:"
    rf"(?:sin|cos|tan)\s*[{SYMBOL_CHARS}][A-Za-z0-9{GREEK_CHARS}]{{0,4}}"
    rf"|[-+]?\d+(?:\.\d+)?[{SYMBOL_CHARS}][A-Za-z0-9{GREEK_CHARS}]{{0,4}}"
    rf"|[{SYMBOL_CHARS}]?[dδΔ∆]?[{SYMBOL_CHARS}]{{1,4}}(?:_[A-Za-z0-9{GREEK_CHARS}]+)?"
    rf"|[-+]?\d+(?:\.\d+)?"
    rf")"
)
FORMULA_EXPR_RE = rf"{FORMULA_TOKEN_RE}(?:\s*(?:[+\-−·*])\s*{FORMULA_TOKEN_RE}|\s*/\s*{FORMULA_TOKEN_RE}|\s+{FORMULA_TOKEN_RE})*"
EQUATION_SUMMARY_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:[Δ∆])?[A-Za-z][A-Za-z0-9]*)\s*=\s*"
    r"([-+]?\d+(?:\.\d+)?(?:×10\^?[-+]?\d+)?)(?:\s*([A-Za-z]+))?"
)
RELATION_SUMMARY_RE = re.compile(
    rf"(?<![A-Za-z0-9])({FORMULA_TOKEN_RE})\s*(≤|≥|<=|>=|<|>)\s*({FORMULA_EXPR_RE})(?![A-Za-z0-9])"
)
SYMBOLIC_EQUATION_SUMMARY_RE = re.compile(rf"(?<![A-Za-z0-9])({FORMULA_EXPR_RE})\s*=\s*({FORMULA_EXPR_RE})(?![A-Za-z0-9])")
DOLLAR_LATEX_SUMMARY_RE = re.compile(r"\$([^$\n]+)\$")
AUDIT_PROMPT_FILL = "FFF2CC"
ANSWER_BODY_FIRST_LINE_INDENT_CM = 0.74
TOP_LEVEL_SUBQUESTION_RE = re.compile(
    r"^\s*(?:[（(]\s*(?:[1-9]\d*|[一二三四五六七八九十]+)\s*[）)]|第\s*(?:[1-9]\d*|[一二三四五六七八九十]+)\s*(?:小问|问))"
)
SUBQUESTION_MARKER_RE = re.compile(r"(?m)^(\s*)[（(]\s*((?:[1-9]|1\d|20))\s*[）)]\s*(?:[、.．]\s*)?")


def normalize_answer_hierarchy_markers(text: str) -> str:
    return SUBQUESTION_MARKER_RE.sub(r"\1(\2)", str(text or ""))


def figure_display_width_cm(image_path: Path) -> float:
    """Use more of the page for landscape figures so their labels remain readable."""
    try:
        with Image.open(image_path) as image:
            width, height = image.size
        ratio = width / max(height, 1)
    except Exception:
        return 10.5
    if ratio >= 1.7:
        return 13.8
    if ratio >= 1.25:
        return 12.2
    return 10.5


def _word_fit_report_path(image_path: Path) -> Path:
    if image_path.parent.name == "figures":
        return image_path.parent.parent / "figure_word_fit.json"
    return image_path.parent / "figure_word_fit.json"


def _record_word_fit(original: Path, fitted: Path, item: dict) -> None:
    report_path = _word_fit_report_path(original)
    try:
        data = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    except Exception:
        data = {}
    items = [entry for entry in data.get("items", []) if isinstance(entry, dict)] if isinstance(data, dict) else []
    key = str(original)
    items = [entry for entry in items if str(entry.get("original_path") or "") != key]
    items.append({"original_path": key, "fitted_path": str(fitted), **item})
    report_path.write_text(json.dumps({"schema_version": "answer_book.figure_word_fit.v1", "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_figure_image_for_word(image_path: Path, display_width_cm: float) -> Path:
    """Pad very wide figures so Word insertion keeps a readable minimum height."""
    try:
        with Image.open(image_path) as image:
            source = image.convert("RGB")
            width, height = source.size
    except Exception:
        return image_path
    if width <= 0 or height <= 0 or display_width_cm <= 0:
        return image_path
    display_height_cm = display_width_cm * height / width
    display_aspect_ratio = display_width_cm / max(display_height_cm, 0.01)
    required_height_cm = MIN_WIDE_WORD_FIGURE_HEIGHT_CM if display_aspect_ratio >= WIDE_WORD_FIGURE_ASPECT_RATIO else MIN_WORD_FIGURE_HEIGHT_CM
    if display_height_cm >= required_height_cm:
        return image_path
    required_height = int((width * required_height_cm / display_width_cm) + 0.999)
    if required_height <= height:
        return image_path
    fit_path = image_path.with_name(f"{image_path.stem}_wordfit{image_path.suffix or '.png'}")
    top_pad = (required_height - height) // 2
    canvas = Image.new("RGB", (width, required_height), "white")
    canvas.paste(source, (0, top_pad))
    canvas.save(fit_path)
    _record_word_fit(
        image_path,
        fit_path,
        {
            "original_width_px": width,
            "original_height_px": height,
            "fitted_width_px": width,
            "fitted_height_px": required_height,
            "display_width_cm": round(display_width_cm, 2),
            "original_display_height_cm": round(display_height_cm, 2),
            "target_min_height_cm": required_height_cm,
            "top_pad_px": top_pad,
            "bottom_pad_px": required_height - height - top_pad,
            "method": "white_vertical_padding",
        },
    )
    return fit_path


def add_figure_picture(paragraph, image_path: Path) -> None:
    display_width_cm = figure_display_width_cm(image_path)
    fitted_path = prepare_figure_image_for_word(image_path, display_width_cm)
    paragraph.add_run().add_picture(str(fitted_path), width=Cm(display_width_cm))


def set_run_font(run, east="宋体", west="Times New Roman", size=11, bold=False):
    run.font.name = west
    run._element.rPr.rFonts.set(qn("w:eastAsia"), east)
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = RGBColor(0, 0, 0)


def set_run_shading(run, fill: str = AUDIT_PROMPT_FILL) -> None:
    rpr = run._element.get_or_add_rPr()
    shd = rpr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        rpr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)


def set_para(p, align=None):
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    if align is not None:
        p.alignment = align


def add_text_paragraph(doc: Document, text: str, bold: bool = False, size: float = 11, align=None, skip_formula_audit: bool = False):
    p = doc.add_paragraph()
    set_para(p, align)
    r = p.add_run(normalize_answer_hierarchy_markers(text))
    set_run_font(r, size=size, bold=bold)
    return p


def _latex_symbol(value: str) -> str:
    return "".join(GREEK_LATEX.get(ch, ch) for ch in str(value))


def _latex_variable(value: str) -> str:
    token = str(value)
    if token.startswith(("Δ", "∆")) and len(token) > 1:
        return rf"\Delta {_latex_symbol(token[1:])}"
    return _latex_symbol(token)


def _latex_atom(value: str) -> str:
    token = str(value).strip()
    if "_" in token:
        base, sub = token.split("_", 1)
        return f"{_latex_symbol(base)}_{{{_latex_symbol(sub)}}}"
    match = re.fullmatch(r"([A-Za-z]{1,3})([₀₁₂₃₄₅₆₇₈₉]+)([A-Za-z]?)", token)
    if match:
        head = match.group(1)
        digits = match.group(2).translate(SUBSCRIPT_DIGITS)
        tail = match.group(3)
        suffix = f"\\mathrm{{{tail}}}" if tail else ""
        return f"\\mathrm{{{head}}}_{{{digits}}}{suffix}"
    return _latex_symbol(token)


def _latex_reaction(value: str) -> str:
    parts = re.findall(rf"{SCRIPTED_ATOM_RE}|{CHEM_SUBSCRIPT_RE}|[+→⇌]", str(value))
    out: list[str] = []
    for part in parts:
        if part == "+":
            out.append("+")
        elif part == "→":
            out.append(r"\to")
        elif part == "⇌":
            out.append(r"\rightleftharpoons")
        else:
            out.append(_latex_atom(part))
    return "".join(out)


def _latex_sqrt(value: str) -> str:
    text = str(value).strip()
    approx = ""
    approx_match = re.search(r"≈\s*([-+]?\d+(?:\.\d+)?)", text)
    if approx_match:
        approx = rf"\approx {approx_match.group(1)}"
    inner_match = re.search(r"√\s*(?:\(([^)]+)\)|([A-Za-z0-9]+))", text)
    inner = (inner_match.group(1) or inner_match.group(2) or "").strip() if inner_match else ""
    if "/" in inner:
        numerator, denominator = [part.strip() for part in inner.split("/", 1)]
        body = rf"\frac{{{_latex_symbol(numerator)}}}{{{_latex_symbol(denominator)}}}"
    else:
        body = _latex_symbol(inner)
    return rf"\sqrt{{{body}}}{approx}"


def _latex_equation(match: re.Match[str]) -> str:
    variable = _latex_variable(match.group(1))
    value = match.group(2).replace("×", r"\times ")
    value = re.sub(r"10\^?([-+]?\d+)", r"10^{\1}", value)
    unit = match.group(3) or ""
    unit_text = rf"\ \mathrm{{{unit}}}" if unit else ""
    return f"{variable}={value}{unit_text}"


def _latex_relation_token(value: str) -> str:
    token = str(value).strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token):
        return token
    trig = re.fullmatch(r"(sin|cos|tan)\s*(.+)", token)
    if trig:
        return rf"\{trig.group(1)} {_latex_symbol(trig.group(2))}"
    coefficient = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)(.+)", token)
    if coefficient:
        return f"{coefficient.group(1)}{_latex_relation_token(coefficient.group(2))}"
    if len(token) >= 3 and token[0].isalpha() and token[1] in {"δ", "Δ", "∆"}:
        return f"{_latex_symbol(token[0])}{_latex_relation_token(token[1:])}"
    if token.startswith("d") and len(token) > 1:
        return rf"\mathrm{{d}}{_latex_symbol(token[1:])}"
    if token.startswith("δ") and len(token) > 1:
        return rf"\delta {_latex_symbol(token[1:])}"
    if token.startswith(("Δ", "∆")) and len(token) > 1:
        return rf"\Delta {_latex_symbol(token[1:])}"
    return _latex_atom(token) if "_" in token else _latex_variable(token)


def _latex_relation_operand(value: str) -> str:
    text = str(value).strip()
    if re.fullmatch(rf"{FORMULA_TOKEN_RE}\s*/\s*{FORMULA_TOKEN_RE}", text):
        numerator, denominator = [part.strip() for part in text.split("/", 1)]
        return rf"\frac{{{_latex_relation_token(numerator)}}}{{{_latex_relation_token(denominator)}}}"
    expr_parts = re.split(r"(\s*(?:[+\-−·*])\s*)", text)
    if len(expr_parts) > 1:
        converted: list[str] = []
        operators = {"−": "-", "·": r"\cdot", "*": r"\cdot"}
        for part in expr_parts:
            stripped = part.strip()
            if not stripped:
                continue
            if stripped in {"+", "-", "−", "·", "*"}:
                converted.append(operators.get(stripped, stripped))
            else:
                converted.append(_latex_relation_operand(stripped))
        return " ".join(converted)
    implicit_parts = [part for part in re.split(r"\s+", text) if part]
    if len(implicit_parts) > 1 and all(re.fullmatch(FORMULA_TOKEN_RE, part) for part in implicit_parts):
        return r" ".join(_latex_relation_token(part) for part in implicit_parts)
    return _latex_relation_token(text)


def _latex_relation(match: re.Match[str]) -> str:
    operators = {
        "≤": r"\le",
        ">=": r"\ge",
        "≥": r"\ge",
        "<=": r"\le",
        "<": "<",
        ">": ">",
    }
    left = _latex_relation_operand(match.group(1))
    operator = operators.get(match.group(2), match.group(2))
    right = _latex_relation_operand(match.group(3))
    return f"{left} {operator} {right}"


def _latex_symbolic_equation(match: re.Match[str]) -> str:
    left = _latex_relation_operand(match.group(1))
    right = _latex_relation_operand(match.group(2))
    return f"{left}={right}"


def _answer_summary_formula_candidates(text: str) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str]] = []
    for match in DOLLAR_LATEX_SUMMARY_RE.finditer(text):
        latex = match.group(1).strip()
        if latex:
            candidates.append((match.start(), match.end(), latex))
    for match in REACTION_SUMMARY_RE.finditer(text):
        candidates.append((match.start(), match.end(), _latex_reaction(match.group(0))))
    for match in SQRT_SUMMARY_RE.finditer(text):
        candidates.append((match.start(), match.end(), _latex_sqrt(match.group(0))))
    for match in RELATION_SUMMARY_RE.finditer(text):
        candidates.append((match.start(), match.end(), _latex_relation(match)))
    for match in SYMBOLIC_EQUATION_SUMMARY_RE.finditer(text):
        candidates.append((match.start(), match.end(), _latex_symbolic_equation(match)))
    for match in EQUATION_SUMMARY_RE.finditer(text):
        candidates.append((match.start(), match.end(), _latex_equation(match)))
    for match in SCRIPTED_SUMMARY_RE.finditer(text):
        candidates.append((match.start(), match.end(), _latex_atom(match.group(0))))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str]] = []
    cursor = -1
    for start, end, latex in candidates:
        if start < cursor:
            continue
        selected.append((start, end, latex))
        cursor = end
    return selected


def add_answer_summary_paragraph(doc: Document, answer_summary: str, size: float = 11, strict_formula_audit: bool = True):
    p = doc.add_paragraph()
    set_para(p)
    prefix = p.add_run("答：")
    set_run_font(prefix, size=size)
    text = normalize_answer_hierarchy_markers(answer_summary)
    cursor = 0
    for start, end, latex in _answer_summary_formula_candidates(text):
        plain = text[cursor:start]
        if plain:
            if strict_formula_audit and looks_like_symbolic_formula(plain):
                raise ValueError(f"Formula-like text remained in answer summary: {plain[:120]}")
            run = p.add_run(plain)
            set_run_font(run, size=size)
        p._p.append(omml_from_latex(latex))
        cursor = end
    tail = text[cursor:]
    if tail:
        if strict_formula_audit and looks_like_symbolic_formula(tail):
            raise ValueError(f"Formula-like text remained in answer summary: {tail[:120]}")
        run = p.add_run(tail)
        set_run_font(run, size=size)
    return p


def _append_formula_text_runs(
    p,
    text: str,
    size: float = 11,
    strict_formula_audit: bool = True,
    audit_label: str = "text",
):
    value = normalize_answer_hierarchy_markers(text)
    cursor = 0
    for start, end, latex in _answer_summary_formula_candidates(value):
        plain = value[cursor:start]
        if plain:
            if strict_formula_audit and looks_like_symbolic_formula(plain):
                raise ValueError(f"Formula-like text remained in {audit_label}: {plain[:120]}")
            run = p.add_run(plain)
            set_run_font(run, size=size)
        p._p.append(omml_from_latex(latex))
        cursor = end
    tail = value[cursor:]
    if tail:
        if strict_formula_audit and looks_like_symbolic_formula(tail):
            raise ValueError(f"Formula-like text remained in {audit_label}: {tail[:120]}")
        run = p.add_run(tail)
        set_run_font(run, size=size)
    return p


def add_labeled_formula_text_paragraph(
    doc: Document,
    label: str,
    text: str,
    size: float = 11,
    strict_formula_audit: bool = True,
):
    p = doc.add_paragraph()
    set_para(p)
    prefix = p.add_run(f"{label}：")
    set_run_font(prefix, size=size, bold=True)
    return _append_formula_text_runs(p, text, size=size, strict_formula_audit=strict_formula_audit, audit_label=label)


def add_formula_text_body_paragraph(
    doc: Document,
    text: str,
    size: float = 11,
    strict_formula_audit: bool = True,
):
    p = doc.add_paragraph()
    set_para(p)
    p.paragraph_format.first_line_indent = Cm(ANSWER_BODY_FIRST_LINE_INDENT_CM)
    return _append_formula_text_runs(p, text, size=size, strict_formula_audit=strict_formula_audit, audit_label="答案")


def add_mixed_paragraph(
    doc: Document,
    segments: list[dict],
    formulas: dict[str, dict],
    label: str = "",
    base_dir: Path | None = None,
    initial_paragraph=None,
    force_skip_formula_text_audit: bool = False,
):
    skip_formula_text_audit = str(label).strip() == "教材依据" or force_skip_formula_text_audit
    highlight_label = str(label).strip() == "待复核公式"
    p = initial_paragraph
    label_pending = bool(label) and initial_paragraph is None

    def ensure_text_paragraph():
        nonlocal p, label_pending
        if p is None:
            p = doc.add_paragraph()
            set_para(p)
            if label_pending and label:
                r = p.add_run(f"{label}：")
                set_run_font(r, bold=True)
                if highlight_label:
                    set_run_shading(r)
                label_pending = False
        return p

    def break_text_paragraph():
        nonlocal p
        p = None

    def add_text_run(text: str, highlight: bool = False):
        nonlocal p
        if not text:
            return
        chunks = re.split(r"(\n+)", normalize_answer_hierarchy_markers(text))
        for chunk in chunks:
            if not chunk:
                continue
            if "\n" in chunk:
                break_text_paragraph()
                continue
            if p is not None and TOP_LEVEL_SUBQUESTION_RE.match(chunk):
                break_text_paragraph()
            r = ensure_text_paragraph().add_run(chunk)
            set_run_font(r)
            if highlight:
                set_run_shading(r)

    for seg in segments:
        typ = seg.get("type")
        if typ == "text":
            text = str(seg.get("text", ""))
            if not text:
                continue
            add_text_run(text, str(seg.get("highlight") or "") == "unconfirmed_evidence")
        elif typ == "formula_ref":
            fid = str(seg.get("formula_id", ""))
            formula = formulas.get(fid)
            if not formula:
                raise ValueError(f"Missing formula for formula_ref: {fid}")
            latex = str(formula.get("latex", ""))
            if bool(seg.get("inline")):
                ensure_text_paragraph()._p.append(omml_from_latex(latex))
            elif bool(formula.get("display", True)) and not skip_formula_text_audit:
                break_text_paragraph()
                add_formula_paragraph(doc, latex)
            else:
                ensure_text_paragraph()._p.append(omml_from_latex(latex))
        elif typ == "image_ref":
            image_path = Path(str(seg.get("path") or ""))
            if base_dir and not image_path.is_absolute():
                image_path = base_dir / image_path
            break_text_paragraph()
            if image_path.exists():
                pic_p = doc.add_paragraph()
                set_para(pic_p, WD_ALIGN_PARAGRAPH.CENTER)
                add_figure_picture(pic_p, image_path)
            else:
                r = ensure_text_paragraph().add_run(f"[缺失图:{seg.get('image_id','') or image_path}]")
                set_run_font(r)
        else:
            raise ValueError(f"Unsupported segment type: {typ}")
    if label_pending and label:
        ensure_text_paragraph()
    return p


def add_split_block(doc: Document, segments: list[dict], formulas: dict[str, dict], label: str = "", base_dir: Path | None = None):
    if label:
        add_text_paragraph(doc, f"{label}：", bold=True)
    for seg in segments:
        typ = seg.get("type")
        if typ == "text":
            text = str(seg.get("text", "")).strip()
            if text:
                add_text_paragraph(doc, text)
        elif typ == "formula_ref":
            fid = str(seg.get("formula_id", ""))
            formula = formulas.get(fid)
            if not formula:
                raise ValueError(f"Missing formula for formula_ref: {fid}")
            add_formula_paragraph(doc, str(formula.get("latex", "")))
        elif typ == "image_ref":
            image_path = Path(str(seg.get("path") or ""))
            if base_dir and not image_path.is_absolute():
                image_path = base_dir / image_path
            if image_path.exists():
                pic_p = doc.add_paragraph()
                set_para(pic_p, WD_ALIGN_PARAGRAPH.CENTER)
                add_figure_picture(pic_p, image_path)
            else:
                add_text_paragraph(doc, f"[缺失图:{seg.get('image_id','') or image_path}]")
        else:
            raise ValueError(f"Unsupported segment type: {typ}")


def add_formula_paragraph(doc: Document, latex: str):
    p = doc.add_paragraph()
    set_para(p, WD_ALIGN_PARAGRAPH.CENTER)
    p._p.append(omml_from_latex(latex))
    return p


def safe_warning_text(warnings: list) -> str:
    text = "；".join(str(x) for x in warnings)
    if looks_like_formula(text):
        return "该题存在需人工复核提示，详细内容请查看结构化答案审计信息。"
    return text


def add_page_field(paragraph):
    run = paragraph.add_run()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    r.append(t)
    fld.append(r)
    run._r.append(fld)


def setup_document() -> Document:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(18.2)
    section.page_height = Cm(25.7)
    for side in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
        setattr(section, side, Cm(1.70))
    section.header_distance = Cm(1.30)
    section.footer_distance = Cm(1.30)
    header_p = section.header.paragraphs[0]
    header_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = header_p.add_run(HEADER_TEXT)
    set_run_font(r, east="黑体", size=12, bold=True)
    footer_p = section.footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = footer_p.add_run(FOOTER_TEXT)
    set_run_font(r, east="华文新魏", size=10.5)
    page_p = section.footer.add_paragraph()
    page_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    start = page_p.add_run("第 ")
    set_run_font(start, size=10.5)
    add_page_field(page_p)
    end = page_p.add_run(" 页")
    set_run_font(end, size=10.5)
    return doc


def section_display_title(raw: str) -> str:
    return raw or "解析"


def _section_item(raw) -> dict:
    return raw if isinstance(raw, dict) else {"section": raw}


def is_choice_section(raw) -> bool:
    return is_choice_question(_section_item(raw))


def is_calculation_section(raw) -> bool:
    return is_calculation_question(_section_item(raw))


def is_short_answer_section(raw) -> bool:
    return is_short_answer_question(_section_item(raw))


def is_term_explanation_section(raw) -> bool:
    return is_term_explanation_question(_section_item(raw))


def is_graphic_section(raw) -> bool:
    return question_has_type(_section_item(raw), "作图题")


def is_fill_section(raw) -> bool:
    return question_has_type(_section_item(raw), "填空题")


def should_hide_top_answer(section: str) -> bool:
    return is_calculation_section(section) or is_short_answer_section(section)


def should_show_answer_summary(section: str, answer: str, answer_summary: str) -> bool:
    if should_hide_top_answer(section):
        return False
    if is_choice_section(section):
        return False
    if not answer_summary:
        return False
    if answer_summary in {"待复核", "待补充", "见解析"}:
        return False
    return answer_summary != answer or answer == "见解析"


def display_block_label(section: str, label: str) -> str:
    raw = str(label or "")
    if is_calculation_section(section) and raw == "解题步骤":
        return "答案"
    return raw


def should_split_block(section: str, label: str) -> bool:
    return is_calculation_section(section) and str(label or "") == "解题步骤"


def _answer_text(answer: str, answer_summary: str) -> str:
    summary = str(answer_summary or "").strip()
    if summary and summary not in {"待复核", "待补充", "见解析"}:
        return summary
    return str(answer or "").strip() or "待复核"


def _find_block(fragment: dict, label: str) -> dict | None:
    for block in fragment.get("blocks", []):
        if str(block.get("label", "")) == label:
            return block
    return None


def _add_block_if_present(doc: Document, fragment: dict, formulas: dict[str, dict], label: str, base_dir: Path) -> None:
    block = _find_block(fragment, label)
    if block is not None:
        add_mixed_paragraph(doc, block.get("segments", []), formulas, label, base_dir)


def _add_number_and_evidence(doc: Document, number: str, fragment: dict, formulas: dict[str, dict], base_dir: Path) -> None:
    p = doc.add_paragraph()
    set_para(p)
    number_run = p.add_run(f"{number}、")
    set_run_font(number_run, size=11)
    block = _find_block(fragment, "教材依据")
    if block is None:
        return
    label_run = p.add_run("教材依据：")
    set_run_font(label_run, size=11, bold=True)
    add_mixed_paragraph(
        doc,
        block.get("segments", []),
        formulas,
        "",
        base_dir,
        initial_paragraph=p,
        force_skip_formula_text_audit=True,
    )


def _add_answer_text(doc: Document, answer_text: str, formulas: dict[str, dict], strict_formula_audit: bool, base_dir: Path) -> None:
    text = str(answer_text or "")
    if "$" in text or _answer_summary_formula_candidates(text):
        add_labeled_formula_text_paragraph(doc, "答案", text, size=11, strict_formula_audit=strict_formula_audit)
        return
    add_mixed_paragraph(
        doc,
        [{"type": "text", "text": text}],
        formulas,
        "答案",
        base_dir,
    )


def _add_indented_answer_text(doc: Document, answer_text: str, strict_formula_audit: bool) -> None:
    add_text_paragraph(doc, "答案：", bold=True, size=11)
    text = normalize_answer_hierarchy_markers(answer_text)
    if "$" in text or _answer_summary_formula_candidates(text):
        add_formula_text_body_paragraph(doc, text, size=11, strict_formula_audit=strict_formula_audit)
        return
    p = doc.add_paragraph()
    set_para(p)
    p.paragraph_format.first_line_indent = Cm(ANSWER_BODY_FIRST_LINE_INDENT_CM)
    run = p.add_run(text)
    set_run_font(run, size=11)


def build_docx_from_fragments(fragments_json: Path, output_docx: Path, *, strict_answer_summary_formula_audit: bool = True) -> Path:
    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    doc = setup_document()
    add_text_paragraph(doc, "真题答案解析", bold=True, size=16, align=WD_ALIGN_PARAGRAPH.CENTER)
    current_section = ""
    for fragment in data.get("fragments", []):
        section = str(fragment.get("section", ""))
        if section and section != current_section:
            current_section = section
            add_text_paragraph(doc, section_display_title(section), bold=True, size=11)
        qid = str(fragment.get("question_id", "")).replace("_", "-")
        number = str(fragment.get("number") or qid)
        formulas = {str(f.get("formula_id")): f for f in fragment.get("formulas", [])}
        answer = str(fragment.get("answer", ""))
        answer_summary = str(fragment.get("answer_summary", "")).strip()
        section_context = {
            "section": section,
            "question_type": fragment.get("question_type") or "",
            "subquestions": fragment.get("subquestions") or [],
        }
        if is_term_explanation_section(section_context):
            _add_number_and_evidence(doc, number, fragment, formulas, fragments_json.parent)
            _add_indented_answer_text(doc, _answer_text(answer, answer_summary), strict_answer_summary_formula_audit)
            continue
        if is_graphic_section(section_context):
            _add_number_and_evidence(doc, number, fragment, formulas, fragments_json.parent)
            _add_indented_answer_text(doc, _answer_text(answer, answer_summary), strict_answer_summary_formula_audit)
            for label in ("图示", "解析", "易错点及注意事项"):
                _add_block_if_present(doc, fragment, formulas, label, fragments_json.parent)
            continue
        if is_short_answer_section(section_context):
            _add_number_and_evidence(doc, number, fragment, formulas, fragments_json.parent)
            _add_indented_answer_text(doc, _answer_text(answer, answer_summary), strict_answer_summary_formula_audit)
            for block in fragment.get("blocks", []):
                label = str(block.get("label", ""))
                if label in {"教材依据", "答案"}:
                    continue
                add_mixed_paragraph(doc, block.get("segments", []), formulas, label, fragments_json.parent)
            continue
        if is_calculation_section(section_context):
            _add_number_and_evidence(doc, number, fragment, formulas, fragments_json.parent)
            for block in fragment.get("blocks", []):
                label = str(block.get("label", ""))
                if label == "教材依据":
                    continue
                display_label = display_block_label(section_context, label)
                if should_split_block(section_context, label):
                    add_split_block(doc, block.get("segments", []), formulas, display_label, fragments_json.parent)
                else:
                    add_mixed_paragraph(doc, block.get("segments", []), formulas, display_label, fragments_json.parent)
            continue
        fill_answer_text = _answer_text(answer, answer_summary)
        if is_fill_section(section_context) and ("$" in fill_answer_text or _answer_summary_formula_candidates(fill_answer_text)):
            add_text_paragraph(doc, f"{number}、", size=11)
            _add_answer_text(doc, fill_answer_text, formulas, strict_answer_summary_formula_audit, fragments_json.parent)
            for block in fragment.get("blocks", []):
                add_mixed_paragraph(doc, block.get("segments", []), formulas, display_block_label(section_context, str(block.get("label", ""))), fragments_json.parent)
            continue
        if should_hide_top_answer(section_context):
            add_text_paragraph(doc, f"{number}、", size=11)
        else:
            add_text_paragraph(doc, f"{number}、{answer}", size=11)
        if should_show_answer_summary(section_context, answer, answer_summary):
            add_answer_summary_paragraph(doc, answer_summary, size=11, strict_formula_audit=strict_answer_summary_formula_audit)
        for block in fragment.get("blocks", []):
            label = str(block.get("label", ""))
            display_label = display_block_label(section_context, label)
            if should_split_block(section_context, label):
                add_split_block(doc, block.get("segments", []), formulas, display_label, fragments_json.parent)
            else:
                add_mixed_paragraph(doc, block.get("segments", []), formulas, display_label, fragments_json.parent)
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_docx)
    return output_docx
