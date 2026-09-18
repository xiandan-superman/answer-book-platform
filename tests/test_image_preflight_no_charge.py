from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import provider_control, settings


@pytest.fixture
def image_provider(monkeypatch):
    monkeypatch.setattr(settings, "load_api_keys", lambda: None)
    monkeypatch.setattr(settings, "load_dotenv", lambda: None)
    return replace(settings.list_providers()["lingsuan_image"], api_key="test-only")


@pytest.mark.parametrize("invalid", ["", "key", "url", "capability", "model", "text_model"])
def test_task_image_preflight_never_calls_model_or_changes_health(image_provider, monkeypatch, invalid):
    provider = image_provider
    model = provider.image_model
    if invalid == "key":
        provider = replace(provider, api_key="")
    elif invalid == "url":
        provider = replace(provider, base_url="")
    elif invalid == "capability":
        provider = replace(provider, supports_image_generation=False)
    elif invalid == "model":
        model = "unregistered-image"
        provider = replace(provider, image_model=model)
    elif invalid == "text_model":
        provider = replace(settings.list_providers()["gemini_smart_router"], api_key="test-only")
        model = provider.default_model

    def forbidden(*args, **kwargs):
        pytest.fail("Task image preflight must not call a model or write health evidence")

    monkeypatch.setattr("app.llm_client.create_llm_client", forbidden)
    monkeypatch.setattr(provider_control, "record_provider_observation", forbidden)
    result = provider_control.probe_route(
        provider_name=provider.name, provider_config=provider, model=model,
        capability="image_generation", source="task_preflight",
    )
    assert result["ok"] is (not invalid)
    assert result["skipped"] is True
    assert result["route"]["verification_status"] == "configuration_only"


def test_explicit_image_probe_still_generates_and_records(image_provider, monkeypatch):
    calls = []

    class Client:
        def generate_image(self, *args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(model=image_provider.image_model)

    observations = []
    monkeypatch.setattr("app.llm_client.create_llm_client", lambda provider: Client())
    monkeypatch.setattr(provider_control, "record_provider_observation", lambda **kwargs: observations.append(kwargs) or {})
    result = provider_control.probe_route(
        provider_name=image_provider.name, provider_config=image_provider,
        model=image_provider.image_model, capability="image_generation", source="manual_probe",
    )
    assert result["ok"] is True
    assert len(calls) == len(observations) == 1
    assert observations[0]["source"] == "manual_probe"
    assert observations[0]["success"] is True


def test_smart_router_preflight_is_delegated_without_local_key(monkeypatch):
    provider = SimpleNamespace(
        name="image_smart_router",
        api_key="",
        image_model="image-smart-router",
        image_model_options=("image-smart-router",),
        supports_image_generation=True,
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Smart-router preflight must not call a local model")

    monkeypatch.setattr("app.llm_client.create_llm_client", forbidden)
    result = provider_control.probe_route(
        provider_name=provider.name,
        provider_config=provider,
        model="image-smart-router",
        capability="image_generation",
        source="task_preflight",
    )
    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["route"]["eligibility"] == "delegated"
