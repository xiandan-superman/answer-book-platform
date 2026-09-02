from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .pdf_render import pdf_page_count, render_pdf_pages
from .render_word import export_docx_to_pdf

REPRESENTATION_SCHEMA = "answer_book.input_representations.v1"


def _pdf_page_texts(pdf: Path, page_count: int) -> list[str]:
    """Extract page-aligned text without making it a required dependency."""

    try:
        import pypdfium2 as pdfium

        texts: list[str] = []
        with pdfium.PdfDocument(str(pdf)) as document:
            for index in range(min(len(document), max(0, page_count))):
                page = document[index]
                text_page = None
                try:
                    text_page = page.get_textpage()
                    texts.append(str(text_page.get_text_range() or ""))
                finally:
                    if text_page is not None:
                        text_page.close()
                    page.close()
        return texts
    except Exception:
        return []


def render_page_representation(
    source: Path,
    output_dir: Path,
    *,
    source_format: str,
    max_pages: int,
    dpi: int = 135,
) -> dict[str, Any]:
    """Create a deterministic page-visual representation for a PDF or DOCX.

    Failure is returned as representation state so a caller can keep a usable
    structured-text representation.  The caller decides whether the missing
    visual representation is fatal for the current material.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_format = str(source_format or "").strip().lower()
    pdf = source
    try:
        if normalized_format == "docx":
            pdf = output_dir / "source.pdf"
            export_docx_to_pdf(source, pdf)
        elif normalized_format != "pdf":
            raise ValueError(f"unsupported page representation source: {normalized_format}")
        total_pages = pdf_page_count(pdf)
        if total_pages <= 0:
            raise RuntimeError("document page count is unavailable")
        included_count = min(total_pages, max(0, int(max_pages)))
        paths = render_pdf_pages(
            pdf,
            output_dir,
            prefix="page",
            dpi=dpi,
            image_format="jpeg",
            first_page=1,
            last_page=included_count,
        )
        return {
            "kind": "page_visuals",
            "status": "ready" if included_count == total_pages else "degraded",
            "source_format": normalized_format,
            "page_count_total": total_pages,
            "page_numbers_included": list(range(1, len(paths) + 1)),
            "page_numbers_omitted": list(range(len(paths) + 1, total_pages + 1)),
            "paths": [str(path) for path in paths],
            "page_texts": _pdf_page_texts(pdf, len(paths)),
            "error": "",
        }
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return {
            "kind": "page_visuals",
            "status": "failed",
            "source_format": normalized_format,
            "page_count_total": 0,
            "page_numbers_included": [],
            "page_numbers_omitted": [],
            "paths": [],
            "page_texts": [],
            "error": str(exc)[:500],
        }
