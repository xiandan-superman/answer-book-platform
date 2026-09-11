from __future__ import annotations

import hashlib
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
from .provider_errors import ProviderErrorInfo, classify_provider_error
from .runtime_capacity import provider_request_max_concurrency

STATE_VERSION = 1
FAILURE_THRESHOLD = 3
MAX_HISTORY_PER_ROUTE = 30
TEXT_TTL_SECONDS = 5 * 60 * 60
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
    return {"version": STATE_VERSION, "updated_at": _now(), "routes": {}, "discoveries": {}}


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


def _supports_tools(provider: Any, model: str) -> bool:
    profile = dict((getattr(provider, "model_profiles", {}) or {}).get(model, {}))
    return bool(profile.get("supports_tool_calls"))


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
                if _supports_tools(provider, model):
                    capabilities.append("tool_call")
                for capability in capabilities:
                    rows.append({
                        "route_id": route_id(provider.name, account, model, protocol, capability),
                        "provider": provider.name,
                        "model": model,
                        "protocol": protocol,
                        "capability": capability,
                        "account_fingerprint": account,
                        "configured": account != "unconfigured",
                        "approved": True,
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
    return rows


def is_approved_route(provider: str, model: str, protocol: str, capability: str) -> bool:
    return any(
        row["provider"] == provider and row["model"] == model and
        row["protocol"] == protocol and row["capability"] == capability
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
        if success:
            current.update({
                "status": "available", "consecutive_provider_failures": 0,
                "last_success_at": event["checked_at"], "last_latency_ms": event["elapsed_ms"],
                "last_error": None, "responsibility": "none",
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
        ttl = IMAGE_TTL_SECONDS if catalog["capability"] == "image_generation" else TEXT_TTL_SECONDS
        stale = bool(record.get("last_observed_at") and now - _timestamp(record["last_observed_at"]) > ttl)
        latencies = [int(item.get("elapsed_ms") or 0) for item in history if item.get("success") and item.get("elapsed_ms")]
        effective_allowed = status not in {"unavailable", "configuration_error", "not_configured"}
        base_concurrency = int(catalog.get("production_concurrency") or 0)
        effective_concurrency = (
            0 if not effective_allowed else
            1 if status == "degraded" else base_concurrency
        )
        routes.append({
            **catalog, **{key: value for key, value in record.items() if key != "history"},
            "status": status, "stale": stale, "effective_allowed": effective_allowed,
            "effective_concurrency": effective_concurrency,
            "p50_latency_ms": _percentile(latencies, .5), "p95_latency_ms": _percentile(latencies, .95),
            "history": list(reversed(history)),
        })
    counts: dict[str, int] = {}
    for row in routes:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    configured_routes = [row for row in routes if row["configured"]]
    verified_routes = [row for row in configured_routes if row.get("last_observed_at")]
    available_verified = [row for row in verified_routes if row["status"] == "available"]
    attention_routes = [
        row for row in configured_routes
        if row["status"] in {"degraded", "unavailable", "configuration_error"}
    ]
    configured_providers = {row["provider"] for row in configured_routes}
    configured_models = {(row["provider"], row["model"]) for row in configured_routes}
    supported_models = {(row["provider"], row["model"]) for row in routes}
    effective_concurrency = sum(
        max(0, int(row.get("effective_concurrency") or 0))
        for row in configured_routes if row["capability"] == "text"
    )
    return {
        "schema_version": "answer_book.provider_control.v1", "generated_at": _now(),
        "failure_threshold": FAILURE_THRESHOLD, "counts": counts, "routes": routes,
        "summary": {
            "supported_provider_count": len({row["provider"] for row in routes}),
            "configured_provider_count": len(configured_providers),
            "supported_model_count": len(supported_models),
            "configured_model_count": len(configured_models),
            "verified_route_count": len(verified_routes),
            "attention_route_count": len(attention_routes),
            "available_rate": round(len(available_verified) * 100 / len(verified_routes), 1) if verified_routes else None,
            "effective_text_concurrency": effective_concurrency,
        },
        "discoveries": list((state.get("discoveries") or {}).values()),
        "policies": {
            "identity_scope": "provider+account_fingerprint+model+protocol+capability",
            "recovery": "one successful exact probe restores immediately",
            "active_health_interval_hours": TEXT_TTL_SECONDS // 3600,
            "image_active_probe": "manual_only", "discovery": "candidate_only_manual_approval",
            "automatic_mutations": ["exact_route_temporary_disable", "exact_route_immediate_restore"],
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
        advertised = sorted({
            str(item.get("id") if isinstance(item, dict) else item).strip()[:200]
            for item in (raw_models if isinstance(raw_models, list) else [])
            if str(item.get("id") if isinstance(item, dict) else item).strip()
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
    if not is_approved_route(provider, model, protocol or "unknown", capability):
        yield
        return
    rid = route_id(provider, account_fingerprint(api_key), model, protocol or "unknown", capability)
    with _LOCK:
        record = dict((_read_state().get("routes") or {}).get(rid) or {})
    status = str(record.get("status") or "unverified")
    if status in {"unavailable", "configuration_error", "not_configured"}:
        error = record.get("last_error") or {}
        title = str(error.get("title") or "当前模型路线不可用")
        action = str(error.get("suggested_action") or "请在供应商运行中心执行精确检测后重试。")
        raise RuntimeError(f"{title}。{action}")
    limited = status == "degraded"
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


def probe_route(
    *, provider_name: str, model: str, protocol: str = "", capability: str = "text",
    source: str = "manual_probe", api_key: str = "",
) -> dict[str, Any]:
    from .llm_client import create_llm_client
    from .prompt_registry import prompt_contract
    from .settings import get_provider

    provider = get_provider(provider_name)
    selected_key = str(api_key or provider.api_key or "").strip()
    selected_protocol = str(protocol or _protocol_for(provider, model)).strip().lower()
    profiles = {name: dict(value) for name, value in provider.model_profiles.items()}
    profile = profiles.setdefault(model, {})
    profile["api_protocol"] = selected_protocol
    provider = replace(provider, api_key=selected_key, api_protocol=selected_protocol, model_profiles=profiles)
    client = create_llm_client(provider)
    started = time.monotonic()
    try:
        with explicit_probe_source(source):
            if capability == "image_generation":
                with tempfile.TemporaryDirectory(prefix="answer-book-provider-probe-") as raw_tmp:
                    with prompt_contract("system.provider_connection_image"):
                        result = client.generate_image(
                            "A single small black circle centered on a plain white background.",
                            Path(raw_tmp) / "probe.png", model=model, size=provider.image_size, timeout=45,
                        )
                    detail: Any = {"model": result.model}
            elif capability == "tool_call":
                tools = [{"type": "function", "name": "health_echo", "description": "Return the supplied token", "parameters": {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}}]
                with prompt_contract("system.provider_connection_text"):
                    raw = client.create_tool_response(
                        [{"role": "user", "content": "Call health_echo with token pong. Do not answer normally."}],
                        tools=tools, model=model, max_tokens=128, timeout=30, json_object=False,
                    )
                if "health_echo" not in json.dumps(raw, ensure_ascii=False):
                    raise RuntimeError("模型未按要求返回原生工具调用")
                detail = {"native_tool_call": True}
            else:
                content: Any = "Return exactly this JSON object: {\"ping\":\"pong\"}"
                if capability == "vision":
                    content = [
                        {"type": "text", "text": "The image is a test pixel. Return exactly {\"ping\":\"pong\"}."},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nKsAAAAASUVORK5CYII="}},
                    ]
                with prompt_contract("system.provider_connection_text"):
                    detail = client.chat_json_object(
                        [{"role": "user", "content": content}], model=model, max_tokens=128,
                        timeout=30, attempts=1, task_stage="provider_health_probe",
                    )
        elapsed = round((time.monotonic() - started) * 1000)
        route = record_provider_observation(
            provider=provider.name, model=model, protocol=selected_protocol, capability=capability,
            api_key=selected_key, success=True, elapsed_ms=elapsed, source=source,
        )
        return {"ok": True, "route": route, "detail": detail}
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
    candidates = [
        row for row in provider_control_snapshot()["routes"]
        if row["configured"] and not row["protected"] and row["capability"] == "text" and
        (not row.get("last_observed_at") or row.get("stale"))
    ]
    if not candidates:
        return {"ok": True, "skipped": True, "reason": "没有到期的文本路线"}
    candidates.sort(key=lambda row: str(row.get("last_observed_at") or ""))
    row = candidates[0]
    return probe_route(
        provider_name=row["provider"], model=row["model"], protocol=row["protocol"],
        capability=row["capability"], source="scheduled_probe",
    )


def start_provider_control_scheduler() -> None:
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
        return
    _SCHEDULER_STOP.clear()

    def loop() -> None:
        if _SCHEDULER_STOP.wait(300):
            return
        while not _SCHEDULER_STOP.is_set():
            try:
                probe_next_due_route()
                discover_next_due_provider()
            except Exception:
                pass
            # Spread the active budget across the 3-5 hour freshness window;
            # never burst through the catalog after a restart.
            _SCHEDULER_STOP.wait(300)

    _SCHEDULER_THREAD = threading.Thread(target=loop, name="provider-control", daemon=True)
    _SCHEDULER_THREAD.start()


def stop_provider_control_scheduler() -> None:
    _SCHEDULER_STOP.set()
