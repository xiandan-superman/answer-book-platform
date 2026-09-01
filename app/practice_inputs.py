from __future__ import annotations

import base64
import mimetypes
import re
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from docx import Document
from lxml import etree
from PIL import Image

from .omml_input import find_omml2mathml_xsl, mixed_text_with_structured_math
from .pdf_render import pdf_page_count, render_pdf_pages
from .practice_source_store import (
    extraction_cache_key,
    load_extraction_cache,
    load_practice_source_file,
    save_extraction_cache,
)

MAX_FILE_COUNT = 12
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_BYTES = 36 * 1024 * 1024
MAX_REFERENCE_IMAGES = 24
MAX_ANALYSIS_IMAGES = MAX_REFERENCE_IMAGES
MIN_MEANINGFUL_TEXT_CHARS = 40


def _has_meaningful_text(text: str) -> bool:
    """Prefer native document text when it is substantial enough to analyze."""
    # Structured formula metadata improves fidelity but must not make a nearly
    # image-only document look text-rich merely because MathML has long tags.
    without_formula_metadata = re.sub(
        r"⟦(?:MATHML|OMML_[A-Z_]+|IMAGE(?:_REF)?):?.*?⟧",
        "",
        str(text or ""),
        flags=re.DOTALL,
    )
    visible = "".join(character for character in without_formula_metadata if character.isalnum())
    return len(visible) >= MIN_MEANINGFUL_TEXT_CHARS


def _decode_file(item: dict[str, Any]) -> tuple[str, str, bytes]:
    name = Path(str(item.get("name") or "未命名文件")).name
    mime = str(item.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream")
    if item.get("resource_id"):
        data = load_practice_source_file(item)
    else:
        encoded = str(item.get("data_url") or "")
        if "," in encoded and encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError(f"{name} 的文件内容无效。") from exc
    if not data:
        raise ValueError(f"{name} 是空文件。")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{name} 超过 12 MB 限制。")
    return name, mime, data


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _reference_image_data_url(data: bytes, mime: str) -> str:
    """Losslessly compact embedded document images before network transport."""
    original = _data_url(data, mime)
    if len(data) < 32 * 1024 or mime.lower() not in {"image/png", "image/jpeg", "image/jpg", "image/bmp", "image/tiff"}:
        return original
    try:
        with Image.open(BytesIO(data)) as source:
            converted = source.convert("RGBA" if "A" in source.getbands() else "RGB")
            output = BytesIO()
            converted.save(output, format="WEBP", lossless=True, method=6)
        compact = output.getvalue()
    except Exception:
        return original
    return _data_url(compact, "image/webp") if len(compact) <= len(data) * 0.9 else original


def _docx_content(name: str, data: bytes) -> tuple[str, list[str], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="practice-docx-") as raw_dir:
        path = Path(raw_dir) / name
        path.write_bytes(data)
        Document(path)
        diagnostics: dict[str, Any] = {
            "format": "docx",
            "omml_formula_count": 0,
            "omml_structured_formula_count": 0,
            "omml_degraded_formula_count": 0,
            "omml_input_backend": "microsoft_omml2mathml_xsl" if find_omml2mathml_xsl() else "visible_token_fallback",
            "table_count": 0,
            "embedded_image_count": 0,
            "image_anchor_count": 0,
            "image_anchor_count_total": 0,
            "image_count_included": 0,
            "warnings": [],
        }
        blocks: list[str] = []
        # python-docx exposes normal runs but omits Office Math (OMML). Read
        # document.xml as well so formula-bearing questions are not silently
        # truncated before they reach analysis.
        with ZipFile(path) as archive:
            root = etree.fromstring(archive.read("word/document.xml"))
            ns = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
            }
            diagnostics["omml_formula_count"] = len(root.xpath(".//m:oMathPara|.//m:oMath[not(ancestor::m:oMathPara)]", namespaces=ns))
            diagnostics["omml_text_token_count"] = len(root.xpath(".//m:t/text()", namespaces=ns))
            diagnostics["table_count"] = len(root.xpath(".//w:tbl", namespaces=ns))
            diagnostics["embedded_image_count"] = sum(
                1
                for member in archive.namelist()
                if member.startswith("word/media/") and not member.endswith("/")
            )
            rel_root = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
            rel_ns = {"pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
            rel_targets = {
                str(row.get("Id")): str(row.get("Target"))
                for row in rel_root.xpath(".//pr:Relationship", namespaces=rel_ns)
                if str(row.get("Type") or "").endswith("/image")
            }
            drawing_ns = {
                **ns,
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            }

            def media_members(element: Any) -> list[str]:
                members: list[str] = []
                for rel_id in element.xpath(".//a:blip/@r:embed", namespaces=drawing_ns):
                    target = rel_targets.get(str(rel_id), "").lstrip("/")
                    if target.startswith("media/"):
                        target = f"word/{target}"
                    if target.startswith("word/media/") and target not in members:
                        members.append(target)
                return members

            ordered_media: list[str] = []
            for member in media_members(root):
                if member not in ordered_media:
                    ordered_media.append(member)
            for member in sorted(
                name
                for name in archive.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            ):
                if member not in ordered_media:
                    ordered_media.append(member)
            diagnostics["embedded_image_order"] = ordered_media
            # Prefer figures that share a paragraph/table cell with visible
            # source text. Decorative or unanchored media only fills remaining
            # reference slots, preserving body order throughout.
            contextual_media: list[str] = []
            for element in root.xpath("./w:body/w:p|./w:body/w:tbl//w:tc", namespaces=ns):
                visible_text = "".join(element.xpath(".//w:t/text()|.//m:t/text()", namespaces=ns)).strip()
                if not visible_text:
                    continue
                for member in media_members(element):
                    if member not in contextual_media:
                        contextual_media.append(member)
            reference_media = contextual_media + [member for member in ordered_media if member not in contextual_media]
            diagnostics["reference_image_order"] = reference_media[:MAX_REFERENCE_IMAGES]
        anchorable_media = set(diagnostics["reference_image_order"])

        def image_anchor_suffix(element: Any) -> str:
            members: list[str] = []
            for rel_id in element.xpath(".//a:blip/@r:embed", namespaces=drawing_ns):
                target = rel_targets.get(str(rel_id), "").lstrip("/")
                if target.startswith("media/"):
                    target = f"word/{target}"
                if target.startswith("word/media/"):
                    members.append(target)
            diagnostics["image_anchor_count_total"] += len(members)
            included_members = [member for member in members if member in anchorable_media]
            diagnostics["image_anchor_count"] += len(included_members)
            return "".join(f"⟦IMAGE:{member}⟧" for member in included_members)

        table_index = 0
        for child in root.xpath("./w:body/*", namespaces=ns):
            local_name = etree.QName(child).localname
            if local_name == "p":
                extracted = mixed_text_with_structured_math(child)
                diagnostics["omml_structured_formula_count"] += extracted.structured_formula_count
                diagnostics["omml_degraded_formula_count"] += extracted.degraded_formula_count
                paragraph_text = extracted.text + image_anchor_suffix(child)
                if paragraph_text:
                    blocks.append(paragraph_text)
                continue
            if local_name != "tbl":
                continue
            table_index += 1
            blocks.append(f"\n### 表格 {table_index}")
            for row in child.xpath("./w:tr", namespaces=ns):
                cells: list[str] = []
                for cell in row.xpath("./w:tc", namespaces=ns):
                    extracted = mixed_text_with_structured_math(cell)
                    diagnostics["omml_structured_formula_count"] += extracted.structured_formula_count
                    diagnostics["omml_degraded_formula_count"] += extracted.degraded_formula_count
                    cells.append(extracted.text + image_anchor_suffix(cell))
                if any(cells):
                    blocks.append(" | ".join(cells))
        images: list[str] = []
        with ZipFile(path) as archive:
            for member in diagnostics.get("reference_image_order", []):
                member = f"word/media/{member}" if not str(member).startswith("word/media/") else str(member)
                image = archive.read(member)
                image_mime = mimetypes.guess_type(member)[0] or "image/png"
                if image_mime.startswith("image/"):
                    images.append(_reference_image_data_url(image, image_mime))
        diagnostics["image_count_included"] = len(images)
        if diagnostics["omml_degraded_formula_count"]:
            diagnostics["warnings"].append(
                f"{diagnostics['omml_degraded_formula_count']} 个 OMML 公式仅保留可见字符，结构化转换不可用。"
            )
        if diagnostics["omml_formula_count"] and not diagnostics["omml_text_token_count"]:
            diagnostics["warnings"].append("检测到 OMML 公式，但公式可见字符为空。")
        if diagnostics["embedded_image_count"] > diagnostics["image_count_included"]:
            diagnostics["warnings"].append("内嵌图片超过当前传递上限，部分图片未传给模型。")
        return "\n".join(blocks), images, diagnostics


def _pdf_content(name: str, data: bytes) -> tuple[str, list[str], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="practice-pdf-") as raw_dir:
        root = Path(raw_dir)
        source = root / name
        text_path = root / "content.txt"
        source.write_bytes(data)
        total_pages = pdf_page_count(source)
        diagnostics: dict[str, Any] = {
            "format": "pdf",
            "page_count_total": total_pages,
            "page_numbers_used": [],
            "page_numbers_omitted": [],
            "warnings": [],
        }
        try:
            subprocess.run(
                ["pdftotext", "-layout", str(source), str(text_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=45,
            )
            text = text_path.read_text(encoding="utf-8", errors="replace").strip()
        except (FileNotFoundError, subprocess.SubprocessError):
            text = ""
        # Text PDFs do not need page rendering. Besides being faster, this
        # prevents a text-first document from doing unused image work before
        # the model call.
        if _has_meaningful_text(text):
            diagnostics["page_numbers_used"] = list(range(1, total_pages + 1)) if total_pages else []
            return text, [], diagnostics
        images: list[str] = []
        try:
            rendered_pages = render_pdf_pages(
                source,
                root,
                prefix="page",
                dpi=135,
                image_format="jpeg",
                first_page=1,
                last_page=MAX_ANALYSIS_IMAGES,
            )
            for image_path in rendered_pages:
                images.append(_data_url(image_path.read_bytes(), "image/jpeg"))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            if not text:
                raise ValueError(f"{name} 无法解析；请确认 PDF 未损坏。") from exc
        used_count = min(total_pages or len(images), len(images))
        diagnostics["page_numbers_used"] = list(range(1, used_count + 1))
        if total_pages > used_count:
            diagnostics["page_numbers_omitted"] = list(range(used_count + 1, total_pages + 1))
            diagnostics["warnings"].append(f"PDF 共 {total_pages} 页，仅向模型传递前 {used_count} 页图像。")
        return text, images, diagnostics


def parse_practice_sources(payload: dict[str, Any]) -> dict[str, Any]:
    cache_key = extraction_cache_key(payload)
    cached = load_extraction_cache(cache_key) if cache_key else None
    if cached is not None:
        return cached
    text_parts: list[str] = []
    direct_text = str(payload.get("question_text") or "").strip()
    if direct_text:
        text_parts.append(f"## 手动输入\n\n{direct_text}")

    # Native images (an uploaded/pasted screenshot) always require vision.
    # Document renderings and embedded images are fallbacks: keep them only
    # when native text extraction did not produce a useful body of text.
    images: list[str] = []
    fallback_images: list[str] = []
    reference_images: list[str] = []
    file_diagnostics: list[dict[str, Any]] = []
    legacy_image = str(payload.get("image_data_url") or "").strip()
    if legacy_image:
        if not legacy_image.startswith("data:image/"):
            raise ValueError("题目图片格式无效。")
        images.append(legacy_image)
        reference_images.append(legacy_image)

    files = payload.get("source_files") if isinstance(payload.get("source_files"), list) else []
    if len(files) > MAX_FILE_COUNT:
        raise ValueError(f"一次最多上传 {MAX_FILE_COUNT} 个文件。")
    total_bytes = 0
    names: list[str] = []
    def filter_image_anchors(text: str, member_reference_numbers: dict[str, int]) -> tuple[str, int]:
        included_count = 0

        def keep(match: re.Match[str]) -> str:
            nonlocal included_count
            member = match.group(1)
            reference_number = member_reference_numbers.get(member)
            if reference_number is None:
                return ""
            included_count += 1
            return f"⟦IMAGE_REF:{reference_number};MEMBER:{member}⟧"

        filtered = re.sub(r"⟦IMAGE:([^⟧]+)⟧", keep, text)
        return filtered, included_count

    for raw in files:
        if not isinstance(raw, dict):
            continue
        name, mime, data = _decode_file(raw)
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("上传文件总大小不能超过 36 MB。")
        names.append(name)
        suffix = Path(name).suffix.lower()
        if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            image = _data_url(data, mime if mime.startswith("image/") else "image/png")
            images.append(image)
            reference_images.append(image)
        elif mime == "application/pdf" or suffix == ".pdf":
            text, pages, diagnostics = _pdf_content(name, data)
            if text:
                text_parts.append(f"## 文件：{name}\n\n{text}")
            if not _has_meaningful_text(text):
                fallback_images.extend(pages)
            reference_slots = max(0, MAX_REFERENCE_IMAGES - len(reference_images))
            included_references = pages[:reference_slots]
            reference_images.extend(included_references)
            diagnostics["reference_image_count_included"] = len(included_references)
            diagnostics["name"] = name
            diagnostics["analysis_mode"] = "text" if _has_meaningful_text(text) else "vision"
            diagnostics["page_images_available"] = len(pages)
            diagnostics["image_count_included"] = 0
            file_diagnostics.append(diagnostics)
        elif suffix == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            text, embedded, diagnostics = _docx_content(name, data)
            diagnostics["name"] = name
            use_embedded_images = not _has_meaningful_text(text)
            diagnostics["analysis_mode"] = "mixed" if use_embedded_images and embedded else "text"
            if not use_embedded_images:
                diagnostics["image_count_included"] = 0
            file_diagnostics.append(diagnostics)
            reference_slots = max(0, MAX_REFERENCE_IMAGES - len(reference_images))
            included_references = embedded[:reference_slots]
            diagnostics["reference_image_count_included"] = len(included_references)
            if text:
                first_reference_number = len(reference_images) + 1
                member_reference_numbers = {
                    member: first_reference_number + index
                    for index, member in enumerate(
                        diagnostics.get("reference_image_order", [])[: len(included_references)]
                    )
                }
                filtered_text, included_anchor_count = filter_image_anchors(text, member_reference_numbers)
                diagnostics["image_anchor_count_included"] = included_anchor_count
                text_parts.append(f"## 文件：{name}\n\n{filtered_text}")
            if use_embedded_images:
                fallback_images.extend(embedded)
            reference_images.extend(included_references)
            if len(embedded) > len(included_references):
                warning = "内嵌图片超过全局参考图像上限，部分图片未传给模型。"
                if warning not in diagnostics["warnings"]:
                    diagnostics["warnings"].append(warning)
        elif mime.startswith("text/") or suffix in {".txt", ".md"}:
            text_parts.append(f"## 文件：{name}\n\n{data.decode('utf-8', errors='replace').strip()}")
        else:
            raise ValueError(f"暂不支持文件类型：{name}")

    # The model receives one global image budget, not one budget per file.
    # Reconcile per-file counts after truncation so the UI cannot claim that
    # every image from a later file was delivered.
    remaining = max(0, MAX_ANALYSIS_IMAGES - len(images))
    for diagnostics in file_diagnostics:
        if diagnostics.get("analysis_mode") == "text":
            continue
        available = int(
            diagnostics.get("page_images_available")
            if diagnostics.get("format") == "pdf"
            else diagnostics.get("embedded_image_count")
            or 0
        )
        included = min(available, remaining)
        diagnostics["image_count_included"] = included
        remaining -= included
        if diagnostics.get("format") == "pdf":
            total_pages = int(diagnostics.get("page_count_total") or available)
            diagnostics["page_numbers_used"] = list(range(1, included + 1))
            diagnostics["page_numbers_omitted"] = list(range(included + 1, total_pages + 1))
        if available > included:
            warning = (
                "PDF 页面图像超过全局传递上限，部分页面未传给模型。"
                if diagnostics.get("format") == "pdf"
                else "内嵌图片超过全局传递上限，部分图片未传给模型。"
            )
            if warning not in diagnostics["warnings"]:
                diagnostics["warnings"].append(warning)
    images.extend(fallback_images[: max(0, MAX_ANALYSIS_IMAGES - len(images))])
    images = images[:MAX_ANALYSIS_IMAGES]
    # Preserve positional identity: IMAGE_REF:n in extracted DOCX text points
    # to the nth item in this list, even when two files contain equal bytes.
    reference_images = reference_images[:MAX_REFERENCE_IMAGES]
    text = "\n\n".join(part for part in text_parts if part.strip()).strip()
    if not text and not images:
        raise ValueError("请填写题目文字或上传题目文件。")
    result = {
        "text": text,
        "images": images,
        "reference_images": reference_images,
        "reference_image_count": len(reference_images),
        "file_names": names,
        "file_diagnostics": file_diagnostics,
        "analysis_mode": "mixed" if text and images else ("vision" if images else "text"),
    }
    if cache_key:
        save_extraction_cache(cache_key, result)
    return result
