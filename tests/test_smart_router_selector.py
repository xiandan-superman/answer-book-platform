import json
import subprocess
from pathlib import Path

from app import settings
from app.llm_client import create_llm_client
from app.model_capability_registry import get_model_capability, load_model_capability_registry


def test_virtual_router_is_not_physical_verification_evidence():
    registry = load_model_capability_registry()
    for provider, model in (
        ("gemini_smart_router", "gemini-smart-router"),
        ("gpt_smart_router", "gpt-smart-router"),
        ("image_smart_router", "image-smart-router"),
    ):
        assert provider not in registry["providers"]
        profile = get_model_capability(provider, model)
        assert profile["last_verified_at"] == ""
        if provider == "image_smart_router":
            assert profile["thinking"] == "not_applicable"
            assert profile["task_support"]["image_generation"] == "limited"
        else:
            assert profile["thinking"] == "delegated_to_candidate"
            assert profile["task_support"]["image_generation"] == "forbidden"


def test_public_router_config_passes_real_shared_frontend_selector(monkeypatch):
    monkeypatch.setattr(settings, "load_api_keys", lambda: None)
    monkeypatch.setattr(settings, "load_dotenv", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "test-only")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://router.example.workers.dev")
    configs = {
        name: settings.list_providers()[name].redacted()
        for name in ("gemini_smart_router", "gpt_smart_router", "image_smart_router")
    }
    source = (Path(__file__).resolve().parents[1] / "web/app.js").read_text()
    shared = source[source.index("const TASK_SUPPORT_OK"):source.index("function providerEntriesByCapability")]
    options = source[source.index("function taskModelOptions("):source.index("function modelFamilyName(")]
    script = shared + options + "\nconst configs = " + json.dumps(configs) + """;
    const assert = require('node:assert/strict');
    function userVisibleProviderEntries() { return Object.entries(configs); }
    for (const [provider, cfg] of Object.entries(configs).filter(([provider]) => provider !== 'image_smart_router')) {
      const model = cfg.default_model;
      for (const purpose of ['', 'reasoning', 'correctness', 'practice', 'knowledge']) {
        assert.deepEqual(taskModelOptions('text', cfg, purpose), [model]);
      }
      assert.deepEqual(taskModelOptions('vision', cfg), [model]);
      assert.equal(registeredModelSupportsKind(cfg, model, 'image'), false);
    }
    assert.equal(configuredTaskProviderEntries('text', 'knowledge').length, 2);
    assert.deepEqual(taskModelOptions('image', configs.image_smart_router, 'image_generation'), ['image-smart-router']);
    assert.equal(configuredTaskProviderEntries('image', 'image_generation').length, 1);
    configs.gemini_smart_router.api_key_set = false;
    configs.gpt_smart_router.api_key_set = false;
    assert.equal(configuredTaskProviderEntries('text', 'knowledge').length, 0);
    configs.image_smart_router.api_key_set = false;
    assert.equal(configuredTaskProviderEntries('image', 'image_generation').length, 0);
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_gpt_virtual_router_reports_responses_and_keeps_router_client(monkeypatch):
    monkeypatch.setattr(settings, "load_api_keys", lambda: None)
    monkeypatch.setattr(settings, "load_dotenv", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "test-only")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://router.example.workers.dev")

    provider = settings.list_providers()["gpt_smart_router"]

    assert provider.api_protocol == "responses"
    assert provider.model_profiles["gpt-smart-router"]["api_protocol"] == "responses"
    assert type(create_llm_client(provider)).__name__ == "SmartGeminiClient"


def test_image_virtual_router_is_image_only_and_uses_shared_gateway(monkeypatch):
    monkeypatch.setattr(settings, "load_api_keys", lambda: None)
    monkeypatch.setattr(settings, "load_dotenv", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "test-only")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://router.example.workers.dev")

    provider = settings.list_providers()["image_smart_router"]

    assert provider.supports_text_generation is False
    assert provider.supports_image_generation is True
    assert provider.image_model == "image-smart-router"
    assert provider.redacted()["gateway_ready"] is True
    assert type(create_llm_client(provider)).__name__ == "SmartGeminiClient"
