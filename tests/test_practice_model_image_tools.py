from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PracticeModelImageToolTests(unittest.TestCase):
    def test_practice_tool_loop_registers_source_pixels_for_image_edit(self):
        from app.exercise_generation import _practice_model_tool_loop
        from app.settings import ProviderConfig

        main_provider = ProviderConfig(
            name="test-main-reference-loop",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="main-key",
            default_model="vision-model",
            model_options=("vision-model",),
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            api_protocol="responses",
            responses_streaming=False,
            supports_vision=True,
            vision_model="vision-model",
            vision_model_options=("vision-model",),
            model_capabilities={"vision-model": ("text", "vision")},
            model_profiles={"vision-model": {"supports_tool_calls": True}},
            allow_custom_model=True,
        )
        image_provider = ProviderConfig(
            name="test-image-reference-loop",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="image-key",
            default_model="unused",
            model_options=(),
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            image_model="gpt-image-2",
            image_model_options=("gpt-image-2",),
            supports_image_generation=True,
            supports_text_generation=False,
            allow_custom_model=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            Image.new("RGB", (137, 91), "white").save(source)
            raw = source.read_bytes()
            data_url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
            with patch("app.exercise_generation.OUTPUTS_DIR", Path(tmp) / "outputs"), patch(
                "app.exercise_generation.get_provider",
                return_value=image_provider,
            ):
                loop = _practice_model_tool_loop(
                    {
                        "image_orchestration": "main_model_tool_loop",
                        "image_provider": image_provider.name,
                        "image_model": "gpt-image-2",
                        "generation_run_id": "run",
                    },
                    main_provider,
                    "vision-model",
                    scope_key="source-item",
                    reference_images=[data_url],
                )

            tool = loop.tools["generate_image"]
            self.assertEqual(1, len(tool.reference_paths))
            self.assertEqual(raw, tool.reference_paths[0].read_bytes())
            self.assertEqual(
                [str(tool.reference_paths[0])],
                tool.definition()["parameters"]["properties"]["referenced_image_paths"]["items"]["enum"],
            )

    def test_practice_call_uses_agent_loop_without_calling_normal_chat(self):
        from app.exercise_generation import _call_practice_json

        class FakeLoop:
            def run_json(self, messages, **kwargs):
                return SimpleNamespace(
                    value={"exercises": [{"batch_index": 1, "stem": "普通文字题"}]},
                    generated_artifacts=[],
                    steps=1,
                    tool_calls=0,
                )

        client = SimpleNamespace(config=SimpleNamespace())
        client.chat_json = lambda *args, **kwargs: self.fail("normal chat path must not run")
        raw = _call_practice_json(
            client,
            [{"role": "user", "content": "生题"}],
            model="vision-model",
            temperature=0.2,
            thinking="medium",
            tool_loop=FakeLoop(),
        )

        self.assertEqual("普通文字题", raw["exercises"][0]["stem"])
        self.assertEqual(0, raw["_image_tool_loop"]["tool_calls"])

    def test_accepted_practice_asset_is_bound_to_the_correct_exercise(self):
        from app.exercise_generation import _bind_practice_generated_images

        with tempfile.TemporaryDirectory() as tmp:
            outputs = Path(tmp) / "outputs"
            image_path = outputs / "practice_images" / "agent" / "run" / "asset.png"
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (128, 96), "white").save(image_path)
            raw = {
                "exercises": [
                    {
                        "batch_index": 1,
                        "stem": "根据图回答。",
                        "generated_images": [
                            {"asset_id": "img_sha256_a", "title": "题图", "description": "结构关系"}
                        ],
                    },
                    {"batch_index": 2, "stem": "不需要图。"},
                ],
                "_image_tool_artifacts": [
                    {
                        "asset_id": "img_sha256_a",
                        "path": str(image_path),
                        "provider": "p",
                        "model": "imge-2",
                    }
                ],
            }

            bound = _bind_practice_generated_images(raw)

            self.assertEqual(1, len(bound["exercises"][0]["figures"]))
            self.assertNotIn("figures", bound["exercises"][1])
            self.assertEqual("main_model_accepted", bound["exercises"][0]["figures"][0]["figure_purpose"])

    def test_nested_practice_asset_must_have_been_shown_to_main_model(self):
        from app.image_artifacts import ImageArtifactStore
        from app.llm_client import LLMError, ResponsesAPIClient
        from app.model_tool_loop import ModelToolLoop
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="p",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="vision-model",
            model_options=(),
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            api_protocol="responses",
            responses_streaming=False,
            allow_custom_model=True,
            supports_vision=True,
            vision_model="vision-model",
            vision_model_options=("vision-model",),
            model_capabilities={"vision-model": ("text", "vision")},
            model_profiles={"vision-model": {"supports_tool_calls": True}},
        )
        client = ResponsesAPIClient(provider)
        client.create_tool_response = lambda input_items, **kwargs: {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"exercises":[{"generated_images":[{"asset_id":"img_sha256_fake"}]}]}',
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            with self.assertRaisesRegex(LLMError, "had not inspected"):
                ModelToolLoop(client, [], store).run_json(
                    [{"role": "user", "content": "生题"}],
                    model="vision-model",
                    max_tokens=1000,
                    thinking="medium",
                    timeout=30,
                )


if __name__ == "__main__":
    unittest.main()
