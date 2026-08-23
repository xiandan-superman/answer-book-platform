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


def test_desktop_release_requires_and_embeds_support_receiver_secrets() -> None:
    assert WORKFLOW.count("SUPPORT_RECEIVER_URL") >= 4
    assert WORKFLOW.count("SUPPORT_RECEIVER_TOKEN") >= 4
    assert WORKFLOW.count("Require support receiver configuration") == 2


def test_desktop_release_requires_and_embeds_hybrid_cloud_secrets() -> None:
    assert WORKFLOW.count("HYBRID_CLOUD_URL") >= 4
    assert WORKFLOW.count("HYBRID_CLOUD_TOKEN") >= 4
    assert WORKFLOW.count("Require hybrid cloud configuration") == 2
    assert WORKFLOW.count('ANSWER_BOOK_HYBRID_ENABLED: "0"') == 2


def test_desktop_bundle_generates_hybrid_configuration_at_build_time() -> None:
    spec = (ROOT / "build" / "answer_book_platform.spec").read_text(encoding="utf-8")
    assert 'os.environ.get("ANSWER_BOOK_HYBRID_URL"' in spec
    assert 'os.environ.get("ANSWER_BOOK_HYBRID_TOKEN"' in spec
    assert 'os.environ.get("ANSWER_BOOK_HYBRID_ENABLED"' in spec
    assert '"enabled": bool(hybrid_enabled and hybrid_url and hybrid_token)' in spec
    assert '(str(hybrid_config_build), "config")' in spec


def test_desktop_bundle_collects_and_verifies_latex2mathml_runtime_data() -> None:
    spec = (ROOT / "build" / "answer_book_platform.spec").read_text(encoding="utf-8")
    windows_builder = (ROOT / "scripts" / "build_windows_app.py").read_text(encoding="utf-8")

    assert 'collect_data_files("latex2mathml")' in spec
    assert '"latex2mathml" / "unimathsymbols.txt"' in windows_builder
