from __future__ import annotations

import csv
import html as html_lib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .mineru_content import rows_from_mineru_content_list
from .textbook_package import is_textbook_package, prepare_textbook_package
from .text_utils import clean_text


BLOCK_FIELDS = [
    "block_id",
    "textbook",
    "source_file",
    "page_idx",
    "block_index",
    "reading_order",
    "block_type",
    "source_type",
    "chapter_section",
    "bbox",
    "text",
    "caption",
    "ocr_text",
    "asset_path",
    "table_html",
    "visual_summary",
    "visual_status",
    "visual_unreadable_reason",
    "table_rows",
    "surrounding_text_refs",
    "surrounding_text_preview",
    "retrieval_text",
    "char_count",
]
PAGE_MAP_FIELDS = [
    "textbook",
    "citation_textbook",
    "source_file",
    "pdf_page_idx",
    "printed_page",
    "page_source",
    "verified",
    "confidence",
    "notes",
]


PAGE_MARKERS = [
    re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->", re.IGNORECASE),
]
PAGE_NUMBER_TEXT_RE = re.compile(r"^\s*(?:第\s*)?(?:p\s*\.?\s*)?(\d{1,4})\s*页?\s*$", re.IGNORECASE)
ISOLATED_PAGE_NUMBER = re.compile(r"^\s*(\d{1,4})\s*$")
SPLIT_TEXTBOOK_SUFFIX_RE = re.compile(r"^(.*第\s*\d+\s*版[上下])\d+$")
MATERIAL_ANALYSIS_SPLIT_RE = re.compile(r"^(材料现代分析测试方法)[12]$")
DECIMAL_SECTION_RE = re.compile(r"^\s*(\d{1,2})\s*[.．]\s*(\d{1,2})\b")
CHAPTER_RE = re.compile(r"第\s*([一二三四五六七八九十百\d]+)\s*章")
SECTION_RE = re.compile(r"第\s*([一二三四五六七八九十百\d]+)\s*节")
CHINESE_ORDER_RE = re.compile(r"^\s*([一二三四五六七八九十])\s*[、.．]")
TITLE_BLOCK_TYPES = {"title", "heading", "header"}
PAGE_NUMBER_BLOCK_TYPES = {"page_number", "page-num", "page_num", "page"}
MIN_PAGE_MAP_RATIO = 0.6


@dataclass
class IndexResult:
    textbook_count: int
    block_count: int
    blocks_csv: str
    page_map_csv: str
    page_map_ok: bool = True
    page_map_issues: list[dict[str, Any]] | None = None


def citation_textbook_name(name: str) -> str:
    m = SPLIT_TEXTBOOK_SUFFIX_RE.match(name.strip())
    if m:
        return m.group(1)
    m = MATERIAL_ANALYSIS_SPLIT_RE.match(name.strip())
    if m:
        return m.group(1)
    return name.strip()


def chinese_number_to_int(value: str) -> int | None:
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2 and text[1] in digits:
        return 10 + digits[text[1]]
    if text.endswith("十") and len(text) == 2 and text[0] in digits:
        return digits[text[0]] * 10
    if "十" in text:
        left, right = text.split("十", 1)
        if left in digits and right in digits:
            return digits[left] * 10 + digits[right]
    return None


def detect_section(text: str, current_chapter: str = "") -> tuple[str, str]:
    clean = str(text or "").strip()
    decimal = DECIMAL_SECTION_RE.search(clean)
    if decimal:
        return f"{int(decimal.group(1))}.{int(decimal.group(2))}", str(int(decimal.group(1)))
    chapter = CHAPTER_RE.search(clean)
    if chapter:
        number = chinese_number_to_int(chapter.group(1))
        if number is not None:
            return str(number), str(number)
    section = SECTION_RE.search(clean)
    if section and current_chapter:
        number = chinese_number_to_int(section.group(1))
        if number is not None:
            return f"{current_chapter}.{number}", current_chapter
    ordered = CHINESE_ORDER_RE.search(clean)
    if ordered and current_chapter:
        number = chinese_number_to_int(ordered.group(1))
        if number is not None:
            return f"{current_chapter}.{number}", current_chapter
    return "", current_chapter


def collect_texts(obj: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(obj, dict):
        for key in ("text", "content", "html", "latex", "caption"):
            val = obj.get(key)
            if isinstance(val, str) and clean_text(val):
                texts.append(clean_text(val))
        for key in (
            "lines",
            "spans",
            "blocks",
            "preproc_blocks",
            "discarded_blocks",
            "dropped_blocks",
            "para_blocks",
            "cells",
            "children",
        ):
            val = obj.get(key)
            if isinstance(val, (list, dict)):
                texts.extend(collect_texts(val))
    elif isinstance(obj, list):
        for item in obj:
            texts.extend(collect_texts(item))
    elif isinstance(obj, str) and clean_text(obj):
        texts.append(clean_text(obj))
    return texts


def collect_values(obj: Any, key_name: str) -> list[str]:
    values: list[str] = []
    if isinstance(obj, dict):
        value = obj.get(key_name)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        for child in obj.values():
            if isinstance(child, (list, dict)):
                values.extend(collect_values(child, key_name))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(collect_values(item, key_name))
    return values


def collect_texts_by_type(obj: Any, type_keyword: str) -> list[str]:
    texts: list[str] = []
    if isinstance(obj, dict):
        block_type = str(obj.get("type") or obj.get("block_type") or obj.get("category") or "").lower()
        if type_keyword in block_type:
            texts.extend(collect_texts(obj))
        for child in obj.values():
            if isinstance(child, (list, dict)):
                texts.extend(collect_texts_by_type(child, type_keyword))
    elif isinstance(obj, list):
        for item in obj:
            texts.extend(collect_texts_by_type(item, type_keyword))
    return texts


def html_to_text(value: str) -> str:
    text = re.sub(r"<eq>(.*?)</eq>", r" \1 ", str(value or ""), flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(html_lib.unescape(text))


def block_type_of(obj: dict[str, Any]) -> str:
    return str(obj.get("type") or obj.get("block_type") or obj.get("category") or "text").strip() or "text"


def source_type_for_block(block_type: str) -> str:
    normalized = str(block_type or "").strip().lower()
    if normalized in {"image", "chart"}:
        return "figure_block"
    if normalized == "table":
        return "table_block"
    return "text_block"


def normalize_bbox(value: Any) -> str:
    if isinstance(value, list) and value:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return ""


def asset_path_for_block(source_path: Path | None, block: dict[str, Any]) -> str:
    paths = collect_values(block, "image_path")
    if not paths:
        return ""
    raw = paths[0]
    path = Path(raw)
    if path.is_absolute() or source_path is None:
        return str(path)
    resolved = source_path.parent / path
    return str(resolved if resolved.exists() else path)


def empty_block_record(block_type: str, text: str = "", bbox: str = "") -> dict[str, Any]:
    return {
        "block_type": block_type,
        "source_type": source_type_for_block(block_type),
        "bbox": bbox,
        "text": clean_text(text),
        "caption": "",
        "ocr_text": "",
        "asset_path": "",
        "table_html": "",
        "visual_summary": "",
        "visual_status": "",
        "visual_unreadable_reason": "",
        "table_rows": "",
    }


def block_record_from_obj(block: dict[str, Any], source_path: Path | None = None) -> dict[str, Any]:
    block_type = block_type_of(block)
    source_type = source_type_for_block(block_type)
    all_text = clean_text(" ".join(collect_texts(block)))
    caption = clean_text(" ".join(dict.fromkeys(collect_texts_by_type(block, "caption"))))
    table_html = next((value for value in collect_values(block, "html") if "<table" in value.lower()), "")
    table_text = html_to_text(table_html)
    asset_path = asset_path_for_block(source_path, block)
    if source_type == "table_block":
        text = clean_text(" ".join(part for part in [caption, table_text or all_text] if clean_text(part)))
        ocr_text = clean_text(all_text if all_text != caption else "")
    elif source_type == "figure_block":
        text = clean_text(" ".join(part for part in [caption, all_text] if clean_text(part)))
        ocr_text = clean_text(all_text if all_text != caption else "")
    else:
        text = all_text
        ocr_text = ""
    return {
        "block_type": block_type,
        "source_type": source_type,
        "bbox": normalize_bbox(block.get("bbox")),
        "text": text,
        "caption": caption,
        "ocr_text": ocr_text,
        "asset_path": asset_path,
        "table_html": table_html,
        "visual_summary": "",
        "visual_status": "",
        "visual_unreadable_reason": "",
        "table_rows": "",
    }


def page_block_records(page: Any, source_path: Path | None = None) -> list[dict[str, Any]]:
    if not isinstance(page, dict):
        return [empty_block_record("text", text) for text in collect_texts(page)]
    page_number_records: list[dict[str, Any]] = []
    discarded_blocks = page.get("discarded_blocks")
    if isinstance(discarded_blocks, list):
        for block in discarded_blocks:
            if not isinstance(block, dict):
                continue
            block_type = block_type_of(block).lower()
            if block_type in PAGE_NUMBER_BLOCK_TYPES:
                text = clean_text(" ".join(collect_texts(block)))
                if text:
                    page_number_records.append(empty_block_record(block_type, text, normalize_bbox(block.get("bbox"))))
    for key in ("preproc_blocks", "blocks", "para_blocks"):
        blocks = page.get(key)
        if not isinstance(blocks, list):
            continue
        records: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            record = block_record_from_obj(block, source_path)
            if clean_text(record.get("text", "")) or record.get("asset_path") or record.get("table_html"):
                records.append(record)
        if records:
            return records + page_number_records
    return [empty_block_record("text", text) for text in collect_texts(page)] + page_number_records


def page_records_from_json(data: dict[str, Any], source_path: Path) -> list[tuple[int, list[dict[str, Any]]]]:
    pages: list[tuple[int, list[dict[str, Any]]]] = []
    pdf_info = data.get("pdf_info")
    if isinstance(pdf_info, list):
        for fallback_idx, page in enumerate(pdf_info):
            if not isinstance(page, dict):
                continue
            page_idx = page.get("page_idx", page.get("page_id", fallback_idx))
            try:
                page_idx = int(page_idx)
            except Exception:
                page_idx = fallback_idx
            pages.append((page_idx, page_block_records(page, source_path)))
    elif isinstance(data.get("pages"), list):
        for fallback_idx, page in enumerate(data["pages"]):
            page_idx = page.get("page_idx", fallback_idx) if isinstance(page, dict) else fallback_idx
            try:
                page_idx = int(page_idx)
            except Exception:
                page_idx = fallback_idx
            pages.append((page_idx, page_block_records(page, source_path)))
    else:
        pages.append((0, page_block_records(data, source_path)))
    return pages


def rows_from_json(name: str, path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for page_idx, records in page_records_from_json(data, path):
        block_i = 0
        for record in records:
            text = clean_text(record.get("text", ""))
            if not text and not record.get("asset_path") and not record.get("table_html"):
                continue
            block_i += 1
            block_id = f"{name}:p{page_idx}:b{block_i}"
            rows.append(
                {
                    "block_id": block_id,
                    "textbook": name,
                    "source_file": str(path),
                    "page_idx": page_idx,
                    "block_index": block_i,
                    "reading_order": block_i,
                    "block_type": record.get("block_type", "text"),
                    "source_type": record.get("source_type", "text_block"),
                    "chapter_section": "",
                    "bbox": record.get("bbox", ""),
                    "text": text,
                    "caption": clean_text(record.get("caption", "")),
                    "ocr_text": clean_text(record.get("ocr_text", "")),
                    "asset_path": str(record.get("asset_path", "")),
                    "table_html": str(record.get("table_html", "")),
                    "visual_summary": str(record.get("visual_summary", "")),
                    "visual_status": str(record.get("visual_status", "")),
                    "visual_unreadable_reason": str(record.get("visual_unreadable_reason", "")),
                    "table_rows": str(record.get("table_rows", "")),
                    "surrounding_text_refs": "",
                    "surrounding_text_preview": "",
                    "retrieval_text": "",
                    "char_count": len(text),
                }
            )
    return rows


def rows_from_text(name: str, path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_idx = 0
    block_i = 0
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = clean_text(raw)
        if not text:
            continue
        marker = re.search(r"<!--\s*page\s*:\s*(\d+)\s*-->", text, flags=re.I)
        if marker:
            page_idx = int(marker.group(1))
            continue
        block_i += 1
        block_id = f"{name}:p{page_idx}:b{block_i}"
        rows.append(
            {
                "block_id": block_id,
                "textbook": name,
                "source_file": str(path),
                "page_idx": page_idx,
                "block_index": block_i,
                "reading_order": block_i,
                "block_type": "text",
                "source_type": "text_block",
                "chapter_section": "",
                "bbox": "",
                "text": text,
                "caption": "",
                "ocr_text": "",
                "asset_path": "",
                "table_html": "",
                "visual_summary": "",
                "visual_status": "",
                "visual_unreadable_reason": "",
                "table_rows": "",
                "surrounding_text_refs": "",
                "surrounding_text_preview": "",
                "retrieval_text": "",
                "char_count": len(text),
            }
        )
    return rows


def bind_surrounding_text(rows: list[dict[str, Any]], window: int = 1) -> list[dict[str, Any]]:
    by_page: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("textbook", "")), str(row.get("source_file", "")), int(row.get("page_idx", 0)))
        by_page.setdefault(key, []).append(row)
    for page_rows in by_page.values():
        page_rows.sort(key=lambda item: int(item.get("reading_order") or item.get("block_index") or 0))
        for index, row in enumerate(page_rows):
            if row.get("source_type") not in {"figure_block", "table_block"}:
                row["retrieval_text"] = clean_text(str(row.get("text", "")))
                continue
            refs: list[str] = []
            previews: list[str] = []
            neighbors = page_rows[max(0, index - window) : index] + page_rows[index + 1 : index + 1 + window]
            for neighbor in neighbors:
                if neighbor.get("source_type") != "text_block":
                    continue
                if str(neighbor.get("block_type", "")).lower() in PAGE_NUMBER_BLOCK_TYPES:
                    continue
                text = clean_text(str(neighbor.get("text", "")))
                if not text:
                    continue
                block_id = str(neighbor.get("block_id", "")).strip()
                if block_id:
                    refs.append(block_id)
                previews.append(text[:220])
            row["surrounding_text_refs"] = ",".join(dict.fromkeys(refs))
            row["surrounding_text_preview"] = clean_text(" ".join(previews))[:700]
            row["retrieval_text"] = clean_text(
                " ".join(
                    part
                    for part in [
                        str(row.get("caption", "")),
                        str(row.get("text", "")),
                        str(row.get("ocr_text", "")),
                        str(row.get("visual_summary", "")),
                        str(row.get("table_rows", "")),
                        str(row.get("surrounding_text_preview", "")),
                    ]
                    if clean_text(part)
                )
            )
    for row in rows:
        if not row.get("retrieval_text"):
            row["retrieval_text"] = clean_text(str(row.get("text", "")))
    return rows


def enrich_rows_with_sections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current_by_source: dict[tuple[str, str], tuple[str, str]] = {}
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (str(item.get("textbook", "")), str(item.get("source_file", "")), int(item.get("page_idx", 0)), int(item.get("block_index", 0)))):
        key = (str(row.get("textbook", "")), str(row.get("source_file", "")))
        current_section, current_chapter = current_by_source.get(key, ("", ""))
        block_type = str(row.get("block_type", "")).strip().lower()
        detected = ""
        if block_type in TITLE_BLOCK_TYPES:
            detected, current_chapter = detect_section(str(row.get("text", "")), current_chapter)
        if detected:
            current_section = detected
        current_by_source[key] = (current_section, current_chapter)
        enriched = dict(row)
        enriched["chapter_section"] = current_section
        out.append(enriched)
    return out


def textbook_name_from_file(path: Path) -> str:
    name = path.stem
    for suffix in ("_content_list", "_layout", "_middle", "_origin", "_blocks", "layout", "block_list"):
        name = name.replace(suffix, "")
    return name.strip("_ -") or path.stem


def discover_textbooks(textbooks_dir: Path) -> list[Path]:
    allowed = {".json", ".md", ".markdown", ".txt", ".zip"}
    return sorted(p for p in textbooks_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed)


def extract_printed_page(rows: list[dict[str, Any]]) -> tuple[str, str, str, str]:
    candidates: list[tuple[int, str, str]] = []
    for row in rows:
        block_type = str(row.get("block_type", "")).strip().lower()
        if block_type not in PAGE_NUMBER_BLOCK_TYPES:
            continue
        text = str(row.get("text", ""))
        m = PAGE_NUMBER_TEXT_RE.match(text) or ISOLATED_PAGE_NUMBER.match(text)
        if m:
            candidates.append((int(m.group(1)), "page_number_block", "high"))
    if candidates:
        printed, source, confidence = candidates[-1]
        return str(printed), source, "true", confidence

    # Only markup-style page markers are accepted from normal content. A phrase
    # like "详见405页脚注" is a reference to another page, not this page number.
    for row in rows[:6] + rows[-6:]:
        text = str(row.get("text", ""))
        for pattern in PAGE_MARKERS:
            m = pattern.search(text)
            if m:
                candidates.append((int(m.group(1)), "explicit_marker", "high"))
        isolated = ISOLATED_PAGE_NUMBER.match(text)
        if isolated:
            candidates.append((int(isolated.group(1)), "isolated_page_number", "medium"))
        full_page = PAGE_NUMBER_TEXT_RE.match(text)
        if full_page:
            candidates.append((int(full_page.group(1)), "page_number_line", "medium"))
    if not candidates:
        return "", "unmapped", "false", "none"
    printed, source, confidence = candidates[-1]
    return str(printed), source, "true", confidence


def build_page_map(rows: list[dict[str, Any]], citation_names_by_source: dict[str, str] | None = None) -> list[dict[str, str]]:
    citation_names_by_source = citation_names_by_source or {}
    by_page: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["textbook"]), str(row["source_file"]), int(row["page_idx"]))
        by_page.setdefault(key, []).append(row)
    out: list[dict[str, str]] = []
    for (textbook, source_file, page_idx), page_rows in sorted(by_page.items()):
        printed, page_source, verified, confidence = extract_printed_page(page_rows)
        citation_name = (
            citation_names_by_source.get(source_file)
            or citation_names_by_source.get(str(Path(source_file).expanduser().resolve()))
            or citation_textbook_name(textbook)
        )
        out.append(
            {
                "textbook": textbook,
                "citation_textbook": citation_name,
                "source_file": source_file,
                "pdf_page_idx": str(page_idx),
                "printed_page": printed,
                "page_source": page_source,
                "verified": verified,
                "confidence": confidence,
                "notes": "" if verified == "true" else "printed page not verified; do not cite this row",
            }
        )
    return infer_missing_pages(out)


def _page_identity(row: dict[str, str], page_key: str = "pdf_page_idx") -> tuple[str, str, str]:
    """Identify a physical source page, not just its display textbook name."""
    return (
        str(row.get("textbook", "")).strip(),
        str(row.get("source_file", "")).strip(),
        str(row.get(page_key, "")).strip(),
    )


def _page_source_label(textbook: str, source_file: str) -> str:
    source_name = Path(source_file).name if source_file else "未标明来源文件"
    return f"{textbook}（{source_name}）"


def audit_page_map(page_rows: list[dict[str, str]]) -> tuple[bool, list[dict[str, Any]]]:
    by_source: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in page_rows:
        textbook = str(row.get("textbook", "")).strip()
        if textbook:
            source_file = str(row.get("source_file", "")).strip()
            by_source.setdefault((textbook, source_file), []).append(row)
    issues: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, str, str]] = set()
    duplicate_identities: set[tuple[str, str, str]] = set()
    for row in page_rows:
        identity = _page_identity(row)
        if identity in seen_identities:
            duplicate_identities.add(identity)
        seen_identities.add(identity)
    for textbook, source_file, page_idx in sorted(duplicate_identities):
        issues.append(
            {
                "textbook": textbook,
                "source_file": source_file,
                "pdf_page_idx": page_idx,
                "message": f"{_page_source_label(textbook, source_file)} 的 PDF 第 {page_idx} 页存在重复页码表记录。",
            }
        )
    for (textbook, source_file), rows in sorted(by_source.items()):
        total = len(rows)
        mapped = sum(1 for row in rows if str(row.get("printed_page", "")).strip())
        ratio = mapped / total if total else 0.0
        if total >= 5 and (mapped == 0 or ratio < MIN_PAGE_MAP_RATIO):
            label = _page_source_label(textbook, source_file)
            issues.append(
                {
                    "textbook": textbook,
                    "source_file": source_file,
                    "page_count": total,
                    "mapped_page_count": mapped,
                    "mapped_ratio": round(ratio, 4),
                    "message": f"{label} 页码读取失败或覆盖率过低：{mapped}/{total} 页可用。请先校准教材页码后再继续。",
                }
            )
    return not issues, issues


def infer_missing_pages(page_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    offsets_by_source: dict[tuple[str, str], Counter[int]] = {}
    for row in page_rows:
        if row.get("verified") != "true" or not row.get("printed_page"):
            continue
        try:
            printed_page = int(row["printed_page"])
            pdf_page_idx = int(row["pdf_page_idx"])
            if pdf_page_idx < 30 and printed_page < 20:
                continue
            offset = printed_page - pdf_page_idx
        except Exception:
            continue
        offsets_by_source.setdefault((row["textbook"], row.get("source_file", "")), Counter())[offset] += 1
    dominant: dict[tuple[str, str], tuple[int, int]] = {}
    for source_key, counter in offsets_by_source.items():
        if not counter:
            continue
        offset, count = counter.most_common(1)[0]
        if count >= 1:
            dominant[source_key] = (offset, count)
    out: list[dict[str, str]] = []
    for row in page_rows:
        row = dict(row)
        source_key = (row.get("textbook", ""), row.get("source_file", ""))
        if row.get("verified") != "true" and source_key in dominant:
            offset, count = dominant[source_key]
            try:
                printed = int(row["pdf_page_idx"]) + offset
            except Exception:
                out.append(row)
                continue
            if printed > 0:
                row.update(
                    {
                        "printed_page": str(printed),
                        "page_source": "inferred_sequence",
                        "verified": "true",
                        "confidence": "medium" if count >= 2 else "low",
                        "notes": f"inferred from dominant page offset {offset} based on {count} detected pages; review if citation is critical",
                    }
                )
        out.append(row)
    return out


def load_manual_page_map(path: Path) -> dict[tuple[str, str, str], dict[str, str]]:
    if not path.exists():
        return {}
    manual: dict[tuple[str, str, str], dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            textbook = str(row.get("textbook", "")).strip()
            source_file = str(row.get("source_file", "")).strip()
            page_idx = str(row.get("pdf_page_idx") or row.get("page_idx") or "").strip()
            printed_page = str(row.get("printed_page", "")).strip()
            # A manual row without source_file is ambiguous for a multi-volume
            # textbook and must not be applied to the wrong volume.
            if not textbook or not source_file or not page_idx or not printed_page:
                continue
            manual[(textbook, source_file, page_idx)] = {
                "citation_textbook": str(row.get("citation_textbook") or citation_textbook_name(textbook)).strip(),
                "printed_page": printed_page,
                "page_source": str(row.get("page_source") or "manual").strip(),
                "verified": str(row.get("verified") or "true").strip(),
                "confidence": str(row.get("confidence") or "high").strip(),
                "notes": str(row.get("notes") or "manual verified printed page").strip(),
            }
    return manual


def apply_manual_page_map(
    page_rows: list[dict[str, str]], manual: dict[tuple[str, str, str], dict[str, str]]
) -> list[dict[str, str]]:
    if not manual:
        return page_rows
    out: list[dict[str, str]] = []
    for row in page_rows:
        key = _page_identity(row)
        if key in manual:
            row = dict(row)
            row.update(manual[key])
        out.append(row)
    return out


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_textbook_index_for_files(
    files: list[Path],
    stage_dir: Path,
    manual_csv: Path | None = None,
    citation_names_by_source: dict[str, str] | None = None,
) -> IndexResult:
    rows: list[dict[str, Any]] = []
    package_audits: list[dict[str, Any]] = []
    for path in files:
        name = textbook_name_from_file(path)
        if is_textbook_package(path):
            package = prepare_textbook_package(path)
            package_name = citation_names_by_source.get(str(path.resolve())) if citation_names_by_source else ""
            rows.extend(rows_from_mineru_content_list(package_name or package.citation_name or name, package))
            if package.audit_path.exists():
                try:
                    package_audits.append(json.loads(package.audit_path.read_text(encoding="utf-8")))
                except Exception:
                    pass
        elif path.suffix.lower() == ".json":
            rows.extend(rows_from_json(name, path))
        else:
            rows.extend(rows_from_text(name, path))
    rows = enrich_rows_with_sections(rows)
    rows = bind_surrounding_text(rows)
    blocks_csv = stage_dir / "textbook_blocks.csv"
    page_map_csv = stage_dir / "textbook_page_map.csv"
    write_csv(blocks_csv, BLOCK_FIELDS, rows)
    page_rows = build_page_map(rows, citation_names_by_source)
    if manual_csv is not None:
        page_rows = apply_manual_page_map(page_rows, load_manual_page_map(manual_csv))
    write_csv(page_map_csv, PAGE_MAP_FIELDS, page_rows)
    page_map_ok, page_map_issues = audit_page_map(page_rows)
    result = IndexResult(len(files), len(rows), str(blocks_csv), str(page_map_csv), page_map_ok, page_map_issues)
    status = asdict(result)
    if package_audits:
        status["textbook_package_audits"] = package_audits
        (stage_dir / "textbook_package_audit.json").write_text(json.dumps({"packages": package_audits}, ensure_ascii=False, indent=2), encoding="utf-8")
    (stage_dir / "textbook_index_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def build_textbook_index(textbooks_dir: Path, stage_dir: Path) -> IndexResult:
    files = discover_textbooks(textbooks_dir)
    return build_textbook_index_for_files(files, stage_dir, textbooks_dir / "textbook_page_map.manual.csv")
