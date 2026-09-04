import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RELEASE = (ROOT / ".github" / "workflows" / "source-release.yml").read_text(encoding="utf-8")
DESKTOP_RELEASE = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
QUALITY = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")


def test_source_release_runs_only_after_the_complete_main_quality_workflow() -> None:
    assert "workflow_run:" in SOURCE_RELEASE
    assert 'workflows: ["Platform quality gates"]' in SOURCE_RELEASE
    assert "branches: [main]" in SOURCE_RELEASE
    assert "github.event.workflow_run.conclusion == 'success'" in SOURCE_RELEASE
    assert "github.event.workflow_run.event == 'push'" in SOURCE_RELEASE
    assert "github.event.workflow_run.head_branch == 'main'" in SOURCE_RELEASE
    assert "workflow_dispatch:" not in SOURCE_RELEASE
    assert "tags:" not in SOURCE_RELEASE
    assert "scripts/verify_release_package.py" in SOURCE_RELEASE


def test_source_release_creates_the_tag_only_after_packaging_and_smoke() -> None:
    assert SOURCE_RELEASE.index("scripts/package_release.py") < SOURCE_RELEASE.index("git tag")
    assert SOURCE_RELEASE.index("Verify packaged source starts") < SOURCE_RELEASE.index("git tag")
    assert 'git push origin "refs/tags/${RELEASE_TAG}"' in SOURCE_RELEASE


def test_source_release_is_idempotent_and_verifies_the_public_result() -> None:
    assert "Release and stable feed already exist" in SOURCE_RELEASE
    assert "mode=recover-feed" in SOURCE_RELEASE
    assert "refusing to downgrade" in SOURCE_RELEASE
    assert "--clobber" in SOURCE_RELEASE
    assert "bounded retries" in SOURCE_RELEASE
    assert "Download and verify the public release and stable feed" in SOURCE_RELEASE
    assert "published != stable" in SOURCE_RELEASE
    assert "published source asset checksum" in SOURCE_RELEASE


def test_source_release_smoke_starts_the_packaged_zip_with_isolated_data() -> None:
    assert "unzip -q" in SOURCE_RELEASE
    assert "ANSWER_BOOK_DATA_DIR" in SOURCE_RELEASE
    assert '$smoke_root/scripts/start_platform.py' in SOURCE_RELEASE
    assert "/api/version" in SOURCE_RELEASE


def test_source_release_updates_only_the_safe_v2_feed() -> None:
    assert "update-stable-v2.json" in SOURCE_RELEASE
    assert "contents/update-stable.json" not in SOURCE_RELEASE


def test_release_workflows_require_version_specific_changelog_notes() -> None:
    command = 'python scripts/extract_release_notes.py --version "$RELEASE_VERSION"'
    assert command in SOURCE_RELEASE
    assert command in DESKTOP_RELEASE
    assert "--source-download-repository \"$RELEASE_REPOSITORY\"" in SOURCE_RELEASE
    assert "--notes-file dist/github-release-notes.md" in SOURCE_RELEASE
    assert "--notes-file dist/release-notes.md" in DESKTOP_RELEASE


def test_quality_matrix_covers_supported_python_profiles_and_browser_smoke() -> None:
    assert 'python-version: ["3.11"]' in QUALITY
    assert "constraints-py39.txt" not in QUALITY
    assert "constraints-py311.txt" in QUALITY
    assert "playwright install --with-deps chromium" in QUALITY
    assert "tests/e2e/test_platform_smoke.py" in QUALITY
    assert "source-dependency-locks:" in QUALITY
    assert "constraints-source-macos-py39.txt" not in QUALITY
    assert "constraints-source-macos-py311.txt" in QUALITY
    assert "constraints-source-windows-py39.txt" not in QUALITY
    assert "constraints-source-windows-py311.txt" in QUALITY
    assert "python -m pip check" in QUALITY


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
