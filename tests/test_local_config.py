from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class LocalConfigTests(unittest.TestCase):
    def test_yunwu_key_is_persisted_to_local_env_file(self) -> None:
        from app.local_config import update_dotenv_values

        with tempfile.TemporaryDirectory() as raw_tmp:
            project_root = Path(raw_tmp)
            with patch("app.local_config.PROJECT_ROOT", project_root), patch.dict(os.environ, {}, clear=False):
                result = update_dotenv_values({"YUNWU_API_KEY": "test-yunwu-key"})

            self.assertTrue(result["updated"])
            self.assertIn("YUNWU_API_KEY=test-yunwu-key", (project_root / ".env").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
