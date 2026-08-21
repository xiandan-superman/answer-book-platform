from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .paths import PROJECT_ROOT

PROJECT_FONT_ROOT = PROJECT_ROOT / "assets" / "fonts" / "dolbydu-font"
PROJECT_FONT_ALIASES = {
    "DengXian": "FZZhongDengXian-Z07S",
    "等线": "FZZhongDengXian-Z07S",
    "Microsoft YaHei": "Microsoft Yahei",
    "微软雅黑": "Microsoft Yahei",
    "SimSun": "SimSun",
    "宋体": "SimSun",
    "SimHei": "SimHei",
    "黑体": "SimHei",
    "FangSong": "FangSong",
    "仿宋": "FangSong",
    "KaiTi": "Kaiti",
    "楷体": "Kaiti",
}


def project_font_directories(font_root: Path = PROJECT_FONT_ROOT) -> list[Path]:
    """Return only project font directories that contain usable font files."""

    if not font_root.is_dir():
        return []
    suffixes = {".otf", ".ttc", ".ttf"}
    return [
        directory
        for directory in sorted({path.parent for path in font_root.rglob("*") if path.suffix.lower() in suffixes})
        if directory.is_dir()
    ]


def project_font_diagnostics(font_root: Path = PROJECT_FONT_ROOT) -> dict[str, Any]:
    directories = project_font_directories(font_root)
    font_files = [
        path
        for directory in directories
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".otf", ".ttc", ".ttf"}
    ]
    return {
        "enabled": bool(directories),
        "font_root": str(font_root),
        "font_directory_count": len(directories),
        "font_file_count": len(font_files),
        "aliases": dict(PROJECT_FONT_ALIASES),
    }


def write_project_fontconfig(
    output_path: Path,
    *,
    font_root: Path = PROJECT_FONT_ROOT,
    inherited_config: str = "",
) -> Path | None:
    """Write a private Fontconfig file for one LibreOffice conversion.

    Project fonts remain local to the child process.  We include any explicit
    caller configuration first, then common system configurations, so adding
    the bundled CJK fonts does not hide fonts already installed on the host.
    """

    directories = project_font_directories(font_root)
    if not directories:
        return None
    includes: list[str] = []
    for candidate in (
        inherited_config,
        "/etc/fonts/fonts.conf",
        "/opt/homebrew/etc/fonts/fonts.conf",
        "/usr/local/etc/fonts/fonts.conf",
    ):
        candidate = str(candidate or "").strip()
        if candidate and candidate != str(output_path) and candidate not in includes:
            includes.append(candidate)
    lines = ["<?xml version=\"1.0\"?>", "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">", "<fontconfig>"]
    lines.extend(f"  <include ignore_missing=\"yes\">{escape(path)}</include>" for path in includes)
    lines.extend(f"  <dir>{escape(str(directory))}</dir>" for directory in directories)
    for requested, preferred in PROJECT_FONT_ALIASES.items():
        lines.extend(
            (
                "  <alias>",
                f"    <family>{escape(requested)}</family>",
                f"    <prefer><family>{escape(preferred)}</family></prefer>",
                "  </alias>",
            )
        )
    lines.append("</fontconfig>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def libreoffice_font_environment(
    config_path: Path,
    *,
    base_environment: dict[str, str] | None = None,
    font_root: Path = PROJECT_FONT_ROOT,
) -> dict[str, str]:
    """Build a child-only environment with bundled fonts and stable temp paths."""

    environment = dict(os.environ if base_environment is None else base_environment)
    inherited_config = environment.get("FONTCONFIG_FILE", "")
    written = write_project_fontconfig(
        config_path,
        font_root=font_root,
        inherited_config=inherited_config,
    )
    directories = project_font_directories(font_root)
    if written is not None:
        environment["FONTCONFIG_FILE"] = str(written)
    if directories:
        existing = environment.get("SAL_FONTPATH", "").strip()
        font_path = os.pathsep.join(str(directory) for directory in directories)
        environment["SAL_FONTPATH"] = os.pathsep.join(part for part in (font_path, existing) if part)
    return environment
