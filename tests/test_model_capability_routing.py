from __future__ import annotations

from pathlib import Path

from app.pipeline import required_visual_understanding_failures
from app.question_understanding import attach_question_visuals, build_question_understanding
from app.settings import ProviderConfig, _model_supports_vision_cached, provider_model_supports_vision


def _provider(**updates) -> ProviderConfig:
    values = {
        "name": "bailian",
        "type": "openai_compatible",
        "base_url": "https://example.invalid",
        "api_key": "key",
        "default_model": "qwen3.7-plus",
        "model_options": ("qwen3.7-plus", "qwen3.7-max"),
        "model_option_labels": {
            "qwen3.7-plus": "Qwen3.7-Plus 多模态旗舰",
            "qwen3.7-max": "Qwen3.7-Max 文本旗舰",
        },
        "allow_custom_model": True,
        "model_hint": "",
        "temperature": 0.1,
        "max_tokens": 1000,
        "supports_vision": True,
        "vision_model": "qwen3.7-flash",
        "model_capabilities": {
            "qwen3.7-plus": ("text", "vision"),
            "qwen3.7-max": ("text",),
        },
    }
    values.update(updates)
    return ProviderConfig(**values)


def test_model_specific_capabilities_distinguish_multimodal_and_text_models() -> None:
    provider = _provider()

    assert provider_model_supports_vision(provider, "qwen3.7-plus") is True
    assert provider_model_supports_vision(provider, "qwen3.7-max") is False


def test_model_capability_resolution_is_cached_without_remote_probe() -> None:
    provider = _provider()
    _model_supports_vision_cached.cache_clear()

    assert provider_model_supports_vision(provider, "qwen3.7-plus") is True
    assert provider_model_supports_vision(provider, "qwen3.7-plus") is True

    cache = _model_supports_vision_cached.cache_info()
    assert cache.misses == 1
    assert cache.hits == 1


def test_direct_multimodal_understanding_does_not_call_separate_vision_model(tmp_path: Path) -> None:
    image = tmp_path / "question.png"
    image.write_bytes(b"image-bytes")
    question = {"question_id": "q1", "stem": "根据图像回答。", "image_refs": [str(image)]}

    understanding = build_question_understanding(
        question,
        tmp_path / "assets",
        provider=_provider(),
        model="qwen3.7-flash",
        client=object(),  # would fail immediately if a remote call were attempted
        direct_multimodal=("bailian", "qwen3.7-plus"),
    )

    assert understanding["direct_multimodal"] is True
    assert understanding["vision_used"] is False
    assert understanding["visual_delivery"] == "direct_with_answer_request"
    assert required_visual_understanding_failures({"items": [understanding]}) == []


def test_direct_multimodal_prompt_attaches_each_source_image_once(tmp_path: Path) -> None:
    image = tmp_path / "question.png"
    image.write_bytes(b"image-bytes")
    question = {
        "image_refs": [str(image)],
        "question_understanding": {"images": [{"path": str(image)}]},
    }

    messages = attach_question_visuals(
        [{"role": "user", "content": "请解析题目"}],
        question,
    )

    content = messages[0]["content"]
    assert sum(1 for item in content if item.get("type") == "image_url") == 1


def test_text_only_model_does_not_inherit_provider_level_vision_support() -> None:
    provider = _provider(
        vision_model="qwen3.7-plus",
        vision_model_options=("qwen3.7-plus",),
    )

    assert provider.supports_vision is True
    assert provider_model_supports_vision(provider, "qwen3.7-max") is False
