from __future__ import annotations

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class LocalConfigTests(unittest.TestCase):
    def test_removed_provider_keys_are_not_exposed(self) -> None:
        from app.api_key_config import ALLOWED_API_KEY_NAMES

        self.assertNotIn("OPENAI_API_KEY", ALLOWED_API_KEY_NAMES)
        self.assertNotIn("ZHIPU_API_KEY", ALLOWED_API_KEY_NAMES)

    def test_yunwu_key_is_persisted_to_independent_json_file(self) -> None:
        from app.local_config import update_dotenv_values

        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            key_file = project_root / "config" / "api_keys.json"
            with (
                patch("app.api_key_config.API_KEY_FILE", key_file),
                patch("app.api_key_config.LOCAL_CONFIG_DIR", project_root / "config"),
                patch("app.api_key_config.DATA_ROOT", project_root),
                patch("app.api_key_config.ensure_project_dirs"),
                patch.dict(os.environ, {}, clear=False),
            ):
                result = update_dotenv_values({"YUNWU_API_KEY": "test-yunwu-key"})

            self.assertTrue(result["updated"])
            saved = json.loads(key_file.read_text(encoding="utf-8"))
            self.assertEqual("test-yunwu-key", saved["keys"]["YUNWU_API_KEY"])
            self.assertNotIn("test-yunwu-key", json.dumps(result))

    def test_lingsuan_key_is_allowed_and_persisted(self) -> None:
        from app.api_key_config import ALLOWED_API_KEY_NAMES
        from app.local_config import update_dotenv_values

        self.assertIn("LINGSUAN_API_KEY", ALLOWED_API_KEY_NAMES)
        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            key_file = project_root / "config" / "api_keys.json"
            with (
                patch("app.api_key_config.API_KEY_FILE", key_file),
                patch("app.api_key_config.LOCAL_CONFIG_DIR", project_root / "config"),
                patch("app.api_key_config.DATA_ROOT", project_root),
                patch("app.api_key_config.ensure_project_dirs"),
                patch.dict(os.environ, {}, clear=False),
            ):
                result = update_dotenv_values({"LINGSUAN_API_KEY": "test-lingsuan-key"})

            self.assertTrue(result["updated"])
            saved = json.loads(key_file.read_text(encoding="utf-8"))
            self.assertEqual("test-lingsuan-key", saved["keys"]["LINGSUAN_API_KEY"])
            self.assertNotIn("test-lingsuan-key", json.dumps(result))

    def test_legacy_env_is_migrated_only_when_key_file_is_missing(self) -> None:
        from app.api_key_config import ensure_api_key_file

        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            config_dir = project_root / "config"
            config_dir.mkdir()
            (project_root / ".env").write_text("DEEPSEEK_API_KEY=legacy-test-key\n", encoding="utf-8")
            key_file = config_dir / "api_keys.json"
            with (
                patch("app.api_key_config.API_KEY_FILE", key_file),
                patch("app.api_key_config.LOCAL_CONFIG_DIR", config_dir),
                patch("app.api_key_config.DATA_ROOT", project_root),
                patch("app.api_key_config.ensure_project_dirs"),
                patch.dict(os.environ, {}, clear=True),
            ):
                ensure_api_key_file()

            saved = json.loads(key_file.read_text(encoding="utf-8"))
            self.assertEqual("legacy-test-key", saved["keys"]["DEEPSEEK_API_KEY"])


if __name__ == "__main__":
    unittest.main()
