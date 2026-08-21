from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .pdf_render import render_pdf_pages
from .render_fonts import libreoffice_font_environment


def export_docx_to_pdf(docx: Path, pdf: Path) -> Path:
    system = platform.system()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    if system == "Darwin":
        timeout = int(os.environ.get("WORD_EXPORT_TIMEOUT_SECONDS", "25"))
        attempts = int(os.environ.get("WORD_EXPORT_ATTEMPTS", "1"))
        with tempfile.TemporaryDirectory(prefix=".word-export-", dir=pdf.parent) as raw_tmp:
            temporary_dir = Path(raw_tmp)
            unique_docx = temporary_dir / "source.docx"
            temporary_pdf = temporary_dir / "rendered.pdf"
            shutil.copy2(docx, unique_docx)
            pdf.unlink(missing_ok=True)
            script = f'''
tell application "Microsoft Word"
    activate
    set theDoc to missing value
    try
        open POSIX file "{unique_docx}"
        delay 1
        set theDoc to active document
        save as theDoc file name (POSIX file "{temporary_pdf}") file format format PDF
        close theDoc saving no
    on error errMsg number errNum
        try
            if theDoc is not missing value then close theDoc saving no
        end try
        error errMsg number errNum
    end try
end tell
'''
            last_error = None
            for _ in range(max(1, attempts)):
                try:
                    subprocess.run(["osascript"], input=script, text=True, check=True, timeout=timeout, capture_output=True)
                    if not temporary_pdf.is_file():
                        raise RuntimeError("Microsoft Word reported success without creating the PDF")
                    temporary_pdf.replace(pdf)
                    return pdf
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
                    last_error = exc
                    time.sleep(2)
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                export_docx_to_pdf_with_soffice(unique_docx, temporary_pdf, soffice)
                temporary_pdf.replace(pdf)
                return pdf
            if last_error:
                raise last_error
            raise RuntimeError("Microsoft Word PDF export failed")
    if system == "Windows":
        timeout = int(os.environ.get("WORD_EXPORT_TIMEOUT_SECONDS", "60"))
        code = """
import sys
import win32com.client

word = None
document = None
try:
    # Never attach to (and later quit) the user's existing Word session.
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    document = word.Documents.Open(sys.argv[1])
    document.SaveAs2(sys.argv[2], FileFormat=17)
finally:
    if document is not None:
        document.Close(False)
    if word is not None:
        word.Quit()
"""
        try:
            subprocess.run(
                [sys.executable, "-c", code, str(docx.resolve()), str(pdf.resolve())],
                check=True,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            return pdf
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                return export_docx_to_pdf_with_soffice(docx, pdf, soffice)
            raise RuntimeError(_subprocess_failure_message("Microsoft Word COM PDF export failed", exc)) from exc
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("No Microsoft Word automation or soffice found for PDF export")
    return export_docx_to_pdf_with_soffice(docx, pdf, soffice)


def _subprocess_failure_message(prefix: str, exc: subprocess.CalledProcessError | subprocess.TimeoutExpired) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{prefix}: timed out after {exc.timeout} seconds"
    stderr = (exc.stderr or "").strip()
    stdout = (exc.stdout or "").strip()
    detail = stderr or stdout or str(exc)
    return f"{prefix}: {detail}"


def export_docx_to_pdf_with_soffice(docx: Path, pdf: Path, soffice: str) -> Path:
    pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".libreoffice-render-", dir=pdf.parent) as raw_tmp:
        runtime_dir = Path(raw_tmp)
        profile_dir = runtime_dir / "profile"
        conversion_dir = runtime_dir / "output"
        render_source = runtime_dir / "source.docx"
        profile_dir.mkdir()
        conversion_dir.mkdir()
        shutil.copy2(docx, render_source)
        environment = libreoffice_font_environment(runtime_dir / "fonts.conf")
        environment["HOME"] = str(runtime_dir)
        environment.setdefault("TMPDIR", tempfile.gettempdir())
        subprocess.run(
            [
                soffice,
                "--headless",
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(conversion_dir),
                str(render_source),
            ],
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
            env=environment,
        )
        converted = conversion_dir / "source.pdf"
        if not converted.is_file():
            raise RuntimeError("LibreOffice reported success without creating the PDF")
        converted.replace(pdf)
    return pdf


def render_pdf_to_png(pdf: Path, output_dir: Path, prefix: str = "page") -> list[Path]:
    return render_pdf_pages(pdf, output_dir, prefix=prefix, dpi=150, image_format="png")
