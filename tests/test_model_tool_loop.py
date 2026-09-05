from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _FakeImageTool:
    name = "generate_image"

    def __init__(self, store):
        self.store = store
        self.calls = []

    def definition(self):
        return {
            "type": "function",
            "name": self.name,
            "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}},
        }

    def execute(self, arguments, *, call_id):
        self.calls.append((arguments, call_id))
        source = self.store.root / "tool-output.png"
        Image.new("RGB", (128, 96), "white").save(source)
        artifact = self.store.register(
            source,
            provider="fake-image-provider",
            model="fake-image-model",
            source_call_id=call_id,
        )
        return {"ok": True, "asset": artifact.to_dict()}


class _LargeFailingTool:
    name = "generate_image"

    def definition(self):
        return {
            "type": "function",
            "name": self.name,
            "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}},
        }

    def execute(self, arguments, *, call_id):
        return {"ok": False, "error": {"code": "IMAGE_FAILED", "message": "x" * 3000}}


class ModelToolLoopTests(unittest.TestCase):
    def _client(self):
        from app.llm_client import ResponsesAPIClient
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="fake-main",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="fake-vision-model",
            model_options=(),
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            api_protocol="responses",
            responses_streaming=False,
            supports_vision=True,
            vision_model="fake-vision-model",
            vision_model_options=("fake-vision-model",),
            model_capabilities={"fake-vision-model": ("text", "vision")},
            model_profiles={"fake-vision-model": {"supports_tool_calls": True}},
            allow_custom_model=True,
        )
        return ResponsesAPIClient(provider)

    def _chat_client(self):
        from app.llm_client import OpenAICompatibleClient
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="fake-gemini",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="gemini-test",
            model_options=("gemini-test",),
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            api_protocol="chat_completions",
            supports_vision=True,
            vision_model="gemini-test",
            vision_model_options=("gemini-test",),
            model_capabilities={"gemini-test": ("text", "vision")},
            model_profiles={
                "gemini-test": {
                    "api_protocol": "chat_completions",
                    "supports_tool_calls": True,
                    "omit_parameters": ["temperature"],
                }
            },
            allow_custom_model=False,
        )
        return OpenAICompatibleClient(provider)

    def test_no_image_task_finishes_in_one_model_turn(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append((list(input_items), kwargs))
                return {
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": '{"answer":"A","generated_images":[]}'}]}
                    ]
                }

            client.create_tool_response = create
            result = ModelToolLoop(client, [tool], store).run_json(
                [{"role": "user", "content": "回答即可"}],
                model="fake-vision-model",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual("A", result.value["answer"])
            self.assertEqual(1, result.steps)
            self.assertEqual(0, result.tool_calls)
            self.assertEqual([], tool.calls)
            self.assertEqual(1, len(requests))

    def test_real_image_tool_exposes_and_executes_the_registered_method(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="custom-image",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="unused",
            model_options=(),
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            allow_custom_model=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = ImageGenerationTool(provider, "image-model", store)

            def generate(prompt, output_path, **kwargs):
                self.assertEqual("教学示意图", prompt)
                self.assertEqual(240, kwargs["timeout"])
                Image.new("RGB", (128, 96), "white").save(output_path)
                return SimpleNamespace(
                    path=output_path,
                    provider="custom-image",
                    model=kwargs["model"],
                )

            with patch(
                "app.model_tool_loop.OpenAICompatibleClient.generate_image",
                side_effect=generate,
            ):
                result = tool.execute(
                    {"prompt": "教学示意图", "referenced_image_paths": []},
                    call_id="call/1",
                )

            self.assertTrue(result["ok"])
            self.assertEqual("generate", result["operation"])
            self.assertEqual(0, result["reference_image_count"])
            self.assertIsNotNone(store.get(result["asset"]["asset_id"]))

    def test_real_image_tool_uses_edit_request_for_registered_original(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="custom-image",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="unused",
            model_options=(),
            model_hint="",
            temperature=0.1,
            max_tokens=1024,
            allow_custom_model=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.png"
            Image.new("RGB", (137, 91), "white").save(source)
            original_bytes = source.read_bytes()
            store = ImageArtifactStore(root / "artifacts")
            tool = ImageGenerationTool(
                provider,
                "image-model",
                store,
                reference_images=[source],
            )
            definition = tool.definition()
            registered_path = str(source.resolve())
            self.assertEqual(
                [registered_path],
                definition["parameters"]["properties"]["referenced_image_paths"]["items"]["enum"],
            )

            def edit(prompt, references, output_path, **kwargs):
                self.assertEqual("保留结构并补全答案", prompt)
                self.assertEqual([source.resolve()], references)
                self.assertEqual(original_bytes, references[0].read_bytes())
                Image.new("RGB", (128, 96), "white").save(output_path)
                return SimpleNamespace(
                    path=output_path,
                    provider="custom-image",
                    model=kwargs["model"],
                )

            with patch(
                "app.model_tool_loop.OpenAICompatibleClient.edit_image",
                side_effect=edit,
            ) as edit_mock, patch(
                "app.model_tool_loop.OpenAICompatibleClient.generate_image"
            ) as generate_mock:
                result = tool.execute(
                    {
                        "prompt": "保留结构并补全答案",
                        "referenced_image_paths": [registered_path],
                    },
                    call_id="call/edit",
                )

            self.assertEqual("edit", result["operation"])
            self.assertEqual(1, result["reference_image_count"])
            edit_mock.assert_called_once()
            generate_mock.assert_not_called()

    def test_image_tool_canonicalizes_both_selectors_by_available_image(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.png"
            Image.new("RGB", (128, 96), "white").save(source)
            tool = ImageGenerationTool(
                SimpleNamespace(
                    type="openai_compatible",
                    base_url="https://example.test/v1",
                    api_key="key",
                    image_model="gpt-image-2",
                    image_size="",
                ),
                "gpt-image-2",
                ImageArtifactStore(root / "artifacts"),
                reference_images=[source],
            )
            registered_path = str(source.resolve())

            def edit(_prompt, references, output_path, **_kwargs):
                self.assertEqual([source.resolve()], references)
                Image.new("RGB", (128, 96), "white").save(output_path)
                return SimpleNamespace(path=output_path, provider="test", model="image")

            with patch(
                "app.model_tool_loop.OpenAICompatibleClient.edit_image",
                side_effect=edit,
            ):
                first = tool.execute(
                    {
                        "prompt": "edit",
                        "referenced_image_paths": [registered_path],
                        "num_last_images_to_include": 1,
                    },
                    call_id="both",
                )
            self.assertEqual("ignored_unavailable_recent_image_selector", first["argument_normalization"])

            def edit_recent(_prompt, references, output_path, **_kwargs):
                self.assertEqual(1, len(references))
                self.assertNotEqual(source.resolve(), references[0])
                Image.new("RGB", (128, 96), "white").save(output_path)
                return SimpleNamespace(path=output_path, provider="test", model="image")

            with patch(
                "app.model_tool_loop.OpenAICompatibleClient.edit_image",
                side_effect=edit_recent,
            ):
                second = tool.execute(
                    {
                        "prompt": "continue editing the recent image",
                        "referenced_image_paths": [str(root / "truncated-or-stale.png")],
                        "num_last_images_to_include": 1,
                    },
                    call_id="both-real",
                )
            self.assertEqual("preferred_recent_image_selector", second["argument_normalization"])
            self.assertEqual(1, second["reference_image_count"])
            with self.assertRaisesRegex(ValueError, "unregistered"):
                tool.execute(
                    {
                        "prompt": "edit",
                        "referenced_image_paths": [str(root / "other.png")],
                    },
                    call_id="other",
                )
            with self.assertRaisesRegex(ValueError, "more than 5"):
                tool.execute(
                    {
                        "prompt": "edit",
                        "referenced_image_paths": [str(source.resolve())] * 6,
                    },
                    call_id="too-many",
                )

    def test_image_tool_treats_explicit_empty_paths_and_unavailable_recent_as_generate(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = ImageGenerationTool(
                SimpleNamespace(
                    type="openai_compatible",
                    base_url="https://example.test/v1",
                    api_key="key",
                    image_model="gpt-image-2",
                    image_size="",
                ),
                "gpt-image-2",
                ImageArtifactStore(root / "artifacts"),
            )

            def generate(_prompt, output_path, **_kwargs):
                Image.new("RGB", (128, 96), "white").save(output_path)
                return SimpleNamespace(path=output_path, provider="test", model="image")

            with patch(
                "app.model_tool_loop.OpenAICompatibleClient.generate_image",
                side_effect=generate,
            ):
                result = tool.execute(
                    {
                        "prompt": "generate from scratch",
                        "referenced_image_paths": [],
                        "num_last_images_to_include": 1,
                    },
                    call_id="empty-paths",
                )

            self.assertEqual("generate", result["operation"])
            self.assertEqual("ignored_unavailable_recent_image_selector", result["argument_normalization"])

    def test_image_tool_can_edit_the_last_generated_image(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ImageArtifactStore(root / "artifacts")
            tool = ImageGenerationTool(
                SimpleNamespace(
                    type="openai_compatible",
                    image_model="gpt-image-2",
                    image_size="",
                ),
                "gpt-image-2",
                store,
            )

            def generate(_prompt, output_path, **_kwargs):
                Image.new("RGB", (128, 96), "white").save(output_path)
                return SimpleNamespace(
                    path=output_path,
                    provider="custom-image",
                    model="gpt-image-2",
                )

            with patch(
                "app.model_tool_loop.OpenAICompatibleClient.generate_image",
                side_effect=generate,
            ):
                first_result = tool.execute({"prompt": "初稿"}, call_id="first")
            previous_path = Path(store.get(first_result["asset"]["asset_id"]).path)

            def edit(_prompt, references, output_path, **_kwargs):
                self.assertEqual([previous_path], references)
                Image.new("RGB", (128, 96), "white").save(output_path)
                return SimpleNamespace(
                    path=output_path,
                    provider="custom-image",
                    model="gpt-image-2",
                )

            with patch(
                "app.model_tool_loop.OpenAICompatibleClient.edit_image",
                side_effect=edit,
            ):
                result = tool.execute(
                    {"prompt": "修正标注", "num_last_images_to_include": 1},
                    call_id="revise",
                )

            self.assertEqual("edit", result["operation"])
            self.assertEqual(1, result["reference_image_count"])

    def test_answer_tool_contract_makes_real_image_tool_authoritative_without_forcing_a_call(self):
        from app.answer_generation import _with_main_model_image_tool_contract

        original = [
            {"role": "system", "content": "系统规则"},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "hard_rules": ["For 作图题, output figure_specs."],
                        "question": {"question_type": "作图题", "stem": "按题意作图"},
                        "output_schema_example": {
                            "answer": "见图",
                            "figure_specs": [{"kind": "custom_diagram"}],
                            "drawing_code_specs": [{"code": "..."}],
                            "generated_images": [],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        routed = _with_main_model_image_tool_contract(original)
        payload = json.loads(routed[-1]["content"])
        rules = "\n".join(payload["hard_rules"])

        self.assertEqual("main_model_tool_loop", payload["image_tool_orchestration"])
        self.assertIn("You alone decide", rules)
        self.assertIn("call generate_image", rules)
        self.assertIn("required answer content rather than optional decoration", rules)
        self.assertIn("leave figure_specs and drawing_code_specs empty", rules)
        self.assertIn("do not call generate_image", rules)
        self.assertNotIn("For 作图题, output figure_specs.", payload["hard_rules"])
        self.assertNotIn("figure_specs", payload["output_schema_example"])
        self.assertNotIn("drawing_code_specs", payload["output_schema_example"])
        self.assertIn("generated_images", payload["output_schema_example"])
        self.assertNotIn("image_tool_orchestration", json.loads(original[-1]["content"]))

    def test_retry_transaction_redelivers_previously_generated_image(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            source = Path(tmp) / "source.png"
            Image.new("RGB", (96, 96), "white").save(source)
            artifact = store.register(
                source,
                provider="image-provider",
                model="image-model",
                source_call_id="call_first",
            )
            client = self._client()
            seen_inputs = []

            def create(input_items, **kwargs):
                seen_inputs.append(input_items)
                return {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "answer": "见图",
                                            "generated_images": [{"asset_id": artifact.asset_id}],
                                        }
                                    ),
                                }
                            ],
                        }
                    ]
                }

            client.create_tool_response = create
            loop = ModelToolLoop(client, [], store)
            loop._session_artifacts[artifact.asset_id] = artifact

            result = loop.run_json(
                [{"role": "user", "content": "修复答案"}],
                model="fake-vision-model",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual(artifact.asset_id, result.value["generated_images"][0]["asset_id"])
            self.assertEqual(artifact.asset_id, result.generated_artifacts[0]["asset_id"])
            redelivered = seen_inputs[0][-1]["content"]
            self.assertTrue(any(item.get("type") == "input_image" for item in redelivered))

    def test_answer_tool_contract_rewrites_multimodal_user_payload(self):
        from app.answer_generation import _with_main_model_image_tool_contract

        original = [
            {"role": "system", "content": "系统规则"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "hard_rules": ["If needs_figure, output figure_specs."],
                                "output_schema": {"figure_specs": [], "generated_images": []},
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ],
            },
        ]

        routed = _with_main_model_image_tool_contract(original)
        payload = json.loads(routed[-1]["content"][0]["text"])

        self.assertEqual("main_model_tool_loop", payload["image_tool_orchestration"])
        self.assertNotIn("figure_specs", payload["output_schema"])
        self.assertIn("generated_images", payload["output_schema"])
        self.assertEqual("image_url", routed[-1]["content"][1]["type"])

    def test_generated_image_satisfies_a_numbered_drawing_answer_unit(self):
        from app.answer_generation import semantic_generation_issues

        question = {
            "question_id": "q1",
            "question_type": "简答题",
            "subquestions": [
                {"number": "1", "stem": "画出示意图", "question_type": "作图题"},
                {"number": "2", "stem": "说明原因", "question_type": "简答题"},
            ],
            "drawing_generation_mode": "figure_specs",
        }
        fragment = {
            "question_id": "q1",
            "answer": "见分项答案",
            "answer_units": [
                {"number": "1", "answer": "见图", "analysis_segments": [{"text": "图示如下。"}]},
                {"number": "2", "answer": "原因", "analysis_segments": [{"text": "原因说明。"}]},
            ],
            "generated_images": [
                {"asset_id": "img_sha256_checked", "answer_unit_number": "1", "caption": "示意图"}
            ],
            "figure_specs": [],
            "drawing_code_specs": [],
            "formulas": [],
            "blocks": [],
        }

        issues = semantic_generation_issues(question, fragment)

        self.assertFalse(any(issue.startswith("missing_drawing_answer_units:") for issue in issues), issues)

    def test_confirmed_answer_image_intent_cannot_finish_without_image_output(self):
        from app.answer_generation import semantic_generation_issues

        question = {
            "question_id": "q1",
            "stem": "写出聚合反应产物。",
            "question_type": "简答题",
            "image_refs": ["source.png"],
            "answer_figure_required": True,
            "question_understanding": {"needs_figure": True},
        }
        fragment = {
            "question_id": "q1",
            "answer": "聚合物结构如下。",
            "generated_images": [],
            "figure_specs": [],
            "drawing_code_specs": [],
            "formulas": [],
            "blocks": [],
        }

        self.assertIn("missing_required_answer_figure", semantic_generation_issues(question, fragment))

    def test_image_tool_exposes_monochrome_default_without_forbidding_required_color(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool

        with tempfile.TemporaryDirectory() as tmp:
            tool = ImageGenerationTool(SimpleNamespace(image_model="gpt-image-2", image_size=""), "gpt-image-2", ImageArtifactStore(Path(tmp)))
            definition = tool.definition()
            visible_contract = definition["description"] + definition["parameters"]["properties"]["prompt"]["description"]

        self.assertIn("black, white, and grayscale", visible_contract)
        self.assertIn("Do not use color to distinguish content", visible_contract)
        self.assertIn("explicitly makes color part", visible_contract)

    def test_generated_image_is_returned_to_same_model_before_binding(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append(json.loads(json.dumps(input_items)))
                if len(requests) == 1:
                    return {
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "generate_image",
                                "arguments": '{"prompt":"画一张受力示意图"}',
                            }
                        ]
                    }
                output = input_items[-1]["output"]
                asset_payload = json.loads(output[0]["text"])
                asset_id = asset_payload["asset"]["asset_id"]
                return {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "answer": "见图",
                                            "generated_images": [
                                                {"asset_id": asset_id, "caption": "受力示意图"}
                                            ],
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                        }
                    ]
                }

            client.create_tool_response = create
            result = ModelToolLoop(client, [tool], store).run_json(
                [{"role": "user", "content": "必要时配图"}],
                model="fake-vision-model",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual(2, result.steps)
            self.assertEqual(1, result.tool_calls)
            self.assertEqual(1, len(result.generated_artifacts))
            self.assertEqual("input_image", requests[1][-1]["output"][1]["type"])
            self.assertTrue(requests[1][-1]["output"][1]["image_url"].startswith("data:image/png;base64,"))
            self.assertEqual(
                result.generated_artifacts[0]["asset_id"],
                result.value["generated_images"][0]["asset_id"],
            )

    def test_main_model_cannot_bind_an_asset_it_never_received(self):
        from app.image_artifacts import ImageArtifactStore
        from app.llm_client import LLMError
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._client()
            client.create_tool_response = lambda input_items, **kwargs: {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"answer":"伪造","generated_images":[{"asset_id":"img_sha256_fake"}]}',
                            }
                        ],
                    }
                ]
            }
            with self.assertRaisesRegex(LLMError, "had not inspected"):
                ModelToolLoop(client, [tool], store).run_json(
                    [{"role": "user", "content": "回答"}],
                    model="fake-vision-model",
                    max_tokens=1000,
                    thinking="medium",
                    timeout=30,
                )

    def test_invalid_final_json_is_repaired_by_the_same_main_model(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append(json.loads(json.dumps(input_items)))
                if len(requests) == 1:
                    return {
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {"type": "output_text", "text": "检查完成。\n```json\n{not valid}\n```"}
                                ],
                            }
                        ]
                    }
                return {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": '{"answer":"已修复","generated_images":[]}'}
                            ],
                        }
                    ]
                }

            client.create_tool_response = create
            result = ModelToolLoop(client, [tool], store).run_json(
                [{"role": "user", "content": "回答"}],
                model="fake-vision-model",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual("已修复", result.value["answer"])
            self.assertEqual(2, result.steps)
            self.assertEqual(0, result.tool_calls)
            self.assertIn("Repair only its JSON syntax", requests[1][-1]["content"][0]["text"])

    def test_chat_completions_tool_result_and_pixels_return_to_same_model(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop, tool_loop_supported

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._chat_client()
            requests = []

            def create(messages, **kwargs):
                requests.append(json.loads(json.dumps(messages)))
                if len(requests) == 1:
                    return {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "chat_call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "generate_image",
                                                "arguments": '{"prompt":"画一张晶体结构示意图"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                visual_message = messages[-1]
                tool_message = messages[-2]
                asset_id = json.loads(tool_message["content"])["asset"]["asset_id"]
                self.assertEqual("tool", tool_message["role"])
                self.assertEqual("chat_call_1", tool_message["tool_call_id"])
                self.assertEqual("user", visual_message["role"])
                self.assertTrue(
                    any(part.get("type") == "image_url" for part in visual_message["content"])
                )
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "answer": "见图",
                                        "generated_images": [{"asset_id": asset_id}],
                                    }
                                ),
                            }
                        }
                    ]
                }

            client.create_tool_response = create
            self.assertTrue(tool_loop_supported(client, client.config, "gemini-test"))
            result = ModelToolLoop(client, [tool], store).run_json(
                [{"role": "user", "content": "必要时配图"}],
                model="gemini-test",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual(2, result.steps)
            self.assertEqual(1, result.tool_calls)
            self.assertEqual(
                result.generated_artifacts[0]["asset_id"],
                result.value["generated_images"][0]["asset_id"],
            )

    def test_chat_tool_transport_uses_native_function_schema_without_response_format(self):
        client = self._chat_client()
        captured = {}

        def post(url, payload, **kwargs):
            captured.update({"url": url, "payload": payload, **kwargs})
            return {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}

        client._post_json = post
        client.create_tool_response(
            [{"role": "user", "content": "test"}],
            tools=[
                {
                    "type": "function",
                    "name": "generate_image",
                    "description": "generate",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            model="gemini-test",
            max_tokens=1000,
            thinking="medium",
            timeout=30,
        )

        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual("generate_image", captured["payload"]["tools"][0]["function"]["name"])
        self.assertNotIn("response_format", captured["payload"])
        self.assertEqual("chat_tools", captured["purpose"])

    def test_model_profile_chat_protocol_overrides_responses_provider_for_tool_loop(self):
        from dataclasses import replace

        from app.image_artifacts import ImageArtifactStore
        from app.llm_client import OpenAICompatibleClient, ResponsesAPIClient
        from app.model_tool_loop import ModelToolLoop
        from app.settings import list_providers

        provider = replace(list_providers()["bailian"], api_key="test-secret")
        client = OpenAICompatibleClient(provider)
        self.assertIsInstance(client, ResponsesAPIClient)
        requests = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def request(req, timeout):
            payload = json.loads(req.data)
            requests.append((req.full_url, payload))
            if len(requests) == 1:
                return Response({
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "chat_call_1",
                                "type": "function",
                                "function": {
                                    "name": "generate_image",
                                    "arguments": '{"prompt":"diagram"}',
                                },
                            }],
                        }
                    }]
                })
            tool_result = json.loads(payload["messages"][-2]["content"])
            asset_id = tool_result["asset"]["asset_id"]
            return Response({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({
                            "answer": "ok",
                            "generated_images": [{"asset_id": asset_id}],
                        }),
                    }
                }]
            })

        client._urlopen = request
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            result = ModelToolLoop(client, [_FakeImageTool(store)], store).run_json(
                [{"role": "user", "content": "generate if needed"}],
                model="qwen3-vl-flash",
                max_tokens=1000,
                thinking="minimal",
                timeout=30,
            )

        self.assertEqual(1, result.tool_calls)
        self.assertTrue(all(url.endswith("/chat/completions") for url, _payload in requests))
        self.assertEqual("qwen3-vl-flash", requests[0][1]["model"])

    def test_chat_retry_transaction_redelivers_prior_pixels_in_chat_format(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            source = Path(tmp) / "prior.png"
            Image.new("RGB", (64, 64), "white").save(source)
            artifact = store.register(
                source,
                provider="image-provider",
                model="image-model",
                source_call_id="prior_call",
            )
            client = self._chat_client()
            seen = []

            def create(messages, **kwargs):
                seen.append(json.loads(json.dumps(messages)))
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "answer": "reuse",
                                        "generated_images": [{"asset_id": artifact.asset_id}],
                                    }
                                ),
                            }
                        }
                    ]
                }

            client.create_tool_response = create
            loop = ModelToolLoop(client, [], store)
            loop._session_artifacts[artifact.asset_id] = artifact
            loop.run_json(
                [{"role": "user", "content": "repair"}],
                model="gemini-test",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            content = seen[0][-1]["content"]
            self.assertEqual(["text", "text", "image_url"], [part["type"] for part in content])

    def test_image_tool_runtime_rejects_unknown_and_non_strict_arguments(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool

        with tempfile.TemporaryDirectory() as tmp:
            tool = ImageGenerationTool(
                SimpleNamespace(image_model="gpt-image-2", image_size=""),
                "gpt-image-2",
                ImageArtifactStore(Path(tmp)),
            )
            with self.assertRaisesRegex(ValueError, "unknown image tool argument"):
                tool.execute({"prompt": "diagram", "quality": "high"}, call_id="unknown")
            with self.assertRaisesRegex(ValueError, "prompt must be a string"):
                tool.execute({"prompt": 123}, call_id="prompt-type")
            with self.assertRaisesRegex(ValueError, "must be an integer"):
                tool.execute(
                    {"prompt": "revise", "num_last_images_to_include": 1.0},
                    call_id="count-type",
                )

    def test_tool_lifecycle_is_durable_and_invalid_arguments_remain_model_visible(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool, ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = ImageGenerationTool(
                SimpleNamespace(image_model="gpt-image-2", image_size=""),
                "gpt-image-2",
                store,
            )
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append(json.loads(json.dumps(input_items)))
                if len(requests) == 1:
                    return {
                        "output": [{
                            "type": "function_call",
                            "call_id": "bad_args",
                            "name": "generate_image",
                            "arguments": '{"prompt":"diagram","quality":"high"}',
                        }]
                    }
                tool_result = json.loads(input_items[-1]["output"][0]["text"])
                self.assertEqual("INVALID_TOOL_ARGUMENTS", tool_result["error"]["info"]["code"])
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": '{"answer":"text fallback","generated_images":[]}',
                        }],
                    }]
                }

            client.create_tool_response = create
            with patch("app.model_tool_loop.OpenAICompatibleClient.generate_image") as generate:
                result = ModelToolLoop(client, [tool], store).run_json(
                    [{"role": "user", "content": "answer"}],
                    model="fake-vision-model",
                    max_tokens=1000,
                    thinking="medium",
                    timeout=30,
                )

            generate.assert_not_called()
            self.assertEqual("text fallback", result.value["answer"])
            events = [
                json.loads(line)
                for line in Path(result.tool_event_log).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                ["agent/request", "agent/completion", "tool/call", "tool/result", "agent/request", "agent/completion"],
                [event["type"] for event in events],
            )
            self.assertEqual("INVALID_TOOL_ARGUMENTS", events[3]["result"]["error"]["info"]["code"])
            self.assertTrue(events[2]["arguments_sha256"])

    def test_same_call_id_is_idempotent_across_answer_repair_rounds(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._client()
            turn = 0

            def create(input_items, **kwargs):
                nonlocal turn
                turn += 1
                if turn in {1, 3}:
                    return {
                        "output": [{
                            "type": "function_call",
                            "call_id": "stable_call",
                            "name": "generate_image",
                            "arguments": '{"prompt":"same diagram"}',
                        }]
                    }
                output = next(
                    item["output"]
                    for item in reversed(input_items)
                    if isinstance(item, dict) and item.get("type") == "function_call_output"
                )
                asset_id = json.loads(output[0]["text"])["asset"]["asset_id"]
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": json.dumps({
                                "answer": "ok",
                                "generated_images": [{"asset_id": asset_id}],
                            }),
                        }],
                    }]
                }

            client.create_tool_response = create
            loop = ModelToolLoop(client, [tool], store)
            for _ in range(2):
                loop.run_json(
                    [{"role": "user", "content": "answer"}],
                    model="fake-vision-model",
                    max_tokens=1000,
                    thinking="medium",
                    timeout=30,
                )

            self.assertEqual(1, len(tool.calls))
            results = [
                json.loads(line)
                for line in loop.tool_event_log_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["type"] == "tool/result"
            ]
            self.assertEqual([False, True], [event["cache_hit"] for event in results])

    def test_restart_marks_started_without_result_as_unknown_and_never_replays_tool(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop, ToolEventLog, _tool_call_fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            session_id = "stable-answer-session"
            arguments = {"prompt": "may already have completed"}
            ToolEventLog(store.root / "tool_events.jsonl", session_id=session_id).append(
                "tool/started",
                call_id="ambiguous_call",
                tool="generate_image",
                arguments_sha256=_tool_call_fingerprint("generate_image", arguments),
            )
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append(json.loads(json.dumps(input_items)))
                if len(requests) == 1:
                    return {
                        "output": [{
                            "type": "function_call",
                            "call_id": "ambiguous_call",
                            "name": "generate_image",
                            "arguments": json.dumps(arguments),
                        }]
                    }
                recovered = json.loads(input_items[-1]["output"][0]["text"])
                self.assertEqual("TOOL_OUTCOME_UNKNOWN", recovered["error"]["info"]["code"])
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": '{"answer":"verify externally","generated_images":[]}',
                        }],
                    }]
                }

            client.create_tool_response = create
            result = ModelToolLoop(
                client,
                [tool],
                store,
                session_id=session_id,
            ).run_json(
                [{"role": "user", "content": "resume"}],
                model="fake-vision-model",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual("verify externally", result.value["answer"])
            self.assertEqual([], tool.calls)

    def test_repeat_reminder_is_advisory_and_appended_after_the_third_call(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append(json.loads(json.dumps(input_items)))
                if len(requests) <= 3:
                    return {
                        "output": [{
                            "type": "function_call",
                            "call_id": f"repeat_{len(requests)}",
                            "name": "generate_image",
                            "arguments": '{"prompt":"same diagram"}',
                        }]
                    }
                reminder = input_items[-1]["content"][0]["text"]
                self.assertIn("repeating the exact same tool call", reminder)
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": '{"answer":"done","generated_images":[]}',
                        }],
                    }]
                }

            client.create_tool_response = create
            result = ModelToolLoop(client, [tool], store).run_json(
                [{"role": "user", "content": "answer"}],
                model="fake-vision-model",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual("done", result.value["answer"])
            self.assertEqual(3, len(tool.calls))
            event_types = [
                json.loads(line)["type"]
                for line in Path(result.tool_event_log).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(1, event_types.count("user/message"))

    def test_tool_call_limit_returns_a_structured_result_instead_of_ending_the_turn(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            tool = _FakeImageTool(store)
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append(json.loads(json.dumps(input_items)))
                if len(requests) <= 2:
                    return {
                        "output": [{
                            "type": "function_call",
                            "call_id": f"budget_{len(requests)}",
                            "name": "generate_image",
                            "arguments": json.dumps({"prompt": f"diagram {len(requests)}"}),
                        }]
                    }
                denied = json.loads(input_items[-1]["output"][0]["text"])
                self.assertEqual("TOOL_CALL_LIMIT", denied["error"]["info"]["code"])
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": '{"answer":"finished without another image","generated_images":[]}',
                        }],
                    }]
                }

            client.create_tool_response = create
            result = ModelToolLoop(client, [tool], store, max_tool_calls=1).run_json(
                [{"role": "user", "content": "answer"}],
                model="fake-vision-model",
                max_tokens=1000,
                thinking="medium",
                timeout=30,
            )

            self.assertEqual("finished without another image", result.value["answer"])
            self.assertEqual(1, len(tool.calls))
            self.assertEqual(2, result.tool_calls)

    def test_context_pressure_compacts_only_old_failed_tool_result(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ModelToolLoop

        with tempfile.TemporaryDirectory() as tmp:
            store = ImageArtifactStore(Path(tmp))
            client = self._client()
            requests = []

            def create(input_items, **kwargs):
                requests.append(json.loads(json.dumps(input_items)))
                if len(requests) <= 3:
                    return {
                        "output": [{
                            "type": "function_call",
                            "call_id": f"failed_{len(requests)}",
                            "name": "generate_image",
                            "arguments": json.dumps({"prompt": f"diagram {len(requests)}"}),
                        }]
                    }
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": '{"answer":"text result","generated_images":[]}',
                        }],
                    }]
                }

            client.create_tool_response = create
            with (
                patch(
                    "app.model_tool_loop.measure_request_tokens",
                    return_value=SimpleNamespace(estimated_input_tokens=999999),
                ),
                patch("app.model_tool_loop.model_stage_quality_limit", return_value=1),
            ):
                result = ModelToolLoop(client, [_LargeFailingTool()], store).run_json(
                    [{"role": "user", "content": "不可压缩的题干与证据"}],
                    model="fake-vision-model",
                    max_tokens=1000,
                    thinking="medium",
                    timeout=30,
                )

            first_result = next(
                item for item in requests[3] if item.get("type") == "function_call_output"
            )
            self.assertIn("original_sha256", first_result["output"][0]["text"])
            self.assertEqual("不可压缩的题干与证据", requests[3][0]["content"])
            events = [
                json.loads(line)
                for line in Path(result.tool_event_log).read_text(encoding="utf-8").splitlines()
            ]
            compacted = [event for event in events if event["type"] == "history/compacted"]
            self.assertEqual(1, len(compacted))
            self.assertFalse(compacted[0]["core_history_changed"])
            self.assertFalse(compacted[0]["tool_pairing_changed"])


if __name__ == "__main__":
    unittest.main()
