from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _FakeSSEResponse:
    def __init__(self, events):
        self.lines = iter(
            line.encode("utf-8")
            for event in events
            for line in (f"data: {json.dumps(event)}\n", "\n")
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def readline(self):
        return next(self.lines, b"")


class LLMProtocolAdapterTests(unittest.TestCase):
    def _provider(self, **overrides):
        from app.settings import ProviderConfig

        values = {
            "name": "test",
            "type": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key": "test-key",
            "default_model": "test-model",
            "model_options": (),
            "allow_custom_model": True,
            "model_hint": "",
            "temperature": 0.1,
            "max_tokens": 1024,
        }
        values.update(overrides)
        return ProviderConfig(**values)

    def test_existing_provider_defaults_to_chat_completions(self):
        from app.llm_client import OpenAICompatibleClient, create_llm_client

        client = create_llm_client(self._provider())
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual("chat_completions", client.config.api_protocol)

    def test_all_configured_text_providers_use_responses_without_chat_fallback(self):
        from app.llm_client import OpenAICompatibleClient, ResponsesAPIClient
        from app.settings import get_provider, list_providers

        providers = list_providers()
        self.assertNotIn("openai", providers)
        self.assertNotIn("zhipu", providers)
        # Protocol assertions must not depend on the operator's current local
        # default provider (paid validation may intentionally select Ark).
        self.assertEqual("bailian", get_provider("bailian").name)
        for name, provider in providers.items():
            with self.subTest(provider=name):
                client = OpenAICompatibleClient(provider)
                self.assertEqual("responses", provider.api_protocol)
                self.assertTrue(provider.responses_streaming)
                self.assertFalse(provider.responses_fallback_to_chat)
                self.assertIsInstance(client, ResponsesAPIClient)

    def test_deepseek_exposes_text_and_multimodal_flash_models(self):
        from app.settings import list_providers, provider_model_supports_vision

        provider = list_providers()["deepseek"]
        self.assertEqual(
            ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp"),
            provider.model_options,
        )
        self.assertEqual("deepseek-v4-flash-vision-exp", provider.vision_model)
        self.assertEqual(("deepseek-v4-flash-vision-exp",), provider.vision_model_options)
        self.assertFalse(provider_model_supports_vision(provider, "deepseek-v4-flash"))
        self.assertTrue(provider_model_supports_vision(provider, "deepseek-v4-flash-vision-exp"))

    def test_stale_local_protocol_overrides_cannot_restore_chat_for_builtin_providers(self):
        from app.settings import list_providers

        raw = json.loads((ROOT / "config" / "providers.example.json").read_text(encoding="utf-8"))
        for item in raw["providers"].values():
            item["api_protocol"] = "chat_completions"
            item["responses_fallback_to_chat"] = True
            item["responses_streaming"] = False

        with patch("app.settings.load_provider_config_file", return_value=raw):
            providers = list_providers()

        for name, provider in providers.items():
            with self.subTest(provider=name):
                self.assertEqual("responses", provider.api_protocol)
                self.assertFalse(provider.responses_fallback_to_chat)
                self.assertTrue(provider.responses_streaming)

    def test_unknown_protocol_fails_before_making_a_request(self):
        from app.llm_client import OpenAICompatibleClient

        with self.assertRaisesRegex(ValueError, "Unsupported API protocol"):
            OpenAICompatibleClient(self._provider(api_protocol="unknown"))

    def test_responses_protocol_is_opt_in_and_normalizes_output_text(self):
        from app.llm_client import OpenAICompatibleClient, ResponsesAPIClient, create_llm_client

        client = create_llm_client(self._provider(api_protocol="responses"))
        self.assertIsInstance(client, ResponsesAPIClient)
        self.assertIsInstance(OpenAICompatibleClient(self._provider(api_protocol="responses")), ResponsesAPIClient)
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return _FakeResponse(
                {
                    "id": "resp_test",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"ok":true}'}],
                        }
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            )

        client._urlopen = fake_urlopen
        result = client.chat_json(
            [{"role": "user", "content": "return JSON"}],
            model="test-model",
            max_tokens=512,
        )

        self.assertEqual('{"ok":true}', result.content)
        self.assertEqual("/responses", result.raw["_request"]["endpoint"])
        self.assertEqual(1, len(requests))
        body = json.loads(requests[0][0].data.decode("utf-8"))
        self.assertEqual("test-model", body["model"])
        self.assertEqual(512, body["max_output_tokens"])
        self.assertEqual({"type": "json_object"}, body["text"]["format"])
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertNotIn("temperature", body)
        self.assertTrue(result.raw["_request"]["stream"])
        self.assertEqual("provider_default", result.raw["_request"]["reasoning_effort"])

    def test_responses_stream_accumulates_deltas_and_keeps_final_usage(self):
        from app.llm_client import ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses", thinking_mode="high"))
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data.decode("utf-8")))
            return _FakeSSEResponse(
                [
                    {"type": "response.created", "response": {"id": "resp_stream"}},
                    {"type": "response.output_text.delta", "delta": '{"ok":'},
                    {"type": "response.output_text.delta", "delta": "true}"},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_stream",
                            "object": "response",
                            "status": "completed",
                            "usage": {"input_tokens": 3, "output_tokens": 2},
                            "output": [],
                        },
                    },
                ]
            )

        client._urlopen = fake_urlopen
        result = client.chat_json([{"role": "user", "content": "return JSON"}])

        self.assertEqual('{"ok":true}', result.content)
        self.assertEqual({"effort": "high"}, requests[0]["reasoning"])
        self.assertTrue(requests[0]["stream"])
        self.assertEqual(3, result.raw["usage"]["input_tokens"])
        self.assertEqual("high", result.raw["_request"]["reasoning_effort"])

    def test_responses_stream_salvages_complete_json_without_completed_event(self):
        from app.llm_client import ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        client._urlopen = lambda _request, timeout: _FakeSSEResponse([
            {"type": "response.output_text.delta", "delta": '{"ok":'},
            {"type": "response.output_text.delta", "delta": "true}"},
        ])
        result = client.chat_json([{"role": "user", "content": "return JSON"}])
        self.assertEqual('{"ok":true}', result.content)
        self.assertTrue(result.raw["_stream_salvaged"])

    def test_responses_stream_rejects_partial_json_without_completed_event(self):
        from app.llm_client import LLMError, ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        client._urlopen = lambda _request, timeout: _FakeSSEResponse([
            {"type": "response.output_text.delta", "delta": '{"ok":'},
        ])
        with self.assertRaisesRegex(LLMError, "without a completed response"):
            client.chat_json([{"role": "user", "content": "return JSON"}])

    def test_responses_stream_salvages_output_text_done_event(self):
        from app.llm_client import ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        client._urlopen = lambda _request, timeout: _FakeSSEResponse([
            {"type": "response.output_text.done", "text": '{"ok":true}'},
        ])
        result = client.chat_json([{"role": "user", "content": "return JSON"}])
        self.assertEqual('{"ok":true}', result.content)
        self.assertTrue(result.raw["_stream_salvaged"])

    def test_responses_auto_omits_effort_and_legacy_enabled_means_medium(self):
        from app.llm_client import ResponsesAPIClient

        payloads = []
        client = ResponsesAPIClient(self._provider(api_protocol="responses"))

        def fake_urlopen(request, timeout):
            payloads.append(json.loads(request.data.decode("utf-8")))
            return _FakeResponse({"output_text": '{"ok":true}', "status": "completed"})

        client._urlopen = fake_urlopen
        client.chat_json([{"role": "user", "content": "JSON"}], thinking="auto")
        client.chat_json([{"role": "user", "content": "JSON"}], thinking="enabled")

        self.assertNotIn("reasoning", payloads[0])
        self.assertEqual({"effort": "medium"}, payloads[1]["reasoning"])

    def test_responses_json_mode_injects_required_json_instruction(self):
        from app.llm_client import ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data.decode("utf-8")))
            return _FakeResponse({"output_text": '{"ok":true}', "status": "completed"})

        client._urlopen = fake_urlopen
        client.chat_json([{"role": "user", "content": "Return only {\"ok\":true}"}])

        self.assertEqual("system", requests[0]["input"][0]["role"])
        self.assertIn("JSON", requests[0]["input"][0]["content"])

    def test_responses_adapter_translates_vision_message_parts(self):
        from app.llm_client import ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data.decode("utf-8")))
            return _FakeResponse({"output_text": '{"ok":true}', "status": "completed"})

        client._urlopen = fake_urlopen
        client.chat_json(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "识别图片"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ],
                }
            ],
            model="test-model",
        )

        user_input = next(item for item in requests[0]["input"] if item.get("role") == "user")
        self.assertEqual("input_text", user_input["content"][0]["type"])
        self.assertEqual("input_image", user_input["content"][1]["type"])
        self.assertEqual("data:image/png;base64,abc", user_input["content"][1]["image_url"])

    def test_responses_incomplete_output_retries_and_reports_usage(self):
        from app.llm_client import ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        responses = [
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"ok":false}'}],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 512,
                    "output_tokens_details": {"reasoning_tokens": 200},
                },
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"ok":true}'}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        ]

        def fake_urlopen(request, timeout):
            return _FakeResponse(responses.pop(0))

        client._urlopen = fake_urlopen
        value = client.chat_json_object(
            [{"role": "user", "content": "return JSON"}],
            model="test-model",
            max_tokens=512,
            attempts=2,
        )

        self.assertTrue(value["ok"])
        attempts = client.last_json_retry_report["attempts"]
        self.assertEqual(2, len(attempts))
        self.assertEqual("length", attempts[0]["finish_reason"])
        self.assertEqual(10, attempts[0]["prompt_tokens"])
        self.assertEqual(512, attempts[0]["completion_tokens"])
        self.assertEqual(200, attempts[0]["reasoning_tokens"])
        self.assertEqual("stop", attempts[1]["finish_reason"])

    def test_responses_endpoint_404_falls_back_to_chat_completions(self):
        from app.llm_client import ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        urls = []

        def fake_urlopen(request, timeout):
            urls.append(request.full_url)
            if request.full_url.endswith("/responses"):
                raise urllib.error.HTTPError(
                    request.full_url,
                    404,
                    "Not Found",
                    {},
                    io.BytesIO(b'{"error":"Not Found"}'),
                )
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"ok":true}'},
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }
            )

        client._urlopen = fake_urlopen
        result = client.chat_json(
            [{"role": "user", "content": "return JSON"}],
            model="test-model",
        )

        self.assertEqual(
            ["https://example.test/v1/responses", "https://example.test/v1/chat/completions"],
            urls,
        )
        self.assertEqual('{"ok":true}', result.content)
        self.assertEqual("responses", result.raw["_request"]["protocol_requested"])
        self.assertEqual("chat_completions", result.raw["_request"]["protocol_used"])
        self.assertEqual(
            "responses_endpoint_http_404",
            result.raw["_request"]["protocol_fallback_reason"],
        )

    def test_responses_auth_failure_does_not_fall_back(self):
        from app.llm_client import LLMError, ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        urls = []

        def fake_urlopen(request, timeout):
            urls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                io.BytesIO(b'{"error":"invalid api key"}'),
            )

        client._urlopen = fake_urlopen
        with self.assertRaisesRegex(LLMError, "Provider HTTP 401"):
            client.chat_json(
                [{"role": "user", "content": "return JSON"}],
                model="test-model",
            )

        self.assertEqual(["https://example.test/v1/responses"], urls)

    def test_responses_model_404_does_not_fall_back(self):
        from app.llm_client import LLMError, ResponsesAPIClient

        client = ResponsesAPIClient(self._provider(api_protocol="responses"))
        urls = []

        def fake_urlopen(request, timeout):
            urls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"error":"model not found"}'),
            )

        client._urlopen = fake_urlopen
        with self.assertRaisesRegex(LLMError, "model not found"):
            client.chat_json(
                [{"role": "user", "content": "return JSON"}],
                model="missing-model",
            )

        self.assertEqual(["https://example.test/v1/responses"], urls)

    def test_responses_fallback_can_be_disabled(self):
        from app.llm_client import LLMError, ResponsesAPIClient

        client = ResponsesAPIClient(
            self._provider(
                api_protocol="responses",
                responses_fallback_to_chat=False,
            )
        )
        urls = []

        def fake_urlopen(request, timeout):
            urls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"error":"Not Found"}'),
            )

        client._urlopen = fake_urlopen
        with self.assertRaisesRegex(LLMError, "Provider HTTP 404"):
            client.chat_json(
                [{"role": "user", "content": "return JSON"}],
                model="test-model",
            )

        self.assertEqual(["https://example.test/v1/responses"], urls)

    def test_provider_test_protocol_override_is_temporary(self):
        from app.server import _provider_test_protocol_override

        original = self._provider()
        updated, overridden = _provider_test_protocol_override(
            original,
            {
                "api_protocol": "responses",
                "responses_fallback_to_chat": False,
            },
        )

        self.assertTrue(overridden)
        self.assertEqual("chat_completions", original.api_protocol)
        self.assertEqual("responses", updated.api_protocol)
        self.assertFalse(updated.responses_fallback_to_chat)

    def test_provider_test_protocol_override_rejects_unknown_value(self):
        from app.server import _provider_test_protocol_override

        with self.assertRaisesRegex(ValueError, "Unsupported API protocol"):
            _provider_test_protocol_override(
                self._provider(),
                {"api_protocol": "invalid"},
            )

    def test_provider_test_protocol_summary_reports_fallback(self):
        from app.server import _provider_test_protocol_summary

        summary = _provider_test_protocol_summary(
            {
                "attempts": [
                    {
                        "model": "test-model",
                        "error": "",
                        "protocol_requested": "responses",
                        "protocol_used": "chat_completions",
                        "protocol_fallback_reason": "responses_endpoint_http_404",
                    }
                ]
            },
            "responses",
        )

        self.assertEqual("responses", summary["api_protocol_requested"])
        self.assertEqual("chat_completions", summary["api_protocol_used"])
        self.assertTrue(summary["protocol_fallback"])
        self.assertEqual(
            "responses_endpoint_http_404",
            summary["protocol_fallback_reason"],
        )


if __name__ == "__main__":
    unittest.main()
