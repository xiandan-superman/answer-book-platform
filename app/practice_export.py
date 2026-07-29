from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .omml import omml_from_latex


BLUE = RGBColor(37, 99, 235)
DARK_BLUE = RGBColor(31, 77, 120)
MUTED = RGBColor(100, 116, 139)
INK = RGBColor(31, 41, 55)
FONT = "PingFang SC"
CHART_FONT_PATHS = (
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
)


def _text(value: Any, limit: int = 10000) -> str:
    return str(value or "").strip()[:limit]


def _set_run(run, *, size: float = 11, bold: bool = False, color: RGBColor = INK) -> None:
    run.font.name = FONT
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    for key in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(key), FONT)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _set_cell_free_style(style, *, size: float, color: RGBColor, bold: bool = False) -> None:
    style.font.name = FONT
    fonts = style._element.get_or_add_rPr().get_or_add_rFonts()
    for key in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(key), FONT)
    style.font.size = Pt(size)
    style.font.color.rgb = color
    style.font.bold = bold


def _page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    prefix = paragraph.add_run("第 ")
    _set_run(prefix, size=9, color=MUTED)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    paragraph._p.append(field)
    suffix = paragraph.add_run(" 页")
    _set_run(suffix, size=9, color=MUTED)


def _configure_document(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.82)
    section.bottom_margin = Inches(0.82)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.header_distance = Inches(0.42)
    section.footer_distance = Inches(0.42)

    normal = doc.styles["Normal"]
    _set_cell_free_style(normal, size=11, color=INK)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.25

    for name, size, color, before, after in (
        ("Title", 26, DARK_BLUE, 0, 8),
        ("Subtitle", 12, MUTED, 0, 18),
        ("Heading 1", 16, BLUE, 18, 10),
        ("Heading 2", 13, DARK_BLUE, 14, 7),
        ("Heading 3", 12, DARK_BLUE, 10, 5),
    ):
        style = doc.styles[name]
        _set_cell_free_style(style, size=size, color=color, bold=name != "Subtitle")
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    for name in ("List Number", "List Bullet"):
        style = doc.styles[name]
        _set_cell_free_style(style, size=11, color=INK)
        style.paragraph_format.left_indent = Inches(0.375)
        style.paragraph_format.first_line_indent = Inches(-0.188)
        style.paragraph_format.space_after = Pt(4)
        style.paragraph_format.line_spacing = 1.25

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = header.add_run("专项练习生成平台  |  研究生专项训练")
    _set_run(run, size=9, color=MUTED)
    _page_number(section.footer.paragraphs[0])


def _add_title_block(doc: Document, data: dict[str, Any]) -> None:
    kicker = doc.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kicker.paragraph_format.space_after = Pt(8)
    run = kicker.add_run("研究生专项训练")
    _set_run(run, size=11, bold=True, color=BLUE)

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("专项练习")

    goal = _text((data.get("blueprint") or {}).get("training_goal"))
    subtitle = doc.add_paragraph(style="Subtitle")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run(goal or "围绕原题核心考点形成由浅入深的专项训练")

    analysis = data.get("source_analysis") if isinstance(data.get("source_analysis"), dict) else {}
    metadata = [
        _text(analysis.get("subject")),
        _text(analysis.get("question_type")),
        _text(analysis.get("difficulty")),
    ]
    metadata = [item for item in metadata if item]
    if metadata:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(16)
        run = paragraph.add_run("  ·  ".join(metadata))
        _set_run(run, size=9.5, color=MUTED)


def _add_question(doc: Document, item: dict[str, Any], index: int) -> None:
    heading = doc.add_paragraph(style="Heading 2")
    heading.add_run(
        f"第 {index} 题  |  {_text(item.get('question_type'), 30) or '综合题'}"
        f"  |  {_text(item.get('difficulty'), 20) or '进阶'}"
    )
    target = _text(item.get("target_skill"), 800)
    if target:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(f"训练能力：{target}")
        _set_run(run, size=9.5, bold=True, color=BLUE)
    stem = doc.add_paragraph()
    stem.paragraph_format.keep_together = True
    stem.add_run(_text(item.get("stem")))
    _add_structured_assets(doc, item, "stem")
    for option in item.get("options") or []:
        if not isinstance(option, dict):
            continue
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.left_indent = Inches(0.2)
        label = paragraph.add_run(f"{_text(option.get('label'), 4)}. ")
        _set_run(label, bold=True, color=BLUE)
        paragraph.add_run(_text(option.get("text"), 3000))
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(4)


def _add_answer(doc: Document, item: dict[str, Any], index: int) -> None:
    heading = doc.add_paragraph(style="Heading 2")
    heading.add_run(f"第 {index} 题")
    answer = doc.add_paragraph()
    label = answer.add_run("参考答案：")
    _set_run(label, bold=True, color=BLUE)
    answer.add_run(_text(item.get("answer")))
    _add_structured_assets(doc, item, "solution")
    steps = item.get("solution_steps") if isinstance(item.get("solution_steps"), list) else []
    if steps:
        subheading = doc.add_paragraph(style="Heading 3")
        subheading.add_run("解析")
        for step in steps:
            paragraph = doc.add_paragraph(style="List Number")
            paragraph.add_run(_text(step, 3000))
    points = [_text(value, 100) for value in (item.get("knowledge_points") or []) if _text(value, 100)]
    if points:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("涉及知识点：" + "、".join(points))
        _set_run(run, size=9.5, color=MUTED)


def _matches_location(value: Any, location: str) -> bool:
    return location in (_text(value, 30) or "stem")


def _add_formula(doc: Document, formula: dict[str, Any]) -> None:
    caption = _text(formula.get("caption"), 300)
    if caption:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(caption)
        _set_run(run, size=9.5, color=MUTED)
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    try:
        paragraph._p.append(omml_from_latex(_text(formula.get("latex"), 3000)))
    except Exception:
        run = paragraph.add_run(_text(formula.get("latex"), 3000))
        _set_run(run, size=11, color=DARK_BLUE)


def _add_data_table(doc: Document, spec: dict[str, Any]) -> None:
    title = _text(spec.get("title"), 300)
    if title:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(title)
        _set_run(run, size=9.5, bold=True, color=DARK_BLUE)
    headers = spec.get("headers") if isinstance(spec.get("headers"), list) else []
    rows = spec.get("rows") if isinstance(spec.get("rows"), list) else []
    columns = max(len(headers), max((len(row) for row in rows if isinstance(row, list)), default=0))
    if not columns:
        return
    table = doc.add_table(rows=1 if headers else 0, cols=columns)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    width = Inches(6.5 / columns)
    if headers:
        for index, value in enumerate(headers):
            cell = table.rows[0].cells[index]
            cell.width = width
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            run = cell.paragraphs[0].add_run(_text(value, 500))
            _set_run(run, size=9.5, bold=True, color=DARK_BLUE)
    for raw in rows:
        if not isinstance(raw, list):
            continue
        cells = table.add_row().cells
        for index in range(columns):
            cells[index].width = width
            cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            run = cells[index].paragraphs[0].add_run(_text(raw[index] if index < len(raw) else "", 500))
            _set_run(run, size=9.5, color=INK)
    doc.add_paragraph()


def _chart_png(spec: dict[str, Any]) -> BytesIO | None:
    series = spec.get("series") if isinstance(spec.get("series"), list) else []
    if not any(isinstance(row, dict) and row.get("points") for row in series):
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.font_manager import FontProperties
    except Exception:
        return None
    chart_font_path = next((path for path in CHART_FONT_PATHS if path.exists()), None)
    chart_font = FontProperties(fname=str(chart_font_path)) if chart_font_path else None
    fig, ax = plt.subplots(figsize=(6.2, 3.2), dpi=160)
    kind = _text(spec.get("figure_type"), 30)
    for index, row in enumerate(series):
        if not isinstance(row, dict):
            continue
        points = row.get("points") or []
        xs = [point[0] for point in points if isinstance(point, list) and len(point) >= 2]
        ys = [point[1] for point in points if isinstance(point, list) and len(point) >= 2]
        if not xs:
            continue
        label = _text(row.get("name"), 100) or f"Series {index + 1}"
        if kind == "bar":
            ax.bar(xs, ys, alpha=0.8, label=label)
        elif kind == "scatter":
            ax.scatter(xs, ys, s=22, label=label)
        else:
            ax.plot(xs, ys, marker="o", linewidth=1.8, markersize=3.5, label=label)
    ax.set_xlabel(_text(spec.get("x_label"), 100), fontproperties=chart_font)
    ax.set_ylabel(_text(spec.get("y_label"), 100), fontproperties=chart_font)
    ax.grid(alpha=0.2)
    if len(series) > 1 or any(_text(row.get("name"), 100) for row in series if isinstance(row, dict)):
        ax.legend(frameon=False, prop=chart_font)
    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def _add_figure(doc: Document, spec: dict[str, Any]) -> None:
    title = _text(spec.get("title"), 300)
    if title:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(title)
        _set_run(run, size=9.5, bold=True, color=DARK_BLUE)
    image = _chart_png(spec)
    if image is not None:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run().add_picture(image, width=Inches(6.0))
    description = _text(spec.get("description"), 1500)
    if description:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(description)
        _set_run(run, size=9.5, color=MUTED)


def _add_structured_assets(doc: Document, item: dict[str, Any], location: str) -> None:
    for formula in item.get("formulas") or []:
        if isinstance(formula, dict) and _matches_location(formula.get("location"), location):
            _add_formula(doc, formula)
    for table in item.get("tables") or []:
        if isinstance(table, dict) and _matches_location(table.get("location"), location):
            _add_data_table(doc, table)
    for figure in item.get("figures") or []:
        if isinstance(figure, dict) and _matches_location(figure.get("location"), location):
            _add_figure(doc, figure)


def _apply_run_fonts(doc: Document) -> None:
    paragraphs = list(doc.paragraphs)
    for section in doc.sections:
        paragraphs.extend(section.header.paragraphs)
        paragraphs.extend(section.footer.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    for paragraph in paragraphs:
        for run in paragraph.runs:
            fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
            for key in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
                fonts.set(qn(key), FONT)


def build_practice_docx(data: dict[str, Any]) -> bytes:
    if not isinstance(data, dict) or not isinstance(data.get("exercises"), list) or not data["exercises"]:
        raise ValueError("没有可导出的专项练习。")
    doc = Document()
    _configure_document(doc)
    _add_title_block(doc, data)

    doc.add_paragraph("练习题", style="Heading 1")
    for index, item in enumerate(data["exercises"], start=1):
        if isinstance(item, dict):
            _add_question(doc, item, index)

    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_paragraph("参考答案与解析", style="Heading 1")
    for index, item in enumerate(data["exercises"], start=1):
        if isinstance(item, dict):
            _add_answer(doc, item, index)

    _apply_run_fonts(doc)
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
