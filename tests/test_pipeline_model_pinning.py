from app.pipeline import _pin_text_provider_model, _pin_vision_provider_model
from app.settings import ProviderConfig


def _provider() -> ProviderConfig:
    return ProviderConfig(
        name="test",
        type="openai_compatible",
        base_url="https://example.invalid",
        api_key="",
        default_model="gpt-5.6-sol",
        model_options=("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
        allow_custom_model=False,
        model_hint="",
        temperature=0.1,
        max_tokens=1000,
        supports_vision=True,
        vision_model="gpt-5.6-sol",
        vision_model_options=("gpt-5.6-sol", "gpt-5.6-terra"),
    )


def test_text_role_recovery_cannot_silently_switch_away_from_selected_model() -> None:
    pinned = _pin_text_provider_model(_provider(), "gpt-5.6-terra")

    assert pinned.default_model == "gpt-5.6-terra"
    assert pinned.model_options == ("gpt-5.6-terra",)


def test_vision_role_recovery_is_pinned_to_selected_vision_model() -> None:
    pinned = _pin_vision_provider_model(_provider(), "gpt-5.6-terra")

    assert pinned.default_model == "gpt-5.6-terra"
    assert pinned.model_options == ("gpt-5.6-terra",)
    assert pinned.vision_model == "gpt-5.6-terra"
    assert pinned.vision_model_options == ("gpt-5.6-terra",)
