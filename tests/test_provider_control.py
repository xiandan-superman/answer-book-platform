from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import provider_control


@pytest.fixture()
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "provider-control" / "state.json"
    monkeypatch.setattr(provider_control, "PROVIDER_CONTROL_STATE", target)
    monkeypatch.setattr(provider_control, "is_approved_route", lambda *args, **kwargs: True)
    return target


def observe(*, success: bool, capability: str = "text", error: object = None):
    return provider_control.record_provider_observation(
        provider="vendor_a", model="model-a", protocol="responses",
        capability=capability, api_key="secret-key", success=success,
        elapsed_ms=123, source="task_runtime", error=error,
    )


def test_three_provider_failures_block_only_exact_route_and_success_restores(isolated_state: Path) -> None:
    for expected in ("degraded", "degraded", "unavailable"):
        row = observe(success=False, error="Provider HTTP 503: overloaded")
        assert row["status"] == expected

    sibling = observe(success=False, capability="vision", error="invalid local response schema")
    assert sibling.get("status") is None

    restored = observe(success=True)
    assert restored["status"] == "available"
    assert restored["consecutive_provider_failures"] == 0


def test_authentication_failure_is_configuration_error(isolated_state: Path) -> None:
    row = observe(success=False, error="Provider HTTP 401: invalid api key")
    assert row["status"] == "configuration_error"
    assert row["responsibility"] == "user_or_account_configuration"


def test_platform_or_request_failure_does_not_poison_recovered_route(isolated_state: Path) -> None:
    assert observe(success=True)["status"] == "available"
    row = observe(success=False, error="local JSON schema validation failed")
    assert row["status"] == "available"
    assert row["consecutive_provider_failures"] == 0
    assert row["last_platform_or_request_error"]["kind"] == "provider_error"


def test_state_never_persists_api_key(isolated_state: Path) -> None:
    observe(success=True)
    raw = isolated_state.read_text(encoding="utf-8")
    assert "secret-key" not in raw
    assert provider_control.account_fingerprint("secret-key") in raw


def test_snapshot_separates_catalog_observation_and_effective_policy(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = [{
        "route_id": provider_control.route_id(
            "vendor_a", provider_control.account_fingerprint("secret-key"),
            "model-a", "responses", "text",
        ),
        "provider": "vendor_a", "model": "model-a", "protocol": "responses",
        "capability": "text", "account_fingerprint": provider_control.account_fingerprint("secret-key"),
        "configured": True, "approved": True, "protected": False,
        "production_concurrency": 8,
    }]
    monkeypatch.setattr(provider_control, "approved_catalog", lambda: catalog)
    observe(success=False, error="Provider HTTP 503: overloaded")
    snapshot = provider_control.provider_control_snapshot()
    route = snapshot["routes"][0]
    assert route["approved"] is True
    assert route["status"] == "degraded"
    assert route["effective_allowed"] is True
    assert route["effective_concurrency"] == 1


def test_unconfigured_supported_route_is_neutral_and_not_counted_as_incident(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = [{
        "route_id": provider_control.route_id(
            "vendor_a", "unconfigured", "model-a", "responses", "text",
        ),
        "provider": "vendor_a", "model": "model-a", "protocol": "responses",
        "capability": "text", "account_fingerprint": "unconfigured",
        "configured": False, "approved": True, "protected": False,
        "production_concurrency": 8,
    }]
    monkeypatch.setattr(provider_control, "approved_catalog", lambda: catalog)
    snapshot = provider_control.provider_control_snapshot()
    route = snapshot["routes"][0]
    assert route["status"] == "not_configured"
    assert route["effective_allowed"] is False
    assert route["responsibility"] == "none"
    assert route.get("last_error") is None
    assert snapshot["summary"]["attention_route_count"] == 0
    assert snapshot["summary"]["supported_provider_count"] == 1
    assert snapshot["summary"]["configured_provider_count"] == 0


def test_account_fingerprints_are_isolated(isolated_state: Path) -> None:
    first = provider_control.record_provider_observation(
        provider="vendor_a", model="same", protocol="responses", capability="text",
        api_key="account-one", success=False, error="Provider HTTP 503",
    )
    second = provider_control.record_provider_observation(
        provider="vendor_a", model="same", protocol="responses", capability="text",
        api_key="account-two", success=True,
    )
    state = json.loads(isolated_state.read_text(encoding="utf-8"))
    assert first["route_id"] != second["route_id"]
    assert len(state["routes"]) == 2


def test_runtime_guard_allows_user_selected_unavailable_model(isolated_state: Path) -> None:
    for _ in range(3):
        observe(success=False, error="Provider HTTP 503: overloaded")
    with provider_control.runtime_route_guard(
            provider="vendor_a", api_key="secret-key", model="model-a",
            protocol="responses", capability="text",
    ):
        observe(success=True)
    assert observe(success=True)["status"] == "available"


def test_model_discovery_only_records_candidates_and_never_changes_allowlist(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = SimpleNamespace(
        name="vendor_a", api_key="secret-key", base_url="https://vendor.invalid/v1",
        user_agent="test", model_options=("approved-model",), default_model="approved-model",
        vision_model_options=(), vision_model="", image_model_options=(), image_model="",
    )
    monkeypatch.setattr("app.settings.get_provider", lambda name: provider)

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return b'{"data":[{"id":"approved-model"},{"id":"new-candidate"}]}'

    monkeypatch.setattr(provider_control.urllib.request, "urlopen", lambda request, timeout: Response())
    result = provider_control.discover_provider_models("vendor_a")
    assert result["candidate_models"] == ["new-candidate"]
    assert result["approved_models"] == ["approved-model"]
    persisted = json.loads(isolated_state.read_text(encoding="utf-8"))
    assert persisted["discoveries"][result["discovery_id"]]["candidate_models"] == ["new-candidate"]
    with provider_control.explicit_probe_source("task_preflight"):
        with provider_control.runtime_route_guard(
            provider="vendor_a", api_key="secret-key", model="model-a",
            protocol="responses", capability="text",
        ):
            pass


def test_catalog_does_not_surface_main_model_tool_loop_as_a_status_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SimpleNamespace(
        name="vendor_a", api_key="secret-key", model_options=("vision-model",),
        default_model="vision-model", model_profiles={}, api_protocol="responses",
        supports_text_generation=True, supports_image_generation=True,
        image_model_options=("image-model",), image_model="image-model",
    )
    monkeypatch.setattr("app.settings.list_providers", lambda: {"vendor_a": provider})
    monkeypatch.setattr("app.settings.provider_model_supports_vision", lambda *_args: True)
    monkeypatch.setattr(provider_control, "provider_request_max_concurrency", lambda _provider: 2)

    rows = provider_control.approved_catalog()
    image_edit = next(row for row in rows if row["capability"] == "image_edit")

    assert all(row["capability"] != "tool_call" for row in rows)
    assert provider_control.is_approved_route("vendor_a", "vision-model", "responses", "tool_call") is True
    assert image_edit["approved"] is True
    assert image_edit["manual_probe_only"] is True


def test_tool_capability_is_implicitly_allowed_but_not_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SimpleNamespace(
        name="wawapi_openai", api_key="secret-key", model_options=("gpt-5.6-sol",),
        default_model="gpt-5.6-sol",
        model_profiles={"gpt-5.6-sol": {"supports_tool_calls": True, "api_protocol": "responses"}},
        api_protocol="responses", supports_text_generation=True,
        supports_image_generation=False, image_model_options=(), image_model="",
    )
    monkeypatch.setattr("app.settings.list_providers", lambda: {"wawapi_openai": provider})
    monkeypatch.setattr("app.settings.provider_model_supports_vision", lambda *_args: True)
    monkeypatch.setattr(provider_control, "provider_request_max_concurrency", lambda _provider: 2)

    assert all(row["capability"] != "tool_call" for row in provider_control.approved_catalog())
    assert provider_control.is_approved_route(
        "wawapi_openai", "gpt-5.6-sol", "responses", "tool_call"
    ) is True


def test_due_scheduler_does_not_probe_tool_or_image_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {"configured": True, "protected": False, "capability": "image_generation", "stale": True,
         "last_observed_at": "", "provider": "vendor", "model": "image", "protocol": "images", "onboarding": {"status": "passed"}},
        {"configured": True, "protected": False, "capability": "tool_call", "stale": True,
         "last_observed_at": "", "provider": "vendor", "model": "text", "protocol": "responses",
         "capability_candidate": True},
    ]
    monkeypatch.setattr(provider_control, "provider_control_snapshot", lambda: {"routes": rows})
    seen = []
    monkeypatch.setattr(provider_control, "probe_route", lambda **kwargs: seen.append(kwargs) or {"ok": True})

    result = provider_control.probe_next_due_route()
    assert result["skipped"] is True
    assert seen == []


def test_main_model_policy_enables_backend_and_public_profile_without_probe() -> None:
    from dataclasses import replace

    from app.llm_client import OpenAICompatibleClient
    from app.model_tool_loop import tool_loop_supported
    from app.settings import list_providers

    base = list_providers()["wawapi_openai"]
    profiles = {name: dict(value) for name, value in base.model_profiles.items()}
    profiles["gpt-5.6-sol"] = {
        **profiles.get("gpt-5.6-sol", {}),
        "api_protocol": "responses",
        "supports_tool_calls": False,
    }
    provider = replace(
        base,
        name="runtime_verified_fixture",
        api_key="secret-key",
        api_protocol="responses",
        model_profiles=profiles,
    )
    assert tool_loop_supported(OpenAICompatibleClient(provider), provider, "gpt-5.6-sol")
    public_profile = provider.redacted()["model_profiles"]["gpt-5.6-sol"]
    assert public_profile["supports_tool_calls"] is False
    assert "tool_call_verification" not in public_profile


def test_startup_backfills_each_model_even_when_legacy_health_was_tested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "provider": "already_tested", "account_fingerprint": "a", "configured": True,
            "protected": False, "model": "main", "protocol": "responses", "capability": "text",
            "last_probe_at": "2026-09-12T00:00:00+00:00",
            "history": [{"source": "manual_probe", "success": True}],
        },
        {
            "provider": "new_text", "account_fingerprint": "b", "configured": True,
            "protected": False, "model": "main", "protocol": "responses", "capability": "text",
            "history": [],
        },
        {
            "provider": "new_text", "account_fingerprint": "b", "configured": True,
            "protected": False, "model": "main", "protocol": "responses", "capability": "vision",
            "history": [],
        },
        {
            "provider": "new_text", "account_fingerprint": "b", "configured": True,
            "protected": False, "model": "main", "protocol": "responses", "capability": "tool_call",
            "history": [],
        },
        {
            "provider": "new_image", "account_fingerprint": "c", "configured": True,
            "protected": False, "model": "image", "protocol": "images", "capability": "image_generation",
            "history": [],
        },
    ]
    providers = {
        "already_tested": SimpleNamespace(supports_text_generation=True, default_model="main", image_model=""),
        "new_text": SimpleNamespace(supports_text_generation=True, default_model="main", image_model=""),
        "new_image": SimpleNamespace(supports_text_generation=False, default_model="", image_model="image"),
    }
    monkeypatch.setattr(provider_control, "provider_control_snapshot", lambda: {"routes": rows})
    monkeypatch.setattr("app.settings.list_providers", lambda: providers)
    seen = []
    monkeypatch.setattr(
        provider_control,
        "probe_model_onboarding",
        lambda **kwargs: seen.append(kwargs) or {"ok": True},
    )

    result = provider_control.probe_startup_unverified_providers()

    assert result["tested_provider_count"] == 3
    assert [(row["provider_name"], row["capability"]) for row in seen] == [
        ("already_tested", "text"),
        ("new_text", "text"),
        ("new_image", "image_generation"),
    ]


def test_scheduler_runs_first_time_provider_probe_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = threading.Event()
    monkeypatch.setattr(provider_control, "_SCHEDULER_THREAD", None)
    monkeypatch.setattr(
        provider_control,
        "probe_startup_unverified_providers",
        lambda: called.set() or {"ok": True, "tested_provider_count": 0, "results": []},
    )

    provider_control.start_provider_control_scheduler()
    try:
        assert called.wait(1)
    finally:
        provider_control.stop_provider_control_scheduler()
        thread = provider_control._SCHEDULER_THREAD
        if thread is not None:
            thread.join(timeout=1)


def test_visual_probe_rejects_prompt_only_false_positive(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from app.settings import list_providers

    provider = replace(list_providers()["wawapi_openai"], api_key="secret-key")

    class FakeClient:
        def chat_json_object(self, *_args, **_kwargs):
            return {"ping": "pong"}

    monkeypatch.setattr("app.settings.get_provider", lambda _name: provider)
    monkeypatch.setattr("app.llm_client.create_llm_client", lambda _provider: FakeClient())
    monkeypatch.setattr(
        provider_control, "_visual_challenge",
        lambda: ("data:image/png;base64,fake", {"left": "red", "right": "blue"}),
    )

    result = provider_control.probe_route(
        provider_name="vendor_a", model="model-a", protocol="responses", capability="vision",
    )

    assert result["ok"] is False
    assert result["error_code"] == "probe_validation_failed"
    assert result["route"]["last_platform_or_request_error"]["kind"] == "probe_validation_failed"
    assert result["route"].get("consecutive_provider_failures", 0) == 0


@pytest.mark.parametrize("index", range(3))
def test_visual_challenge_uses_unambiguous_pixels(monkeypatch: pytest.MonkeyPatch, index: int) -> None:
    import base64
    import io
    from PIL import Image

    monkeypatch.setattr(provider_control.time, "time_ns", lambda: index)
    uri, expected = provider_control._visual_challenge()
    image = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    colors = {"red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 255, 0), "yellow": (255, 255, 0)}
    assert image.getpixel((12, 24)) == colors[expected["left"]]
    assert image.getpixel((72, 24)) == colors[expected["right"]]
    assert (245, 176, 25) not in {image.getpixel((12, 24)), image.getpixel((72, 24))}


def test_visual_answer_normalizes_only_harmless_formatting() -> None:
    expected = {"left": "green", "right": "yellow"}
    assert provider_control._visual_answer_matches({"left": " Green ", "right": "YELLOW", "extra": "ok"}, expected)
    for answer in ({"left": "green", "right": "orange"}, {"ping": "pong"},
                   {"left": "yellow", "right": "green"}, {"left": None, "right": "yellow"}, []):
        assert not provider_control._visual_answer_matches(answer, expected)


def test_old_vision_evidence_does_not_expire_model_health(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = [{"route_id": provider_control.route_id("vendor_a", provider_control.account_fingerprint("secret-key"), "model-a", "responses", cap),
                "provider": "vendor_a", "model": "model-a", "protocol": "responses", "capability": cap,
                "account_fingerprint": provider_control.account_fingerprint("secret-key"),
                "configured": True, "approved": True, "protected": False, "production_concurrency": 8}
               for cap in ("text", "vision")]
    monkeypatch.setattr(provider_control, "approved_catalog", lambda: catalog)
    monkeypatch.setattr(provider_control, "_now", lambda: "2020-01-01T00:00:00+00:00")
    observe(success=True, capability="vision")
    monkeypatch.undo()
    monkeypatch.setattr(provider_control, "PROVIDER_CONTROL_STATE", isolated_state)
    monkeypatch.setattr(provider_control, "approved_catalog", lambda: catalog)
    monkeypatch.setattr(provider_control, "is_approved_route", lambda *args, **kwargs: True)
    observe(success=True)
    snapshot = provider_control.provider_control_snapshot()
    assert snapshot["summary"]["healthy_model_count"] == 1
    vision = next(row for row in snapshot["routes"] if row["capability"] == "vision")
    assert vision["confidence"] == "expired"
    assert vision["capability_registration"] == "registered"
    assert vision["model_health_relevant"] is False
    observe(success=False, capability="vision", error="Provider HTTP 503: overloaded")
    assert provider_control.provider_control_snapshot()["summary"]["attention_model_count"] == 1


@pytest.mark.parametrize("success", [True, False])
def test_onboarding_is_one_visual_request_and_health_cannot_rewrite_inputs(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, success: bool,
) -> None:
    from dataclasses import replace
    from app.settings import list_providers

    provider = replace(list_providers()["wawapi_openai"], api_key="secret-key")
    monkeypatch.setattr("app.settings.get_provider", lambda _: provider)
    monkeypatch.setattr("app.settings.provider_model_supports_vision", lambda *_: True)
    calls = []

    class Client:
        def chat_json_object(self, messages, **kwargs):
            calls.append(messages)
            if not success:
                raise RuntimeError("Provider HTTP 503: overloaded")
            return {"left": "red", "right": "blue"}

    monkeypatch.setattr("app.llm_client.create_llm_client", lambda _: Client())
    monkeypatch.setattr(provider_control, "_visual_challenge", lambda: ("data:image/png;base64,fake", {"left": "red", "right": "blue"}))
    kwargs = {"provider_name": provider.name, "model": "model-a", "protocol": "responses"}
    first = provider_control.probe_model_onboarding(**kwargs)
    saved = first["onboarding"]
    assert first["ok"] is success
    assert saved["registered_inputs"] == ["text", "image"]
    assert len(calls) == 1
    assert [part["type"] for part in calls[0][0]["content"]] == ["text", "image_url"]
    for _ in range(3):
        provider_control.record_provider_observation(
            provider=provider.name, model="model-a", protocol="responses", capability="vision",
            api_key="secret-key", success=False, error="Provider HTTP 503: overloaded",
        )
    again = provider_control.probe_model_onboarding(**kwargs)
    assert again["skipped"] is True
    assert again["onboarding"] == saved
    assert len(calls) == 1
    assert "secret-key" not in isolated_state.read_text()


def test_periodic_checks_only_connectivity_after_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"configured": True, "protected": False, "capability": cap,
             "stale": True, "provider": "vendor", "model": "main", "protocol": "responses",
             "onboarding": {"status": "passed"}} for cap in ("text", "vision")]
    monkeypatch.setattr(provider_control, "provider_control_snapshot", lambda: {"routes": rows})
    calls = []
    monkeypatch.setattr(provider_control, "probe_route", lambda **kwargs: calls.append(kwargs) or {"ok": True})
    provider_control.probe_next_due_route()
    assert [call["capability"] for call in calls] == ["text"]


def test_capacity_counts_shared_lingsuan_gate_once(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import concurrency

    providers = {name: SimpleNamespace(name=name, api_key="key", base_url="https://same.invalid",
                 supports_text_generation=True) for name in ("lingsuan_openai", "lingsuan_google")}
    monkeypatch.setattr("app.settings.list_providers", lambda: providers)
    monkeypatch.setattr(concurrency, "_provider_request_limit", lambda _: 8)
    monkeypatch.setattr(concurrency, "_MODEL_REQUEST_GATES", {})
    pools = concurrency.provider_capacity_snapshot()
    assert len(pools) == 1
    assert pools[0]["limit"] == 8
    provider_control.record_capacity_observation(pools[0]["pool_id"], concurrent=4, success=True, pressure=False)
    provider_control.record_capacity_observation(pools[0]["pool_id"], concurrent=5, success=False, pressure=True)
    observed = concurrency.provider_capacity_snapshot()[0]["observations"]
    assert observed["observed_success_concurrency"] == 4
    assert observed["pressure_events"] == 1
    assert observed["samples"] == 2


def test_health_schedule_and_confidence_follow_recent_evidence() -> None:
    from datetime import datetime, timezone

    now = 1800000000.0
    timestamp = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
    events = [{"checked_at": timestamp, "success": True, "source": "task_runtime"} for _ in range(5)]
    record = {"status": "available", "last_observed_at": timestamp, "history": events}
    normal = provider_control.health_evidence(record, now)
    assert normal["confidence"] == "high"
    assert normal["check_interval_seconds"] == provider_control.TEXT_TTL_SECONDS
    assert normal["check_due"] is False
    failed = provider_control.health_evidence({**record, "status": "degraded", "consecutive_provider_failures": 1}, now)
    assert failed["check_interval_seconds"] == 300
    cooling = provider_control.health_evidence({**record, "status": "unavailable", "consecutive_provider_failures": 5}, now)
    assert cooling["check_interval_seconds"] == 3600
    expired = provider_control.health_evidence(record, now + provider_control.TEXT_TTL_SECONDS)
    assert expired["confidence"] == "expired"
    assert expired["check_due"] is True


def test_shared_capacity_recovers_gradually_without_exceeding_safety_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.concurrency import _FairProviderGate

    gate = _FairProviderGate(10)
    monkeypatch.setattr("app.concurrency.random.uniform", lambda *_: 0)
    gate.record_provider_pressure(backoff=(0, 0))
    assert gate.snapshot()["limit"] == 5
    gate.set_limit(10)
    assert gate.snapshot()["limit"] == 5
    gate.record_success()
    assert gate.snapshot()["limit"] == 5
    for _ in range(200):
        gate.record_success()
    assert gate.snapshot()["limit"] == 10
