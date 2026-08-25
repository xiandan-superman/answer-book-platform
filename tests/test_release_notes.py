from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.extract_release_notes import extract_version_section

ROOT = Path(__file__).resolve().parents[1]


def test_current_version_has_specific_changelog_entry() -> None:
    version = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
    section = extract_version_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)

    assert section.startswith(f"## [{version}]")
    assert "OpenRouter" in section
    assert "stealth/ox-alpha" in section
    assert "z-ai/glm-5.2:free" in section
    assert "minimax/minimax-m3:free" in section


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
