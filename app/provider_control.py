from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import DATA_ROOT
from .provider_errors import ProbeValidationError, ProviderErrorInfo, classify_provider_error
from .runtime_capacity import provider_request_max_concurrency

STATE_VERSION = 1
FAILURE_THRESHOLD = 3
MAX_HISTORY_PER_ROUTE = 30
TEXT_TTL_SECONDS = 5 * 60 * 60
VISION_TTL_SECONDS = 24 * 60 * 60
TOOL_CALL_TTL_SECONDS = 24 * 60 * 60
IMAGE_TTL_SECONDS = 24 * 60 * 60
PROTECTED_PROVIDERS: frozenset[str] = frozenset()
PROVIDER_CONTROL_STATE = DATA_ROOT / "provider_control" / "state.json"

_LOCK = threading.RLock()
_PROBE_CONTEXT = threading.local()
_SCHEDULER_STOP = threading.Event()
_SCHEDULER_THREAD: threading.Thread | None = None
_ROUTE_CONDITION = threading.Condition()
_ROUTE_ACTIVE: dict[str, int] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def account_fingerprint(api_key: str) -> str:
    key = str(api_key or "").strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else "unconfigured"


def route_id(provider: str, account: str, model: str, protocol: str, capability: str) -> str:
    identity = "\x1f".join((provider, account, model, protocol, capability))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "updated_at": _now(), "routes": {}, "discoveries": {}, "onboarding": {}}


def model_onboarding_record(provider: str, api_key: str, model: str, protocol: str) -> dict[str, Any]:
    identity = route_id(provider, account_fingerprint(api_key), model, protocol, "onboarding")
    with _LOCK:
        return dict((_read_state().get("onboarding") or {}).get(identity) or {})


def record_capacity_observation(pool_id: str, *, concurrent: int, success: bool, pressure: bool) -> None:
    with _LOCK:
        state = _read_state()
        record = state.setdefault("capacity", {}).setdefault(pool_id, {})
        record["samples"] = int(record.get("samples") or 0) + 1
        record["successes"] = int(record.get("successes") or 0) + int(success)
        record["pressure_events"] = int(record.get("pressure_events") or 0) + int(pressure)
        if success:
            record["observed_success_concurrency"] = max(int(record.get("observed_success_concurrency") or 0), concurrent)
        if pressure:
            record["last_pressure_concurrency"] = concurrent
        record["updated_at"] = _now()
        _write_state(state)


def capacity_observations() -> dict[str, Any]:
    with _LOCK:
        return dict(_read_state().get("capacity") or {})


def probe_model_onboarding(
    *, provider_name: str, model: str, protocol: str = "", api_key: str = "",
    capability: str = "text", source: str = "onboarding_probe",
    thinking_mode: str = "",
    provider_config: Any = None,
) -> dict[str, Any]:
    """Persist one attempt per account/model/protocol, independent of route health.

    Claim before network I/O so concurrent requests or a restart cannot duplicate
    the paid test. An interrupted/failed attempt never changes registered inputs.
    """
    from .settings import get_provider, provider_model_supports_vision

    provider = provider_config if provider_config is not None else get_provider(provider_name)
    key = str(api_key or provider.api_key or "").strip()
    protocol = str(protocol or _protocol_for(provider, model)).strip().lower()
    if not key:
        return {"ok": False, "skipped": True, "reason": "请先配置 API Key"}
    selected = "image_generation" if capability == "image_generation" else (
        "vision" if provider_model_supports_vision(provider, model) else "text"
    )
    identity = route_id(provider.name, account_fingerprint(key), model, protocol, "onboarding")
    record = {
        "provider": provider.name, "model": model, "protocol": protocol,
        "account_fingerprint": account_fingerprint(key), "started_at": _now(),
        "status": "started", "capability": selected,
        "registered_inputs": ["text", "image"] if selected == "vision" else ["text"],
        "kind": "image" if selected == "image_generation" else "text", "source": source,
    }
    with _LOCK:
        state = _read_state()
        records = state.setdefault("onboarding", {})
        if identity in records:
            existing = dict(records[identity])
            return {"ok": existing.get("status") == "passed", "skipped": True, "onboarding": existing}
        records[identity] = record
        _write_state(state)
    result = probe_route(
        provider_name=provider.name, model=model, protocol=protocol,
        capability=selected, source=source, api_key=key,
        thinking_mode=thinking_mode,
        provider_config=provider,
    )
    record.update({"status": "passed" if result.get("ok") else "failed", "completed_at": _now()})
    if not result.get("ok"):
        record["error"] = {name: result.get(name) for name in ("error", "error_title", "error_code", "suggested_action")}
    with _LOCK:
        state = _read_state()
        state.setdefault("onboarding", {})[identity] = record
        _write_state(state)
    return {**result, "onboarding": record}


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(PROVIDER_CONTROL_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("routes"), dict):
        return _empty_state()
    return data


def _write_state(data: dict[str, Any]) -> None:
    data["version"] = STATE_VERSION
    data["updated_at"] = _now()
    PROVIDER_CONTROL_STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix="provider-control-", suffix=".json", dir=PROVIDER_CONTROL_STATE.parent)
    temp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, PROVIDER_CONTROL_STATE)
    finally:
        temp_path.unlink(missing_ok=True)


def _protocol_for(provider: Any, model: str) -> str:
    profile = dict((getattr(provider, "model_profiles", {}) or {}).get(model, {}))
    return str(profile.get("api_protocol") or getattr(provider, "api_protocol", "chat_completions") or "chat_completions").strip().lower()


def _is_capability_probe_source(source: str) -> bool:
    value = str(source or "").strip().lower()
    return value in {
        "connection_test",
        "manual_probe",
        "scheduled_probe",
        "startup_probe",
        "test_probe",
        "onboarding_probe",
    }


def _capability_ttl(capability: str, *, approved: bool = True) -> int:
    if capability == "vision":
        return VISION_TTL_SECONDS
    if capability == "tool_call":
        return TOOL_CALL_TTL_SECONDS
    if capability in {"image_generation", "image_edit"}:
        return IMAGE_TTL_SECONDS
    return TEXT_TTL_SECONDS


def approved_catalog() -> list[dict[str, Any]]:
    from .settings import list_providers, provider_model_supports_vision

    rows: list[dict[str, Any]] = []
    for provider in list_providers().values():
        account = account_fingerprint(provider.api_key)
        if getattr(provider, "supports_text_generation", True):
            models = tuple(dict.fromkeys((*provider.model_options, provider.default_model)))
            for model in filter(None, models):
                protocol = _protocol_for(provider, model)
                capabilities = ["text"]
                if provider_model_supports_vision(provider, model):
                    capabilities.append("vision")
                for capability in dict.fromkeys(capabilities):
                    rows.append({
                        "route_id": route_id(provider.name, account, model, protocol, capability),
                        "provider": provider.name,
                        "model": model,
                        "protocol": protocol,
                        "capability": capability,
                        "account_fingerprint": account,
                        "configured": account != "unconfigured",
                        "approved": True,
                        "capability_candidate": False,
                        "approval_source": "registry",
                        "protected": provider.name in PROTECTED_PROVIDERS,
                        "production_concurrency": provider_request_max_concurrency(provider),
                    })
        if getattr(provider, "supports_image_generation", False):
            models = tuple(dict.fromkeys((*provider.image_model_options, provider.image_model)))
            for model in filter(None, models):
                protocol = _protocol_for(provider, model)
                rows.append({
                    "route_id": route_id(provider.name, account, model, protocol, "image_generation"),
                    "provider": provider.name,
                    "model": model,
                    "protocol": protocol,
                    "capability": "image_generation",
                    "account_fingerprint": account,
                    "configured": account != "unconfigured",
                    "approved": True,
                    "protected": provider.name in PROTECTED_PROVIDERS,
                    "production_concurrency": provider_request_max_concurrency(provider),
                })
                rows.append({
                    "route_id": route_id(provider.name, account, model, protocol, "image_edit"),
                    "provider": provider.name,
                    "model": model,
                    "protocol": protocol,
                    "capability": "image_edit",
                    "account_fingerprint": account,
                    "configured": account != "unconfigured",
                    "approved": True,
                    "protected": provider.name in PROTECTED_PROVIDERS,
                    "production_concurrency": provider_request_max_concurrency(provider),
                    "manual_probe_only": True,
                })
    return rows


def is_approved_route(provider: str, model: str, protocol: str, capability: str) -> bool:
    if capability == "tool_call":
        return True
    return any(
        row["provider"] == provider and row["model"] == model and
        row["protocol"] == protocol and row["capability"] == capability and row.get("approved") is True
        for row in approved_catalog()
    )


def _responsibility(info: ProviderErrorInfo) -> str:
    if info.requires_configuration or info.kind in {"provider_quota_exhausted", "provider_permission"}:
        return "user_or_account_configuration"
    if info.kind in {
        "provider_concurrency_limit", "provider_conflict", "provider_gateway_client_blocked",
        "provider_internal_error", "provider_network", "provider_overloaded",
        "provider_rate_limit", "provider_route_degraded", "provider_route_pool_unavailable",
        "provider_timeout",
    }:
        return "provider_service"
    return "platform_or_request"


def record_provider_observation(
    *, provider: str, model: str, protocol: str, capability: str,
    api_key: str = "", success: bool, elapsed_ms: int = 0,
    source: str = "task_runtime", error: Any = None, task_id: str = "",
) -> dict[str, Any]:
    account = account_fingerprint(api_key)
    rid = route_id(provider, account, model, protocol or "unknown", capability or "text")
    info = classify_provider_error(error) if error is not None else None
    responsibility = "none" if success else _responsibility(info) if info else "platform_or_request"
    event = {
        "checked_at": _now(), "success": bool(success), "elapsed_ms": max(0, int(elapsed_ms or 0)),
        "source": str(source or "task_runtime"), "task_id": str(task_id or "")[:120],
        "responsibility": responsibility,
    }
    if info:
        event["error"] = {
            "kind": info.kind, "title": info.title, "message": info.message,
            "suggested_action": info.suggested_action, "status_code": info.status_code,
            "failure_state": info.failure_state,
        }
    with _LOCK:
        state = _read_state()
        routes = state.setdefault("routes", {})
        current = dict(routes.get(rid) or {})
        current.update({
            "route_id": rid, "provider": provider, "model": model,
            "protocol": protocol or "unknown", "capability": capability or "text",
            "account_fingerprint": account, "last_observed_at": event["checked_at"],
        })
        history = list(current.get("history") or [])
        history.append(event)
        current["history"] = history[-MAX_HISTORY_PER_ROUTE:]
        current["observation_count"] = int(current.get("observation_count") or 0) + 1
        if _is_capability_probe_source(source):
            current.update({
                "last_probe_at": event["checked_at"],
                "last_probe_source": event["source"],
                "last_probe_success": bool(success),
            })
        if success:
            current.update({
                "status": "available", "consecutive_provider_failures": 0,
                "last_success_at": event["checked_at"], "last_latency_ms": event["elapsed_ms"],
                "last_error": None, "responsibility": "none",
                "last_platform_or_request_error": None,
            })
        elif responsibility != "platform_or_request":
            failures = int(current.get("consecutive_provider_failures") or 0) + 1
            status = "configuration_error" if responsibility == "user_or_account_configuration" else (
                "unavailable" if failures >= FAILURE_THRESHOLD else "degraded"
            )
            current.update({
                "status": status, "consecutive_provider_failures": failures,
                "last_failure_at": event["checked_at"], "last_error": event.get("error"),
                "responsibility": responsibility,
            })
        else:
            current["last_platform_or_request_error"] = event.get("error")
        routes[rid] = current
        _write_state(state)
        return dict(current)


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))]


def health_evidence(record: dict[str, Any], now: float) -> dict[str, Any]:
    history = [event for event in record.get("history", [])
               if now - _timestamp(event.get("checked_at", "")) <= 24 * 3600]
    age = max(0, now - _timestamp(record.get("last_observed_at", "")))
    status = record.get("status", "unverified")
    failures = int(record.get("consecutive_provider_failures") or 0)
    interval = 3600 if status == "configuration_error" else (
        min(3600, 300 * 2 ** min(4, max(0, failures - 1))) if failures else TEXT_TTL_SECONDS
    )
    # A recovery success gets a short observation window before normal cadence.
    if not failures and any(not event.get("success") for event in history[-5:]):
        interval = 600
    last = _timestamp(record.get("last_observed_at", ""))
    runtime = sum(event.get("source") == "task_runtime" for event in history)
    confidence = "expired" if last and age > interval else "high" if len(history) >= 5 and runtime >= 3 else "medium" if len(history) >= 3 else "low"
    return {
        "confidence": confidence, "sample_count": len(history), "runtime_sample_count": runtime,
        "success_rate": round(100 * sum(bool(event.get("success")) for event in history) / len(history), 1) if history else None,
        "evidence_source": history[-1].get("source", "") if history else "",
        "check_interval_seconds": interval, "next_check_at": datetime.fromtimestamp(last + interval, timezone.utc).isoformat() if last else "",
        "check_due": not last or age >= interval,
    }


def provider_control_snapshot() -> dict[str, Any]:
    with _LOCK:
        state = _read_state()
    observed = state.get("routes") or {}
    routes: list[dict[str, Any]] = []
    now = time.time()
    for catalog in approved_catalog():
        record = dict(observed.get(catalog["route_id"]) or {})
        history = list(record.get("history") or [])[-10:]
        status = (
            str(record.get("status") or "unverified")
            if catalog["configured"] else "not_configured"
        )
        if not catalog["configured"] and not record.get("last_error"):
            record["responsibility"] = "none"
        ttl = _capability_ttl(catalog["capability"], approved=bool(catalog.get("approved")))
        stale = bool(record.get("last_observed_at") and now - _timestamp(record["last_observed_at"]) > ttl)
        latencies = [int(item.get("elapsed_ms") or 0) for item in history if item.get("success") and item.get("elapsed_ms")]
        blocked = not bool(catalog.get("approved")) or not catalog["configured"]
        eligibility = "blocked" if blocked else "warning" if stale or status != "available" else "allowed"
        verification_status = (
            "not_configured" if not catalog["configured"] else
            "never_tested" if not record.get("last_probe_at") else
            "expired" if stale else
            "passed" if record.get("last_probe_success", record.get("last_probe_at") == record.get("last_success_at")) else
            "failed"
        )
        effective_allowed = not blocked
        base_concurrency = int(catalog.get("production_concurrency") or 0)
        effective_concurrency = (
            0 if not effective_allowed else
            1 if status in {"degraded", "unavailable", "configuration_error"} else base_concurrency
        )
        routes.append({
            **catalog, **{key: value for key, value in record.items() if key != "history"},
            "status": status,
            "health_status": status,
            # Vision registration is durable; only actual vision incidents affect
            # model health. Its old onboarding timestamp cannot expire text health.
            "model_health_relevant": catalog["capability"] in {"text", "image_generation"} or (
                catalog["capability"] == "vision" and status in {"degraded", "unavailable", "configuration_error"}
            ),
            "capability_registration": "registered" if catalog.get("approved") else "candidate",
            "input_verification": (
                "passed" if record.get("last_probe_success") else
                "failed" if record.get("last_probe_at") else "never_tested"
            ),
            "verification_status": verification_status,
            "eligibility": eligibility,
            "stale": stale,
            "effective_allowed": effective_allowed,
            "effective_concurrency": effective_concurrency,
            "p50_latency_ms": _percentile(latencies, .5), "p95_latency_ms": _percentile(latencies, .95),
            "history": list(reversed(history)),
            **health_evidence(record, now),
            "onboarding": dict((state.get("onboarding") or {}).get(route_id(
                catalog["provider"], catalog["account_fingerprint"], catalog["model"], catalog["protocol"], "onboarding",
            )) or {}),
            "model_activity": {
                key: value for key, value in (observed.get(route_id(
                    catalog["provider"], catalog["account_fingerprint"], catalog["model"], catalog["protocol"], "tool_call",
                )) or {}).items()
                if key in {"status", "last_error", "last_observed_at", "last_platform_or_request_error"}
            },
        })
    counts: dict[str, int] = {}
    for row in routes:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    configured_routes = [row for row in routes if row["configured"] and row.get("approved")]
    verified_routes = [row for row in configured_routes if row.get("last_observed_at")]
    available_verified = [row for row in verified_routes if row["status"] == "available"]
    attention_routes = [
        row for row in configured_routes
        if row["status"] in {"degraded", "unavailable", "configuration_error"}
    ]
    capability_candidates = [row for row in routes if row["configured"] and row.get("capability_candidate")]
    detected_candidates = [row for row in capability_candidates if row["status"] == "candidate_detected"]
    onboarding_targets = [row for row in routes if row["configured"] and row["capability"] in {"text", "image_generation"}]
    startup_probe_pending_provider_count = sum(not row.get("onboarding") for row in onboarding_targets)
    configured_providers = {row["provider"] for row in configured_routes}
    configured_models = {(row["provider"], row["model"]) for row in configured_routes}
    supported_models = {(row["provider"], row["model"]) for row in routes}
    from .concurrency import provider_capacity_snapshot

    capacity = provider_capacity_snapshot()
    text_pools = [pool for pool in capacity if pool["supports_text"]]
    effective_concurrency = sum(int(pool["limit"]) for pool in text_pools)
    model_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in configured_routes:
        if row["model_health_relevant"]:
            model_groups.setdefault((row["provider"], row["model"], row["protocol"]), []).append(row)
    healthy_models = sum(all(row["status"] == "available" and row["confidence"] != "expired" for row in group) for group in model_groups.values())
    attention_models = sum(any(row["status"] in {"degraded", "unavailable", "configuration_error"} for row in group) for group in model_groups.values())
    return {
        "schema_version": "answer_book.provider_control.v1", "generated_at": _now(),
        "state_updated_at": str(state.get("updated_at") or ""),
        "failure_threshold": FAILURE_THRESHOLD, "counts": counts, "routes": routes,
        "capacity_pools": capacity,
        "summary": {
            "supported_provider_count": len({row["provider"] for row in routes}),
            "configured_provider_count": len(configured_providers),
            "supported_model_count": len(supported_models),
            "configured_model_count": len(configured_models),
            "healthy_model_count": healthy_models,
            "attention_model_count": attention_models,
            "verified_route_count": len(verified_routes),
            "attention_route_count": len(attention_routes),
            "capability_candidate_count": len(capability_candidates),
            "detected_capability_candidate_count": len(detected_candidates),
            "startup_probe_pending_provider_count": startup_probe_pending_provider_count,
            "startup_probe_target_count": len(onboarding_targets),
            "available_rate": round(len(available_verified) * 100 / len(verified_routes), 1) if verified_routes else None,
            "effective_text_concurrency": effective_concurrency,
            "uncapped_text_pool_count": sum(int(pool["configured_limit"]) <= 0 for pool in text_pools),
        },
        "discoveries": [record for record in (state.get("discoveries") or {}).values()
                        if (record.get("provider"), record.get("account_fingerprint"))
                        in {(row["provider"], row["account_fingerprint"]) for row in routes if row["configured"]}],
        "policies": {
            "identity_scope": "provider+account_fingerprint+model+protocol+capability",
            "recovery": "one successful exact probe restores immediately",
            "active_health_interval_hours": TEXT_TTL_SECONDS // 3600,
            "input_verification": "once_per_account_model_protocol",
            "image_active_probe": "first_startup_probe_then_manual",
            "image_edit_active_probe": "manual_only",
            "discovery": "candidate_model_ids_require_manual_approval",
            "automatic_mutations": [
                "exact_route_capability_registration",
                "exact_route_health_warning",
                "exact_route_immediate_restore",
            ],
            "forbidden_mutations": ["add_model", "delete_model", "switch_protocol", "cross_account_poisoning"],
        },
    }


def discover_provider_models(provider_name: str, *, source: str = "scheduled_discovery") -> dict[str, Any]:
    """Read an advertised model list as candidates; never mutate the allowlist."""
    from .settings import get_provider

    provider = get_provider(provider_name)
    account = account_fingerprint(provider.api_key)
    discovery_id = f"{provider.name}|{account}"
    approved = sorted(set(filter(None, (
        *provider.model_options, provider.default_model,
        *provider.vision_model_options, provider.vision_model,
        *provider.image_model_options, provider.image_model,
    ))))
    record: dict[str, Any] = {
        "discovery_id": discovery_id, "provider": provider.name,
        "account_fingerprint": account, "checked_at": _now(), "source": source,
        "approved_models": approved,
    }
    try:
        if not provider.api_key:
            raise ValueError("API key is not configured")
        request = urllib.request.Request(
            f"{provider.base_url.rstrip('/')}/models",
            headers={
                "Authorization": f"Bearer {provider.api_key}",
                "Accept": "application/json",
                "User-Agent": str(provider.user_agent or "AnswerBookProviderControl/1.0"),
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw_models = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(payload, dict) or not isinstance(raw_models, list):
            raise ValueError("供应商返回的模型目录格式无法识别")
        advertised = sorted({
            str(item.get("id") if isinstance(item, dict) else item).strip()[:200]
            for item in (raw_models if isinstance(raw_models, list) else [])
            if isinstance(item.get("id") if isinstance(item, dict) else item, str)
            and str(item.get("id") if isinstance(item, dict) else item).strip()
        })
        record.update({
            "ok": True, "advertised_models": advertised,
            "candidate_models": sorted(set(advertised) - set(approved)),
            "approved_not_advertised": sorted(set(approved) - set(advertised)),
        })
    except Exception as exc:
        info = classify_provider_error(exc)
        record.update({
            "ok": False, "advertised_models": [], "candidate_models": [],
            "approved_not_advertised": [], "error_title": info.title,
            "error": info.message, "suggested_action": info.suggested_action,
        })
    with _LOCK:
        state = _read_state()
        previous = (state.get("discoveries") or {}).get(discovery_id) or {}
        if not record["ok"]:
            for field in ("advertised_models", "candidate_models", "approved_not_advertised", "last_success_at"):
                if field in previous:
                    record[field] = previous[field]
        else:
            record["last_success_at"] = record["checked_at"]
        state.setdefault("discoveries", {})[discovery_id] = record
        _write_state(state)
    return record


def discover_next_due_provider() -> dict[str, Any]:
    from .settings import list_providers

    with _LOCK:
        discoveries = dict(_read_state().get("discoveries") or {})
    due: list[tuple[float, str]] = []
    now = time.time()
    for provider in list_providers().values():
        if not provider.api_key:
            continue
        did = f"{provider.name}|{account_fingerprint(provider.api_key)}"
        checked = _timestamp((discoveries.get(did) or {}).get("checked_at", ""))
        if not checked or now - checked >= 24 * 60 * 60:
            due.append((checked, provider.name))
    if not due:
        return {"ok": True, "skipped": True, "reason": "没有到期的模型列表发现任务"}
    due.sort()
    return discover_provider_models(due[0][1])


@contextmanager
def explicit_probe_source(source: str) -> Iterator[None]:
    previous = getattr(_PROBE_CONTEXT, "source", "")
    _PROBE_CONTEXT.source = source
    try:
        yield
    finally:
        _PROBE_CONTEXT.source = previous


def current_observation_source(default: str = "task_runtime") -> str:
    return str(getattr(_PROBE_CONTEXT, "source", "") or default)


def explicit_probe_active() -> bool:
    return bool(getattr(_PROBE_CONTEXT, "source", ""))


@contextmanager
def runtime_route_guard(
    *, provider: str, api_key: str, model: str, protocol: str, capability: str,
) -> Iterator[None]:
    """Apply an exact-route block/reduction without affecting sibling models."""
    if explicit_probe_active():
        yield
        return
    if capability == "tool_call":
        # Main-model tool calls are never gated by control-center observations.
        # A real request is attempted and any provider error belongs to that task.
        yield
        return
    if not is_approved_route(provider, model, protocol or "unknown", capability):
        yield
        return
    rid = route_id(provider, account_fingerprint(api_key), model, protocol or "unknown", capability)
    with _LOCK:
        record = dict((_read_state().get("routes") or {}).get(rid) or {})
    status = str(record.get("status") or "unverified")
    # Historical health is advisory. The selected task makes a real request;
    # its existing bounded retry/circuit policy handles an actual failure.
    limited = status in {"degraded", "unavailable", "configuration_error"}
    if limited:
        with _ROUTE_CONDITION:
            while int(_ROUTE_ACTIVE.get(rid) or 0) >= 1:
                _ROUTE_CONDITION.wait(timeout=0.5)
            _ROUTE_ACTIVE[rid] = int(_ROUTE_ACTIVE.get(rid) or 0) + 1
    try:
        yield
    finally:
        if limited:
            with _ROUTE_CONDITION:
                _ROUTE_ACTIVE[rid] = max(0, int(_ROUTE_ACTIVE.get(rid) or 0) - 1)
                _ROUTE_CONDITION.notify_all()


def _visual_challenge() -> tuple[str, dict[str, str]]:
    """Build a visual-only challenge whose answer is not disclosed in the prompt."""

    from PIL import Image

    palettes = (
        ((255, 0, 0), (0, 0, 255), "red", "blue"),
        ((0, 255, 0), (255, 255, 0), "green", "yellow"),
        ((0, 0, 255), (255, 0, 0), "blue", "red"),
    )
    left, right, left_name, right_name = palettes[time.time_ns() % len(palettes)]
    image = Image.new("RGB", (96, 48), right)
    image.paste(left, (0, 0, 48, 48))
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    encoded = base64.b64encode(payload.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}", {"left": left_name, "right": right_name}


def _visual_answer_matches(answer: Any, expected: dict[str, str]) -> bool:
    # Extra metadata/casing are harmless; missing, swapped or incorrect colors are not.
    return isinstance(answer, dict) and all(
        isinstance(answer.get(side), str) and answer[side].strip().lower() == color
        for side, color in expected.items()
    )


def probe_route(
    *, provider_name: str, model: str, protocol: str = "", capability: str = "text",
    source: str = "manual_probe", api_key: str = "",
    thinking_mode: str = "",
    provider_config: Any = None,
) -> dict[str, Any]:
    from .llm_client import create_llm_client
    from .prompt_registry import prompt_contract
    from .settings import get_provider

    provider = provider_config if provider_config is not None else get_provider(provider_name)
    selected_key = str(api_key or provider.api_key or "").strip()
    selected_protocol = str(protocol or _protocol_for(provider, model)).strip().lower()
    if capability == "tool_call":
        return {
            "ok": True,
            "skipped": True,
            "reason": "主模型工具调用按平台策略直接开启，无需探测或开白",
            "route": {
                "provider": provider.name,
                "model": model,
                "protocol": selected_protocol,
                "capability": capability,
                "account_fingerprint": account_fingerprint(selected_key),
                "verification_status": "not_required",
                "eligibility": "allowed" if selected_key else "blocked",
            },
        }
    profiles = {name: dict(value) for name, value in provider.model_profiles.items()}
    profile = profiles.setdefault(model, {})
    profile["api_protocol"] = selected_protocol
    provider = replace(provider, api_key=selected_key, api_protocol=selected_protocol, model_profiles=profiles)
    if thinking_mode:
        provider = replace(provider, thinking_mode=thinking_mode)
    started = time.monotonic()
    try:
        client = create_llm_client(provider)
        with explicit_probe_source(source):
            if capability == "image_generation":
                with tempfile.TemporaryDirectory(prefix="answer-book-provider-probe-") as raw_tmp:
                    with prompt_contract("system.provider_connection_image"):
                        result = client.generate_image(
                            "A single small black circle centered on a plain white background.",
                            Path(raw_tmp) / "probe.png", model=model, size=provider.image_size, timeout=45,
                        )
                    detail: Any = {"model": result.model}
            elif capability == "image_edit":
                from PIL import Image

                with tempfile.TemporaryDirectory(prefix="answer-book-provider-edit-probe-") as raw_tmp:
                    source_image = Path(raw_tmp) / "source.png"
                    Image.new("RGB", (64, 64), (235, 35, 35)).save(source_image, format="PNG")
                    with prompt_contract("system.provider_connection_image"):
                        result = client.edit_image(
                            "Change this image to a solid blue square. Do not add text.",
                            [source_image], Path(raw_tmp) / "edited.png", model=model,
                            size=provider.image_size, timeout=45,
                        )
                    detail = {"model": result.model, "reference_image_accepted": True}
            else:
                content: Any = "Return exactly this JSON object: {\"ping\":\"pong\"}"
                if capability == "vision":
                    image_data, expected = _visual_challenge()
                    content = [
                        {"type": "text", "text": 'Inspect the image. Return only JSON with lowercase color names: {"left":"...","right":"..."}.'},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ]
                with prompt_contract("system.provider_connection_text"):
                    detail = client.chat_json_object(
                        [{"role": "user", "content": content}], model=model, max_tokens=128,
                        timeout=30, attempts=1, task_stage="provider_health_probe",
                    )
                if capability == "vision" and not _visual_answer_matches(detail, expected):
                    raise ProbeValidationError("视觉探针未正确识别图像内容")
                if capability == "text" and detail != {"ping": "pong"}:
                    raise ProbeValidationError("文本探针未返回要求的内容")
        elapsed = round((time.monotonic() - started) * 1000)
        route = record_provider_observation(
            provider=provider.name, model=model, protocol=selected_protocol, capability=capability,
            api_key=selected_key, success=True, elapsed_ms=elapsed, source=source,
        )
        implied_capabilities = ("text",) if capability == "vision" else ()
        for implied_capability in implied_capabilities:
            record_provider_observation(
                provider=provider.name,
                model=model,
                protocol=selected_protocol,
                capability=implied_capability,
                api_key=selected_key,
                success=True,
                elapsed_ms=elapsed,
                source=f"{source}_implied",
            )
        return {
            "ok": True, "route": route, "detail": detail, "content": detail,
            "provider": provider.name, "model": model, "thinking_mode": provider.thinking_mode,
            "retry_report": getattr(client, "last_json_retry_report", {}),
        }
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        route = record_provider_observation(
            provider=provider.name, model=model, protocol=selected_protocol, capability=capability,
            api_key=selected_key, success=False, elapsed_ms=elapsed, source=source, error=exc,
        )
        info = classify_provider_error(exc)
        return {
            "ok": False, "route": route, "error": info.message,
            "error_title": info.title, "error_code": info.kind,
            "suggested_action": info.suggested_action,
            "requires_configuration": info.requires_configuration,
            "retryable": info.retryable, "provider_error": asdict(info),
        }


def probe_next_due_route() -> dict[str, Any]:
    onboarding = probe_next_pending_onboarding()
    if not onboarding.get("skipped"):
        return onboarding
    candidates = [
        row for row in provider_control_snapshot()["routes"]
        if row["configured"] and not row["protected"] and
        row["capability"] == "text" and
        (row.get("check_due") or not row.get("last_observed_at"))
    ]
    if not candidates:
        return {"ok": True, "skipped": True, "reason": "没有到期的低成本能力路线"}
    candidates.sort(
        key=lambda row: (
            str(row.get("next_check_at") or ""),
            {"text": 0, "vision": 1}.get(str(row.get("capability") or ""), 9),
        )
    )
    row = candidates[0]
    return probe_route(
        provider_name=row["provider"], model=row["model"], protocol=row["protocol"],
        capability=row["capability"], source="scheduled_probe",
    )


def probe_next_pending_onboarding() -> dict[str, Any]:
    for row in provider_control_snapshot()["routes"]:
        if (row.get("configured") and not row.get("protected")
                and row["capability"] in {"text", "image_generation"}
                and not row.get("onboarding")):
            return probe_model_onboarding(
                provider_name=row["provider"], model=row["model"], protocol=row["protocol"],
                capability=row["capability"],
            )
    return {"ok": True, "skipped": True, "reason": "所有已接入模型均已记录一次接入测试"}


def _startup_probe_target(provider: Any, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick connectivity evidence without probing main-model tool capability."""

    default_model = str(getattr(provider, "default_model", "") or "").strip()
    text_rows = [
        row for row in rows
        if row.get("capability") == "text" and (not default_model or row.get("model") == default_model)
    ]
    if text_rows:
        return text_rows[0]
    image_model = str(getattr(provider, "image_model", "") or "").strip()
    image_rows = [
        row for row in rows
        if row.get("capability") == "image_generation" and (not image_model or row.get("model") == image_model)
    ]
    return image_rows[0] if image_rows else None


def probe_startup_unverified_providers() -> dict[str, Any]:
    """Backfill one durable onboarding attempt for each existing model."""
    snapshot = provider_control_snapshot()
    rows = [
        row for row in snapshot["routes"]
        if row.get("configured") and not row.get("protected")
        and row["capability"] in {"text", "image_generation"} and not row.get("onboarding")
    ]
    results: list[dict[str, Any]] = []
    for row in rows:
        if _SCHEDULER_STOP.is_set():
            break
        result = probe_model_onboarding(
            provider_name=row["provider"],
            model=row["model"],
            protocol=row["protocol"],
            capability=row["capability"],
            source="onboarding_probe",
        )
        results.append({
            "provider": row["provider"],
            "model": row["model"],
            "capability": row["capability"],
            "ok": bool(result.get("ok")),
        })
        if _SCHEDULER_STOP.is_set():
            break
    return {
        "ok": all(item["ok"] for item in results),
        "tested_provider_count": len(results),
        "results": results,
    }


def start_provider_control_scheduler() -> None:
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
        return
    _SCHEDULER_STOP.clear()

    def loop() -> None:
        try:
            probe_startup_unverified_providers()
        except Exception:
            pass
        if _SCHEDULER_STOP.wait(300):
            return
        while not _SCHEDULER_STOP.is_set():
            try:
                probe_next_due_route()
                discover_next_due_provider()
            except Exception:
                pass
            # Spread the active budget across capability-specific freshness
            # windows; image generation/editing remain manual to avoid hidden cost.
            _SCHEDULER_STOP.wait(300)

    _SCHEDULER_THREAD = threading.Thread(target=loop, name="provider-control", daemon=True)
    _SCHEDULER_THREAD.start()


def stop_provider_control_scheduler() -> None:
    _SCHEDULER_STOP.set()
