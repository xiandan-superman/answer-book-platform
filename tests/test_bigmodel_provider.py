from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"reasoning_content": "private", "content": '{"ping":"pong"}'},
            }],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        }).encode("utf-8")


def _provider():
    from app.settings import list_providers

    with patch("app.settings.CONFIG_DIR", ROOT / "config"):
        return list_providers()["bigmodel"]


def test_bigmodel_exposes_only_glm53_flash_with_vision() -> None:
    from app.settings import provider_model_supports_vision, provider_supports_image_generation

    provider = _provider()

    assert provider.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert provider.api_protocol == "chat_completions"
    assert provider.api_key_env == "ZAI_API_KEY"
    assert provider.default_model == "glm-5.3-flash"
    assert provider.model_options == ("glm-5.3-flash",)
    assert provider.vision_model == "glm-5.3-flash"
    assert provider.vision_model_options == ("glm-5.3-flash",)
    assert provider_model_supports_vision(provider, "glm-5.3-flash")
    assert not provider_supports_image_generation(provider)
    assert provider.image_model == ""
    assert provider.thinking_mode == "xhigh"


def test_bigmodel_chat_uses_required_thinking_and_documented_sampling() -> None:
    from app.llm_client import OpenAICompatibleClient

    requests = []
    client = OpenAICompatibleClient(replace(_provider(), api_key="test-secret"))

    def request(value, timeout):
        requests.append(value)
        return _Response()

    client._urlopen = request
    result = client.chat_json(
        [{"role": "user", "content": "Return JSON"}],
        model="glm-5.3-flash",
        thinking="disabled",
    )

    payload = json.loads(requests[0].data)
    assert requests[0].full_url == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert payload["thinking"] == {"type": "enabled", "clear_thinking": False}
    assert payload["reasoning_effort"] == "low"
    assert payload["temperature"] == 1.0
    assert payload["top_p"] == 0.95
    assert payload["response_format"] == {"type": "json_object"}
    assert result.content == '{"ping":"pong"}'


def test_bigmodel_frontend_label_and_key_slot_are_available() -> None:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'bigmodel: "ZAI_API_KEY"' in source
    assert 'bigmodel: "智谱 BigModel"' in source
