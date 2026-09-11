from __future__ import annotations

import json
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


def test_runtime_guard_blocks_unavailable_but_explicit_probe_can_recover(isolated_state: Path) -> None:
    for _ in range(3):
        observe(success=False, error="Provider HTTP 503: overloaded")
    with pytest.raises(RuntimeError, match="模型服务"):
        with provider_control.runtime_route_guard(
            provider="vendor_a", api_key="secret-key", model="model-a",
            protocol="responses", capability="text",
        ):
            pass


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
