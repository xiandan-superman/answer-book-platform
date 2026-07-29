from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class BailianQwen37MaxTests(unittest.TestCase):
    def test_example_exposes_qwen37max_as_text_model_only(self) -> None:
        providers = json.loads((ROOT / "config" / "providers.example.json").read_text(encoding="utf-8"))
        bailian = providers["providers"]["bailian"]

        self.assertIn("qwen3.7-max", bailian["model_options"])
        self.assertIn("qwen3.7-max", bailian["json_mode_unsupported_models"])
        self.assertNotEqual("qwen3.7-max", bailian["vision_model"])

    def test_qwen37max_skips_unsupported_response_format(self) -> None:
        from app.llm_client import OpenAICompatibleClient
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="bailian",
            type="openai_compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="test-key",
            default_model="qwen3.7-max",
            model_options=("qwen3.7-max",),
            allow_custom_model=True,
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            json_mode_unsupported_models=("qwen3.7-max",),
        )
        client = OpenAICompatibleClient(provider)
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data.decode("utf-8")))
            return _Response({"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]})

        client._urlopen = fake_urlopen
        value = client.chat_json_object(
            [{"role": "user", "content": 'Return JSON only: {"ok":true}'}],
            model="qwen3.7-max",
            attempts=1,
        )

        self.assertEqual({"ok": True}, value)
        self.assertEqual(1, len(requests))
        self.assertNotIn("response_format", requests[0])

    def test_existing_local_bailian_config_gets_the_builtin_model_option(self) -> None:
        from app.settings import list_providers

        with patch(
            "app.settings.load_provider_config_file",
            return_value={
                "providers": {
                    "bailian": {
                        "type": "openai_compatible",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key_env": "DASHSCOPE_API_KEY",
                        "default_model": "qwen3.7-plus",
                        "vision_model": "qwen3-vl-plus",
                        "supports_vision": True,
                        "model_options": ["qwen3.7-plus"],
                    }
                }
            },
        ):
            bailian = list_providers()["bailian"]

        self.assertIn("qwen3.7-max", bailian.model_options)
        self.assertIn("qwen3.7-max", bailian.json_mode_unsupported_models)


if __name__ == "__main__":
    unittest.main()
