from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")


def test_desktop_release_only_publishes_for_version_tags() -> None:
    assert "workflow_dispatch:" in WORKFLOW
    assert '"v[0-9]+.[0-9]+.[0-9]+"' in WORKFLOW
    assert "if: github.ref_type == 'tag'" in WORKFLOW


def test_desktop_release_builds_both_supported_platforms() -> None:
    assert "runs-on: macos-15" in WORKFLOW
    assert 'test "$(uname -m)" = "arm64"' in WORKFLOW
    assert "runs-on: windows-latest" in WORKFLOW
    assert "macos-arm64.dmg" in WORKFLOW
    assert "windows-x86_64.zip" in WORKFLOW


def test_desktop_release_publishes_checksum_manifest_to_public_repo() -> None:
    assert "RELEASE_REPO_TOKEN" in WORKFLOW
    assert "xiandan-superman/answer-book-platform-releases" in WORKFLOW
    assert "scripts/build_update_manifest.py" in WORKFLOW
    assert "update-manifest.json" in WORKFLOW
    assert "update-stable.json" in WORKFLOW
