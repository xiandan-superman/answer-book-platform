from __future__ import annotations

import base64
import hashlib
import json
import math
import statistics
import threading
from collections import Counter
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from .paths import LOGS_DIR

TOKEN_METER_SCHEMA = "answer_book.token_meter.v1"
NON_CORE_COMPACTION_SCHEMA = "answer_book.non_core_compaction.v1"
_CALIBRATION_LOCK = threading.RLock()
_CALIBRATION_CACHE: dict[tuple[str, str, str], tuple[tuple[int, int], tuple[float, int]]] = {}


def estimate_text_tokens(value: Any) -> int:
    text = str(value or "")
    non_ascii = sum(1 for character in text if ord(character) > 127)
    return non_ascii + math.ceil((len(text) - non_ascii) / 4)


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value or "")


def _image_dimensions(value: str) -> tuple[int, int] | None:
    if not value.startswith("data:image/") or ";base64," not in value:
        return None
    try:
        encoded = value.split(",", 1)[1]
        # Image dimensions live in the header for supported formats.  Decode a
        # bounded prefix so metering never duplicates a multi-megabyte pixel
        # payload merely to estimate its context cost.
        raw = base64.b64decode(encoded[:1_000_000], validate=False)
        with Image.open(BytesIO(raw)) as image:
            return image.size
    except Exception:
        return None


def _image_tokens(value: Any) -> int:
    url = ""
    if isinstance(value, str):
        url = value
    elif isinstance(value, dict):
        url = str(value.get("url") or value.get("image_url") or "")
    dimensions = _image_dimensions(url)
    if dimensions is None:
        return 1024
    width, height = dimensions
    tiles = max(1, math.ceil(width / 512) * math.ceil(height / 512))
    return 85 + (170 * tiles)


def _walk_payload(value: Any) -> tuple[int, int, int]:
    """Return text, image and structural token estimates for wire-visible data."""

    if value is None:
        return 0, 0, 1
    if isinstance(value, (bool, int, float)):
        return estimate_text_tokens(value), 0, 1
    if isinstance(value, str):
        if value.startswith("data:image/"):
            return 0, _image_tokens(value), 1
        return estimate_text_tokens(value), 0, 1
    if isinstance(value, list):
        text = image = 0
        for item in value:
            item_text, item_image, _ = _walk_payload(item)
            text += item_text
            image += item_image
        return text, image, max(1, len(value) * 2)
    if isinstance(value, dict):
        kind = str(value.get("type") or "").lower()
        if kind in {"image_url", "input_image", "image"}:
            source = value.get("image_url", value.get("url", value))
            return 0, _image_tokens(source), 4
        text = image = 0
        for key, item in value.items():
            text += estimate_text_tokens(key)
            item_text, item_image, _ = _walk_payload(item)
            text += item_text
            image += item_image
        return text, image, max(1, len(value) * 2)
    return estimate_text_tokens(value), 0, 1


def _calibration_factor(
    provider: str,
    model: str,
    *,
    ledger_path: Path,
    minimum_samples: int = 3,
) -> tuple[float, int]:
    try:
        stat = ledger_path.stat()
        signature = (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        signature = (0, 0)
    cache_key = (str(ledger_path), provider, model)
    with _CALIBRATION_LOCK:
        cached = _CALIBRATION_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature:
            return cached[1]
    ratios: list[float] = []
    try:
        handle = ledger_path.open("r", encoding="utf-8")
    except OSError:
        result = (1.0, 0)
        with _CALIBRATION_LOCK:
            _CALIBRATION_CACHE[cache_key] = (signature, result)
        return result
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("usage_source") != "provider_reported":
                continue
            if str(row.get("provider") or "") != provider or str(row.get("model") or "") != model:
                continue
            actual = row.get("prompt_tokens")
            estimated = row.get("estimated_prompt_tokens")
            if isinstance(actual, int) and actual > 0 and isinstance(estimated, int) and estimated > 0:
                ratios.append(actual / estimated)
    if len(ratios) < max(1, int(minimum_samples)):
        result = (1.0, len(ratios))
    else:
        result = (
            max(0.5, min(3.0, float(statistics.median(ratios[-200:])))),
            len(ratios),
        )
    with _CALIBRATION_LOCK:
        _CALIBRATION_CACHE[cache_key] = (signature, result)
    return result


@dataclass(frozen=True)
class TokenMeasurement:
    schema_version: str
    provider: str
    model: str
    estimated_input_tokens: int
    text_tokens: int
    image_tokens: int
    tool_schema_tokens: int
    structural_tokens: int
    calibration_factor: float
    calibration_sample_count: int
    provider_usage_calibrated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure_request_tokens(
    messages: Iterable[dict[str, Any]],
    *,
    provider: str = "",
    model: str = "",
    tools: Iterable[dict[str, Any]] = (),
    fixed_overhead_tokens: int = 0,
    ledger_path: Path = LOGS_DIR / "model_calls.jsonl",
) -> TokenMeasurement:
    message_list = list(messages)
    text_tokens, image_tokens, structural_tokens = _walk_payload(message_list)
    tool_list = list(tools)
    tool_schema_tokens = estimate_text_tokens(_json_text(tool_list)) if tool_list else 0
    factor, sample_count = _calibration_factor(str(provider or ""), str(model or ""), ledger_path=ledger_path)
    subtotal = text_tokens + image_tokens + tool_schema_tokens + structural_tokens
    estimated = max(1, math.ceil(subtotal * factor) + max(0, int(fixed_overhead_tokens)))
    return TokenMeasurement(
        schema_version=TOKEN_METER_SCHEMA,
        provider=str(provider or ""),
        model=str(model or ""),
        estimated_input_tokens=estimated,
        text_tokens=text_tokens,
        image_tokens=image_tokens,
        tool_schema_tokens=tool_schema_tokens,
        structural_tokens=structural_tokens,
        calibration_factor=round(factor, 4),
        calibration_sample_count=sample_count,
        provider_usage_calibrated=sample_count >= 3,
    )


def _failed_tool_result(value: Any) -> tuple[bool, str]:
    if not isinstance(value, str):
        return False, ""
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return False, ""
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return False, ""
    raw_error = payload.get("error")
    error: dict[str, Any] = dict(raw_error) if isinstance(raw_error, dict) else {}
    return True, str(error.get("code") or "TOOL_ERROR")[:80]


def _compacted_tool_text(value: str, error_code: str) -> str:
    return json.dumps(
        {
            "schema_version": NON_CORE_COMPACTION_SCHEMA,
            "ok": False,
            "compacted": True,
            "error_code": error_code,
            "original_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def compact_non_core_history(
    messages: Iterable[dict[str, Any]],
    *,
    retain_recent_failures: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministically prune only old failed tool output text.

    Successful tool results, pixels, prompts, user messages, evidence and tool
    call identities are never rewritten.  Returning a fresh JSON copy also
    prevents mutation of the caller's authoritative history.
    """

    compacted = json.loads(json.dumps(list(messages), ensure_ascii=False))
    candidates: list[tuple[str, int, int | None, str, str]] = []
    for index, item in enumerate(compacted):
        if not isinstance(item, dict):
            continue
        if item.get("role") == "tool" and isinstance(item.get("content"), str):
            failed, code = _failed_tool_result(item["content"])
            if failed:
                candidates.append(("chat", index, None, item["content"], code))
        elif item.get("type") == "function_call_output" and isinstance(item.get("output"), list):
            output = item["output"]
            if any(isinstance(part, dict) and str(part.get("type") or "").endswith("image") for part in output):
                continue
            for output_index, part in enumerate(output):
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    continue
                failed, code = _failed_tool_result(part["text"])
                if failed:
                    candidates.append(("responses", index, output_index, part["text"], code))

    eligible = candidates[: max(0, len(candidates) - max(0, int(retain_recent_failures)))]
    removed_chars = 0
    changed_count = 0
    for kind, index, candidate_part_index, original, code in eligible:
        replacement = _compacted_tool_text(original, code)
        if len(replacement) >= len(original):
            continue
        if kind == "chat":
            compacted[index]["content"] = replacement
        else:
            assert candidate_part_index is not None
            compacted[index]["output"][candidate_part_index]["text"] = replacement
        removed_chars += len(original) - len(replacement)
        changed_count += 1
    return compacted, {
        "schema_version": NON_CORE_COMPACTION_SCHEMA,
        "compacted_result_count": changed_count,
        "removed_characters": removed_chars,
        "core_history_changed": False,
        "tool_pairing_changed": False,
        "model_calls_added": 0,
    }


def build_token_meter_report(*, ledger_path: Path = LOGS_DIR / "model_calls.jsonl") -> dict[str, Any]:
    routes: Counter[tuple[str, str]] = Counter()
    calibrated: Counter[tuple[str, str]] = Counter()
    try:
        handle = ledger_path.open("r", encoding="utf-8")
    except OSError:
        handle = None
    if handle is not None:
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                route = (str(row.get("provider") or ""), str(row.get("model") or ""))
                routes[route] += 1
                if (
                    row.get("usage_source") == "provider_reported"
                    and isinstance(row.get("prompt_tokens"), int)
                    and isinstance(row.get("estimated_prompt_tokens"), int)
                ):
                    calibrated[route] += 1
    return {
        "schema_version": TOKEN_METER_SCHEMA,
        "mode": "active_measurement_shadow_policy",
        "request_components": ["text", "images", "tool_schemas", "tool_results", "message_structure"],
        "route_count": len(routes),
        "provider_reported_calibration_route_count": sum(1 for count in calibrated.values() if count >= 3),
        "provider_reported_calibration_sample_count": sum(calibrated.values()),
        "compaction_policy": {
            "active": True,
            "eligible_content": "old_failed_tool_result_text_only",
            "retained_recent_failures": 2,
            "core_history_compression_allowed": False,
            "model_backed_summarization": False,
        },
        "added_model_calls": 0,
        "added_network_requests": 0,
    }
