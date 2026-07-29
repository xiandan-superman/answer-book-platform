from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any

from .text_utils import clean_text
from .textbook_package import TextbookPackage, resolve_package_asset


def _bbox(value: Any) -> str:
    if isinstance(value, list) and value:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return ""


def _caption(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("content") or item.get("text") or ""))
        return clean_text(" ".join(parts))
    return ""


def _html_to_text(value: str) -> str:
    text = re.sub(r"<eq>(.*?)</eq>", r" \1 ", str(value or ""), flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(html_lib.unescape(text))


def _asset_path(package: TextbookPackage, raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return str(resolve_package_asset(package.root, package.content_list, package.images_root, text))


def _block_type(raw_type: str) -> str:
    text = str(raw_type or "").strip().lower()
    if text == "equation":
        return "equation"
    return text or "text"


def _source_type(block_type: str) -> str:
    if block_type == "table":
        return "table_block"
    if block_type in {"image", "chart"}:
        return "figure_block"
    if block_type == "equation":
        return "equation_block"
    return "text_block"


def _text_for_item(item: dict[str, Any], block_type: str) -> tuple[str, str, str, str]:
    caption = ""
    table_html = ""
    ocr_text = ""
    if block_type == "table":
        caption = _caption(item.get("table_caption"))
        table_html = str(item.get("table_body") or item.get("html") or "")
        footnote = _caption(item.get("table_footnote"))
        text = clean_text(" ".join(part for part in [caption, _html_to_text(table_html), footnote] if clean_text(part)))
        ocr_text = footnote
        return text, caption, ocr_text, table_html
    if block_type in {"image", "chart"}:
        caption = _caption(item.get("image_caption") or item.get("chart_caption"))
        footnote = _caption(item.get("image_footnote") or item.get("chart_footnote"))
        content = clean_text(str(item.get("content") or item.get("text") or ""))
        text = clean_text(" ".join(part for part in [caption, content, footnote] if clean_text(part)))
        ocr_text = clean_text(" ".join(part for part in [content, footnote] if clean_text(part)))
        return text, caption, ocr_text, ""
    if block_type == "equation":
        text = clean_text(str(item.get("text") or item.get("content") or ""))
        return text, "", "", ""
    text = clean_text(str(item.get("text") or item.get("content") or ""))
    return text, "", "", ""


def rows_from_mineru_content_list(name: str, package: TextbookPackage) -> list[dict[str, Any]]:
    if package.content_list is None:
        return []
    data = json.loads(package.content_list.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    counters_by_page: dict[int, int] = {}
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        try:
            page_idx = int(item.get("page_idx") or 0)
        except Exception:
            page_idx = 0
        counters_by_page[page_idx] = counters_by_page.get(page_idx, 0) + 1
        block_index = counters_by_page[page_idx]
        block_type = _block_type(str(item.get("type") or "text"))
        source_type = _source_type(block_type)
        text, caption, ocr_text, table_html = _text_for_item(item, block_type)
        raw_asset = item.get("img_path") or item.get("image_path")
        asset_path = _asset_path(package, raw_asset)
        if not text and not asset_path and not table_html:
            continue
        block_id = f"{name}:p{page_idx}:b{block_index}"
        visual_status = ""
        visual_summary = ""
        visual_reason = ""
        if source_type in {"figure_block", "table_block", "equation_block"} and asset_path:
            if source_type == "table_block" and table_html:
                visual_status = "text_extract_available"
            elif caption or ocr_text:
                visual_status = "caption_or_ocr_available"
            else:
                visual_status = "needs_visual_understanding"
                visual_reason = "该教材块有图片资源，但缺少可供文本模型读取的 caption/OCR/结构化摘要。"
        rows.append(
            {
                "block_id": block_id,
                "textbook": name,
                "source_file": str(package.content_list),
                "page_idx": page_idx,
                "block_index": block_index,
                "reading_order": index,
                "block_type": block_type,
                "source_type": source_type,
                "chapter_section": "",
                "bbox": _bbox(item.get("bbox")),
                "text": text,
                "caption": caption,
                "ocr_text": ocr_text,
                "asset_path": asset_path,
                "table_html": table_html,
                "visual_summary": visual_summary,
                "visual_status": visual_status,
                "visual_unreadable_reason": visual_reason,
                "table_rows": "",
                "surrounding_text_refs": "",
                "surrounding_text_preview": "",
                "retrieval_text": "",
                "char_count": len(text),
            }
        )
    return rows
