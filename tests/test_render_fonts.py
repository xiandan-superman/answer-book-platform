from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.render_fonts import libreoffice_font_environment, project_font_diagnostics
from app.render_word import export_docx_to_pdf_with_soffice


def _write_font_fixture(root: Path) -> Path:
    font_dir = root / "unicode"
    font_dir.mkdir(parents=True)
    (font_dir / "SimSun.ttc").write_bytes(b"fixture")
    return font_dir


def test_libreoffice_environment_injects_project_fonts_without_mutating_parent(tmp_path) -> None:
    font_root = tmp_path / "fonts"
    font_dir = _write_font_fixture(font_root)
    config = tmp_path / "runtime" / "fonts.conf"
    parent = {"PATH": "/usr/bin", "FONTCONFIG_FILE": "/custom/fonts.conf", "SAL_FONTPATH": "/host/fonts"}

    environment = libreoffice_font_environment(config, base_environment=parent, font_root=font_root)

    assert parent["FONTCONFIG_FILE"] == "/custom/fonts.conf"
    assert environment["FONTCONFIG_FILE"] == str(config)
    assert str(font_dir) in environment["SAL_FONTPATH"]
    assert "/host/fonts" in environment["SAL_FONTPATH"]
    xml = config.read_text(encoding="utf-8")
    assert "/custom/fonts.conf" in xml
    assert "DengXian" in xml
    assert "FZZhongDengXian-Z07S" in xml


def test_soffice_export_uses_private_profile_and_child_font_environment(tmp_path) -> None:
    source = tmp_path / "source.docx"
    target = tmp_path / "renamed.pdf"
    source.write_bytes(b"docx")

    def fake_run(command, **kwargs):
        assert any(str(value).startswith("-env:UserInstallation=file:") for value in command)
        assert kwargs["env"]["HOME"].startswith(str(tmp_path))
        assert kwargs["env"].get("SAL_FONTPATH")
        Path(command[command.index("--outdir") + 1], "source.pdf").write_bytes(b"pdf")

    with patch("app.render_word.subprocess.run", side_effect=fake_run):
        result = export_docx_to_pdf_with_soffice(source, target, "/opt/libreoffice/soffice")

    assert result == target
    assert target.read_bytes() == b"pdf"


def test_project_font_diagnostics_reports_bundle() -> None:
    report = project_font_diagnostics()

    assert report["enabled"] is True
    assert report["font_file_count"] >= 1
    assert report["aliases"]["宋体"] == "SimSun"
