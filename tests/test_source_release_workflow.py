import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RELEASE = (ROOT / ".github" / "workflows" / "source-release.yml").read_text(encoding="utf-8")
DESKTOP_RELEASE = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
QUALITY = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")


def test_source_release_requires_full_quality_gate_before_packaging() -> None:
    assert "python scripts/run_quality_gates.py --full" in SOURCE_RELEASE
    assert SOURCE_RELEASE.index("run_quality_gates.py --full") < SOURCE_RELEASE.index("git archive")


def test_source_release_smoke_starts_the_packaged_zip_with_isolated_data() -> None:
    assert "unzip -q" in SOURCE_RELEASE
    assert "ANSWER_BOOK_DATA_DIR" in SOURCE_RELEASE
    assert "/api/version" in SOURCE_RELEASE


def test_source_release_updates_only_the_safe_v2_feed() -> None:
    assert "update-stable-v2.json" in SOURCE_RELEASE
    assert "contents/update-stable.json" not in SOURCE_RELEASE


def test_release_workflows_require_version_specific_changelog_notes() -> None:
    command = 'python scripts/extract_release_notes.py --version "$RELEASE_VERSION"'
    assert command in SOURCE_RELEASE
    assert command in DESKTOP_RELEASE
    assert "--notes-file dist/release-notes.md" in SOURCE_RELEASE
    assert "--notes-file dist/release-notes.md" in DESKTOP_RELEASE


def test_quality_matrix_covers_supported_python_profiles_and_browser_smoke() -> None:
    assert 'python-version: ["3.9", "3.11"]' in QUALITY
    assert "constraints-py39.txt" in QUALITY
    assert "constraints-py311.txt" in QUALITY
    assert "playwright install --with-deps chromium" in QUALITY
    assert "tests/e2e/test_platform_smoke.py" in QUALITY


def test_manifest_builder_runs_outside_repository_working_directory(tmp_path: Path) -> None:
    asset = tmp_path / "source.zip"
    asset.write_bytes(b"source")
    output = tmp_path / "manifest.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_update_manifest.py"),
            "--version",
            "9.9.9",
            "--asset",
            f"source={asset}",
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.is_file()
