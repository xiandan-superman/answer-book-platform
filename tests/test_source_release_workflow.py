from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "source-release.yml").read_text(encoding="utf-8")


def test_source_release_is_tag_driven_and_checksum_verified() -> None:
    assert '"v[0-9]+.[0-9]+.[0-9]+"' in WORKFLOW
    assert "git archive" in WORKFLOW
    assert '--asset "source=$asset"' in WORKFLOW
    assert "scripts/build_update_manifest.py" in WORKFLOW
    assert "update-stable.json" in WORKFLOW
    assert "RELEASE_REPO_TOKEN" in WORKFLOW
