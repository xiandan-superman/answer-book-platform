from __future__ import annotations

import json
import posixpath
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from lxml import etree
from zipfile import ZipFile

from .formula_audit import looks_like_formula
from .text_utils import clean_text, cn_to_int


SECTION_TITLE_PREFIX = r"(?:选择题|判断题|正误题|填空题|名词解释题|名词解释|名解题|简答题|问答题|计算题|回答下列问题)"
SECTION_RE = re.compile(
    rf"^([一二三四五六七八九十]+)(?:\s*、\s*|\s*[.．]\s*(?={SECTION_TITLE_PREFIX})|\s+(?={SECTION_TITLE_PREFIX}))(.*)"
)
ITEM_RE = re.compile(r"^(\d{1,2})([、.．])\s*(.*)")
MULTIPART_CUE_RE = re.compile(
    r"(回答下列|回答以下|完成下列|完成以下|按要求|分别|逐项|各小问|小问|问题|如下|试求|计算下列|求下列|试述下列|说明下列|分析下列|作图|画出|绘制)"
)
PAREN_SUBQUESTION_RE = re.compile(r"^[（(]\s*(\d{1,2})\s*[）)]\s*(.*)")
ORDINAL_SUBQUESTION_RE = re.compile(r"^第\s*([一二三四五六七八九十\d]{1,3})\s*(?:小?问|题)\s*[:：、.．]?\s*(.*)")
UNNUMBERED_SECTION_RE = re.compile(
    r"^(选择题|判断题|正误题|填空题|名词解释题|名词解释|名解题|简答题|问答题|计算题|回答下列问题)\s*[（(].*(?:本题|每小题|共|分).*[）)]?\s*$"
)
KNOWN_SUBJECT_TITLES = {"物理化学", "材料现代研究", "材料现代分析测试方法", "材料综合"}
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
IMAGE_MARKER_PREFIX = "__ANSWER_BOOK_IMAGE__:"
TABLE_MARKER_PREFIX = "__ANSWER_BOOK_TABLE__:"


def int_to_cn(value: int) -> str:
    digits = "一二三四五六七八九"
    if 1 <= value <= 9:
        return digits[value - 1]
    if value == 10:
        return "十"
    if 11 <= value <= 19:
        return "十" + digits[value - 11]
    if 20 <= value <= 99:
        tens, ones = divmod(value, 10)
        return digits[tens - 1] + "十" + (digits[ones - 1] if ones else "")
    return str(value)


def _subject_title(text: str) -> str:
    clean = clean_text(text).strip("“”\"'")
    clean = re.sub(r"\s*部分\s*$", "", clean).strip()
    if clean in KNOWN_SUBJECT_TITLES:
        return clean
    if "部分" in text and 2 <= len(clean) <= 20:
        return clean
    return ""


def _unnumbered_section_title(text: str) -> bool:
    return bool(UNNUMBERED_SECTION_RE.match(clean_text(text)))


def _document_relationships(zf: ZipFile) -> dict[str, str]:
    try:
        root = etree.fromstring(zf.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    out: dict[str, str] = {}
    for rel in root.xpath(".//*[local-name()='Relationship']"):
        rid = str(rel.get("Id") or "").strip()
        target = str(rel.get("Target") or "").strip()
        if rid and target:
            out[rid] = target
    return out


def _media_zip_path(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("word/"):
        return posixpath.normpath(target)
    return posixpath.normpath(posixpath.join("word", target))


def _table_rows_from_xml(tbl) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in tbl.xpath("./w:tr", namespaces=NS):
        row: list[str] = []
        for tc in tr.xpath("./w:tc", namespaces=NS):
            parts = tc.xpath(".//w:t/text()|.//m:t/text()", namespaces=NS)
            row.append(clean_text("".join(parts)))
        if any(cell for cell in row):
            rows.append(row)
    return rows


def _table_rows_from_docx_table(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [clean_text(cell.text) for cell in row.cells]
        if any(cells):
            rows.append(cells)
    return rows


def _table_to_text(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    rendered_rows = [" | ".join(cell for cell in row if cell) for row in rows]
    return "表格：" + "；".join(row for row in rendered_rows if row)


def _table_marker(rows: list[list[str]]) -> str:
    return TABLE_MARKER_PREFIX + json.dumps({"rows": rows}, ensure_ascii=False, separators=(",", ":"))


def _table_from_marker(line: str) -> dict:
    if not str(line).startswith(TABLE_MARKER_PREFIX):
        return {}
    try:
        payload = json.loads(str(line)[len(TABLE_MARKER_PREFIX) :])
    except json.JSONDecodeError:
        return {}
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    clean_rows = []
    for row in rows:
        if isinstance(row, list):
            clean_row = [clean_text(str(cell)) for cell in row]
            if any(clean_row):
                clean_rows.append(clean_row)
    if not clean_rows:
        return {}
    return {"rows": clean_rows, "text": _table_to_text(clean_rows)}


def _source_line_text(line: str) -> str:
    if str(line).startswith(TABLE_MARKER_PREFIX):
        return _table_from_marker(line).get("text", "")
    return str(line)


def _docx_paragraph_lines(path: Path, image_dir: Path | None = None) -> list[str]:
    lines: list[str] = []
    image_index = 0
    with ZipFile(path) as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
        relationships = _document_relationships(zf)
        for child in root.xpath("./w:body/*", namespaces=NS):
            local_name = etree.QName(child).localname
            if local_name == "p":
                parts = child.xpath(".//w:t/text()|.//m:t/text()", namespaces=NS)
                text = clean_text("".join(parts))
                if text:
                    lines.append(text)
                if image_dir is None:
                    continue
                for rid in child.xpath(".//a:blip/@r:embed", namespaces=NS):
                    target = relationships.get(str(rid))
                    if not target:
                        continue
                    media_path = _media_zip_path(target)
                    try:
                        image_bytes = zf.read(media_path)
                    except KeyError:
                        continue
                    image_index += 1
                    suffix = Path(media_path).suffix or ".png"
                    image_dir.mkdir(parents=True, exist_ok=True)
                    output = image_dir / f"source_image_{image_index:03d}{suffix}"
                    output.write_bytes(image_bytes)
                    lines.append(f"{IMAGE_MARKER_PREFIX}{output}")
            elif local_name == "tbl":
                rows = _table_rows_from_xml(child)
                if rows:
                    lines.append(_table_marker(rows))
    if lines:
        return lines
    doc = Document(path)
    fallback_lines = [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]
    for table in doc.tables:
        rows = _table_rows_from_docx_table(table)
        if rows:
            fallback_lines.append(_table_marker(rows))
    return fallback_lines


def docx_paragraph_texts(path: Path) -> list[str]:
    out: list[str] = []
    for line in _docx_paragraph_lines(path):
        if line.startswith(IMAGE_MARKER_PREFIX):
            continue
        text = _source_line_text(line)
        if text:
            out.append(text)
    return out


def _snapshot_font(size: int):
    try:
        from PIL import ImageFont

        for path in (
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
        ):
            font_path = Path(path)
            if font_path.exists():
                return ImageFont.truetype(str(font_path), size)
        return ImageFont.load_default()
    except Exception:
        return None


def _wrap_snapshot_text(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        paragraph = clean_text(raw)
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            try:
                width = draw.textlength(candidate, font=font)
            except Exception:
                width = len(candidate) * 14
            if current and width > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def _snapshot_table_text(item: dict) -> list[str]:
    out: list[str] = []
    for table in (item.get("attachments") or {}).get("tables") or []:
        if not isinstance(table, dict):
            continue
        text = clean_text(str(table.get("text") or ""))
        if text:
            out.append(text)
    return out


def _safe_snapshot_name(item: dict, index: int) -> str:
    qid = str(item.get("question_id") or f"question_{index:03d}")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", qid).strip("._") or f"question_{index:03d}"
    return f"{safe}.png"


def _write_question_snapshot(item: dict, snapshot_dir: Path, index: int) -> str:
    """Render a readable whole-question preview for the structure-review UI."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return ""

    width = 1180
    padding = 44
    gap = 18
    max_content_width = width - padding * 2
    title_font = _snapshot_font(34)
    meta_font = _snapshot_font(24)
    body_font = _snapshot_font(28)
    small_font = _snapshot_font(22)
    if not all([title_font, meta_font, body_font, small_font]):
        return ""

    scratch = Image.new("RGB", (width, 200), "white")
    draw = ImageDraw.Draw(scratch)
    title = f"{item.get('number') or index}  {item.get('section') or item.get('section_raw') or ''}".strip()
    text_parts = [str(item.get("stem") or "").strip()]
    text_parts.extend(_snapshot_table_text(item))
    wrapped_text: list[tuple[str, object]] = []
    for part_index, part in enumerate(text_parts):
        if not part:
            continue
        if part_index:
            wrapped_text.append(("", body_font))
        for line in _wrap_snapshot_text(draw, part, body_font, max_content_width):
            wrapped_text.append((line, body_font))

    rendered_images: list[tuple[str, object]] = []
    for raw in item.get("image_refs") or []:
        path = Path(str(raw))
        if not path.exists() or not path.is_file():
            continue
        try:
            image = Image.open(path).convert("RGB")
            image.thumbnail((max_content_width, 620))
            rendered_images.append((path.name, image.copy()))
        except Exception:
            rendered_images.append((path.name, None))

    height = padding
    height += 44 + gap
    if item.get("question_id"):
        height += 30 + gap
    for line, font in wrapped_text:
        height += (34 if line else 18) + 6
    if rendered_images:
        height += gap
    for _name, image in rendered_images:
        if image is not None:
            height += image.height + 58
        else:
            height += 42
    height += padding
    canvas = Image.new("RGB", (width, max(height, 360)), "#ffffff")
    draw = ImageDraw.Draw(canvas)

    y = padding
    draw.text((padding, y), title or f"题目 {index}", font=title_font, fill="#1f2937")
    y += 52
    if item.get("question_id"):
        draw.text((padding, y), str(item.get("question_id")), font=meta_font, fill="#64748b")
        y += 40
    for line, font in wrapped_text:
        if not line:
            y += 18
            continue
        draw.text((padding, y), line, font=font, fill="#1f2937")
        y += 40
    if rendered_images:
        y += gap
    for name, image in rendered_images:
        if image is None:
            draw.text((padding, y), f"[图片暂不能预览] {name}", font=small_font, fill="#94a3b8")
            y += 42
            continue
        x = padding + max(0, (max_content_width - image.width) // 2)
        draw.rounded_rectangle((x - 10, y - 10, x + image.width + 10, y + image.height + 10), radius=16, outline="#dbeafe", width=3, fill="#f8fafc")
        canvas.paste(image, (x, y))
        y += image.height + 24
        label = f"原题图片：{name}"
        label_width = draw.textlength(label, font=small_font)
        draw.text((padding + max(0, (max_content_width - label_width) // 2), y), label, font=small_font, fill="#64748b")
        y += 38

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    output = snapshot_dir / _safe_snapshot_name(item, index)
    canvas.save(output)
    return str(output)


def _attach_question_snapshots(items: list[dict], output_json: Path) -> None:
    snapshot_dir = output_json.parent / "question_snapshots"
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        snapshot = _write_question_snapshot(item, snapshot_dir, index)
        if snapshot:
            item["question_snapshot_refs"] = [snapshot]


def section_kind(raw_title: str, body: list[str]) -> tuple[str, str]:
    if "回答下列问题" in raw_title or "回答问题" in raw_title:
        return "问答题", "qa"
    if "选择" in raw_title:
        return "选择题", "choice"
    if "判断" in raw_title or "正误" in raw_title:
        return "判断题", "judge"
    if "填空" in raw_title:
        return "填空题", "fill"
    if "计算" in raw_title:
        return "计算题", "calc"
    if "简答" in raw_title:
        return "简答题", "qa"
    text = raw_title + " " + " ".join(body[:5])
    if "判断" in text or "正误" in text:
        return "判断题", "judge"
    if "选择" in text:
        return "选择题", "choice"
    if "填空" in text:
        return "填空题", "fill"
    if "计算" in text:
        return "计算题", "calc"
    if "简答" in text:
        return "简答题", "qa"
    return "问答题", "qa"


def split_sections(paragraphs: list[str]) -> list[dict]:
    sections: list[dict] = []
    current: dict | None = None
    subject_index = 1
    subject_title = ""
    subject_seen = False
    last_major_no = 0
    for para in paragraphs:
        subject = _subject_title(para)
        if subject and not SECTION_RE.match(para):
            if current:
                sections.append(current)
                current = None
            if subject_seen or last_major_no:
                subject_index += 1
            subject_seen = True
            subject_title = subject
            continue
        m = SECTION_RE.match(para)
        if m:
            if current:
                sections.append(current)
            major_no = cn_to_int(m.group(1)) or last_major_no + 1 or 1
            last_major_no = major_no
            current = {
                "cn": m.group(1),
                "major_no": major_no,
                "raw_title": para,
                "title_tail": clean_text(m.group(2)),
                "body": [],
                "subject_index": subject_index,
                "subject": subject_title,
            }
        elif _unnumbered_section_title(para):
            if current:
                sections.append(current)
            last_major_no += 1
            current = {
                "cn": int_to_cn(last_major_no),
                "major_no": last_major_no,
                "raw_title": para,
                "title_tail": clean_text(para),
                "body": [],
                "subject_index": subject_index,
                "subject": subject_title,
            }
        elif current:
            current["body"].append(para)
    if current:
        sections.append(current)
    if not sections:
        sections.append({"cn": "一", "major_no": 1, "raw_title": "一、问答题", "title_tail": "", "body": paragraphs, "subject_index": 1, "subject": ""})
    return sections


def has_image_hint(text: str) -> bool:
    return bool(re.search(r"下图|如图|图中|画出|绘制|示意图|衍射花样|标出.*斑点", text))


def _has_multipart_cue(lines: list[str]) -> bool:
    text = clean_text("\n".join(lines[:3]))
    return bool(MULTIPART_CUE_RE.search(text) or text.rstrip().endswith(("：", ":")))


def _subquestion_entry(number: str, marker: str, text: str, raw: str) -> dict[str, str]:
    entry = {
        "number": str(number),
        "marker": marker,
        "stem": clean_text(text),
        "raw": clean_text(raw),
    }
    requirements = _infer_nested_requirements(entry["stem"], str(number))
    if requirements:
        entry["requirements"] = requirements
    return entry


def _requirement_type(text: str) -> str:
    value = clean_text(text)
    if re.search(r"(画出|绘制|作图|示意图|图示|标出)", value):
        return "作图题"
    if re.search(r"(计算|求出|求得|质量比|质量分数|百分数|含量|比例|数值|多少)", value):
        return "计算题"
    if re.search(r"(判断|是否|可逆|正误)", value):
        return "判断题"
    return "简答题"


def _split_requirement_text(text: str) -> list[str]:
    value = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", clean_text(text)).strip()
    if not value:
        return []
    chunks: list[str] = []
    # 先按明显问句边界拆，再把带“画出 A 和 B，计算 C”的复合句继续拆开。
    for piece in re.split(r"(?<=[？?])\s*", value):
        piece = piece.strip(" ；;。")
        if not piece:
            continue
        subpieces = re.split(r"[，,；;]\s*(?=(?:并)?(?:计算|求出|求得|判断|说明|写出|列出|画出|绘制|作图|标出))", piece)
        chunks.extend(part.strip(" ；;。") for part in subpieces if part.strip(" ；;。"))
    out: list[str] = []
    for chunk in chunks:
        split = re.match(r"^(画出|绘制|作图|标出)(.+?)和(.+?)(?:，|,|；|;|$)", chunk)
        if split and any(keyword in split.group(2) + split.group(3) for keyword in ("曲线", "示意图", "组织", "图")):
            left = clean_text(split.group(1) + split.group(2))
            right = clean_text(split.group(1) + split.group(3))
            if left:
                out.append(left)
            if right:
                out.append(right)
            continue
        out.append(chunk)
    merged: list[str] = []
    index = 0
    while index < len(out):
        current = out[index].strip()
        if re.match(r"^(根据|依据|由).{0,16}(结果|数据|条件|计算)$", current) and index + 1 < len(out):
            merged.append(clean_text(f"{current}，{out[index + 1]}"))
            index += 2
            continue
        if current and not re.fullmatch(r"[（(]?\s*\d+(?:\.\d+)?\s*分\s*[）)]?", current):
            merged.append(current)
        index += 1
    return merged


def _infer_nested_requirements(text: str, parent_number: str) -> list[dict[str, str]]:
    parts = _split_requirement_text(text)
    meaningful = [part for part in parts if part]
    if len(meaningful) < 2:
        return []
    types = [_requirement_type(part) for part in meaningful]
    # 多个问句或混合题型才拆；单纯一句话被误切时保持一级小问。
    if len(set(types)) < 2 and len(meaningful) <= 2 and not re.search(r"[？?].+[？?]", text):
        return []
    requirements: list[dict[str, str]] = []
    for index, (part, qtype) in enumerate(zip(meaningful, types), start=1):
        number = f"{parent_number}.{index}"
        requirements.append(
            {
                "number": number,
                "marker": number,
                "stem": clean_text(part),
                "raw": clean_text(part),
                "question_type": qtype,
            }
        )
    return requirements


def _extract_subquestion_from_line(line: str) -> dict[str, str] | None:
    text = clean_text(line)
    m = PAREN_SUBQUESTION_RE.match(text)
    if m:
        return _subquestion_entry(m.group(1), f"({m.group(1)})", m.group(2), text)
    m = ORDINAL_SUBQUESTION_RE.match(text)
    if m:
        raw_no = m.group(1)
        number = str(cn_to_int(raw_no) or raw_no)
        return _subquestion_entry(number, f"第{raw_no}问", m.group(2), text)
    m = ITEM_RE.match(text)
    if m:
        return _subquestion_entry(m.group(1), f"{m.group(1)}{m.group(2)}", m.group(3), text)
    return None


def _collect_subquestions(lines: list[str]) -> list[dict[str, str]]:
    subquestions: list[dict[str, str]] = []
    for line in lines:
        entry = _extract_subquestion_from_line(line)
        if entry:
            subquestions.append(entry)
    return subquestions


def _append_subquestion(current: dict, line: str, number: int, delimiter: str, text: str) -> None:
    current["stem_lines"].append(clean_text(line))
    current["_subquestion_started"] = True
    current["_last_subquestion_number"] = number
    current.setdefault("_subquestion_lines", []).append(clean_text(line))


def _looks_like_subquestion(current: dict, number: int, delimiter: str, text: str) -> bool:
    if not current or not _has_multipart_cue(current.get("stem_lines", [])):
        return False
    if delimiter in {".", "．"}:
        return True
    started = bool(current.get("_subquestion_started"))
    last_number = int(current.get("_last_subquestion_number") or 0)
    if not started:
        return number == 1
    return number == last_number + 1


def question_items(section: dict) -> list[dict]:
    major_no = int(section.get("major_no") or cn_to_int(section["cn"]) or 1)
    subject_index = int(section.get("subject_index") or 1)
    kind, prefix = section_kind(section["raw_title"], section["body"])
    final_section_title = f"{section['cn']}、{kind}"
    body = section["body"]
    items: list[dict] = []
    if prefix in {"judge", "choice", "fill"}:
        current: dict | None = None
        for line in body:
            m = ITEM_RE.match(line)
            if m:
                if current:
                    items.append(current)
                number = int(m.group(1))
                current = {
                    "question_id": f"{prefix}_s{subject_index:02d}_{major_no:02d}_{number:02d}",
                    "subject_index": subject_index,
                    "subject": section.get("subject", ""),
                    "major_number": str(major_no),
                    "section": final_section_title,
                    "section_raw": section["raw_title"],
                    "number": str(number),
                    "stem_lines": [clean_text(m.group(3))] if clean_text(m.group(3)) else [],
                }
            elif current:
                current["stem_lines"].append(line)
        if current:
            items.append(current)
    else:
        current = None
        heading: list[str] = []
        split_any = False
        for line in body:
            m = ITEM_RE.match(line)
            if m:
                number = int(m.group(1))
                delimiter = m.group(2)
                text = clean_text(m.group(3))
                if current and _looks_like_subquestion(current, number, delimiter, text):
                    _append_subquestion(current, line, number, delimiter, text)
                    continue
                split_any = True
                if current:
                    items.append(current)
                starts_multipart = bool(heading and _has_multipart_cue(heading))
                current = {
                    "question_id": f"{prefix}_s{subject_index:02d}_{major_no:02d}_{number:02d}",
                    "subject_index": subject_index,
                    "subject": section.get("subject", ""),
                    "major_number": str(major_no),
                    "section": final_section_title,
                    "section_raw": section["raw_title"],
                    "number": str(number),
                    "stem_lines": heading + ([clean_text(line) if starts_multipart else text] if text else []),
                    "_subquestion_started": starts_multipart,
                    "_last_subquestion_number": number if starts_multipart else 0,
                }
            elif current:
                if _extract_subquestion_from_line(line):
                    current["_subquestion_started"] = True
                current["stem_lines"].append(line)
            else:
                heading.append(line)
        if current:
            items.append(current)
        if not split_any:
            items.append(
                {
                    "question_id": f"{prefix}_s{subject_index:02d}_{major_no:02d}_01",
                    "subject_index": subject_index,
                    "subject": section.get("subject", ""),
                    "major_number": str(major_no),
                    "section": final_section_title,
                    "section_raw": section["raw_title"],
                    "number": "1",
                    "stem_lines": body,
                }
            )
    for item in items:
        image_refs: list[str] = []
        lines = []
        raw_stem_lines = item.pop("stem_lines", [])
        for raw in raw_stem_lines:
            text = clean_text(raw)
            if not text:
                continue
            if text.startswith(IMAGE_MARKER_PREFIX):
                image_refs.append(text[len(IMAGE_MARKER_PREFIX) :].strip())
                continue
            if text.startswith(TABLE_MARKER_PREFIX):
                table = _table_from_marker(text)
                if table:
                    item.setdefault("attachments", {}).setdefault("tables", []).append(table)
                continue
            lines.append(text)
        stem = "\n".join(lines)
        item["stem"] = stem
        subquestions = _collect_subquestions(lines)
        if subquestions:
            item["subquestions"] = subquestions
        item["image_refs"] = image_refs
        item["needs_figure"] = has_image_hint(stem)
        item["formula_refs"] = [f"{item['question_id']}_source_formula"] if looks_like_formula(stem) else []
        item["status"] = "registered"
        for key in ("_subquestion_started", "_last_subquestion_number", "_subquestion_lines"):
            item.pop(key, None)
    return items


def extract_exam_structure(exam_file: Path, output_json: Path) -> dict:
    image_dir = output_json.parent / "source_images"
    paragraphs = _docx_paragraph_lines(exam_file, image_dir=image_dir)
    items: list[dict] = []
    for section in split_sections(paragraphs):
        items.extend(question_items(section))
    _attach_question_snapshots(items, output_json)
    source_paragraphs = [line for line in paragraphs if not str(line).startswith(IMAGE_MARKER_PREFIX)]
    source_paragraphs = [text for line in source_paragraphs if (text := _source_line_text(str(line)))]
    data = {
        "schema_version": "structured_exam.v4.program_extracted",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "exam_file": str(exam_file),
        "paragraph_count": len(source_paragraphs),
        "source_paragraphs": source_paragraphs,
        "items": items,
        "notes": ["Program extracted structure; review warnings before production."],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
