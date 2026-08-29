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

    def test_image_tool_rejects_ambiguous_or_unregistered_reference_selectors(self):
        from app.image_artifacts import ImageArtifactStore
        from app.model_tool_loop import ImageGenerationTool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.png"
            Image.new("RGB", (128, 96), "white").save(source)
            tool = ImageGenerationTool(
                SimpleNamespace(image_model="gpt-image-2", image_size=""),
                "gpt-image-2",
                ImageArtifactStore(root / "artifacts"),
                reference_images=[source],
            )
            with self.assertRaisesRegex(ValueError, "cannot be used together"):
                tool.execute(
                    {
                        "prompt": "edit",
                        "referenced_image_paths": [str(source.resolve())],
                        "num_last_images_to_include": 1,
                    },
                    call_id="both",
                )
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


if __name__ == "__main__":
    unittest.main()
