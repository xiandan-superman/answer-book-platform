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
            "lingsuan_image": "LINGSUAN_IMAGE_API_KEY",
            "lingsuan_google": "LINGSUAN_GOOGLE_API_KEY",
            "lingsuan_xai": "LINGSUAN_XAI_API_KEY",
            "lingsuan_anthropic": "LINGSUAN_ANTHROPIC_API_KEY",
        }
        thinking_defaults = {
            "lingsuan_openai": "auto",
            "lingsuan_image": "auto",
            "lingsuan_google": "auto",
            "lingsuan_xai": "auto",
            "lingsuan_anthropic": "auto",
        }
        expected_protocols = {
            "lingsuan_openai": "responses",
            "lingsuan_image": "responses",
            "lingsuan_google": "chat_completions",
            "lingsuan_xai": "responses",
            "lingsuan_anthropic": "anthropic_messages",
        }

        self.assertNotIn("lingsuan", providers)
        self.assertNotIn("yunwu", providers)
        for name, env_name in expected.items():
            with self.subTest(provider=name):
                provider = providers[name]
                self.assertEqual("https://lingsuan.org/v1", provider.base_url)
                self.assertTrue(provider.user_agent.startswith("Mozilla/5.0 "))
                self.assertEqual(expected_protocols[name], provider.api_protocol)
                if expected_protocols[name] == "responses":
                    self.assertTrue(provider.responses_streaming)
                    self.assertFalse(provider.responses_fallback_to_chat)
                self.assertEqual(thinking_defaults[name], provider.thinking_mode)
                self.assertEqual(env_name, provider.api_key_env)
                self.assertEqual(env_name, provider.redacted()["api_key_env"])
                self.assertFalse(provider.allow_custom_model)

        self.assertEqual(len(set(expected.values())), len(expected))

    def test_stale_local_transport_values_cannot_restore_blocked_gateway_client(self) -> None:
        import json

        from app.settings import list_providers

        raw = json.loads((ROOT / "config" / "providers.example.json").read_text(encoding="utf-8"))
        for name in (
            "lingsuan_openai",
            "lingsuan_image",
            "lingsuan_google",
            "lingsuan_xai",
            "lingsuan_anthropic",
        ):
            raw["providers"][name]["base_url"] = "https://lingsuan.top/v1"
            raw["providers"][name]["user_agent"] = ""

        with patch("app.settings.load_provider_config_file", return_value=raw):
            providers = list_providers()

        for name in (
            "lingsuan_openai",
            "lingsuan_image",
            "lingsuan_google",
            "lingsuan_xai",
            "lingsuan_anthropic",
        ):
            with self.subTest(provider=name):
                self.assertEqual("https://lingsuan.org/v1", providers[name].base_url)
                self.assertTrue(providers[name].user_agent.startswith("Mozilla/5.0 "))

    def test_image_provider_is_image_only_and_uses_gpt_image_2(self) -> None:
        from app.settings import provider_supports_image_generation, resolve_provider_model

        provider = self._providers()["lingsuan_image"]

        self.assertFalse(provider.supports_text_generation)
        self.assertTrue(provider_supports_image_generation(provider))
        self.assertEqual("gpt-image-2", provider.image_model)
        self.assertEqual(("gpt-image-2",), provider.image_model_options)
        self.assertEqual("LINGSUAN_IMAGE_API_KEY", provider.api_key_env)
        with self.assertRaisesRegex(ValueError, "not configured for text generation"):
            resolve_provider_model(provider)

    def test_openai_and_google_models_are_isolated_and_multimodal(self) -> None:
        from app.llm_client import OpenAICompatibleClient
        from app.model_tool_loop import tool_loop_supported
        from app.settings import provider_model_supports_vision

        providers = self._providers()
        openai = providers["lingsuan_openai"]
        google = providers["lingsuan_google"]

        self.assertEqual(
            ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"),
            openai.model_options,
        )
        self.assertEqual(
            (
                "gemini-3.7-flash-low",
                "gemini-3.7-flash-medium",
                "gemini-3.7-flash-high",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
            ),
            google.model_options,
        )
        self.assertEqual("gemini-3.6-flash", google.default_model)
        self.assertEqual("gemini-3.6-flash", google.vision_model)
        self.assertTrue(all(provider_model_supports_vision(openai, model) for model in openai.model_options))
        self.assertTrue(all(provider_model_supports_vision(google, model) for model in google.model_options))
        self.assertTrue(
            all(
                tool_loop_supported(OpenAICompatibleClient(google), google, model)
                for model in google.model_options
            )
        )
        self.assertTrue(set(openai.model_options).isdisjoint(google.model_options))

    def test_stale_local_google_model_arrays_gain_gemini_37_without_losing_saved_defaults(self) -> None:
        import json

        from app.settings import list_providers

        raw = json.loads((ROOT / "config" / "providers.example.json").read_text(encoding="utf-8"))
        google = raw["providers"]["lingsuan_google"]
        google["default_model"] = "gemini-3.6-flash"
        google["vision_model"] = "gemini-3.6-flash"
        for key in ("model_options", "vision_model_options"):
            google[key] = ["gemini-3.6-flash", "gemini-3.5-flash"]
        for model in ("gemini-3.7-flash-low", "gemini-3.7-flash-medium", "gemini-3.7-flash-high"):
            google["model_capabilities"].pop(model, None)
            google["model_profiles"].pop(model, None)

        with patch("app.settings.load_provider_config_file", return_value=raw):
            provider = list_providers()["lingsuan_google"]

        self.assertEqual("gemini-3.6-flash", provider.default_model)
        for model, level in (
            ("gemini-3.7-flash-low", "low"),
            ("gemini-3.7-flash-medium", "medium"),
            ("gemini-3.7-flash-high", "high"),
        ):
            self.assertIn(model, provider.model_options)
            self.assertIn(model, provider.vision_model_options)
            self.assertEqual(("text", "vision"), provider.model_capabilities[model])
            self.assertEqual(level, provider.model_profiles[model]["thinking_minimum"])

    def test_legacy_lingsuan_provider_resolves_to_openai_only(self) -> None:
        from app.settings import get_provider

        with patch("app.settings.CONFIG_DIR", ROOT / "config"):
            provider = get_provider("lingsuan")

        self.assertEqual("lingsuan_openai", provider.name)

    def test_stale_local_high_defaults_do_not_override_vendor_defaults(self) -> None:
        import json

        from app.settings import list_providers

        raw = json.loads((ROOT / "config" / "providers.example.json").read_text(encoding="utf-8"))
        for name in ("lingsuan_openai", "lingsuan_google", "lingsuan_image"):
            raw["providers"][name]["thinking_mode"] = "high"

        with patch("app.settings.load_provider_config_file", return_value=raw):
            providers = list_providers()

        self.assertEqual("auto", providers["lingsuan_openai"].thinking_mode)
        self.assertEqual("auto", providers["lingsuan_google"].thinking_mode)
        self.assertEqual("auto", providers["lingsuan_image"].thinking_mode)

    def test_frontend_names_and_saves_each_supplier_key(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        for provider, env_name, label in (
            ("lingsuan_openai", "LINGSUAN_OPENAI_API_KEY", "灵算 · OpenAI"),
            ("lingsuan_image", "LINGSUAN_IMAGE_API_KEY", "灵算 · OpenAI 图片"),
            ("lingsuan_google", "LINGSUAN_GOOGLE_API_KEY", "灵算 · Google Gemini"),
            ("lingsuan_xai", "LINGSUAN_XAI_API_KEY", "灵算 · xAI"),
            ("lingsuan_anthropic", "LINGSUAN_ANTHROPIC_API_KEY", "灵算 · Anthropic"),
        ):
            self.assertIn(f'{provider}: "{env_name}"', source)
            self.assertIn(f'{provider}: "{label}"', source)
        self.assertIn('thinking_mode: cfg.thinking_mode || "auto"', source)
        self.assertIn('image: ["lingsuan_image", "gpt-image-2"]', source)
        self.assertIn('cfg.supports_text_generation !== false', source)


if __name__ == "__main__":
    unittest.main()
