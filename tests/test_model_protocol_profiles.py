from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import replace

import pytest

from app.llm_client import AnthropicMessagesClient, OpenAICompatibleClient, ResponsesAPIClient
from app.settings import list_providers


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _chat_response(content: str = '{"ok":true}', **message_fields):
    return _Response({
        "choices": [{"finish_reason": "stop", "message": {"content": content, **message_fields}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 2},
    })


def _responses_response(content: str = '{"ok":true}'):
    return _Response({
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}],
        "usage": {"input_tokens": 2, "output_tokens": 2},
    })


def _provider(name: str):
    return replace(list_providers()[name], api_key="test-secret")


def test_provider_model_profiles_keep_supported_models_on_responses():
    providers = list_providers()

    assert providers["bailian"].api_protocol == "responses"
    assert providers["bailian"].model_profiles["qwen-vl-max"]["api_protocol"] == "chat_completions"
    assert "qwen3.7-plus" not in providers["bailian"].model_profiles
    assert providers["ark"].model_profiles["kimi-k2"]["api_protocol"] == "chat_completions"
    assert providers["openrouter"].model_profiles["z-ai/glm-5.2:free"]["api_protocol"] == "chat_completions"


@pytest.mark.parametrize("model", ["qwen3-vl-flash", "qwen-vl-max", "qwen-vl-plus", "qwen-vl-ocr"])
def test_legacy_bailian_vision_models_use_chat_completions(model: str):
    requests = []
    client = OpenAICompatibleClient(_provider("bailian"))
    assert isinstance(client, ResponsesAPIClient)

    def request(url, timeout):
        requests.append(url)
        return _chat_response()

    client._urlopen = request
    result = client.chat_json([{"role": "user", "content": "Return JSON"}], model=model)

    payload = json.loads(requests[0].data)
    assert requests[0].full_url.endswith("/chat/completions")
    assert result.content == '{"ok":true}'
    assert payload["max_tokens"] == (4096 if model == "qwen-vl-ocr" else 24576)


def test_gemini_chat_omits_sampling_and_clamps_disabled_thinking():
    requests = []
    client = OpenAICompatibleClient(_provider("lingsuan_google"))

    def request(url, timeout):
        requests.append(url)
        return _chat_response(reasoning_content="private reasoning")

    client._urlopen = request
    result = client.chat_json(
        [{"role": "user", "content": "Return JSON"}],
        model="gemini-3.6-flash",
        temperature=0.1,
        thinking="disabled",
    )

    payload = json.loads(requests[0].data)
    assert requests[0].full_url.endswith("/chat/completions")
    assert payload["reasoning_effort"] == "minimal"
    assert "thinking" not in payload
    assert "temperature" not in payload
    assert result.content == '{"ok":true}'


def test_deepseek_responses_use_root_endpoint_without_store():
    requests = []
    client = OpenAICompatibleClient(_provider("deepseek"))

    def request(url, timeout):
        requests.append(url)
        return _responses_response()

    client._urlopen = request
    result = client.chat_json([{"role": "user", "content": "Return JSON"}], model="deepseek-v4-flash")

    payload = json.loads(requests[0].data)
    assert requests[0].full_url == "https://api.deepseek.com/responses"
    assert "store" not in payload
    assert result.content == '{"ok":true}'


def test_openrouter_minimax_uses_chat_and_strips_think_blocks():
    requests = []
    client = OpenAICompatibleClient(_provider("openrouter"))

    def request(url, timeout):
        requests.append(url)
        return _chat_response('<think>private analysis</think>{"ok":true}')

    client._urlopen = request
    result = client.chat_json(
        [{"role": "user", "content": "Return JSON"}],
        model="minimax/minimax-m3:free",
    )

    assert requests[0].full_url.endswith("/chat/completions")
    assert result.content == '{"ok":true}'


def test_anthropic_messages_separates_system_thought_and_final_text():
    requests = []
    client = OpenAICompatibleClient(_provider("lingsuan_anthropic"))
    assert isinstance(client, AnthropicMessagesClient)

    def request(url, timeout):
        requests.append(url)
        return _Response({
            "content": [
                {"type": "thinking", "thinking": "private analysis"},
                {"type": "text", "text": '{"ok":true}'},
            ],
            "usage": {"input_tokens": 2, "output_tokens": 2},
        })

    client._urlopen = request
    result = client.chat_json(
        [{"role": "system", "content": "Be accurate"}, {"role": "user", "content": "Return JSON"}],
        model="claude-opus-5",
        thinking="high",
    )

    payload = json.loads(requests[0].data)
    assert requests[0].full_url == "https://lingsuan.top/v1/messages"
    assert requests[0].get_header("Anthropic-version") == "2023-06-01"
    assert payload["system"].startswith("Be accurate")
    assert all(message["role"] != "system" for message in payload["messages"])
    assert payload["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert payload["output_config"]["effort"] == "high"
    assert result.content == '{"ok":true}'


def test_anthropic_messages_fall_back_only_when_gateway_endpoint_is_missing():
    urls = []
    client = OpenAICompatibleClient(_provider("lingsuan_anthropic"))

    def request(url, timeout):
        urls.append(url.full_url)
        if url.full_url.endswith("/messages"):
            raise urllib.error.HTTPError(url.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error":"Not Found"}'))
        return _chat_response()

    client._urlopen = request
    result = client.chat_json([{"role": "user", "content": "Return JSON"}], model="claude-opus-5")

    assert urls == ["https://lingsuan.top/v1/messages", "https://lingsuan.top/v1/chat/completions"]
    assert result.raw["_request"]["protocol_requested"] == "anthropic_messages"


def test_anthropic_uses_official_structured_output_for_confirmed_contract():
    requests = []
    client = OpenAICompatibleClient(_provider("lingsuan_anthropic"))

    def request(url, timeout):
        requests.append(url)
        return _Response({"content": [{"type": "text", "text": '{"exercises":[]}'}]})

    client._urlopen = request
    client.chat_json([{
        "role": "user",
        "content": (
            "只输出合法 JSON。\n\n## 输出结构\n\n"
            '{"exercises":"恰好一项","item_schema":{"batch_index":"整数","stem":"题干",'
            '"knowledge_points":"字符串数组"},"conditional_fields":{"options":{"batch_indexes":[1],'
            '"schema":[{"label":"A","text":"选项"}]}}}'
        ),
    }], model="claude-opus-5")

    output = json.loads(requests[0].data)["output_config"]["format"]
    assert output["type"] == "json_schema"
    item = output["schema"]["properties"]["exercises"]["items"]
    assert item["properties"]["batch_index"] == {"type": "integer"}
    assert item["properties"]["knowledge_points"] == {"type": "array", "items": {"type": "string"}}
    assert "options" in item["properties"]
    assert "options" not in item["required"]


def test_grok_never_sends_disabled_reasoning():
    requests = []
    client = OpenAICompatibleClient(_provider("lingsuan_xai"))

    def request(url, timeout):
        requests.append(url)
        return _responses_response()

    client._urlopen = request
    client.chat_json([{"role": "user", "content": "Return JSON"}], model="grok-4.5", thinking="disabled")

    assert json.loads(requests[0].data)["reasoning"] == {"effort": "low"}
