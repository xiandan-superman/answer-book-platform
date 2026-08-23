from __future__ import annotations

import re
from typing import Any

_PREFIXED_CREDENTIAL = re.compile(
    r"\b(?:sk|ark)-[A-Za-z0-9_-]{8,}\b|\bAIza[0-9A-Za-z_-]{20,}\b",
    re.IGNORECASE,
)
_BEARER_CREDENTIAL = re.compile(r"(?i)(bearer\s+)[^\s,;\"']+")
_NAMED_CREDENTIAL = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|apikey|password|secret|access[_-]?token|refresh[_-]?token|authorization)[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}]+)"
)


def redact_credentials(value: Any) -> str:
    """Remove common provider credentials while preserving diagnostic context."""

    text = str(value or "")
    text = _BEARER_CREDENTIAL.sub(r"\1***", text)
    text = _PREFIXED_CREDENTIAL.sub("***", text)
    return _NAMED_CREDENTIAL.sub(r"\1***", text)


def redact_diagnostic_value(value: Any, *, depth: int = 0) -> Any:
    """Recursively retain diagnostics while removing credential-bearing values."""

    if depth > 10:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, list):
        return [redact_diagnostic_value(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return [redact_diagnostic_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if any(token in lowered for token in ("api_key", "apikey", "authorization", "password", "secret", "access_token", "refresh_token")):
                result[key] = "***"
            else:
                result[key] = redact_diagnostic_value(item, depth=depth + 1)
        return result
    return redact_credentials(value)
