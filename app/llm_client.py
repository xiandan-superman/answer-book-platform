from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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


class OpenAICompatibleClient:
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
            with self._urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except LLMError:
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
                report = self._json_attempt_report(plan, error=str(exc))
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
            with self._urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"Provider HTTP {exc.code}: {body[:800]}") from exc
        except Exception as exc:
            raise LLMError(f"Provider request failed: {exc}") from exc
        if not isinstance(raw, dict):
            raise LLMError(f"Unexpected provider response shape: {raw}")
        return raw

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
        choice = (raw.get("choices") or [{}])[0] if isinstance(raw, dict) else {}
        usage = raw.get("usage") if isinstance(raw, dict) else {}
        completion_details = usage.get("completion_tokens_details") if isinstance(usage, dict) else {}
        content = result.content if result else ""
        return {
            "strategy": plan.get("strategy"),
            "model": plan.get("model"),
            "max_tokens": plan.get("max_tokens"),
            "thinking": (raw.get("_request") or {}).get("thinking") if isinstance(raw.get("_request"), dict) else plan.get("thinking"),
            "compact_prompt": plan.get("strategy") == "compact_fallback_disable_thinking",
            "finish_reason": choice.get("finish_reason"),
            "content_length": len(content or ""),
            "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            "completion_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
            "reasoning_tokens": completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None,
            "response_format": (raw.get("_request") or {}).get("response_format") if isinstance(raw.get("_request"), dict) else None,
            "error": error,
        }


def _is_unsupported_response_format_error(error: str) -> bool:
    text = str(error or "").lower()
    return "response_format" in text and ("not support" in text or "not supported" in text or "not valid" in text or "invalidparameter" in text)


def _is_reasoning_heavy_provider(config: ProviderConfig) -> bool:
    name = str(getattr(config, "name", "") or "").lower()
    base_url = str(getattr(config, "base_url", "") or "").lower()
    return name == "zhipu" or "bigmodel.cn" in base_url


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
    if _is_reasoning_heavy_provider(config) and thinking_mode in {"auto", "enabled"}:
        return max(tokens, int(getattr(config, "max_tokens", 0) or 0), DEFAULT_MODEL_MAX_TOKENS)
    return tokens


def _normalize_thinking_mode(value: Any) -> str:
    text = str(value or "auto").strip().lower()
    aliases = {
        "on": "enabled",
        "enable": "enabled",
        "enabled": "enabled",
        "true": "enabled",
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
    raw = result.raw or {}
    choice = (raw.get("choices") or [{}])[0] if isinstance(raw, dict) else {}
    return str(choice.get("finish_reason") or "")


def _provider_response_detail(result: LLMResult) -> str:
    return _raw_provider_response_detail(result.raw or {})


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
