from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat


def audit_rendered_pages(rendered_dir: Path, min_pages: int = 1) -> list[str]:
    issues: list[str] = []
    pages = sorted(rendered_dir.glob("page-*.png"))
    if len(pages) < min_pages:
        issues.append(f"rendered PNG page count {len(pages)} below expected minimum {min_pages}")
    for page in pages:
        try:
            with Image.open(page) as img:
                width, height = img.size
                if width < 500 or height < 700:
                    issues.append(f"{page.name} too small: {width}x{height}")
                gray = img.convert("L")
                stat = ImageStat.Stat(gray)
                extrema = gray.getextrema()
                pixels = list(gray.getdata())
                very_dark_ratio = sum(1 for value in pixels if value < 120) / max(len(pixels), 1)
                if extrema[1] - extrema[0] < 8:
                    issues.append(f"{page.name} appears blank or nearly uniform")
                if stat.mean and stat.mean[0] > 254 and very_dark_ratio < 0.001:
                    issues.append(f"{page.name} is almost entirely white")
        except Exception as exc:
            issues.append(f"{page.name} could not be inspected: {exc}")
    return issues
