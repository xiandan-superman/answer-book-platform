from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class VersionTests(unittest.TestCase):
    def test_version_includes_git_identifiable_revision(self) -> None:
        from app.version import get_version

        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            (project_root / "VERSION").write_text("7.1\n", encoding="utf-8")
            (project_root / "RELEASE_MANIFEST.json").write_text(
                json.dumps({"source_revision": "abc1234"}),
                encoding="utf-8",
            )
            with patch("app.version.PROJECT_ROOT", project_root):
                self.assertEqual("7.1 abc1234", get_version())

    def test_version_marks_dirty_source_checkout(self) -> None:
        from app.version import get_version

        with (
            patch("app.version.get_base_version", return_value="7.1"),
            patch("app.version.get_source_revision", return_value="abc1234"),
            patch("app.version.is_source_dirty", return_value=True),
        ):
            self.assertEqual("7.1 abc1234+dirty", get_version())

    def test_source_revision_falls_back_to_release_manifest(self) -> None:
        from app.version import get_source_revision

        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            (project_root / "RELEASE_MANIFEST.json").write_text(
                json.dumps({"source_revision": "abc1234"}),
                encoding="utf-8",
            )
            with patch("app.version.PROJECT_ROOT", project_root):
                self.assertEqual("abc1234", get_source_revision())

    def test_release_manifest_status_requires_matching_version(self) -> None:
        from app.version import release_manifest_status

        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            (project_root / "APP_VERSION").write_text("8.17\n", encoding="utf-8")
            (project_root / "VERSION").write_text("8.17\n", encoding="utf-8")
            (project_root / "RELEASE_MANIFEST.json").write_text(
                json.dumps({"version": "8.17"}),
                encoding="utf-8",
            )
            with patch("app.version.PROJECT_ROOT", project_root):
                status = release_manifest_status()

        self.assertTrue(status["version_matches"])
        self.assertEqual([], status["issues"])

    def test_release_manifest_status_rejects_user_visible_version_divergence(self) -> None:
        from app.version import release_manifest_status

        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            (project_root / "APP_VERSION").write_text("0.9.3\n", encoding="utf-8")
            (project_root / "VERSION").write_text("8.27-vnext.r3.1\n", encoding="utf-8")
            (project_root / "RELEASE_MANIFEST.json").write_text(
                json.dumps({"version": "0.9.3"}),
                encoding="utf-8",
            )
            with patch("app.version.PROJECT_ROOT", project_root):
                status = release_manifest_status()

        self.assertFalse(status["version_matches"])
        self.assertIn("APP_VERSION=0.9.3", " ".join(status["issues"]))


if __name__ == "__main__":
    unittest.main()
