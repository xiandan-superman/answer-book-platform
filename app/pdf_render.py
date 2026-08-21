from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def pdf_page_count(pdf: Path) -> int:
    """Return the PDF page count using PDFium, with pdfinfo as a legacy fallback."""
    try:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(str(pdf)) as document:
            return len(document)
    except Exception:
        pdfinfo = shutil.which("pdfinfo")
        if not pdfinfo:
            return 0
        try:
            output = subprocess.run(
                [pdfinfo, str(pdf)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return 0
        for line in output.splitlines():
            if line.lower().startswith("pages:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return 0
        return 0


def _render_with_pdfium(
    pdf: Path,
    output_dir: Path,
    *,
    prefix: str,
    dpi: int,
    image_format: str,
    first_page: int,
    last_page: int | None,
) -> list[Path]:
    import pypdfium2 as pdfium

    suffix = ".jpg" if image_format == "jpeg" else ".png"
    save_format = "JPEG" if image_format == "jpeg" else "PNG"
    paths: list[Path] = []
    with pdfium.PdfDocument(str(pdf)) as document:
        page_count = len(document)
        start = max(0, first_page - 1)
        stop = min(page_count, last_page if last_page is not None else page_count)
        for page_index in range(start, stop):
            page = document[page_index]
            bitmap = None
            image = None
            try:
                bitmap = page.render(scale=dpi / 72.0)
                image = bitmap.to_pil()
                if image_format == "jpeg" and image.mode != "RGB":
                    converted = image.convert("RGB")
                    image.close()
                    image = converted
                output = output_dir / f"{prefix}-{page_index + 1}.{suffix.lstrip('.')}"
                save_options = {"quality": 90} if image_format == "jpeg" else {}
                image.save(output, format=save_format, **save_options)
                paths.append(output)
            finally:
                if image is not None:
                    image.close()
                if bitmap is not None:
                    bitmap.close()
                page.close()
    if not paths:
        raise RuntimeError("PDFium did not render any PDF pages")
    return paths


def _render_with_pdftoppm(
    pdf: Path,
    output_dir: Path,
    *,
    prefix: str,
    dpi: int,
    image_format: str,
    first_page: int,
    last_page: int | None,
) -> list[Path]:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("PDFium rendering failed and pdftoppm is unavailable")
    command = [pdftoppm, "-f", str(first_page)]
    if last_page is not None:
        command.extend(["-l", str(last_page)])
    command.extend([f"-{image_format}", "-r", str(dpi), str(pdf), str(output_dir / prefix)])
    subprocess.run(command, check=True, capture_output=True, timeout=120)
    suffix = "jpg" if image_format == "jpeg" else "png"
    def page_number(path: Path) -> int:
        try:
            return int(path.stem.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            return 0

    paths = sorted(output_dir.glob(f"{prefix}-*.{suffix}"), key=page_number)
    if not paths:
        raise RuntimeError("pdftoppm did not render any PDF pages")
    return paths


def render_pdf_pages(
    pdf: Path,
    output_dir: Path,
    *,
    prefix: str = "page",
    dpi: int = 150,
    image_format: str = "png",
    first_page: int = 1,
    last_page: int | None = None,
) -> list[Path]:
    """Render PDF pages with bundled PDFium and retain Poppler as a fallback."""
    image_format = image_format.lower()
    if image_format not in {"png", "jpeg"}:
        raise ValueError(f"Unsupported PDF render format: {image_format}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "jpg"):
        for old in output_dir.glob(f"{prefix}-*.{suffix}"):
            old.unlink()
    try:
        return _render_with_pdfium(
            pdf,
            output_dir,
            prefix=prefix,
            dpi=dpi,
            image_format=image_format,
            first_page=first_page,
            last_page=last_page,
        )
    except Exception as pdfium_error:
        try:
            return _render_with_pdftoppm(
                pdf,
                output_dir,
                prefix=prefix,
                dpi=dpi,
                image_format=image_format,
                first_page=first_page,
                last_page=last_page,
            )
        except Exception as poppler_error:
            raise RuntimeError(
                f"Unable to render PDF with PDFium ({pdfium_error}) or Poppler ({poppler_error})"
            ) from poppler_error
