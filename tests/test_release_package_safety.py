from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import package_release


def test_release_packager_excludes_the_entire_artifacts_tree(tmp_path: Path) -> None:
    artifact_key = tmp_path / ".artifacts" / "isolated-run" / "config" / "api_keys.json"
    artifact_key.parent.mkdir(parents=True)
    artifact_key.write_text('{"keys": {"secret": "must-not-ship"}}', encoding="utf-8")
    source = tmp_path / "app" / "server.py"
    source.parent.mkdir()
    source.write_text("# safe source", encoding="utf-8")

    with patch.object(package_release, "ROOT", tmp_path):
        assert package_release.should_include(source) is True
        assert package_release.should_include(artifact_key) is False

    assert ".artifacts" in package_release.EXCLUDED_DIRS


def test_release_packager_uses_only_git_index_entries(tmp_path: Path) -> None:
    source = tmp_path / "app" / "server.py"
    source.parent.mkdir()
    source.write_text("# tracked source", encoding="utf-8")
    untracked_key = tmp_path / "build" / "ux-audit-data" / "config" / "api_keys.json"
    untracked_key.parent.mkdir(parents=True)
    untracked_key.write_text('{"keys": {"secret": "must-not-ship"}}', encoding="utf-8")

    completed = SimpleNamespace(stdout=b"app/server.py\0", returncode=0)
    with patch.object(package_release.subprocess, "run", return_value=completed):
        selected = package_release.release_source_files(tmp_path)

    assert selected == [source]
    assert untracked_key not in selected


def test_release_packager_keeps_only_the_allowlisted_mathjax_output_assets(tmp_path: Path) -> None:
    font = tmp_path / "web/vendor/mathjax/output/chtml/fonts/woff-v2/MathJax_Zero.woff"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"official-font-fixture")
    runtime_output = tmp_path / "output/generated.docx"
    runtime_output.parent.mkdir()
    runtime_output.write_bytes(b"private-output")

    assert package_release.should_include(font, tmp_path) is True
    assert package_release.should_include(runtime_output, tmp_path) is False
