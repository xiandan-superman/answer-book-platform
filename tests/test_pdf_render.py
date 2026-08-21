from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.pdf_render import pdf_page_count, render_pdf_pages


class PdfRenderTests(unittest.TestCase):
    def test_pdfium_renders_selected_pages_without_poppler(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pdf = root / "fixture.pdf"
            output = root / "pages"
            pages = [Image.new("RGB", (80, 60), color=color) for color in ("white", "gray", "black")]
            try:
                pages[0].save(pdf, format="PDF", save_all=True, append_images=pages[1:])
            finally:
                for page in pages:
                    page.close()

            with patch("app.pdf_render.shutil.which", return_value=None):
                rendered = render_pdf_pages(pdf, output, first_page=2, last_page=3, dpi=72)

            self.assertEqual(3, pdf_page_count(pdf))
            self.assertEqual(["page-2.png", "page-3.png"], [path.name for path in rendered])
            self.assertTrue(all(path.stat().st_size > 0 for path in rendered))

    def test_poppler_remains_a_runtime_fallback(self) -> None:
        fallback = [Path("page-1.png")]
        with tempfile.TemporaryDirectory() as raw:
            with patch("app.pdf_render._render_with_pdfium", side_effect=RuntimeError("pdfium failed")):
                with patch("app.pdf_render._render_with_pdftoppm", return_value=fallback) as render_fallback:
                    result = render_pdf_pages(Path(raw) / "fixture.pdf", Path(raw) / "pages")

        self.assertEqual(fallback, result)
        render_fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
