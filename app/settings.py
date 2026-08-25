from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .api_key_config import load_api_keys
from .paths import CONFIG_DIR, DATA_ROOT, LOCAL_CONFIG_DIR

DEFAULT_MODEL_MAX_TOKENS = 24576
STRUCTURED_ANSWER_MAX_TOKENS = 24576
DRAWING_CODE_MAX_TOKENS = 32768
FIGURE_AUXILIARY_MAX_TOKENS = 16384
BAILIAN_QWEN37_MAX = "qwen3.7-max"
BAILIAN_QWEN37_MAX_JSON_MODE_UNSUPPORTED = (
    BAILIAN_QWEN37_MAX,
    "qwen3.7-max-2026-05-20",
    "qwen3.7-max-preview",
)
REMOVED_PROVIDER_NAMES = {"yunwu", "lingsuan"}
LEGACY_PROVIDER_ALIASES = {"lingsuan": "lingsuan_openai"}
BUILTIN_RESPONSES_PROVIDER_NAMES = {
    "deepseek",
    "ark",
    "bailian",
    "openrouter",
    "yuanheng",
    "lingsuan_openai",
    "lingsuan_image",
    "lingsuan_google",
    "lingsuan_xai",
    "lingsuan_anthropic",
}
BUILTIN_CHAT_COMPLETIONS_PROVIDER_NAMES = {
    "sensenova",
    "bai",
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    base_url: str
    api_key: str
    default_model: str
    model_options: tuple[str, ...]
    allow_custom_model: bool
    model_hint: str
    temperature: float
    max_tokens: int
    api_key_env: str = ""
    model_option_labels: dict[str, str] = field(default_factory=dict)
    image_model: str = ""
    image_model_options: tuple[str, ...] = ()
    image_model_option_labels: dict[str, str] = field(default_factory=dict)
    image_size: str = "1024x1024"
    supports_text_generation: bool = True
    supports_image_generation: bool = True
    vision_model: str = ""
    vision_model_options: tuple[str, ...] = ()
    supports_vision: bool = False
    model_capabilities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    thinking_mode: str = "auto"
    json_mode_unsupported_models: tuple[str, ...] = ()
    api_protocol: str = "chat_completions"
    responses_fallback_to_chat: bool = True
    responses_streaming: bool = True
    user_agent: str = ""

    def redacted(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "base_url": self.base_url,
            "api_key_set": bool(self.api_key),
            "api_key_env": self.api_key_env,
            "default_model": self.default_model,
            "model_options": list(self.model_options),
            "model_option_labels": dict(self.model_option_labels),
            "allow_custom_model": self.allow_custom_model,
            "model_hint": self.model_hint,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "image_model_set": bool(self.image_model),
            "image_model": self.image_model,
            "image_model_options": list(self.image_model_options),
            "image_model_option_labels": dict(self.image_model_option_labels),
            "image_size": self.image_size,
            "supports_text_generation": self.supports_text_generation,
            "supports_image_generation": self.supports_image_generation,
            "vision_model": self.vision_model,
            "vision_model_options": list(self.vision_model_options),
            "supports_vision": self.supports_vision,
            "model_capabilities": {key: list(value) for key, value in self.model_capabilities.items()},
            "thinking_mode": self.thinking_mode,
            "json_mode_unsupported_models": list(self.json_mode_unsupported_models),
            "api_protocol": self.api_protocol,
            "responses_fallback_to_chat": self.responses_fallback_to_chat,
            "responses_streaming": self.responses_streaming,
            "user_agent": self.user_agent,
        }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Overlay local provider secrets/options without dropping example providers."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_provider_config_file() -> dict[str, Any]:
    local = LOCAL_CONFIG_DIR / "providers.local.json"
    example = CONFIG_DIR / "providers.example.json"
    base = _read_json(example)
    return _merge_config(base, _read_json(local)) if local.exists() else base


def load_dotenv() -> None:
    env_path = DATA_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def list_providers() -> dict[str, ProviderConfig]:
    load_api_keys()
    load_dotenv()
    raw = load_provider_config_file()
    providers: dict[str, ProviderConfig] = {}
    for name, item in raw.get("providers", {}).items():
        # A copied providers.local.json may still contain removed 0.9.0
        # entries. Never resurrect Yunwu or the old cross-supplier Lingsuan
        # provider through a local overlay.
        if name in REMOVED_PROVIDER_NAMES:
            continue
        # Built-in providers have been verified against Responses.  Force the
        # transport here as well as in the shipped JSON because older installs
        # may carry a full providers.local.json that still says Chat Completions.
        # A stale local overlay must not silently restore the old protocol or
        # the Responses-to-Chat double request fallback after an application update.
        builtin_responses = name in BUILTIN_RESPONSES_PROVIDER_NAMES
        builtin_chat_completions = name in BUILTIN_CHAT_COMPLETIONS_PROVIDER_NAMES
        env_name = str(item.get("api_key_env", "")).strip()
        # Frontend key saving writes to .env. Let that saved value override
        # legacy providers.local.json entries so replacing a bad key takes effect.
        api_key = str(os.environ.get(env_name, "") or item.get("api_key", "")).strip()
        image_model_env = str(item.get("image_model_env", "")).strip()
        default_image_model = ""
        if name == "ark":
            default_image_model = "doubao-seedream-5-0-260128"
        if name == "bailian":
            default_image_model = "qwen-image-2.0-pro"
        supports_image_generation = bool(item.get("supports_image_generation", True))
        image_model = str(
            item.get("image_model", "")
            or os.environ.get(image_model_env, "")
            or os.environ.get("ANSWER_BOOK_IMAGE_MODEL", "")
            or default_image_model
        ).strip()
        if not supports_image_generation:
            image_model = ""
        vision_model = str(item.get("vision_model", "") or item.get("default_model", "")).strip()
        supports_vision = bool(item.get("supports_vision", False) or ("vl" in vision_model.lower()) or vision_model.lower().endswith("v") or "vision" in vision_model.lower())
        if not supports_vision:
            vision_model = ""
        model_options = [str(x) for x in item.get("model_options", []) if str(x).strip()]
        model_option_labels = {
            str(key): str(value)
            for key, value in dict(item.get("model_option_labels", {})).items()
            if str(key).strip()
        }
        json_mode_unsupported_models = [
            str(x) for x in item.get("json_mode_unsupported_models", []) if str(x).strip()
        ]
        # Existing installations commonly keep a copied providers.local.json.
        # Keep the current Bailian flagship selectable even before that local
        # file is manually updated.
        if name == "bailian":
            model_options = list(dict.fromkeys([*model_options, BAILIAN_QWEN37_MAX]))
            model_option_labels.setdefault(BAILIAN_QWEN37_MAX, "Qwen3.7-Max 文本旗舰")
            json_mode_unsupported_models = list(
                dict.fromkeys([*json_mode_unsupported_models, *BAILIAN_QWEN37_MAX_JSON_MODE_UNSUPPORTED])
            )
        providers[name] = ProviderConfig(
            name=name,
            type=str(item.get("type", "openai_compatible")),
            base_url=str(item.get("base_url", "")).rstrip("/"),
            api_key=api_key,
            api_key_env=env_name,
            default_model=str(item.get("default_model", "")),
            model_options=tuple(model_options),
            model_option_labels=model_option_labels,
            allow_custom_model=bool(item.get("allow_custom_model", False)),
            model_hint=str(item.get("model_hint", "")),
            temperature=float(item.get("temperature", 0.1)),
            max_tokens=int(item.get("max_tokens", DEFAULT_MODEL_MAX_TOKENS)),
            image_model=image_model,
            image_model_options=tuple(str(x) for x in item.get("image_model_options", []) if supports_image_generation and str(x).strip()),
            image_model_option_labels=(
                {
                    str(key): str(value)
                    for key, value in dict(item.get("image_model_option_labels", {})).items()
                    if str(key).strip()
                }
                if supports_image_generation
                else {}
            ),
            image_size=str(item.get("image_size", "") or os.environ.get("ANSWER_BOOK_IMAGE_SIZE", "") or "1024x1024"),
            supports_text_generation=bool(item.get("supports_text_generation", True)),
            supports_image_generation=supports_image_generation,
            vision_model=vision_model,
            vision_model_options=tuple(str(x) for x in item.get("vision_model_options", []) if str(x).strip()),
            supports_vision=supports_vision,
            model_capabilities={
                str(model): tuple(str(capability).strip().lower() for capability in capabilities if str(capability).strip())
                for model, capabilities in dict(item.get("model_capabilities", {})).items()
                if str(model).strip() and isinstance(capabilities, (list, tuple))
            },
            thinking_mode=str(item.get("thinking_mode", "") or os.environ.get("ANSWER_BOOK_THINKING_MODE", "") or "auto"),
            json_mode_unsupported_models=tuple(json_mode_unsupported_models),
            api_protocol=(
                "responses"
                if builtin_responses
                else "chat_completions"
                if builtin_chat_completions
                else str(item.get("api_protocol", "chat_completions") or "chat_completions").strip().lower()
            ),
            responses_fallback_to_chat=(
                False if builtin_responses else bool(item.get("responses_fallback_to_chat", True))
            ),
            responses_streaming=True if builtin_responses else bool(item.get("responses_streaming", True)),
            user_agent=str(item.get("user_agent", "") or "").strip(),
        )
    return providers


def provider_supports_image_generation(provider: ProviderConfig) -> bool:
    return bool(getattr(provider, "supports_image_generation", True) and getattr(provider, "image_model", ""))


def provider_model_supports_vision(provider: ProviderConfig, model: str) -> bool:
    """Return the capability of the selected model, not merely its provider."""

    selected = str(model or "").strip()
    if not selected:
        return False
    explicit = tuple(
        sorted(
            str(capability).strip().lower()
            for capability in (getattr(provider, "model_capabilities", {}) or {}).get(selected, ())
            if str(capability).strip()
        )
    )
    if not explicit:
        label = str((getattr(provider, "model_option_labels", {}) or {}).get(selected, "") or "")
        identity = f"{selected} {label}".lower()
        if (
            "multimodal" in identity
            or "vision" in identity
            or "ocr" in identity
            or "多模态" in label
            or "视觉" in label
            or "识图" in label
            or "图像" in label
            or re.search(r"(?:^|[-_.])vl(?:$|[-_.])", selected, flags=re.IGNORECASE)
        ):
            explicit = ("vision",)
    configured = tuple(
        sorted(
            {
                str(getattr(provider, "vision_model", "") or "").strip(),
                *[str(item).strip() for item in (getattr(provider, "vision_model_options", ()) or ())],
            }
            - {""}
        )
    )
    return _model_supports_vision_cached(
        str(getattr(provider, "name", "") or ""),
        selected,
        bool(getattr(provider, "supports_vision", False)),
        explicit,
        configured,
    )


@lru_cache(maxsize=512)
def _model_supports_vision_cached(
    provider_name: str,
    model: str,
    provider_supports_vision: bool,
    explicit_capabilities: tuple[str, ...],
    configured_vision_models: tuple[str, ...],
) -> bool:
    """Resolve a provider/model capability once per configuration identity.

    This is deliberately a local declaration lookup, not a paid model probe.
    Changing provider/model configuration changes the cache key automatically.
    """

    del provider_name
    if "vision" in explicit_capabilities or "multimodal" in explicit_capabilities or "image_input" in explicit_capabilities:
        return True
    if explicit_capabilities:
        return False
    return provider_supports_vision and model in configured_vision_models


def get_provider(name: str | None = None) -> ProviderConfig:
    raw = load_provider_config_file()
    providers = list_providers()
    selected = name or raw.get("active_provider")
    selected = LEGACY_PROVIDER_ALIASES.get(str(selected or ""), selected)
    if not selected and providers:
        selected = next(iter(providers))
    if selected not in providers:
        raise ValueError(f"Provider not configured: {selected}")
    return providers[selected]


def resolve_provider_model(provider: ProviderConfig, requested_model: Any = None) -> str:
    if not getattr(provider, "supports_text_generation", True):
        raise ValueError(f"Provider {provider.name} is not configured for text generation")
    model = str(requested_model or provider.default_model).strip()
    if not model:
        raise ValueError(f"Provider {provider.name} has no default model configured")
    if provider.model_options and model not in provider.model_options and not provider.allow_custom_model:
        allowed = ", ".join(provider.model_options)
        raise ValueError(f"Model {model} is not allowed for provider {provider.name}. Supported models: {allowed}")
    return model
