from __future__ import annotations

from unittest.mock import patch

from docx import Document
from PIL import Image

from app.render_audit import audit_docx_pdf_consistency


def _write_docx(path, *, with_drawing: bool = False) -> str:
    text = "这是一段用于确认正式Word与渲染PDF来自同一版本的足够长文本，任何旧文件都不应通过一致性检查。"
    document = Document()
    document.add_paragraph(text)
    if with_drawing:
        image = path.with_suffix(".png")
        Image.new("RGB", (80, 60), "white").save(image)
        document.add_picture(str(image))
    document.save(path)
    return text


def test_consistency_audit_blocks_stale_pdf_text(tmp_path) -> None:
    docx = tmp_path / "answer.docx"
    pdf = tmp_path / "answer.pdf"
    _write_docx(docx)
    pdf.write_bytes(b"placeholder")

    with patch("app.render_audit._pdf_text_and_image_count", return_value=("完全不同的旧版内容", 0)):
        report = audit_docx_pdf_consistency(docx, pdf)

    assert not report["ok"]
    assert any("does not match current DOCX" in issue for issue in report["issues"])


def test_consistency_audit_accepts_matching_text_and_blocks_omitted_drawing(tmp_path) -> None:
    docx = tmp_path / "answer.docx"
    pdf = tmp_path / "answer.pdf"
    text = _write_docx(docx, with_drawing=True)
    pdf.write_bytes(b"placeholder")

    with patch("app.render_audit._pdf_text_and_image_count", return_value=(text, 0)):
        report = audit_docx_pdf_consistency(docx, pdf)

    assert report["anchor_match_ratio"] == 1.0
    assert not report["ok"]
    assert any("image count" in issue for issue in report["issues"])
