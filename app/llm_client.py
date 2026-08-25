from __future__ import annotations

import base64
import datetime as dt
import email.utils
import http.client
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .concurrency import ModelRequestAborted, ensure_model_request_active, model_request_slot
from .model_diagnostics import model_diagnostic_hint, record_model_diagnostic
from .redaction import redact_credentials
from .runtime_monitor import (
    record_model_call_estimate,
    record_model_call_usage,
    record_model_stream_progress,
    track_model_call,
)
from .settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig

_DEFAULT_URLOPEN = urllib.request.urlopen


def _provider_request_headers(config: ProviderConfig, *, accept: str = "") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    user_agent = str(getattr(config, "user_agent", "") or "").strip()
    if user_agent:
        headers["User-Agent"] = user_agent
    if accept:
        headers["Accept"] = accept
    return headers


class LLMError(RuntimeError):
    """Provider failure with machine-readable transport metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        transport_phase: str = "",
        partial_output_received: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.transport_phase = transport_phase
        self.partial_output_received = bool(partial_output_received)


class LLMTimeoutError(LLMError, TimeoutError):
    """Timeout that remains compatible with legacy ``TimeoutError`` checks."""


def _transport_timeout(name: str, default: int, hard_timeout: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, min(max(1, int(hard_timeout)), value))


def _first_byte_timeout(hard_timeout: int) -> int:
    """Scale preparation wait for long stages while retaining a short connect timeout."""

    adaptive_default = min(420, max(120, int(hard_timeout) * 3 // 4))
    return _transport_timeout("PRACTICE_MODEL_FIRST_BYTE_TIMEOUT_SECONDS", adaptive_default, hard_timeout)


class _LayeredHTTPConnection(http.client.HTTPConnection):
    """HTTP connection with distinct connect and first-response deadlines."""

    def __init__(
        self,
        host: str,
        *,
        connect_timeout: float,
        first_byte_timeout: float,
        hard_deadline_monotonic: float,
        **kwargs: Any,
    ) -> None:
        self._first_byte_timeout = max(0.05, float(first_byte_timeout))
        self._hard_deadline_monotonic = float(hard_deadline_monotonic)
        remaining = max(0.05, self._hard_deadline_monotonic - time.monotonic())
        super().__init__(host, timeout=min(max(0.05, float(connect_timeout)), remaining), **kwargs)

    def connect(self) -> None:
        try:
            super().connect()
        except (socket.timeout, TimeoutError) as exc:
            phase = "hard_timeout" if time.monotonic() >= self._hard_deadline_monotonic else "connect"
            message = "模型请求超过单次硬截止时间。" if phase == "hard_timeout" else "模型服务连接超时。"
            raise LLMError(message, transport_phase=phase) from exc
        remaining = self._hard_deadline_monotonic - time.monotonic()
        if remaining <= 0:
            self.close()
            raise LLMError("模型请求超过单次硬截止时间。", transport_phase="hard_timeout")
        if self.sock is not None:
            self.sock.settimeout(min(self._first_byte_timeout, remaining))


class _LayeredHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS variant of :class:`_LayeredHTTPConnection`."""

    def __init__(
        self,
        host: str,
        *,
        connect_timeout: float,
        first_byte_timeout: float,
        hard_deadline_monotonic: float,
        **kwargs: Any,
    ) -> None:
        self._first_byte_timeout = max(0.05, float(first_byte_timeout))
        self._hard_deadline_monotonic = float(hard_deadline_monotonic)
        remaining = max(0.05, self._hard_deadline_monotonic - time.monotonic())
        super().__init__(host, timeout=min(max(0.05, float(connect_timeout)), remaining), **kwargs)

    def connect(self) -> None:
        try:
            super().connect()
        except (socket.timeout, TimeoutError) as exc:
            phase = "hard_timeout" if time.monotonic() >= self._hard_deadline_monotonic else "connect"
            message = "模型请求超过单次硬截止时间。" if phase == "hard_timeout" else "模型服务连接超时。"
            raise LLMError(message, transport_phase=phase) from exc
        remaining = self._hard_deadline_monotonic - time.monotonic()
        if remaining <= 0:
            self.close()
            raise LLMError("模型请求超过单次硬截止时间。", transport_phase="hard_timeout")
        if self.sock is not None:
            self.sock.settimeout(min(self._first_byte_timeout, remaining))


class _LayeredHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, *, connect_timeout: float, first_byte_timeout: float, hard_deadline_monotonic: float) -> None:
        super().__init__()
        self._connection_options = {
            "connect_timeout": connect_timeout,
            "first_byte_timeout": first_byte_timeout,
            "hard_deadline_monotonic": hard_deadline_monotonic,
        }

    def http_open(self, req: urllib.request.Request) -> Any:
        def factory(host: str, **kwargs: Any) -> _LayeredHTTPConnection:
            kwargs.pop("timeout", None)
            return _LayeredHTTPConnection(host, **self._connection_options, **kwargs)

        return self.do_open(factory, req)


class _LayeredHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *, connect_timeout: float, first_byte_timeout: float, hard_deadline_monotonic: float) -> None:
        # Some bundled macOS Python runtimes do not honor SSL_CERT_FILE when
        # their patched default-cert loader is used implicitly.  Resolve the
        # standard OpenSSL environment inputs explicitly while retaining the
        # verified default context and hostname checks.
        cafile = str(os.environ.get("SSL_CERT_FILE") or "").strip() or None
        capath = str(os.environ.get("SSL_CERT_DIR") or "").strip() or None
        context = ssl.create_default_context(cafile=cafile, capath=capath)
        super().__init__(context=context)
        self._context: ssl.SSLContext = context
        self._connection_options = {
            "connect_timeout": connect_timeout,
            "first_byte_timeout": first_byte_timeout,
            "hard_deadline_monotonic": hard_deadline_monotonic,
        }

    def https_open(self, req: urllib.request.Request) -> Any:
        def factory(host: str, **kwargs: Any) -> _LayeredHTTPSConnection:
            kwargs.pop("timeout", None)
            return _LayeredHTTPSConnection(host, **self._connection_options, **kwargs)

        return self.do_open(factory, req, context=self._context)


def _open_provider_response(
    urlopen: Any,
    request: urllib.request.Request,
    *,
    connect_timeout: int,
    first_byte_timeout: int,
    hard_deadline_monotonic: float,
) -> Any:
    """Open one response while preserving injectable test transports."""
    remaining = hard_deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise LLMError("模型请求超过单次硬截止时间。", transport_phase="hard_timeout")
    try:
        if urlopen is _DEFAULT_URLOPEN:
            handlers: list[Any] = [
                _LayeredHTTPHandler(
                    connect_timeout=min(float(connect_timeout), remaining),
                    first_byte_timeout=min(float(first_byte_timeout), remaining),
                    hard_deadline_monotonic=hard_deadline_monotonic,
                ),
                _LayeredHTTPSHandler(
                    connect_timeout=min(float(connect_timeout), remaining),
                    first_byte_timeout=min(float(first_byte_timeout), remaining),
                    hard_deadline_monotonic=hard_deadline_monotonic,
                ),
            ]
            host = str(urllib.parse.urlparse(request.full_url).hostname or "").lower()
            allow_proxy = str(os.environ.get("ANSWER_BOOK_LINGSUAN_USE_SYSTEM_PROXY") or "").strip().lower()
            if host == "lingsuan.top" and allow_proxy not in {"1", "true", "yes"}:
                # Local proxy interception has caused TLS EOFs and misleading
                # gateway 502s. TUN/fake-IP routing still belongs to the OS, but
                # urllib must not add a second configured proxy hop by default.
                handlers.insert(0, urllib.request.ProxyHandler({}))
            opener = urllib.request.build_opener(*handlers)
            return opener.open(request, timeout=min(float(connect_timeout), remaining))
        return urlopen(request, timeout=min(float(first_byte_timeout), remaining))
    except LLMError:
        raise
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            phase = "hard_timeout" if time.monotonic() >= hard_deadline_monotonic else "first_byte"
            message = "模型请求超过单次硬截止时间。" if phase == "hard_timeout" else "模型服务首字节响应超时。"
            raise LLMError(message, transport_phase=phase) from exc
        raise
    except (socket.timeout, TimeoutError) as exc:
        phase = "hard_timeout" if time.monotonic() >= hard_deadline_monotonic else "first_byte"
        message = "模型请求超过单次硬截止时间。" if phase == "hard_timeout" else "模型服务首字节响应超时。"
        raise LLMError(message, transport_phase=phase) from exc


def _read_response_body(
    response: Any,
    *,
    hard_timeout: int,
    hard_deadline_monotonic: float | None = None,
) -> bytes:
    """Read under an idle timeout while repeatedly checking the task lease."""
    deadline = hard_deadline_monotonic or (time.monotonic() + max(1, int(hard_timeout)))
    idle = _transport_timeout("PRACTICE_MODEL_READ_IDLE_TIMEOUT_SECONDS", 45, hard_timeout)
    chunks: list[bytes] = []
    while True:
        ensure_model_request_active()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMError(
                "模型请求超过单次硬截止时间。",
                transport_phase="hard_timeout",
                partial_output_received=bool(chunks),
            )
        _set_stream_read_deadline(response, min(float(idle), remaining))
        try:
            try:
                read_once = getattr(response, "read1", None)
                chunk = read_once(64 * 1024) if callable(read_once) else response.read(64 * 1024)
            except TypeError:
                chunk = response.read()
                ensure_model_request_active()
                return chunk
        except (socket.timeout, TimeoutError) as exc:
            phase = "hard_timeout" if time.monotonic() >= deadline else "read_idle"
            message = "模型请求超过单次硬截止时间。" if phase == "hard_timeout" else "模型响应读取空闲超时。"
            raise LLMError(
                message,
                transport_phase=phase,
                partial_output_received=bool(chunks),
            ) from exc
        if not chunk:
            break
        chunks.append(chunk)
    ensure_model_request_active()
    return b"".join(chunks)


def _http_retry_after_seconds(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        target = email.utils.parsedate_to_datetime(text)
        if target.tzinfo is None:
            target = target.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (target - dt.datetime.now(dt.timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _http_llm_error(exc: urllib.error.HTTPError, body: str) -> LLMError:
    safe_body = redact_credentials(body[:800])
    return LLMError(
        f"Provider HTTP {exc.code}: {safe_body}",
        status_code=int(exc.code),
        retry_after_seconds=_http_retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None),
    )


@dataclass(frozen=True)
class LLMResult:
    provider: str
    model: str
    content: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ImageGenerationResult:
    provider: str
    model: str
    path: Path
    raw: dict[str, Any]


class LLMClientProtocol(Protocol):
    """Stable interface consumed by generation and audit modules."""

    config: ProviderConfig

    def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResult: ...

    def chat_text(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResult: ...

    def chat_json_object(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]: ...

    def generate_image(self, prompt: str, output: Path, **kwargs: Any) -> ImageGenerationResult: ...


_THINKING_ORDER = {"minimal": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4}


def _model_profile(config: ProviderConfig, model: str) -> dict[str, Any]:
    profiles = getattr(config, "model_profiles", {}) or {}
    exact = profiles.get(str(model or "").strip())
    return dict(exact) if isinstance(exact, dict) else {}


def _model_api_protocol(config: ProviderConfig, model: str) -> str:
    profile = _model_profile(config, model)
    return str(profile.get("api_protocol") or getattr(config, "api_protocol", "chat_completions") or "chat_completions").strip().lower()


def _model_omitted_parameters(config: ProviderConfig, model: str) -> set[str]:
    return {
        str(value).strip().lower()
        for value in (_model_profile(config, model).get("omit_parameters") or [])
        if str(value).strip()
    }


def _model_thinking_mode(config: ProviderConfig, model: str, requested: Any) -> str:
    mode = _normalize_thinking_mode(requested)
    minimum = _normalize_thinking_mode(_model_profile(config, model).get("thinking_minimum"))
    if minimum not in _THINKING_ORDER:
        return mode
    if mode in {"disabled", "auto"}:
        return minimum if mode == "disabled" else "auto"
    if mode == "enabled":
        mode = "medium"
    return minimum if _THINKING_ORDER.get(mode, -1) < _THINKING_ORDER[minimum] else mode


def _effective_model_max_tokens(
    config: ProviderConfig,
    model: str,
    requested_tokens: Any,
    thinking_mode: str,
) -> int:
    calculated = _effective_max_tokens(config, requested_tokens, thinking_mode)
    cap = _model_profile(config, model).get("max_tokens")
    try:
        return min(calculated, max(1, int(cap))) if cap is not None else calculated
    except (TypeError, ValueError):
        return calculated


def _client_for_model_protocol(config: ProviderConfig, model: str, protocol: str) -> "OpenAICompatibleClient":
    profiles = {key: dict(value) for key, value in (getattr(config, "model_profiles", {}) or {}).items()}
    profile = dict(profiles.get(model) or {})
    profile["api_protocol"] = protocol
    profiles[model] = profile
    return OpenAICompatibleClient(
        replace(config, api_protocol=protocol, default_model=model, model_profiles=profiles)
    )


def _is_gemini_model(config: ProviderConfig, model: str) -> bool:
    return str(model or "").lower().startswith("gemini-") or str(config.name).lower() == "lingsuan_google"


def _is_grok_model(config: ProviderConfig, model: str) -> bool:
    return str(model or "").lower().startswith("grok-") or str(config.name).lower() == "lingsuan_xai"


def _chat_reasoning_effort(thinking_mode: str) -> str:
    normalized = _normalize_thinking_mode(thinking_mode)
    return normalized if normalized in _THINKING_ORDER else ""


def _strip_think_blocks(content: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", str(content or ""), flags=re.IGNORECASE).strip()


def _message_plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    return "\n".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"}
    )


def _anthropic_message_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        if part_type in {"text", "input_text", "output_text"}:
            parts.append({"type": "text", "text": str(part.get("text") or "")})
            continue
        if part_type not in {"image_url", "input_image"}:
            continue
        raw_url = part.get("image_url")
        url = raw_url.get("url") if isinstance(raw_url, dict) else raw_url
        if not url:
            url = part.get("url")
        url = str(url or "")
        data_match = re.fullmatch(r"data:([^;,]+);base64,(.+)", url, flags=re.DOTALL)
        source = (
            {"type": "base64", "media_type": data_match.group(1), "data": data_match.group(2)}
            if data_match
            else {"type": "url", "url": url}
        )
        if url:
            parts.append({"type": "image", "source": source})
    return parts or _message_plain_text(content)


def _json_schema_from_contract(value: Any, *, field_name: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        properties = {
            str(key): _json_schema_from_contract(item, field_name=str(key))
            for key, item in value.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "items": _json_schema_from_contract(value[0], field_name=field_name) if value else {"type": "string"},
        }
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if field_name == "batch_index":
        return {"type": "integer"}
    if field_name == "knowledge_points":
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def _anthropic_output_schema(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    marker = re.compile(r"(?:^|\n)\s*(?:#{1,3}\s*)?输出结构\s*[：:]?\s*\n")
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        content = _message_plain_text(message.get("content"))
        for match in reversed(list(marker.finditer(content))):
            try:
                contract, _ = decoder.raw_decode(content[match.end():].lstrip())
            except (ValueError, json.JSONDecodeError):
                continue
            if not isinstance(contract, dict):
                continue
            if isinstance(contract.get("item_schema"), dict) and isinstance(contract.get("exercises"), str):
                item_schema = _json_schema_from_contract(contract["item_schema"])
                conditional = contract.get("conditional_fields")
                if isinstance(conditional, dict):
                    for key, entry in conditional.items():
                        if isinstance(entry, dict) and "schema" in entry:
                            item_schema["properties"][str(key)] = _json_schema_from_contract(
                                entry["schema"], field_name=str(key)
                            )
                return {
                    "type": "object",
                    "properties": {"exercises": {"type": "array", "items": item_schema}},
                    "required": ["exercises"],
                    "additionalProperties": False,
                }
            return _json_schema_from_contract(contract)
    return None


class OpenAICompatibleClient:
    def __new__(cls, config: ProviderConfig):
        protocol = str(getattr(config, "api_protocol", "chat_completions") or "chat_completions").strip().lower()
        if cls is OpenAICompatibleClient and protocol in {"responses", "responses_api"}:
            return super().__new__(ResponsesAPIClient)
        if cls is OpenAICompatibleClient and protocol in {"anthropic_messages", "messages"}:
            return super().__new__(AnthropicMessagesClient)
        if cls is OpenAICompatibleClient and protocol not in {"chat_completions", "openai_compatible", ""}:
            raise ValueError(f"Unsupported API protocol: {protocol}")
        return super().__new__(cls)

    def __init__(self, config: ProviderConfig):
        if config.type != "openai_compatible":
            raise ValueError(f"Unsupported provider type: {config.type}")
        self.config = config
        self._urlopen = urllib.request.urlopen
        self._json_response_format_supported: bool | None = None

    def chat_json(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: str | None = None,
        timeout: int = 120,
    ) -> LLMResult:
        if not self.config.api_key:
            raise LLMError(f"API key is not configured for provider: {self.config.name}")
        last_error: LLMError | None = None
        target_model = str(model or self.config.default_model).strip()
        json_mode_unsupported = target_model in set(getattr(self.config, "json_mode_unsupported_models", ()) or ())
        use_response_format_options = [False] if json_mode_unsupported or self._json_response_format_supported is False else [True, False]
        for use_response_format in use_response_format_options:
            try:
                return self._chat_json_once(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    thinking=thinking,
                    timeout=timeout,
                    use_response_format=use_response_format,
                )
            except LLMError as exc:
                last_error = exc
                if use_response_format and _is_unsupported_response_format_error(str(exc)):
                    self._json_response_format_supported = False
                    continue
                raise
        raise last_error or LLMError("Provider request failed")

    def chat_text(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: str | None = None,
        timeout: int = 120,
    ) -> LLMResult:
        return self._chat_json_once(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            timeout=timeout,
            use_response_format=False,
        )

    def _chat_json_once(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: str | None = None,
        timeout: int = 120,
        use_response_format: bool = True,
    ) -> LLMResult:
        target_model = str(model or self.config.default_model).strip()
        protocol = _model_api_protocol(self.config, target_model)
        if protocol not in {"chat_completions", "openai_compatible", ""}:
            delegated = _client_for_model_protocol(self.config, target_model, protocol)
            delegated._urlopen = self._urlopen
            return delegated._chat_json_once(
                messages, model=target_model, temperature=temperature, max_tokens=max_tokens,
                thinking=thinking, timeout=timeout, use_response_format=use_response_format,
            )
        thinking_mode = _model_thinking_mode(
            self.config,
            target_model,
            thinking if thinking is not None else getattr(self.config, "thinking_mode", "auto"),
        )
        requested_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        payload = {
            "model": target_model,
            "messages": messages,
            "max_tokens": _effective_model_max_tokens(self.config, target_model, requested_tokens, thinking_mode),
        }
        omitted = _model_omitted_parameters(self.config, target_model)
        if "temperature" not in omitted:
            payload["temperature"] = self.config.temperature if temperature is None else temperature
        if use_response_format:
            payload["response_format"] = {"type": "json_object"}
        if thinking_mode in {"enabled", "disabled"}:
            payload["thinking"] = {"type": thinking_mode}
        reasoning_effort = _deepseek_reasoning_effort(self.config, thinking_mode)
        if reasoning_effort:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = reasoning_effort
        if _is_gemini_model(self.config, target_model):
            payload.pop("thinking", None)
            gemini_effort = _chat_reasoning_effort(thinking_mode)
            if gemini_effort:
                payload["reasoning_effort"] = gemini_effort
                reasoning_effort = gemini_effort
        if _is_grok_model(self.config, target_model):
            payload.pop("thinking", None)
            grok_effort = _chat_reasoning_effort(thinking_mode)
            if grok_effort:
                payload["reasoning_effort"] = grok_effort
                reasoning_effort = grok_effort
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=data,
            headers=_provider_request_headers(self.config),
            method="POST",
        )
        try:
            with model_request_slot(self.config):
                with track_model_call(
                    provider=self.config.name,
                    model=str(payload["model"]),
                    purpose="chat_json" if use_response_format else "chat_text",
                    timeout=timeout,
                ) as call_record:
                    hard_deadline = time.monotonic() + max(1, int(timeout))
                    try:
                        connect_timeout = _transport_timeout(
                            "PRACTICE_MODEL_CONNECT_TIMEOUT_SECONDS", 15, timeout
                        )
                        first_byte_timeout = _first_byte_timeout(timeout)
                        record_model_call_estimate(call_record, payload)
                        with _open_provider_response(
                            self._urlopen,
                            req,
                            connect_timeout=connect_timeout,
                            first_byte_timeout=first_byte_timeout,
                            hard_deadline_monotonic=hard_deadline,
                        ) as resp:
                            raw = json.loads(_read_response_body(
                                resp,
                                hard_timeout=timeout,
                                hard_deadline_monotonic=hard_deadline,
                            ).decode("utf-8"))
                    except urllib.error.HTTPError as exc:
                        body = _read_response_body(
                            exc,
                            hard_timeout=timeout,
                            hard_deadline_monotonic=hard_deadline,
                        ).decode("utf-8", errors="replace")
                        record_model_diagnostic(
                            call_record,
                            payload,
                            response_payload={"http_status": exc.code, "error_body": body},
                            error=f"Provider HTTP {exc.code}",
                            outcome="failed",
                        )
                        raise _http_llm_error(exc, body) from exc
                    except Exception as exc:
                        record_model_diagnostic(call_record, payload, error=exc, outcome="failed")
                        raise
                    record_model_call_usage(call_record, raw)
                    record_model_diagnostic(call_record, payload, response_payload=raw)
        except (LLMError, ModelRequestAborted):
            raise
        except Exception as exc:
            raise LLMError(f"Provider request failed: {exc}") from exc

        try:
            message = raw["choices"][0]["message"]
            content = message["content"]
        except Exception as exc:
            raise LLMError(f"Unexpected provider response shape: {raw}") from exc
        if isinstance(content, list):
            content = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
                and part.get("type") in {"text", "output_text"}
                and part.get("thought") is not True
                and part.get("is_thinking") is not True
            )
        if isinstance(raw, dict):
            raw["_request"] = {
                "response_format": "json_object" if use_response_format else "prompt_only_json",
                "thinking": thinking_mode,
                "reasoning_effort": reasoning_effort or "provider_default",
                "max_tokens": payload["max_tokens"],
            }
        if content is None:
            detail = _raw_provider_response_detail(raw)
            raise LLMError(f"Model returned empty JSON content; {detail}" if detail else "Model returned empty JSON content")
        if not isinstance(content, str):
            raise LLMError(f"Model content must be a string, got {type(content).__name__}")
        if _is_gemini_model(self.config, target_model):
            content = _separate_gateway_final_json(content)
        if bool(_model_profile(self.config, target_model).get("strip_think_blocks")):
            content = _strip_think_blocks(content)
        return LLMResult(
            provider=self.config.name,
            model=str(payload["model"]),
            content=content,
            raw=raw,
        )

    def chat_json_object(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int = 120,
        attempts: int = 2,
        fallback_model: str | None = None,
        compact_messages: Any | None = None,
        attempt_callback: Any | None = None,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        last_error: LLMError | None = None
        self.last_json_retry_report = {
            "ok": False,
            "attempts": [],
            "recommendations": [],
        }
        plans = self._json_retry_plans(messages, model, max_tokens, attempts, fallback_model, compact_messages, thinking)
        for plan in plans:
            current_messages = plan["messages"]
            result: LLMResult | None = None
            if callable(attempt_callback):
                attempt_callback("started", self._json_attempt_report(plan))
            try:
                with model_diagnostic_hint(strategy=plan.get("strategy"), compact_prompt=plan.get("strategy") == "compact_fallback_disable_thinking"):
                    result = self.chat_json(
                        current_messages,
                        model=plan["model"],
                        temperature=temperature,
                        max_tokens=plan["max_tokens"],
                        thinking=plan.get("thinking"),
                        timeout=timeout,
                    )
                if _finish_reason(result) == "length":
                    detail = _provider_response_detail(result)
                    raise LLMError(f"Model JSON output reached max_tokens; {detail}" if detail else "Model JSON output reached max_tokens")
                value = parse_json_content_with_result(result)
                report = self._json_attempt_report(plan, result=result)
                self.last_json_retry_report["attempts"].append(report)
                self.last_json_retry_report["ok"] = True
                self.last_json_retry_report["recommendations"] = _token_recommendations(self.last_json_retry_report["attempts"])
                if callable(attempt_callback):
                    attempt_callback("succeeded", report)
                return value
            except LLMError as exc:
                last_error = exc
                report = self._json_attempt_report(plan, result=result, error=str(exc))
                self.last_json_retry_report["attempts"].append(report)
                self.last_json_retry_report["recommendations"] = _token_recommendations(self.last_json_retry_report["attempts"])
                if callable(attempt_callback):
                    attempt_callback("failed", report)
        raise last_error or LLMError("Model JSON task failed")

    def generate_image(
        self,
        prompt: str,
        output: Path,
        *,
        model: str | None = None,
        size: str | None = None,
        timeout: int = 240,
    ) -> ImageGenerationResult:
        if not self.config.api_key:
            raise LLMError(f"API key is not configured for provider: {self.config.name}")
        if not getattr(self.config, "supports_image_generation", True):
            raise LLMError(f"Image generation is disabled for provider: {self.config.name}")
        image_model = str(model or self.config.image_model or "").strip()
        if not image_model:
            raise LLMError(f"Image model is not configured for provider: {self.config.name}")
        if _is_dashscope_image_model(self.config, image_model):
            return self._generate_dashscope_image(prompt, output, model=image_model, size=size, timeout=timeout)
        image_size = _effective_image_size(image_model, str(size or self.config.image_size or "1024x1024"))
        last_error: LLMError | None = None
        for use_response_format in (True, False):
            payload = {
                "model": image_model,
                "prompt": prompt,
                "size": image_size,
                "n": 1,
            }
            if use_response_format:
                payload["response_format"] = "b64_json"
            if self.config.name == "sensenova" and image_model == "sensenova-u1.5-lite":
                payload["output_format"] = "png"
                # Generated teaching figures must not contain a provider logo.
                # SenseNova documents this option and recommends setting it explicitly.
                payload["watermark"] = False
            try:
                raw = self._post_json(f"{self.config.base_url}/images/generations", payload, timeout=timeout)
                image_bytes = _image_bytes_from_response(raw)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(image_bytes)
                if isinstance(raw, dict):
                    raw["_request"] = {
                        "endpoint": "/images/generations",
                        "response_format": "b64_json" if use_response_format else "provider_default",
                        "size": payload["size"],
                    }
                return ImageGenerationResult(self.config.name, image_model, output, raw)
            except LLMError as exc:
                last_error = exc
                if use_response_format and _is_unsupported_response_format_error(str(exc)):
                    continue
                if use_response_format and "response_format" in str(exc):
                    continue
                raise
        raise last_error or LLMError("Image generation failed")

    def _generate_dashscope_image(
        self,
        prompt: str,
        output: Path,
        *,
        model: str,
        size: str | None = None,
        timeout: int = 240,
    ) -> ImageGenerationResult:
        image_size = _dashscope_image_size(model, explicit_size=size, configured_size=self.config.image_size)
        payload = {
            "model": model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": {
                "size": image_size,
                "n": 1,
                "watermark": False,
            },
        }
        endpoint = _dashscope_multimodal_generation_endpoint(self.config.base_url)
        raw = self._post_json(endpoint, payload, timeout=timeout)
        image_bytes = _dashscope_image_bytes_from_response(raw)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image_bytes)
        raw["_request"] = {
            "endpoint": endpoint,
            "size": image_size,
            "provider_api": "dashscope_multimodal_generation",
        }
        return ImageGenerationResult(self.config.name, model, output, raw)

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout: int,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers or _provider_request_headers(self.config),
            method="POST",
        )
        try:
            with model_request_slot(self.config):
                with track_model_call(
                    provider=self.config.name,
                    model=str(payload.get("model") or self.config.default_model),
                    purpose="image_generation" if "/images/" in url or "generateContent" in url else "responses",
                    timeout=timeout,
                ) as call_record:
                    hard_deadline = time.monotonic() + max(1, int(timeout))
                    try:
                        connect = _transport_timeout("PRACTICE_MODEL_CONNECT_TIMEOUT_SECONDS", 15, timeout)
                        first_byte = _first_byte_timeout(timeout)
                        record_model_call_estimate(call_record, payload)
                        with _open_provider_response(
                            self._urlopen,
                            req,
                            connect_timeout=connect,
                            first_byte_timeout=first_byte,
                            hard_deadline_monotonic=hard_deadline,
                        ) as resp:
                            raw = json.loads(_read_response_body(
                                resp,
                                hard_timeout=timeout,
                                hard_deadline_monotonic=hard_deadline,
                            ).decode("utf-8"))
                    except urllib.error.HTTPError as exc:
                        body = _read_response_body(
                            exc,
                            hard_timeout=timeout,
                            hard_deadline_monotonic=hard_deadline,
                        ).decode("utf-8", errors="replace")
                        record_model_diagnostic(
                            call_record,
                            payload,
                            response_payload={"http_status": exc.code, "error_body": body},
                            error=f"Provider HTTP {exc.code}",
                            outcome="failed",
                        )
                        raise _http_llm_error(exc, body) from exc
                    except Exception as exc:
                        record_model_diagnostic(call_record, payload, error=exc, outcome="failed")
                        raise
                    record_model_call_usage(call_record, raw)
                    record_model_diagnostic(call_record, payload, response_payload=raw)
        except ModelRequestAborted:
            raise
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Provider request failed: {exc}") from exc
        if not isinstance(raw, dict):
            raise LLMError(f"Unexpected provider response shape: {raw}")
        return raw

    def _post_responses_stream(self, url: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        """Consume an OpenAI Responses SSE stream and return its final response.

        Reading the stream incrementally lets an upstream proxy observe response
        bytes while a long reasoning request is still running.  A few compatible
        gateways return a normal JSON response even when ``stream`` is requested;
        that shape is accepted as a compatibility fallback.
        """
        streaming_payload = {**payload, "stream": True}
        data = json.dumps(streaming_payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=_provider_request_headers(self.config, accept="text/event-stream"),
            method="POST",
        )
        try:
            with model_request_slot(self.config):
                with track_model_call(
                    provider=self.config.name,
                    model=str(payload.get("model") or self.config.default_model),
                    purpose="responses_stream",
                    timeout=timeout,
                ) as call_record:
                    deadline_monotonic = time.monotonic() + max(1, int(timeout))
                    try:
                        connect = _transport_timeout("PRACTICE_MODEL_CONNECT_TIMEOUT_SECONDS", 15, timeout)
                        first_byte = _first_byte_timeout(timeout)
                        record_model_call_estimate(call_record, streaming_payload)
                        with _open_provider_response(
                            self._urlopen,
                            req,
                            connect_timeout=connect,
                            first_byte_timeout=first_byte,
                            hard_deadline_monotonic=deadline_monotonic,
                        ) as resp:
                            if not hasattr(resp, "readline"):
                                raw = json.loads(_read_response_body(
                                    resp,
                                    hard_timeout=timeout,
                                    hard_deadline_monotonic=deadline_monotonic,
                                ).decode("utf-8"))
                                if not isinstance(raw, dict):
                                    raise LLMError(f"Unexpected provider response shape: {raw}")
                            else:
                                raw = _consume_responses_sse(
                                    resp,
                                    deadline_monotonic=deadline_monotonic,
                                    call_record=call_record,
                                )
                    except urllib.error.HTTPError as exc:
                        body = _read_response_body(
                            exc,
                            hard_timeout=timeout,
                            hard_deadline_monotonic=deadline_monotonic,
                        ).decode("utf-8", errors="replace")
                        record_model_diagnostic(
                            call_record,
                            streaming_payload,
                            response_payload={"http_status": exc.code, "error_body": body},
                            error=f"Provider HTTP {exc.code}",
                            outcome="failed",
                        )
                        raise _http_llm_error(exc, body) from exc
                    except Exception as exc:
                        record_model_diagnostic(call_record, streaming_payload, error=exc, outcome="failed")
                        raise
                    record_model_call_usage(call_record, raw)
                    record_model_diagnostic(call_record, streaming_payload, response_payload=raw)
                    return raw
        except (LLMError, ModelRequestAborted):
            raise
        except Exception as exc:
            raise LLMError(f"Provider streaming request failed: {exc}") from exc

    def _json_retry_plans(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        max_tokens: int | None,
        attempts: int,
        fallback_model: str | None,
        compact_messages: Any | None,
        thinking: str | None = None,
    ) -> list[dict[str, Any]]:
        primary_model = str(model or self.config.default_model)
        requested_ceiling = int(max_tokens or self.config.max_tokens or DEFAULT_MODEL_MAX_TOKENS)
        requested_thinking = _normalize_thinking_mode(thinking if thinking is not None else getattr(self.config, "thinking_mode", "auto"))
        base_tokens = _effective_max_tokens(self.config, requested_ceiling, requested_thinking)
        increased_tokens = max(base_tokens * 2, int(self.config.max_tokens or base_tokens), DEFAULT_MODEL_MAX_TOKENS)
        if _is_reasoning_heavy_provider(self.config):
            increased_tokens = max(increased_tokens, 32768)
        # Retries may change strategy, but must not silently exceed the caller's budget ceiling.
        base_tokens = min(base_tokens, requested_ceiling)
        increased_tokens = min(increased_tokens, requested_ceiling)
        if thinking is not None:
            return [
                {"strategy": f"attempt_{idx + 1}", "model": primary_model, "max_tokens": base_tokens, "thinking": requested_thinking, "messages": messages}
                for idx in range(max(1, attempts))
            ]
        if fallback_model or compact_messages:
            target_model = str(fallback_model or primary_model)
            compact = compact_messages(messages) if callable(compact_messages) else messages
            return [
                {"strategy": "primary", "model": primary_model, "max_tokens": base_tokens, "thinking": None, "messages": messages},
                {"strategy": "increase_max_tokens", "model": primary_model, "max_tokens": increased_tokens, "thinking": None, "messages": messages},
                {"strategy": "disable_thinking", "model": primary_model, "max_tokens": increased_tokens, "thinking": "disabled", "messages": messages},
                {"strategy": "fallback_model", "model": target_model, "max_tokens": increased_tokens, "thinking": None, "messages": messages},
                {"strategy": "compact_fallback_disable_thinking", "model": target_model, "max_tokens": increased_tokens, "thinking": "disabled", "messages": compact},
            ]
        return [
            {"strategy": "primary", "model": primary_model, "max_tokens": base_tokens, "thinking": None, "messages": messages},
            {"strategy": "increase_max_tokens", "model": primary_model, "max_tokens": increased_tokens, "thinking": None, "messages": messages},
            {"strategy": "disable_thinking", "model": primary_model, "max_tokens": increased_tokens, "thinking": "disabled", "messages": messages},
        ] if _is_reasoning_heavy_provider(self.config) else [
            {"strategy": f"attempt_{idx + 1}", "model": primary_model, "max_tokens": base_tokens, "thinking": None, "messages": messages}
            for idx in range(max(1, attempts))
        ]

    def _json_attempt_report(self, plan: dict[str, Any], result: LLMResult | None = None, error: str = "") -> dict[str, Any]:
        raw = result.raw if result else {}
        usage = _normalized_usage(raw)
        content = result.content if result else ""
        request_meta = raw.get("_request") if isinstance(raw, dict) else {}
        report = {
            "strategy": plan.get("strategy"),
            "model": plan.get("model"),
            "max_tokens": plan.get("max_tokens"),
            "thinking": request_meta.get("thinking") if isinstance(request_meta, dict) else plan.get("thinking"),
            "compact_prompt": plan.get("strategy") == "compact_fallback_disable_thinking",
            "finish_reason": _normalized_finish_reason(raw),
            "content_length": len(content or ""),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "response_format": request_meta.get("response_format") if isinstance(request_meta, dict) else None,
            "error": error,
        }
        if isinstance(request_meta, dict):
            for key in ("protocol_requested", "protocol_used", "protocol_fallback_reason"):
                if request_meta.get(key):
                    report[key] = request_meta[key]
        return report


class ResponsesAPIClient(OpenAICompatibleClient):
    """Responses API adapter used by every built-in text provider.

    It intentionally reuses the existing retry, image, and JSON parsing helpers
    so text and multimodal calls share one transport implementation.
    """

    def _chat_json_once(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: str | None = None,
        timeout: int = 120,
        use_response_format: bool = True,
    ) -> LLMResult:
        target_model = str(model or self.config.default_model).strip()
        protocol = _model_api_protocol(self.config, target_model)
        if protocol not in {"responses", "responses_api"}:
            delegated = _client_for_model_protocol(self.config, target_model, protocol)
            delegated._urlopen = self._urlopen
            return delegated._chat_json_once(
                messages, model=target_model, temperature=temperature, max_tokens=max_tokens,
                thinking=thinking, timeout=timeout, use_response_format=use_response_format,
            )
        thinking_mode = _model_thinking_mode(
            self.config,
            target_model,
            thinking if thinking is not None else getattr(self.config, "thinking_mode", "auto"),
        )
        requested_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        responses_input = _responses_input_items(messages)
        if use_response_format and not _value_mentions_json(responses_input):
            responses_input.insert(0, {"role": "system", "content": "Return one valid JSON object."})
        payload: dict[str, Any] = {
            "model": target_model,
            "input": responses_input,
            "max_output_tokens": _effective_model_max_tokens(self.config, target_model, requested_tokens, thinking_mode),
        }
        omitted = _model_omitted_parameters(self.config, target_model)
        if "store" not in omitted:
            payload["store"] = False
        # Reasoning models may not accept sampling parameters. Preserve an
        # explicit caller override, but do not forward the Chat Completions
        # provider default automatically.
        if temperature is not None and "temperature" not in omitted:
            payload["temperature"] = temperature
        if use_response_format:
            payload["text"] = {"format": {"type": "json_object"}}
        reasoning_effort = _responses_reasoning_effort(thinking_mode)
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}

        try:
            if bool(getattr(self.config, "responses_streaming", True)):
                raw = self._post_responses_stream(f"{self.config.base_url}/responses", payload, timeout=timeout)
            else:
                raw = self._post_json(f"{self.config.base_url}/responses", payload, timeout=timeout)
        except LLMError as exc:
            if bool(getattr(self.config, "responses_fallback_to_chat", True)) and _is_responses_endpoint_unsupported(str(exc)):
                fallback_client = _client_for_model_protocol(self.config, target_model, "chat_completions")
                fallback_client._urlopen = self._urlopen
                fallback = fallback_client._chat_json_once(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    thinking=thinking,
                    timeout=timeout,
                    use_response_format=use_response_format,
                )
                request_meta = fallback.raw.setdefault("_request", {})
                if isinstance(request_meta, dict):
                    request_meta.update(
                        {
                            "protocol_requested": "responses",
                            "protocol_used": "chat_completions",
                            "protocol_fallback_reason": _safe_protocol_fallback_reason(str(exc)),
                        }
                    )
                return fallback
            raise
        content = _responses_output_text(raw)
        if isinstance(raw, dict):
            raw["_request"] = {
                "endpoint": "/responses",
                "response_format": "json_object" if use_response_format else "prompt_only_json",
                "thinking": thinking_mode,
                "max_output_tokens": payload["max_output_tokens"],
                "store": payload.get("store", "omitted"),
                "stream": bool(getattr(self.config, "responses_streaming", True)),
                "reasoning_effort": reasoning_effort or "provider_default",
            }
        if not content:
            detail = _responses_response_detail(raw)
            raise LLMError(f"Model returned empty response content; {detail}" if detail else "Model returned empty response content")
        return LLMResult(
            provider=self.config.name,
            model=str(payload["model"]),
            content=content,
            raw=raw,
        )


class AnthropicMessagesClient(OpenAICompatibleClient):
    """Native Anthropic Messages adapter with final-text-only extraction."""

    def _chat_json_once(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: str | None = None,
        timeout: int = 120,
        use_response_format: bool = True,
    ) -> LLMResult:
        target_model = str(model or self.config.default_model).strip()
        protocol = _model_api_protocol(self.config, target_model)
        if protocol not in {"anthropic_messages", "messages"}:
            delegated = _client_for_model_protocol(self.config, target_model, protocol)
            delegated._urlopen = self._urlopen
            return delegated._chat_json_once(
                messages, model=target_model, temperature=temperature, max_tokens=max_tokens,
                thinking=thinking, timeout=timeout, use_response_format=use_response_format,
            )
        thinking_mode = _model_thinking_mode(
            self.config,
            target_model,
            thinking if thinking is not None else getattr(self.config, "thinking_mode", "auto"),
        )
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").lower()
            content = message.get("content")
            if role in {"system", "developer"}:
                system_parts.append(_message_plain_text(content))
                continue
            anthropic_messages.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": _anthropic_message_content(content),
            })
        if use_response_format:
            system_parts.append("Return only one valid JSON object as the final answer, with no markdown fence or commentary.")
        requested_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        payload: dict[str, Any] = {
            "model": target_model,
            "max_tokens": _effective_model_max_tokens(self.config, target_model, requested_tokens, thinking_mode),
            "messages": anthropic_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(part for part in system_parts if part)
        omitted = _model_omitted_parameters(self.config, target_model)
        if temperature is not None and "temperature" not in omitted and thinking_mode in {"auto", "disabled"}:
            payload["temperature"] = temperature
        if thinking_mode == "disabled":
            payload["thinking"] = {"type": "disabled"}
        elif thinking_mode != "auto":
            effort = "medium" if thinking_mode == "enabled" else thinking_mode
            payload["thinking"] = {"type": "adaptive", "display": "omitted"}
            payload["output_config"] = {"effort": effort}
        output_schema = _anthropic_output_schema(messages) if use_response_format else None
        if output_schema:
            payload.setdefault("output_config", {})["format"] = {
                "type": "json_schema",
                "schema": output_schema,
            }
        headers = _provider_request_headers(self.config)
        headers["x-api-key"] = self.config.api_key
        headers["anthropic-version"] = "2023-06-01"
        try:
            raw = self._post_json(
                f"{self.config.base_url}/messages",
                payload,
                timeout=timeout,
                headers=headers,
            )
        except LLMError as exc:
            profile = _model_profile(self.config, target_model)
            if bool(profile.get("messages_fallback_to_chat")) and _is_responses_endpoint_unsupported(str(exc)):
                fallback_client = _client_for_model_protocol(self.config, target_model, "chat_completions")
                fallback_client._urlopen = self._urlopen
                fallback = fallback_client._chat_json_once(
                    messages, model=target_model, temperature=temperature, max_tokens=max_tokens,
                    thinking=thinking, timeout=timeout, use_response_format=use_response_format,
                )
                fallback.raw.setdefault("_request", {}).update({
                    "protocol_requested": "anthropic_messages",
                    "protocol_used": "chat_completions",
                    "protocol_fallback_reason": _safe_protocol_fallback_reason(str(exc)),
                })
                return fallback
            raise
        parts = raw.get("content") if isinstance(raw, dict) else None
        content = "".join(
            str(part.get("text") or "")
            for part in (parts or [])
            if isinstance(part, dict) and part.get("type") == "text"
        )
        if not content:
            raise LLMError(f"Model returned empty Messages content; {_raw_provider_response_detail(raw)}")
        raw["_request"] = {
            "endpoint": "/messages",
            "protocol_used": "anthropic_messages",
            "response_format": "json_schema" if output_schema else "prompt_only_json" if use_response_format else "text",
            "thinking": thinking_mode,
            "max_tokens": payload["max_tokens"],
        }
        return LLMResult(provider=self.config.name, model=target_model, content=content, raw=raw)


def create_llm_client(config: ProviderConfig) -> LLMClientProtocol:
    """Create a client from provider configuration without changing defaults."""
    protocol = str(getattr(config, "api_protocol", "chat_completions") or "chat_completions").strip().lower()
    if protocol in {"responses", "responses_api"}:
        return ResponsesAPIClient(config)
    if protocol in {"chat_completions", "openai_compatible", ""}:
        return OpenAICompatibleClient(config)
    if protocol in {"anthropic_messages", "messages"}:
        return AnthropicMessagesClient(config)
    raise ValueError(f"Unsupported API protocol: {protocol}")


def _is_unsupported_response_format_error(error: str) -> bool:
    text = str(error or "").lower()
    format_parameter = "response_format" in text or "text.format" in text or ("text" in text and "format" in text)
    return format_parameter and (
        "not support" in text
        or "not supported" in text
        or "not valid" in text
        or "invalidparameter" in text
        or "unknown parameter" in text
    )


def _is_responses_endpoint_unsupported(error: str) -> bool:
    text = str(error or "").lower()
    if "provider http 405" in text or "provider http 501" in text:
        return True
    if "provider http 404" not in text:
        return False
    # A provider may also use 404 for an unknown model. Do not mask that as
    # protocol incompatibility.
    return "model" not in text


def _safe_protocol_fallback_reason(error: str) -> str:
    text = str(error or "")
    for status in ("404", "405", "501"):
        if f"HTTP {status}" in text:
            return f"responses_endpoint_http_{status}"
    return "responses_endpoint_unsupported"


def _is_reasoning_heavy_provider(config: ProviderConfig) -> bool:
    base_url = str(getattr(config, "base_url", "") or "").lower()
    return "bigmodel.cn" in base_url


def _is_dashscope_image_model(config: ProviderConfig, model: str) -> bool:
    name = str(getattr(config, "name", "") or "").lower()
    base_url = str(getattr(config, "base_url", "") or "").lower()
    model_name = str(model or "").lower()
    return (name in {"bailian", "dashscope"} or "dashscope.aliyuncs.com" in base_url or ".maas.aliyuncs.com" in base_url) and model_name.startswith(("wan", "qwen-image", "z-image"))


def _dashscope_multimodal_generation_endpoint(base_url: str) -> str:
    text = str(base_url or "").rstrip("/")
    marker = "/compatible-mode/v1"
    if marker in text:
        return f"{text.split(marker, 1)[0]}/api/v1/services/aigc/multimodal-generation/generation"
    if text.endswith("/api/v1"):
        return f"{text}/services/aigc/multimodal-generation/generation"
    return f"{text}/api/v1/services/aigc/multimodal-generation/generation"


def _default_dashscope_image_size(model: str) -> str:
    model_name = str(model or "").lower()
    if model_name.startswith("qwen-image"):
        return "2048*2048"
    return "2K"


def _dashscope_image_size(model: str, *, explicit_size: str | None, configured_size: str | None) -> str:
    model_name = str(model or "").lower()
    requested = str(explicit_size if explicit_size is not None else configured_size or "").strip()
    if model_name.startswith("qwen-image"):
        aliases = {"1K": "1024*1024", "2K": "2048*2048", "4K": "4096*4096"}
        if requested.upper() in aliases:
            return aliases[requested.upper()]
        if not requested:
            return _default_dashscope_image_size(model)
    if model_name.startswith("qwen-image") and not requested:
        return _default_dashscope_image_size(model)
    return requested or _default_dashscope_image_size(model)


def _effective_max_tokens(config: ProviderConfig, requested_tokens: Any, thinking_mode: str) -> int:
    tokens = int(requested_tokens or getattr(config, "max_tokens", DEFAULT_MODEL_MAX_TOKENS) or DEFAULT_MODEL_MAX_TOKENS)
    # Explicit reasoning modes consume output tokens before the final answer on
    # several providers, not only on one vendor-specific endpoint. Respect the
    # configured full allowance whenever reasoning was deliberately selected.
    if thinking_mode in {"enabled", "low", "medium", "high", "xhigh"}:
        return max(tokens, int(getattr(config, "max_tokens", 0) or 0), DEFAULT_MODEL_MAX_TOKENS)
    if _is_reasoning_heavy_provider(config) and thinking_mode == "auto":
        return max(tokens, int(getattr(config, "max_tokens", 0) or 0), DEFAULT_MODEL_MAX_TOKENS)
    return tokens


def _normalize_thinking_mode(value: Any) -> str:
    text = str(value or "auto").strip().lower()
    aliases = {
        "on": "enabled",
        "enable": "enabled",
        "enabled": "enabled",
        "true": "enabled",
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "off": "disabled",
        "disable": "disabled",
        "disabled": "disabled",
        "false": "disabled",
        "auto": "auto",
        "default": "auto",
        "none": "auto",
        "": "auto",
    }
    return aliases.get(text, "auto")


def _deepseek_reasoning_effort(config: ProviderConfig, thinking_mode: str) -> str:
    """Translate shared effort levels to the provider's supported values."""

    name = str(getattr(config, "name", "") or "").lower()
    base_url = str(getattr(config, "base_url", "") or "").lower()
    if name != "deepseek" and "api.deepseek.com" not in base_url:
        return ""
    normalized = _normalize_thinking_mode(thinking_mode)
    if normalized in {"low", "medium", "high", "enabled"}:
        return "high"
    if normalized == "xhigh":
        return "max"
    return ""


def _responses_reasoning_effort(thinking_mode: str) -> str:
    """Map the UI's thinking choice to the Responses API effort field."""
    normalized = _normalize_thinking_mode(thinking_mode)
    if normalized in {"minimal", "low", "medium", "high", "xhigh"}:
        return normalized
    if normalized == "enabled":
        return "medium"
    if normalized == "disabled":
        return "none"
    return ""


def _set_stream_read_deadline(response: Any, remaining_seconds: float) -> None:
    """Apply the remaining wall-clock budget to urllib's underlying socket."""

    candidates = [response]
    for _ in range(5):
        current = candidates[-1]
        nested = next(
            (
                getattr(current, name)
                for name in ("_sock", "raw", "fp", "_fp")
                if getattr(current, name, None) is not None
            ),
            None,
        )
        if nested is None or nested in candidates:
            break
        candidates.append(nested)
    for candidate in reversed(candidates):
        setter = getattr(candidate, "settimeout", None)
        if callable(setter):
            try:
                setter(max(0.05, remaining_seconds))
                return
            except Exception:
                continue


def _consume_responses_sse(
    response: Any,
    *,
    deadline_monotonic: float | None = None,
    call_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize Responses API server-sent events into one response object."""
    final_response: dict[str, Any] | None = None
    text_deltas: list[str] = []
    terminal_texts: list[str] = []
    data_lines: list[str] = []
    reasoning_item_ids: set[str] = set()
    step_types: dict[str, str] = {}

    def consume_event() -> None:
        nonlocal final_response
        if not data_lines:
            return
        data_text = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data_text or data_text == "[DONE]":
            return
        try:
            event = json.loads(data_text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Provider returned invalid streaming event: {data_text[:500]}") from exc
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type") or event.get("event_type") or "")
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") in {"reasoning", "thought", "thinking"}:
                item_id = str(item.get("id") or event.get("item_id") or "")
                if item_id:
                    reasoning_item_ids.add(item_id)
                return
        if event_type == "step.start":
            step = event.get("step")
            if isinstance(step, dict):
                step_types[str(event.get("index", ""))] = str(step.get("type") or "")
            return
        if event_type == "step.delta":
            delta = event.get("delta")
            if (
                isinstance(delta, dict)
                and step_types.get(str(event.get("index", ""))) == "model_output"
                and delta.get("type") in {"text", "output_text"}
            ):
                text = delta.get("text")
                if isinstance(text, str):
                    text_deltas.append(text)
                    record_model_stream_progress(call_record, text)
            return
        if event_type == "response.output_text.delta":
            if (
                str(event.get("item_id") or "") in reasoning_item_ids
                or event.get("thought") is True
                or event.get("is_thinking") is True
            ):
                return
            delta = event.get("delta")
            if isinstance(delta, str):
                text_deltas.append(delta)
                record_model_stream_progress(call_record, delta)
            return
        if event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                terminal_texts.append(text)
            return
        if event_type == "response.content_part.done":
            part = event.get("part")
            if (
                isinstance(part, dict)
                and part.get("type") in {"text", "output_text"}
                and part.get("thought") is not True
                and part.get("is_thinking") is not True
                and str(event.get("item_id") or "") not in reasoning_item_ids
            ):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    terminal_texts.append(text)
            return
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                text = _responses_output_text({"output": [item]})
                if text.strip():
                    terminal_texts.append(text)
            return
        if event_type == "response.completed":
            candidate = event.get("response")
            if isinstance(candidate, dict):
                final_response = candidate
            return
        if event_type in {"response.failed", "error"}:
            detail = event.get("error") or event.get("response") or event
            raise LLMError(f"Provider streaming response failed: {str(detail)[:800]}")
        # Compatibility gateways sometimes send the final Response object as a
        # data event without wrapping it in ``response.completed``.
        if event.get("object") == "response" or ("output" in event and "status" in event):
            final_response = event

    while True:
        ensure_model_request_active()
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise LLMTimeoutError(
                    "Responses stream exceeded total wall-clock deadline",
                    transport_phase="hard_timeout",
                    partial_output_received=bool(text_deltas or terminal_texts),
                )
            idle = _transport_timeout("PRACTICE_MODEL_READ_IDLE_TIMEOUT_SECONDS", 45, max(1, int(remaining)))
            _set_stream_read_deadline(response, min(remaining, float(idle)))
        try:
            line = response.readline()
        except (socket.timeout, TimeoutError) as exc:
            phase = "hard_timeout" if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic else "read_idle"
            message = "模型请求超过单次硬截止时间。" if phase == "hard_timeout" else "模型响应读取空闲超时。"
            raise LLMError(
                message,
                transport_phase=phase,
                partial_output_received=bool(text_deltas or terminal_texts),
            ) from exc
        if not line:
            consume_event()
            break
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        stripped = str(line).rstrip("\r\n")
        if not stripped:
            consume_event()
        elif stripped.startswith("data:"):
            data_lines.append(stripped[5:].lstrip())

    if final_response is None:
        accumulated_text = "".join(text_deltas) if text_deltas else (terminal_texts[-1] if terminal_texts else "")
        if not accumulated_text:
            raise LLMError("Provider streaming response ended without a completed response")
        try:
            json.loads(accumulated_text)
        except json.JSONDecodeError as exc:
            raise LLMError("Provider streaming response ended without a completed response") from exc
        final_response = {
            "object": "response",
            "status": "completed",
            "output_text": accumulated_text,
            "_stream_salvaged": True,
        }
    elif text_deltas and not _responses_output_text(final_response):
        final_response["output_text"] = "".join(text_deltas)
    return final_response


def _parse_image_size_pixels(size: str) -> tuple[int, int] | None:
    text = str(size or "").strip().lower().replace("*", "x")
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width = int(left.strip())
        height = int(right.strip())
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _effective_image_size(model: str, size: str) -> str:
    requested = str(size or "1024x1024").strip() or "1024x1024"
    model_name = str(model or "").lower()
    parsed = _parse_image_size_pixels(requested)
    if not parsed or "seedream" not in model_name:
        return requested
    width, height = parsed
    pixels = width * height
    if "seedream-5" in model_name:
        if pixels < 3_686_400:
            return "2048x2048"
        if pixels > 10_404_496:
            return "3072x3072"
    elif "seedream-4-5" in model_name:
        if pixels < 3_686_400:
            return "2048x2048"
        if pixels > 16_777_216:
            return "4096x4096"
    elif "seedream-4-0" in model_name and pixels < 921_600:
        return "1024x1024"
    return requested


def _image_bytes_from_response(raw: dict[str, Any]) -> bytes:
    data = raw.get("data")
    if not isinstance(data, list) or not data:
        raise LLMError(f"Unexpected image response shape: {raw}")
    item = data[0]
    if not isinstance(item, dict):
        raise LLMError(f"Unexpected image item shape: {item}")
    b64 = item.get("b64_json") or item.get("image_base64") or item.get("base64")
    if isinstance(b64, str) and b64.strip():
        try:
            return base64.b64decode(b64)
        except Exception as exc:
            raise LLMError("Provider returned invalid base64 image data") from exc
    url = item.get("url")
    if isinstance(url, str) and url.strip():
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except Exception as exc:
            raise LLMError(f"Failed to download generated image: {exc}") from exc
    raise LLMError(f"Provider image response has no b64_json or url: {item}")


def _dashscope_image_bytes_from_response(raw: dict[str, Any]) -> bytes:
    output = raw.get("output")
    if not isinstance(output, dict):
        code = raw.get("code")
        message = raw.get("message")
        if code or message:
            raise LLMError(f"Provider image generation failed: {code or ''} {message or ''}".strip())
        raise LLMError(f"Unexpected DashScope image response shape: {raw}")
    choices = output.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError(f"Unexpected DashScope image response output shape: {raw}")
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else {}
        content = message.get("content") if isinstance(message, dict) else []
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            image = item.get("image") or item.get("url")
            if isinstance(image, str) and image.strip():
                if image.startswith("data:") and "," in image:
                    image = image.split(",", 1)[1]
                    try:
                        return base64.b64decode(image)
                    except Exception as exc:
                        raise LLMError("Provider returned invalid base64 image data") from exc
                if image.startswith(("http://", "https://")):
                    try:
                        with urllib.request.urlopen(image, timeout=120) as resp:
                            return resp.read()
                    except Exception as exc:
                        raise LLMError(f"Failed to download generated image: {exc}") from exc
                try:
                    return base64.b64decode(image)
                except Exception as exc:
                    raise LLMError("Provider returned unsupported image data") from exc
    raise LLMError(f"DashScope image response has no image URL or base64 data: {raw}")


def parse_json_content(content: str) -> dict[str, Any]:
    if not str(content or "").strip():
        raise LLMError("Model returned empty JSON content")
    cleaned = str(content).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        extracted = _extract_json_object(cleaned)
        if extracted and extracted != cleaned:
            try:
                value = json.loads(extracted)
            except json.JSONDecodeError:
                preview = str(content).replace("\n", "\\n")[:200]
                raise LLMError(f"Model did not return valid JSON: {exc}; content preview: {preview}") from exc
        else:
            preview = str(content).replace("\n", "\\n")[:200]
            raise LLMError(f"Model did not return valid JSON: {exc}; content preview: {preview}") from exc
    if not isinstance(value, dict):
        raise LLMError("Model JSON output must be an object")
    return value


def parse_json_content_with_result(result: LLMResult) -> dict[str, Any]:
    try:
        return parse_json_content(result.content)
    except LLMError as exc:
        detail = _provider_response_detail(result)
        if detail:
            raise LLMError(f"{exc}; {detail}") from exc
        raise


def _finish_reason(result: LLMResult) -> str:
    return _normalized_finish_reason(result.raw or {})


def _provider_response_detail(result: LLMResult) -> str:
    raw = result.raw or {}
    if isinstance(raw, dict) and ("output" in raw or "output_text" in raw):
        return _responses_response_detail(raw)
    return _raw_provider_response_detail(raw)


def _normalized_finish_reason(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    choices = raw.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    if isinstance(choice, dict) and choice.get("finish_reason"):
        return str(choice["finish_reason"])
    status = str(raw.get("status") or "")
    incomplete = raw.get("incomplete_details")
    reason = str(incomplete.get("reason") or "") if isinstance(incomplete, dict) else ""
    if status == "incomplete" and reason in {"max_output_tokens", "max_tokens"}:
        return "length"
    if status == "completed":
        return "stop"
    return reason or status


def _normalized_usage(raw: dict[str, Any]) -> dict[str, int | None]:
    usage = raw.get("usage") if isinstance(raw, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    output_details = usage.get("output_tokens_details")
    if not isinstance(output_details, dict):
        output_details = {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "reasoning_tokens": completion_details.get("reasoning_tokens", output_details.get("reasoning_tokens")),
    }


def _raw_provider_response_detail(raw: dict[str, Any]) -> str:
    choice = (raw.get("choices") or [{}])[0] if isinstance(raw, dict) else {}
    usage = raw.get("usage") if isinstance(raw, dict) else {}
    completion_details = usage.get("completion_tokens_details") if isinstance(usage, dict) else {}
    if not isinstance(completion_details, dict):
        completion_details = {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        message = {}
    reasoning_content = message.get("reasoning_content")
    parts = []
    if choice.get("finish_reason"):
        parts.append(f"finish_reason={choice.get('finish_reason')}")
    if usage.get("completion_tokens") is not None:
        parts.append(f"completion_tokens={usage.get('completion_tokens')}")
    if completion_details.get("reasoning_tokens") is not None:
        parts.append(f"reasoning_tokens={completion_details.get('reasoning_tokens')}")
    if isinstance(reasoning_content, str) and reasoning_content:
        parts.append(f"reasoning_content_length={len(reasoning_content)}")
    return ", ".join(parts)


def _separate_gateway_final_json(text: str) -> str:
    """Recover a final JSON answer when a gateway mislabeled thought as output."""

    cleaned = str(text or "").strip()
    if not cleaned or cleaned.startswith("{"):
        return text

    starts = list(re.finditer(r"\{(?=\s*\")", cleaned))
    for match in reversed(starts[-200:]):
        candidate = _extract_json_object(cleaned[match.start():])
        if not candidate:
            continue
        suffix = cleaned[match.start() + len(candidate):].strip()
        if suffix and not re.fullmatch(r"(?:```(?:json)?\s*)+", suffix, flags=re.IGNORECASE):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            # The practice parser owns safe LaTeX backslash repair. Preserve
            # that repair path when a complete exercises object is apparent.
            if re.match(r'\{\s*"exercises"\s*:', candidate):
                return candidate
            continue
        if isinstance(parsed, dict):
            return candidate
    return text


def _responses_output_text(raw: dict[str, Any]) -> str:
    """Extract text from a Responses output array without exposing reasoning items."""
    if not isinstance(raw, dict):
        return ""
    output = raw.get("output")
    steps = raw.get("steps")
    parts: list[str] = []
    for items, accepted_type in ((output, "message"), (steps, "model_output")):
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("type") != accepted_type:
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") in {"output_text", "text"}
                    and part.get("thought") is not True
                    and part.get("is_thinking") is not True
                ):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        if parts:
            return _separate_gateway_final_json("".join(parts))
    # A populated structured output containing only reasoning must never be
    # replaced with a gateway's convenience field, which may be a thought summary.
    if (isinstance(output, list) and output) or (isinstance(steps, list) and steps):
        return ""
    direct = raw.get("output_text")
    return _separate_gateway_final_json(direct) if isinstance(direct, str) and direct.strip() else ""


def _responses_input_items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate existing Chat Completions messages to Responses input items."""
    translated: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, str):
            translated.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            translated.append(dict(message))
            continue
        parts: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type == "text":
                parts.append({"type": "input_text", "text": str(part.get("text") or "")})
            elif part_type == "image_url":
                image_url = part.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if url:
                    parts.append({"type": "input_image", "image_url": str(url)})
            elif part_type in {"input_text", "input_image"}:
                parts.append(dict(part))
        translated.append({"role": role, "content": parts})
    return translated


def _value_mentions_json(value: Any) -> bool:
    """Return whether textual prompt content explicitly mentions JSON."""
    if isinstance(value, str):
        return "json" in value.lower()
    if isinstance(value, list):
        return any(_value_mentions_json(item) for item in value)
    if isinstance(value, dict):
        return any(_value_mentions_json(item) for key, item in value.items() if key not in {"image_url"})
    return False


def _responses_response_detail(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    parts: list[str] = []
    status = raw.get("status")
    if status:
        parts.append(f"status={status}")
    incomplete = raw.get("incomplete_details")
    if isinstance(incomplete, dict) and incomplete.get("reason"):
        parts.append(f"reason={incomplete['reason']}")
    usage = raw.get("usage")
    if isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens"):
            if usage.get(key) is not None:
                parts.append(f"{key}={usage[key]}")
    return ", ".join(parts)


def _token_recommendations(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for attempt in attempts:
        max_tokens = int(attempt.get("max_tokens") or 0)
        completion_tokens = attempt.get("completion_tokens")
        reasoning_tokens = attempt.get("reasoning_tokens")
        finish_reason = str(attempt.get("finish_reason") or "")
        error = str(attempt.get("error") or "")
        near_limit = isinstance(completion_tokens, int) and max_tokens and completion_tokens >= int(max_tokens * 0.9)
        reasoning_near_limit = isinstance(reasoning_tokens, int) and max_tokens and reasoning_tokens >= int(max_tokens * 0.8)
        if finish_reason == "length" or near_limit or reasoning_near_limit or "finish_reason=length" in error:
            recommendations.append(
                {
                    "reason": "模型输出接近或达到 max_tokens，上调该阶段 max_tokens 可降低空 content 或 JSON 截断风险。",
                    "current_max_tokens": max_tokens,
                    "suggested_max_tokens": max(max_tokens * 2, 4096),
                    "model": attempt.get("model"),
                    "thinking": attempt.get("thinking") or "default",
                }
            )
    return recommendations


def _extract_json_object(content: str) -> str | None:
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx, char in enumerate(content[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : idx + 1]
    return None
