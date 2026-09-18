from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Any, Callable, TypeVar
from uuid import uuid4

from .runtime_monitor import clear_smart_route_wait, current_model_call_context, set_smart_route_wait
from .settings import ProviderConfig, get_provider

SMART_PROVIDER_NAME = "gemini_smart_router"
SMART_MODEL_NAME = "gemini-smart-router"
SMART_GPT_PROVIDER_NAME = "gpt_smart_router"
SMART_GPT_MODEL_NAME = "gpt-smart-router"
SMART_CLAUDE_PROVIDER_NAME = "claude_smart_router"
SMART_CLAUDE_MODEL_NAME = "claude-smart-router"
SMART_IMAGE_PROVIDER_NAME = "image_smart_router"
SMART_IMAGE_MODEL_NAME = "image-smart-router"
SMART_ROUTER_FAMILIES = {
    SMART_PROVIDER_NAME: "gemini",
    SMART_GPT_PROVIDER_NAME: "gpt",
    SMART_CLAUDE_PROVIDER_NAME: "claude",
    SMART_IMAGE_PROVIDER_NAME: "image",
}
SMART_ROUTER_USER_AGENT = "AnswerBookPlatform-SmartRouter/1.0"
_T = TypeVar("_T")


class SmartGeminiRoutingError(RuntimeError):
    pass


def messages_require_vision(messages: list[dict[str, Any]]) -> bool:
    def contains_image(value: Any) -> bool:
        if isinstance(value, dict):
            kind = str(value.get("type") or "").lower()
            if kind in {"image", "image_url", "input_image"}:
                return True
            return any(contains_image(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_image(item) for item in value)
        return False

    return contains_image(messages)


def _router_settings() -> tuple[str, str]:
    endpoint = str(os.environ.get("CLOUDFLARE_SMART_ROUTER_URL", "")).strip().rstrip("/")
    access_key = str(os.environ.get("CLOUDFLARE_SMART_ROUTER_ACCESS_KEY", "")).strip()
    if not endpoint or not access_key:
        raise SmartGeminiRoutingError("智能路由配置不完整：缺少 Cloudflare 路由地址或用户访问 Key。")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SmartGeminiRoutingError("智能路由地址必须是有效的 HTTPS 地址。")
    return endpoint, access_key


def _json_request(url: str, access_key: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 15) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {access_key}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": SMART_ROUTER_USER_AGENT,
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = str(json.loads(detail).get("error") or detail)
        except (TypeError, ValueError):
            message = detail
        raise SmartGeminiRoutingError(message or f"Cloudflare 智能路由返回 HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise SmartGeminiRoutingError(f"无法连接 Cloudflare 智能路由：{exc}") from exc
    if not isinstance(result, dict):
        raise SmartGeminiRoutingError("Cloudflare 智能路由返回了无效响应。")
    return result


def _reserve_route(capability: str, *, family: str = "gemini") -> tuple[str, str, str, str, str, str]:
    endpoint, access_key = _router_settings()
    context = current_model_call_context()
    request_id = uuid4().hex
    task_id = str(context.get("task_id") or context.get("run_id") or "")
    active_item = str(context.get("active_item") or "")
    created = _json_request(f"{endpoint}/v1/reservations", access_key, method="POST", payload={
        "request_id": request_id, "family": family, "capability": capability,
        "task_id": task_id, "stage": str(context.get("stage") or ""), "active_item": active_item,
    })
    reservation_id = str(created.get("reservation_id") or request_id)
    wait_started = time.monotonic()
    try:
        while str(created.get("status") or "") == "waiting":
            waited = max(0, int(time.monotonic() - wait_started))
            set_smart_route_wait(
                task_id=task_id, request_id=request_id, family=family,
                stage=str(context.get("stage") or ""), active_item=active_item,
                waited_seconds=waited,
                message="模型并发已满，正在等待可用名额；任务本身没有异常。",
            )
            if waited >= 60:
                raise SmartGeminiRoutingError("模型并发等待超过 60 秒，当前阶段已停止；任务本身没有异常，可稍后重试。")
            time.sleep(min(1.0, max(0.2, float(created.get("poll_after_ms") or 500) / 1000)))
            created = _json_request(f"{endpoint}/v1/reservations/{urllib.parse.quote(reservation_id, safe='')}", access_key)
        if str(created.get("status") or "") != "ready":
            raise SmartGeminiRoutingError(str(created.get("error") or f"当前没有可用的 {family.upper()} 模型路线。"))
        provider = str(created.get("provider") or "").strip()
        model = str(created.get("model") or "").strip()
        lease = str(created.get("lease") or "").strip()
        if not provider or not model or not lease:
            raise SmartGeminiRoutingError("Cloudflare 智能路由没有返回完整的模型预约信息。")
        protocol = str(created.get("api_protocol") or "chat_completions").strip().lower()
        if protocol not in {"chat_completions", "responses"}:
            raise SmartGeminiRoutingError("Cloudflare 智能路由返回了不支持的模型调用协议。")
        return provider, model, lease, request_id, str(created.get("thinking_minimum") or ""), protocol
    finally:
        clear_smart_route_wait(task_id=task_id, request_id=request_id)


def gateway_candidate_config(
    provider: str,
    model: str,
    lease: str,
    request_id: str,
    thinking_minimum: str,
    api_protocol: str = "chat_completions",
    *,
    virtual_provider: str = SMART_PROVIDER_NAME,
) -> ProviderConfig:
    endpoint, access_key = _router_settings()
    virtual = get_provider(virtual_provider)
    return replace(
        virtual, name="cloudflare_smart_router", base_url=f"{endpoint}/v1", api_key=access_key,
        default_model=model, vision_model=model, image_model=model, smart_router_lease=lease,
        smart_router_request_id=request_id,
        user_agent=SMART_ROUTER_USER_AGENT, api_protocol=api_protocol,
        responses_streaming=False,
        model_profiles={model: {"api_protocol": api_protocol, "supports_tool_calls": True, "thinking_minimum": thinking_minimum}},
    )


def execute_smart_route(
    capability: str,
    operation: Callable[[ProviderConfig], _T],
    *,
    virtual_provider: str = SMART_PROVIDER_NAME,
) -> _T:
    family = SMART_ROUTER_FAMILIES.get(virtual_provider)
    if not family:
        raise SmartGeminiRoutingError("未知的智能路由入口。")
    provider, model, lease, request_id, thinking_minimum, api_protocol = _reserve_route(capability, family=family)
    return operation(gateway_candidate_config(
        provider,
        model,
        lease,
        request_id,
        thinking_minimum,
        api_protocol,
        virtual_provider=virtual_provider,
    ))


def reset_smart_route_state() -> None:
    """Compatibility hook retained for tests; routing state is Cloudflare-owned."""
