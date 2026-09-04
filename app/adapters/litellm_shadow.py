from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import os
import queue
import threading
import time
from typing import Any

from ..concurrency import model_request_slot
from ..paths import LOGS_DIR, PROJECT_ROOT
from ..redaction import redact_credentials
from ..runtime_monitor import record_model_call_usage, track_model_call

_WRITE_LOCK = threading.Lock()
_QUEUE: queue.Queue[tuple[contextvars.Context, tuple[Any, list[dict[str, Any]], str, int, str]]] = queue.Queue(maxsize=4)


def _settings() -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / "open_source_components.json"
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")).get("litellm_shadow") or {})
    except Exception:
        return {}


def _json_valid(value: str) -> bool:
    try:
        return isinstance(json.loads(value), (dict, list))
    except Exception:
        return False


def _has_inline_images(messages: list[dict[str, Any]]) -> bool:
    return any("data:image/" in json.dumps(message, ensure_ascii=False) for message in messages)


def _write_log(row: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, (LOGS_DIR / "litellm_shadow.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _run(config: Any, messages: list[dict[str, Any]], model: str, max_tokens: int, primary_content: str) -> None:
    started = time.monotonic()
    row: dict[str, Any] = {
        "schema_version": "answer_book.litellm_shadow.v1",
        "provider": str(getattr(config, "name", "")),
        "model": model,
        "primary_json_valid": _json_valid(primary_content),
        "primary_sha256": hashlib.sha256(primary_content.encode("utf-8")).hexdigest(),
    }
    try:
        import litellm

        request_payload = {"model": model, "message_count": len(messages), "max_tokens": max_tokens, "shadow": True}
        with model_request_slot(config):
            with track_model_call(
                provider=f"{getattr(config, 'name', '')}:litellm_shadow",
                model=model,
                purpose="litellm_shadow",
                timeout=120,
                request_payload=request_payload,
                protocol="chat_completions",
                endpoint=f"{str(getattr(config, 'base_url', '')).rstrip('/')}/chat/completions",
            ) as call_record:
                response = litellm.completion(
                    model=f"openai/{model}",
                    api_base=str(getattr(config, "base_url", "")),
                    api_key=str(getattr(config, "api_key", "")),
                    messages=messages,
                    max_tokens=max_tokens,
                    timeout=120,
                    num_retries=0,
                    response_format={"type": "json_object"},
                )
                raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
                record_model_call_usage(call_record, raw)
        content = str(response.choices[0].message.content or "")
        row.update(
            {
                "status": "succeeded",
                "shadow_json_valid": _json_valid(content),
                "shadow_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "same_output": content == primary_content,
            }
        )
    except Exception as exc:
        row.update({"status": "failed", "error_type": type(exc).__name__, "error": redact_credentials(str(exc)[:800])})
    finally:
        row["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        _write_log(row)


def _worker() -> None:
    while True:
        context, args = _QUEUE.get()
        try:
            context.run(_run, *args)
        except Exception:
            # A diagnostics filesystem failure must not permanently kill the
            # one shadow worker or escape into the primary request path.
            pass
        finally:
            _QUEUE.task_done()


_THREAD = threading.Thread(target=_worker, name="litellm-shadow", daemon=True)
_THREAD.start()


def submit_litellm_shadow(
    config: Any,
    messages: list[dict[str, Any]],
    *,
    model: str,
    max_tokens: int,
    primary_content: str,
) -> bool:
    settings = _settings()
    env = os.environ.get("ANSWER_BOOK_LITELLM_SHADOW", "").strip().lower()
    enabled = bool(settings.get("enabled", False)) if not env else env not in {"0", "false", "no"}
    provider = str(getattr(config, "name", ""))
    if not enabled or provider not in set(map(str, settings.get("providers") or [])):
        return False
    if not str(getattr(config, "api_key", "")) or _has_inline_images(messages):
        return False
    sample_rate = max(0.0, min(1.0, float(settings.get("sample_rate", 0.1))))
    sample_key = json.dumps([provider, model, messages], ensure_ascii=False, sort_keys=True, default=str)
    bucket = int(hashlib.sha256(sample_key.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    if bucket >= sample_rate:
        return False
    context = contextvars.copy_context()
    try:
        _QUEUE.put_nowait((context, (config, copy.deepcopy(messages), model, max_tokens, primary_content)))
    except queue.Full:
        return False
    return True
