from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.extract_release_notes import extract_version_section, source_download_notice

ROOT = Path(__file__).resolve().parents[1]


def test_current_version_has_specific_changelog_entry() -> None:
    version = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
    section = extract_version_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)

    assert section.startswith(f"## [{version}]")
    assert "### " in section
    assert "- " in section
    assert len(section) >= 120


def test_missing_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="未找到版本"):
        extract_version_section("# 版本更新记录\n", "9.9.9")


def test_release_note_script_writes_only_requested_section(tmp_path: Path) -> None:
    version = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
    output = tmp_path / "release-notes.md"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "extract_release_notes.py"),
            "--version",
            version,
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    notes = output.read_text(encoding="utf-8")
    assert notes.startswith(f"## [{version}]")
    assert "## [0.9.20]" not in notes


def test_source_download_notice_points_to_the_real_release_asset() -> None:
    notice = source_download_notice("0.9.30", "example/answer-book-platform-releases")

    assert "answer-book-platform-0.9.30-source.zip" in notice
    assert "/releases/download/v0.9.30/" in notice
    assert "不要下载" in notice
    assert "Source code (zip)" in notice
