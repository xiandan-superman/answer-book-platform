from __future__ import annotations

import base64
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import smart_gemini_router as router
from app import settings
from app.llm_client import LLMError, OpenAICompatibleClient, ResponsesAPIClient, _provider_request_headers
from app.provider_errors import classify_provider_error


def test_reservation_waits_then_uses_cloudflare_selected_model(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://router.example.workers.dev")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "user-key")
    responses = iter([
        {"reservation_id": "r1", "status": "waiting", "poll_after_ms": 1},
        {"reservation_id": "r1", "status": "ready", "provider": "wawapi_google", "model": "gemini-d", "lease": "lease-1", "thinking_minimum": "medium"},
    ])
    monkeypatch.setattr(router, "_json_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(router.time, "sleep", lambda _seconds: None)

    provider, model, lease, request_id, minimum, protocol = router._reserve_route("text")

    assert (provider, model, lease) == ("wawapi_google", "gemini-d", "lease-1")
    assert request_id
    assert minimum == "medium"
    assert protocol == "chat_completions"


def test_execute_uses_only_cloudflare_reservation_result(monkeypatch) -> None:
    monkeypatch.setattr(router, "_reserve_route", lambda _capability, family="gemini": ("lingsuan_google", f"{family}-a", "lease", "request", "", "chat_completions"))
    candidate = SimpleNamespace(name="lingsuan_google", default_model="gemini-a")
    monkeypatch.setattr(router, "gateway_candidate_config", lambda *_args, **_kwargs: candidate)

    assert router.execute_smart_route("vision", lambda config: (config.name, config.default_model)) == ("lingsuan_google", "gemini-a")


def test_gpt_entry_reserves_the_gpt_family(monkeypatch) -> None:
    captured = {}

    def reserve(capability, *, family="gemini"):
        captured.update(capability=capability, family=family)
        return "lingsuan_openai", "gpt-6-astra", "lease", "request", "", "responses"

    monkeypatch.setattr(router, "_reserve_route", reserve)
    candidate = SimpleNamespace(name="lingsuan_openai", default_model="gpt-6-astra")
    monkeypatch.setattr(router, "gateway_candidate_config", lambda *_args, **_kwargs: candidate)

    result = router.execute_smart_route(
        "text",
        lambda config: (config.name, config.default_model),
        virtual_provider="gpt_smart_router",
    )

    assert result == ("lingsuan_openai", "gpt-6-astra")
    assert captured == {"capability": "text", "family": "gpt"}


def test_image_entry_reserves_the_image_family(monkeypatch) -> None:
    captured = {}

    def reserve(capability, *, family="gemini"):
        captured.update(capability=capability, family=family)
        return "wawapi_image_openai", "gpt-image-2", "lease", "request", "", "chat_completions"

    monkeypatch.setattr(router, "_reserve_route", reserve)
    candidate = SimpleNamespace(name="wawapi_image_openai", default_model="gpt-image-2")
    monkeypatch.setattr(router, "gateway_candidate_config", lambda *_args, **_kwargs: candidate)

    result = router.execute_smart_route(
        "image_generation", lambda config: (config.name, config.default_model),
        virtual_provider="image_smart_router",
    )

    assert result == ("wawapi_image_openai", "gpt-image-2")
    assert captured == {"capability": "image_generation", "family": "image"}


def test_image_router_reports_actual_route_after_generation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "load_api_keys", lambda: None)
    monkeypatch.setattr(settings, "load_dotenv", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "test-only")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://router.example.workers.dev")
    virtual = settings.list_providers()["image_smart_router"]
    candidate = replace(
        virtual, name="cloudflare_smart_router", base_url="https://router.example.workers.dev/v1",
        default_model="gpt-image-2", image_model="gpt-image-2", smart_router_lease="lease",
        smart_router_request_id="request",
    )
    monkeypatch.setattr(
        router, "execute_smart_route",
        lambda capability, operation, *, virtual_provider: operation(candidate),
    )
    monkeypatch.setattr(
        OpenAICompatibleClient,
        "_post_json",
        lambda *_args, **_kwargs: {
            "data": [{"b64_json": base64.b64encode(b"valid-image-bytes").decode()}],
            "_smart_route": {"provider": "wawapi_image_openai", "model": "gpt-image-2"},
        },
    )

    result = OpenAICompatibleClient(virtual).generate_image("draw", tmp_path / "result.png")

    assert result.provider == "wawapi_image_openai"
    assert result.model == "gpt-image-2"
    assert result.path.read_bytes() == b"valid-image-bytes"


def test_router_requires_https_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "http://router.invalid")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "user-key")
    with pytest.raises(router.SmartGeminiRoutingError, match="HTTPS"):
        router._router_settings()


def test_router_requests_use_explicit_user_agent(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok":true}'

    def open_request(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(router.urllib.request, "urlopen", open_request)

    assert router._json_request("https://router.example.workers.dev/status", "user-key") == {"ok": True}
    assert captured["headers"]["User-agent"] == router.SMART_ROUTER_USER_AGENT


def test_gateway_candidate_uses_explicit_user_agent(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://router.example.workers.dev")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "user-key")

    candidate = router.gateway_candidate_config("lingsuan_google", "gemini-a", "lease", "request", "")

    assert candidate.user_agent == router.SMART_ROUTER_USER_AGENT


def test_gpt_gateway_candidate_uses_responses_without_streaming(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_URL", "https://router.example.workers.dev")
    monkeypatch.setenv("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "user-key")

    candidate = router.gateway_candidate_config(
        "lingsuan_openai", "gpt-6-astra", "lease", "request", "", "responses",
        virtual_provider="gpt_smart_router",
    )

    assert candidate.api_protocol == "responses"
    assert candidate.model_profiles["gpt-6-astra"]["api_protocol"] == "responses"
    assert candidate.responses_streaming is False
    assert isinstance(OpenAICompatibleClient(candidate), ResponsesAPIClient)


def test_smart_router_headers_carry_access_key_and_reservation() -> None:
    config = SimpleNamespace(
        api_key="user-access-key", cloudflare_gateway_enabled=False,
        cloudflare_gateway_token="", user_agent="",
        smart_router_lease="lease-1", smart_router_request_id="request-1",
    )

    headers = _provider_request_headers(config)

    assert headers["Authorization"] == "Bearer user-access-key"
    assert headers["x-smart-router-lease"] == "lease-1"
    assert headers["x-smart-router-request-id"] == "request-1"


def test_exhausted_routes_have_safe_user_facing_attempt_summary() -> None:
    error = LLMError("Provider HTTP 503")
    error.status_code = 503
    error.smart_route_attempts = [
        {"provider": "lingsuan", "model": "gemini-a", "status": "503"},
        {"provider": "waw", "model": "gemini-d", "status": "429"},
    ]

    info = classify_provider_error(error)

    assert info.kind == "smart_route_exhausted"
    assert "lingsuan / gemini-a：503" in info.message
    assert "任务本身没有异常" in info.message
