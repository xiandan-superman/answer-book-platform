from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "source-release.yml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SOURCE_UPDATE_DOC = (ROOT / "docs" / "SOURCE_DISTRIBUTION_AND_UPDATE.md").read_text(encoding="utf-8")


def test_source_release_is_tag_driven_and_checksum_verified() -> None:
    assert '"v[0-9]+.[0-9]+.[0-9]+"' in WORKFLOW
    assert "git archive" in WORKFLOW
    assert '--asset "source=$asset"' in WORKFLOW
    assert "scripts/build_update_manifest.py" in WORKFLOW
    assert "update-stable.json" in WORKFLOW
    assert "RELEASE_REPO_TOKEN" in WORKFLOW


def test_end_user_installation_and_legacy_migration_are_documented() -> None:
    for text in (README, SOURCE_UPDATE_DOC):
        assert "answer-book-platform-<版本号>-source.zip" in text
        assert "Source code (zip)" in text
        assert "start_platform.command" in text
        assert "start_platform_windows.bat" in text
        assert "0.9.12" in text
    assert "不要直接双击 `web/index.html`" in README
    assert "config/api_keys.json" in README
