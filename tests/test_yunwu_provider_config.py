from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class YunwuProviderConfigTests(unittest.TestCase):
    def test_local_provider_override_preserves_example_provider_shape(self) -> None:
        from app.settings import _merge_config

        merged = _merge_config(
            {"providers": {"yunwu": {"base_url": "https://yunwu.ai/v1", "default_model": "gpt-5.5"}}},
            {"providers": {"yunwu": {"api_key": "local-key"}}},
        )

        self.assertEqual("https://yunwu.ai/v1", merged["providers"]["yunwu"]["base_url"])
        self.assertEqual("gpt-5.5", merged["providers"]["yunwu"]["default_model"])
        self.assertEqual("local-key", merged["providers"]["yunwu"]["api_key"])

    def test_yunwu_text_vision_and_image_model_options_are_declared(self) -> None:
        from app.settings import list_providers

        with patch("app.settings.CONFIG_DIR", ROOT / "config"):
            provider = list_providers()["yunwu"]

        self.assertEqual("gpt-5.6-sol", provider.default_model)
        self.assertEqual("gpt-5.6-sol", provider.vision_model)
        self.assertIn("gpt-5.5", provider.model_options)
        self.assertIn("gpt-4o", provider.model_options)
        self.assertIn("gemini-3.5-flash", provider.model_options)
        self.assertEqual("YUNWU_API_KEY", provider.api_key_env)
        self.assertEqual("YUNWU_API_KEY", provider.redacted()["api_key_env"])
        self.assertEqual(
            provider.model_options,
            provider.vision_model_options,
        )
        self.assertEqual(
            list(provider.vision_model_options),
            provider.redacted()["vision_model_options"],
        )
        self.assertTrue(provider.supports_image_generation)
        self.assertTrue(provider.redacted()["supports_image_generation"])
        self.assertEqual("gpt-image-2", provider.image_model)
        self.assertIn("gpt-image-2", provider.image_model_options)

    def test_saved_env_key_overrides_legacy_local_provider_key(self) -> None:
        from app.settings import list_providers

        config = {
            "providers": {
                "yunwu": {
                    "type": "openai_compatible",
                    "base_url": "https://yunwu.ai/v1",
                    "api_key": "legacy-local-key",
                    "api_key_env": "YUNWU_API_KEY",
                    "default_model": "gpt-5.5",
                    "model_options": ["gpt-5.5"],
                    "allow_custom_model": True,
                }
            }
        }
        with patch("app.settings.load_provider_config_file", return_value=config), patch.dict(
            "os.environ",
            {"YUNWU_API_KEY": "saved-env-key"},
            clear=False,
        ):
            provider = list_providers()["yunwu"]

        self.assertEqual("saved-env-key", provider.api_key)

    def test_yunwu_default_image_model_takes_precedence_over_global_env(self) -> None:
        from app.settings import list_providers

        with patch("app.settings.CONFIG_DIR", ROOT / "config"), patch.dict(
            "os.environ",
            {"ANSWER_BOOK_IMAGE_MODEL": "gpt-image-1"},
            clear=False,
        ):
            provider = list_providers()["yunwu"]

        self.assertTrue(provider.supports_image_generation)
        self.assertEqual("gpt-image-2", provider.image_model)

    def test_gemini_image_response_location_supports_file_data(self) -> None:
        from app.llm_client import _gemini_image_location, _image_download_headers

        uri, mime_type = _gemini_image_location(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"fileData": {"fileUri": "https://example.test/image", "mimeType": "image/jpeg"}}]
                        }
                    }
                ]
            }
        )

        self.assertEqual("https://example.test/image", uri)
        self.assertEqual("image/jpeg", mime_type)
        self.assertEqual({}, _image_download_headers("https://storage.test/a.jpg?X-Amz-Signature=token", "test-key"))
        self.assertEqual({"Authorization": "Bearer test-key"}, _image_download_headers("https://storage.test/a.jpg", "test-key"))

    def test_frontend_can_save_yunwu_key(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('yunwu: "YUNWU_API_KEY"', source)
        self.assertIn('yunwu: "云雾 API"', source)

    def test_frontend_role_status_uses_key_and_test_state(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("Key已保存 · 未测试", source)
        self.assertIn("未保存Key", source)
        self.assertIn("不可用", source)
        self.assertIn("rememberModelConnectionTest", source)
        self.assertNotIn("? '<i class=\"fas fa-circle\"></i>已配置'", source)


if __name__ == "__main__":
    unittest.main()
