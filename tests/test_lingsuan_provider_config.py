from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class LingsuanProviderConfigTests(unittest.TestCase):
    def _providers(self):
        from app.settings import list_providers

        with patch("app.settings.CONFIG_DIR", ROOT / "config"):
            return list_providers()

    def test_supplier_families_use_independent_keys(self) -> None:
        providers = self._providers()
        expected = {
            "lingsuan_openai": "LINGSUAN_OPENAI_API_KEY",
            "lingsuan_google": "LINGSUAN_GOOGLE_API_KEY",
            "lingsuan_xai": "LINGSUAN_XAI_API_KEY",
            "lingsuan_anthropic": "LINGSUAN_ANTHROPIC_API_KEY",
        }

        self.assertNotIn("lingsuan", providers)
        self.assertNotIn("yunwu", providers)
        for name, env_name in expected.items():
            with self.subTest(provider=name):
                provider = providers[name]
                self.assertEqual("https://lingsuan.top/v1", provider.base_url)
                self.assertEqual("responses", provider.api_protocol)
                self.assertTrue(provider.responses_streaming)
                self.assertEqual("high", provider.thinking_mode)
                self.assertEqual(env_name, provider.api_key_env)
                self.assertEqual(env_name, provider.redacted()["api_key_env"])
                self.assertFalse(provider.allow_custom_model)

        self.assertEqual(len(set(expected.values())), len(expected))

    def test_openai_and_google_models_are_isolated_and_multimodal(self) -> None:
        from app.settings import provider_model_supports_vision

        providers = self._providers()
        openai = providers["lingsuan_openai"]
        google = providers["lingsuan_google"]

        self.assertEqual(
            ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"),
            openai.model_options,
        )
        self.assertEqual(("gemini-3.6-flash", "gemini-3.5-flash"), google.model_options)
        self.assertTrue(all(provider_model_supports_vision(openai, model) for model in openai.model_options))
        self.assertTrue(all(provider_model_supports_vision(google, model) for model in google.model_options))
        self.assertTrue(set(openai.model_options).isdisjoint(google.model_options))

    def test_legacy_lingsuan_provider_resolves_to_openai_only(self) -> None:
        from app.settings import get_provider

        with patch("app.settings.CONFIG_DIR", ROOT / "config"):
            provider = get_provider("lingsuan")

        self.assertEqual("lingsuan_openai", provider.name)

    def test_frontend_names_and_saves_each_supplier_key(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        for provider, env_name, label in (
            ("lingsuan_openai", "LINGSUAN_OPENAI_API_KEY", "灵算 · OpenAI"),
            ("lingsuan_google", "LINGSUAN_GOOGLE_API_KEY", "灵算 · Google Gemini"),
            ("lingsuan_xai", "LINGSUAN_XAI_API_KEY", "灵算 · xAI"),
            ("lingsuan_anthropic", "LINGSUAN_ANTHROPIC_API_KEY", "灵算 · Anthropic"),
        ):
            self.assertIn(f'{provider}: "{env_name}"', source)
            self.assertIn(f'{provider}: "{label}"', source)
        self.assertIn('thinking_mode: cfg.thinking_mode || "auto"', source)


if __name__ == "__main__":
    unittest.main()
