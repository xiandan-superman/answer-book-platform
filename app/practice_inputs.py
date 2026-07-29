from __future__ import annotations

import base64
import mimetypes
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from docx import Document


MAX_FILE_COUNT = 12
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_BYTES = 36 * 1024 * 1024
MAX_PDF_PAGES = 8


def _decode_file(item: dict[str, Any]) -> tuple[str, str, bytes]:
    name = Path(str(item.get("name") or "未命名文件")).name
    mime = str(item.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream")
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


def _docx_content(name: str, data: bytes) -> tuple[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="practice-docx-") as raw_dir:
        path = Path(raw_dir) / name
        path.write_bytes(data)
        document = Document(path)
        blocks: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if text:
                blocks.append(text)
        for table_index, table in enumerate(document.tables, start=1):
            blocks.append(f"\n### 表格 {table_index}")
            for row in table.rows:
                blocks.append(" | ".join(cell.text.strip() for cell in row.cells))
        images: list[str] = []
        with ZipFile(path) as archive:
            for member in sorted(name for name in archive.namelist() if name.startswith("word/media/"))[:MAX_PDF_PAGES]:
                image = archive.read(member)
                image_mime = mimetypes.guess_type(member)[0] or "image/png"
                if image_mime.startswith("image/"):
                    images.append(_data_url(image, image_mime))
        return "\n".join(blocks), images


def _pdf_content(name: str, data: bytes) -> tuple[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="practice-pdf-") as raw_dir:
        root = Path(raw_dir)
        source = root / name
        text_path = root / "content.txt"
        source.write_bytes(data)
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
        images: list[str] = []
        try:
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    "1",
                    "-l",
                    str(MAX_PDF_PAGES),
                    "-jpeg",
                    "-r",
                    "135",
                    str(source),
                    str(root / "page"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=90,
            )
            for image_path in sorted(root.glob("page-*.jpg")):
                images.append(_data_url(image_path.read_bytes(), "image/jpeg"))
        except (FileNotFoundError, subprocess.SubprocessError):
            if not text:
                raise ValueError(f"{name} 无法解析；请确认 PDF 未损坏。")
        return text, images


def parse_practice_sources(payload: dict[str, Any]) -> dict[str, Any]:
    text_parts: list[str] = []
    direct_text = str(payload.get("question_text") or "").strip()
    if direct_text:
        text_parts.append(f"## 手动输入\n\n{direct_text}")

    images: list[str] = []
    legacy_image = str(payload.get("image_data_url") or "").strip()
    if legacy_image:
        if not legacy_image.startswith("data:image/"):
            raise ValueError("题目图片格式无效。")
        images.append(legacy_image)

    files = payload.get("source_files") if isinstance(payload.get("source_files"), list) else []
    if len(files) > MAX_FILE_COUNT:
        raise ValueError(f"一次最多上传 {MAX_FILE_COUNT} 个文件。")
    total_bytes = 0
    names: list[str] = []
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
            images.append(_data_url(data, mime if mime.startswith("image/") else "image/png"))
        elif mime == "application/pdf" or suffix == ".pdf":
            text, pages = _pdf_content(name, data)
            if text:
                text_parts.append(f"## 文件：{name}\n\n{text}")
            images.extend(pages)
        elif suffix == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            text, embedded = _docx_content(name, data)
            if text:
                text_parts.append(f"## 文件：{name}\n\n{text}")
            images.extend(embedded)
        elif mime.startswith("text/") or suffix in {".txt", ".md"}:
            text_parts.append(f"## 文件：{name}\n\n{data.decode('utf-8', errors='replace').strip()}")
        else:
            raise ValueError(f"暂不支持文件类型：{name}")

    images = images[:MAX_PDF_PAGES]
    text = "\n\n".join(part for part in text_parts if part.strip()).strip()
    if not text and not images:
        raise ValueError("请填写题目文字或上传题目文件。")
    return {"text": text, "images": images, "file_names": names}
