from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class LingsuanProviderConfigTests(unittest.TestCase):
    def test_lingsuan_models_and_responses_endpoint_are_declared(self) -> None:
        from app.settings import list_providers

        with patch("app.settings.CONFIG_DIR", ROOT / "config"):
            provider = list_providers()["lingsuan"]

        expected_models = (
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.5",
            "grok-4.5",
            "claude-opus-5",
        )
        self.assertEqual("https://lingsuan.top/v1", provider.base_url)
        self.assertEqual("responses", provider.api_protocol)
        self.assertTrue(provider.responses_streaming)
        self.assertEqual("gpt-5.6-sol", provider.default_model)
        self.assertEqual(expected_models, provider.model_options)
        self.assertEqual("gpt-5.6-sol", provider.vision_model)
        self.assertEqual(
            ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"),
            provider.vision_model_options,
        )
        self.assertTrue(provider.supports_vision)
        self.assertFalse(provider.supports_image_generation)
        self.assertEqual("LINGSUAN_API_KEY", provider.api_key_env)
        self.assertEqual("LINGSUAN_API_KEY", provider.redacted()["api_key_env"])

    def test_frontend_can_name_and_save_lingsuan_key(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('lingsuan: "LINGSUAN_API_KEY"', source)
        self.assertIn('lingsuan: "灵算 API"', source)


if __name__ == "__main__":
    unittest.main()
