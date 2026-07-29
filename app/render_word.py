from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
import os
from pathlib import Path


def export_docx_to_pdf(docx: Path, pdf: Path) -> Path:
    system = platform.system()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    if system == "Darwin":
        timeout = int(os.environ.get("WORD_EXPORT_TIMEOUT_SECONDS", "25"))
        attempts = int(os.environ.get("WORD_EXPORT_ATTEMPTS", "1"))
        script = f'''
tell application "Microsoft Word"
    activate
    set theDoc to missing value
    try
        open POSIX file "{docx}"
        delay 1
        set theDoc to active document
        save as theDoc file name (POSIX file "{pdf}") file format format PDF
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
                return pdf
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                last_error = exc
                time.sleep(2)
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            return export_docx_to_pdf_with_soffice(docx, pdf, soffice)
        if last_error:
            raise last_error
        raise RuntimeError("Microsoft Word PDF export failed")
    if system == "Windows":
        timeout = int(os.environ.get("WORD_EXPORT_TIMEOUT_SECONDS", "60"))
        code = f"""
import win32com.client
word = win32com.client.Dispatch('Word.Application')
word.Visible = False
doc = word.Documents.Open(r'{docx}')
doc.SaveAs2(r'{pdf}', FileFormat=17)
doc.Close(False)
word.Quit()
"""
        try:
            subprocess.run([sys.executable, "-c", code], check=True, timeout=timeout, capture_output=True, text=True)
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
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf.parent), str(docx)],
        check=True,
        timeout=120,
        capture_output=True,
        text=True,
    )
    converted = pdf.parent / (docx.stem + ".pdf")
    if converted != pdf and converted.exists():
        converted.replace(pdf)
    return pdf


def render_pdf_to_png(pdf: Path, output_dir: Path, prefix: str = "page") -> list[Path]:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob(f"{prefix}-*.png"):
        old.unlink()
    out_prefix = output_dir / prefix
    subprocess.run([pdftoppm, "-png", "-r", "150", str(pdf), str(out_prefix)], check=True)
    return sorted(output_dir.glob(f"{prefix}-*.png"))
