from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from .concurrency import ModelRequestAborted, model_request_slot
from .runtime_monitor import record_model_call_usage, track_model_call
from .settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig


class LLMError(RuntimeError):
    pass


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


class OpenAICompatibleClient:
    def __new__(cls, config: ProviderConfig):
        protocol = str(getattr(config, "api_protocol", "chat_completions") or "chat_completions").strip().lower()
        if cls is OpenAICompatibleClient and protocol in {"responses", "responses_api"}:
            return super().__new__(ResponsesAPIClient)
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
        thinking_mode = _normalize_thinking_mode(thinking if thinking is not None else getattr(self.config, "thinking_mode", "auto"))
        requested_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        payload = {
            "model": model or self.config.default_model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": _effective_max_tokens(self.config, requested_tokens, thinking_mode),
        }
        if use_response_format:
            payload["response_format"] = {"type": "json_object"}
        if thinking_mode in {"enabled", "disabled"}:
            payload["thinking"] = {"type": thinking_mode}
        reasoning_effort = _deepseek_reasoning_effort(self.config, thinking_mode)
        if reasoning_effort:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = reasoning_effort
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
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
                    with self._urlopen(req, timeout=timeout) as resp:
                        raw = json.loads(resp.read().decode("utf-8"))
                    record_model_call_usage(call_record, raw)
        except (LLMError, ModelRequestAborted):
            raise
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"Provider HTTP {exc.code}: {body[:800]}") from exc
        except Exception as exc:
            raise LLMError(f"Provider request failed: {exc}") from exc

        try:
            content = raw["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(f"Unexpected provider response shape: {raw}") from exc
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
        if _is_yunwu_gemini_image_model(self.config, image_model):
            return self._generate_yunwu_gemini_image(prompt, output, model=image_model, size=size, timeout=timeout)
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

    def _generate_yunwu_gemini_image(
        self,
        prompt: str,
        output: Path,
        *,
        model: str,
        size: str | None = None,
        timeout: int = 240,
    ) -> ImageGenerationResult:
        endpoint = f"{_native_api_root(self.config.base_url)}/v1beta/models/{model}:generateContent"
        payload = {
            "response_format": "url",
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": _gemini_aspect_ratio(size or self.config.image_size)},
            },
        }
        raw = self._post_json(endpoint, payload, timeout=timeout)
        image_uri, mime_type = _gemini_image_location(raw)
        image_bytes = self._download_image_uri(image_uri, timeout=timeout)
        image_path = output.with_suffix(_image_suffix_for_mime_type(mime_type))
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(image_bytes)
        raw["_request"] = {
            "endpoint": endpoint,
            "provider_api": "yunwu_gemini_generate_content",
            "aspect_ratio": payload["generationConfig"]["imageConfig"]["aspectRatio"],
            "mime_type": mime_type,
        }
        return ImageGenerationResult(self.config.name, model, image_path, raw)

    def _download_image_uri(self, image_uri: str, *, timeout: int) -> bytes:
        if image_uri.startswith("data:") and "," in image_uri:
            try:
                return base64.b64decode(image_uri.split(",", 1)[1])
            except Exception as exc:
                raise LLMError("Provider returned invalid image data URI") from exc
        request = urllib.request.Request(image_uri, headers=_image_download_headers(image_uri, self.config.api_key))
        try:
            with self._urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            raise LLMError(f"Failed to download generated image: {exc}") from exc

    def _post_json(self, url: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
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
                    with self._urlopen(req, timeout=timeout) as resp:
                        raw = json.loads(resp.read().decode("utf-8"))
                    record_model_call_usage(call_record, raw)
        except ModelRequestAborted:
            raise
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"Provider HTTP {exc.code}: {body[:800]}") from exc
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
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
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
                    with self._urlopen(req, timeout=timeout) as resp:
                        if not hasattr(resp, "readline"):
                            raw = json.loads(resp.read().decode("utf-8"))
                            if not isinstance(raw, dict):
                                raise LLMError(f"Unexpected provider response shape: {raw}")
                        else:
                            raw = _consume_responses_sse(resp)
                        record_model_call_usage(call_record, raw)
                        return raw
        except (LLMError, ModelRequestAborted):
            raise
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"Provider HTTP {exc.code}: {body[:800]}") from exc
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
    """Opt-in Responses API adapter.

    Existing providers remain on ``OpenAICompatibleClient``. This adapter is
    selected only by ``create_llm_client`` when ``api_protocol`` is explicitly
    set to ``responses``. It intentionally reuses the existing retry, image,
    and JSON parsing helpers so the migration surface stays small.
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
        thinking_mode = _normalize_thinking_mode(
            thinking if thinking is not None else getattr(self.config, "thinking_mode", "auto")
        )
        requested_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        responses_input = _responses_input_items(messages)
        if use_response_format and not _value_mentions_json(responses_input):
            responses_input.insert(0, {"role": "system", "content": "Return one valid JSON object."})
        payload: dict[str, Any] = {
            "model": model or self.config.default_model,
            "input": responses_input,
            "max_output_tokens": _effective_max_tokens(self.config, requested_tokens, thinking_mode),
            "store": False,
        }
        # Reasoning models may not accept sampling parameters. Preserve an
        # explicit caller override, but do not forward the Chat Completions
        # provider default automatically.
        if temperature is not None:
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
                fallback = super()._chat_json_once(
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
                "store": False,
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


def create_llm_client(config: ProviderConfig) -> LLMClientProtocol:
    """Create a client from provider configuration without changing defaults."""
    protocol = str(getattr(config, "api_protocol", "chat_completions") or "chat_completions").strip().lower()
    if protocol in {"responses", "responses_api"}:
        return ResponsesAPIClient(config)
    if protocol in {"chat_completions", "openai_compatible", ""}:
        return OpenAICompatibleClient(config)
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


def _is_yunwu_gemini_image_model(config: ProviderConfig, model: str) -> bool:
    name = str(getattr(config, "name", "") or "").lower()
    base_url = str(getattr(config, "base_url", "") or "").lower()
    model_name = str(model or "").lower()
    return (name == "yunwu" or "yunwu.ai" in base_url or "yunwu.cloud" in base_url) and model_name.startswith("gemini-") and "image" in model_name


def _native_api_root(base_url: str) -> str:
    text = str(base_url or "").rstrip("/")
    return text[:-3] if text.endswith("/v1") else text


def _gemini_aspect_ratio(size: str) -> str:
    parsed = _parse_image_size_pixels(size)
    if not parsed:
        return "1:1"
    width, height = parsed
    ratio = width / height
    candidates = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9, "9:16": 9 / 16}
    return min(candidates, key=lambda key: abs(candidates[key] - ratio))


def _image_suffix_for_mime_type(mime_type: str) -> str:
    normalized = str(mime_type or "").split(";", 1)[0].strip().lower()
    return {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(normalized, ".png")


def _image_download_headers(image_uri: str, api_key: str) -> dict[str, str]:
    """Do not add Bearer auth to pre-signed object-storage URLs."""
    query_keys = {key.lower() for key in parse_qs(urlparse(image_uri).query)}
    if any(key.startswith("x-amz-") for key in query_keys):
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _gemini_image_location(raw: dict[str, Any]) -> tuple[str, str]:
    candidates = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(candidates, list):
        raise LLMError(f"Unexpected Gemini image response shape: {raw}")
    for candidate in candidates:
        content = candidate.get("content") if isinstance(candidate, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else []
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            file_data = part.get("fileData") or part.get("file_data")
            if isinstance(file_data, dict):
                uri = str(file_data.get("fileUri") or file_data.get("file_uri") or file_data.get("url") or "").strip()
                if uri:
                    return uri, str(file_data.get("mimeType") or file_data.get("mime_type") or "")
            inline_data = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline_data, dict):
                encoded = str(inline_data.get("data") or "").strip()
                if encoded:
                    return f"data:{inline_data.get('mimeType') or inline_data.get('mime_type') or 'image/png'};base64,{encoded}", str(inline_data.get("mimeType") or inline_data.get("mime_type") or "")
    raise LLMError(f"Gemini image response has no fileData or inlineData image: {raw}")


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
    if normalized in {"low", "medium", "high", "xhigh"}:
        return normalized
    if normalized == "enabled":
        return "medium"
    if normalized == "disabled":
        return "none"
    return ""


def _consume_responses_sse(response: Any) -> dict[str, Any]:
    """Normalize Responses API server-sent events into one response object."""
    final_response: dict[str, Any] | None = None
    text_deltas: list[str] = []
    terminal_texts: list[str] = []
    data_lines: list[str] = []

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
        event_type = str(event.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_deltas.append(delta)
            return
        if event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                terminal_texts.append(text)
            return
        if event_type == "response.content_part.done":
            part = event.get("part")
            if isinstance(part, dict):
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
        line = response.readline()
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


def _responses_output_text(raw: dict[str, Any]) -> str:
    """Extract text from a Responses output array without exposing reasoning items."""
    direct = raw.get("output_text") if isinstance(raw, dict) else None
    if isinstance(direct, str) and direct.strip():
        return direct
    output = raw.get("output") if isinstance(raw, dict) else None
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


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
