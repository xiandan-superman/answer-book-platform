from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Protocol

from PIL import Image

from .image_artifacts import ImageArtifact, ImageArtifactStore
from .image_orchestration import DEFAULT_EDUCATIONAL_IMAGE_STYLE_RULE
from .llm_client import (
    AnthropicMessagesClient,
    LLMError,
    OpenAICompatibleClient,
    ResponsesAPIClient,
    parse_json_content,
)
from .model_context_planner import model_stage_quality_limit
from .prompt_registry import prompt_contract
from .token_meter import compact_non_core_history, measure_request_tokens

IMAGE_TOOL_NAME = "generate_image"
MAX_REFERENCE_IMAGES = 5
DEFAULT_REPEAT_REMINDER_THRESHOLD = 3
TOOL_EVENT_LOG_SCHEMA = "answer_book.tool_events.v1"
_REFERENCE_IMAGE_MIMES = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}


class ModelToolLoopUnavailableError(ValueError):
    """The user selected the agent image route but its required route is unavailable."""

    def __init__(self, message: str, *, requires_configuration: bool = False) -> None:
        super().__init__(message)
        self.error_code = (
            "image_tool_configuration_missing"
            if requires_configuration
            else "model_tool_loop_unsupported"
        )
        self.requires_configuration = bool(requires_configuration)
        self.retryable = False


class AgentTool(Protocol):
    name: str

    def definition(self) -> dict[str, Any]: ...

    def validate_arguments(self, arguments: dict[str, Any]) -> None: ...

    def execute(self, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]: ...


@dataclass
class ToolLoopResult:
    value: dict[str, Any]
    steps: int
    tool_calls: int
    generated_artifacts: list[dict[str, Any]] = field(default_factory=list)
    raw_responses: list[dict[str, Any]] = field(default_factory=list)
    tool_event_log: str = ""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _tool_call_fingerprint(name: str, arguments: Any) -> str:
    payload = _canonical_json({"name": str(name or ""), "arguments": arguments})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tool_failure(code: str, message: str, *, name: str = "ToolError") -> dict[str, Any]:
    return {
        "ok": False,
        "is_error": True,
        "error": {
            "message": str(message),
            "info": {"name": str(name), "code": str(code)},
        },
        "content": f"Error: {message}",
    }


class ToolEventLog:
    """Task-local append-only tool lifecycle log with a flush barrier per event."""

    def __init__(self, path: Path, *, session_id: str) -> None:
        self.path = Path(path)
        self.session_id = str(session_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sequence = 0

    def append(self, event_type: str, **payload: Any) -> None:
        with self._lock:
            self._sequence += 1
            event = {
                "schema_version": TOOL_EVENT_LOG_SCHEMA,
                "session_id": self.session_id,
                "sequence": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": str(event_type),
                **payload,
            }
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def recover_call_cache(self) -> dict[str, dict[str, Any]]:
        """Recover completed and ambiguous calls so restarts never replay them blindly."""

        if not self.path.exists():
            return {}
        recovered: dict[str, dict[str, Any]] = {}
        started: dict[str, str] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if str(event.get("session_id") or "") != self.session_id:
                continue
            try:
                self._sequence = max(self._sequence, int(event.get("sequence") or 0))
            except (TypeError, ValueError):
                pass
            call_id = str(event.get("call_id") or "")
            fingerprint = str(event.get("arguments_sha256") or "")
            if not call_id or not fingerprint:
                continue
            event_type = str(event.get("type") or "")
            if event_type == "tool/started":
                started[call_id] = fingerprint
            elif event_type == "tool/result" and isinstance(event.get("result"), dict):
                recovered[call_id] = {
                    "fingerprint": fingerprint,
                    "result": event["result"],
                }
                started.pop(call_id, None)
        for call_id, fingerprint in started.items():
            recovered.setdefault(
                call_id,
                {
                    "fingerprint": fingerprint,
                    "result": _tool_failure(
                        "TOOL_OUTCOME_UNKNOWN",
                        "the prior process stopped after this external tool started; explicit retry is required",
                        name="ToolOutcomeUnknown",
                    ),
                },
            )
        return recovered


def _function_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in raw.get("output", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict) or item.get("type") not in {"function_call", "tool_call"}:
            continue
        name = str(item.get("name") or ((item.get("function") or {}).get("name") if isinstance(item.get("function"), dict) else ""))
        arguments = item.get("arguments")
        if arguments is None and isinstance(item.get("function"), dict):
            arguments = item["function"].get("arguments")
        calls.append(
            {
                "call_id": str(item.get("call_id") or item.get("id") or ""),
                "name": name,
                "arguments": arguments,
            }
        )
    return calls


def _chat_message(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        message = raw["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected Chat Completions tool response shape: {raw}") from exc
    if not isinstance(message, dict):
        raise LLMError(f"Unexpected Chat Completions message shape: {message}")
    return message


def _chat_function_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    message = _chat_message(raw)
    calls: list[dict[str, Any]] = []
    for item in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        calls.append(
            {
                "call_id": str(item.get("id") or item.get("call_id") or ""),
                "name": str(function.get("name") or item.get("name") or ""),
                "arguments": function.get("arguments", item.get("arguments")),
            }
        )
    return calls


def _chat_output_text(raw: dict[str, Any]) -> str:
    content = _chat_message(raw).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict)
        and part.get("type") in {"text", "output_text"}
        and part.get("thought") is not True
        and part.get("is_thinking") is not True
    )


def _generated_image_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        rows = value.get("generated_images")
        if isinstance(rows, list):
            refs.update(
                str(item.get("asset_id") or "").strip()
                for item in rows
                if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
            )
        for item in value.values():
            refs.update(_generated_image_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_generated_image_refs(item))
    return refs


class ImageGenerationTool:
    name = IMAGE_TOOL_NAME

    def __init__(
        self,
        provider: Any,
        model: str,
        store: ImageArtifactStore,
        *,
        size: str = "",
        timeout_seconds: int = 240,
        reference_images: Iterable[str | Path] = (),
    ) -> None:
        self.provider = provider
        self.model = str(model or getattr(provider, "image_model", "") or "")
        self.store = store
        self.size = str(size or getattr(provider, "image_size", "") or "")
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.reference_paths = self._materialize_reference_images(reference_images)
        self._allowed_reference_paths = {
            str(path): path for path in self.reference_paths
        }
        self._generated_paths: list[Path] = []

    def _materialize_reference_images(
        self,
        reference_images: Iterable[str | Path],
    ) -> list[Path]:
        """Create a task-local allowlist while preserving the original image bytes."""

        output: list[Path] = []
        seen: set[str] = set()
        reference_root = self.store.root / "_reference_inputs"
        for raw_reference in reference_images:
            raw_value = str(raw_reference or "").strip()
            if not raw_value:
                continue
            data_match = re.fullmatch(
                r"data:([^;,]+);base64,(.+)",
                raw_value,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if data_match:
                try:
                    image_bytes = base64.b64decode(data_match.group(2), validate=True)
                except Exception as exc:
                    raise ValueError("reference image data URL contains invalid base64") from exc
                image_format, suffix = self._validate_reference_bytes(image_bytes)
                declared_mime = data_match.group(1).lower()
                expected_mime = _REFERENCE_IMAGE_MIMES[image_format][0]
                if declared_mime != expected_mime:
                    raise ValueError(
                        f"reference image MIME mismatch: declared {declared_mime}, actual {expected_mime}"
                    )
                digest = hashlib.sha256(image_bytes).hexdigest()
                reference_root.mkdir(parents=True, exist_ok=True)
                path = (reference_root / f"reference_sha256_{digest}{suffix}").resolve()
                if not path.exists():
                    temporary = path.with_suffix(path.suffix + ".tmp")
                    temporary.write_bytes(image_bytes)
                    temporary.replace(path)
            else:
                path = Path(raw_value).expanduser().resolve(strict=True)
                if not path.is_file():
                    raise ValueError(f"reference image is not a file: {path.name}")
                self._validate_reference_bytes(path.read_bytes())
            canonical = str(path)
            if canonical not in seen:
                seen.add(canonical)
                output.append(path)
        return output

    @staticmethod
    def _validate_reference_bytes(raw: bytes) -> tuple[str, str]:
        if not raw or len(raw) > 50 * 1024 * 1024:
            raise ValueError("reference image must contain 1 byte to 50 MB")
        try:
            with Image.open(BytesIO(raw)) as image:
                image.verify()
                image_format = str(image.format or "").upper()
        except Exception as exc:
            raise ValueError("reference image is unreadable") from exc
        metadata = _REFERENCE_IMAGE_MIMES.get(image_format)
        if metadata is None:
            raise ValueError("reference image format must be PNG, JPEG, or WebP")
        return image_format, metadata[1]

    def definition(self) -> dict[str, Any]:
        available_paths = [str(path) for path in self.reference_paths]
        reference_description = (
            "Use these exact task-local paths when the requested image must preserve or transform source pixels: "
            + json.dumps(available_paths, ensure_ascii=False)
            + ". Their order matches the source-image order delivered in the main-model input."
            if available_paths
            else "No task-local source image paths are available for this call."
        )
        return {
            "type": "function",
            "name": self.name,
            "description": (
                "Generate an image only when you, the main model, judge that the final answer genuinely benefits "
                "from one. The returned image is sent back to you for visual inspection. You may accept it, "
                "call again with a corrected prompt, or omit it from the final answer. Omit both image selectors "
                "to generate from scratch. Provide referenced_image_paths to edit from registered local originals; "
                "provide num_last_images_to_include to revise recent tool outputs. Never provide both selectors. "
                + DEFAULT_EDUCATIONAL_IMAGE_STYLE_RULE
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Complete production prompt describing all required visual content, labels, layout, and exclusions. "
                            + DEFAULT_EDUCATIONAL_IMAGE_STYLE_RULE
                        ),
                    },
                    "referenced_image_paths": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            **({"enum": available_paths} if available_paths else {}),
                        },
                        "maxItems": MAX_REFERENCE_IMAGES,
                        "description": (
                            "Up to 5 registered local source images, read at original quality and passed as true "
                            "pixel inputs to an ImageEditRequest. " + reference_description
                        ),
                    },
                    "num_last_images_to_include": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_REFERENCE_IMAGES,
                        "description": (
                            "Use the last N images generated in this task as edit inputs, typically to correct a "
                            "previous result that you just inspected. Do not combine with referenced_image_paths."
                        ),
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        }

    def validate_arguments(self, arguments: dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        unknown_fields = sorted(set(arguments) - {"prompt", "referenced_image_paths", "num_last_images_to_include"})
        if unknown_fields:
            raise ValueError("unknown image tool argument(s): " + ", ".join(unknown_fields))
        raw_prompt = arguments.get("prompt")
        if not isinstance(raw_prompt, str):
            raise ValueError("prompt must be a string")
        prompt = raw_prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        raw_paths = arguments.get("referenced_image_paths")
        recent_count = arguments.get("num_last_images_to_include")
        if recent_count is not None:
            if isinstance(recent_count, bool) or not isinstance(recent_count, int):
                raise ValueError("num_last_images_to_include must be an integer from 1 to 5")
            if not 1 <= recent_count <= MAX_REFERENCE_IMAGES:
                raise ValueError("num_last_images_to_include must be an integer from 1 to 5")
        recent_selector_available = (
            recent_count is not None and len(self._generated_paths) >= recent_count
        )
        if raw_paths is not None:
            if not isinstance(raw_paths, list):
                raise ValueError("referenced_image_paths must be an array")
            if len(raw_paths) > MAX_REFERENCE_IMAGES:
                raise ValueError("referenced_image_paths cannot contain more than 5 paths")
            for raw_path in raw_paths:
                if not isinstance(raw_path, str):
                    raise ValueError("referenced_image_paths entries must be strings")
                requested = raw_path.strip()
                path = self._allowed_reference_paths.get(requested)
                if path is None and not recent_selector_available:
                    raise ValueError("referenced_image_paths contains an unregistered task-local path")
        if recent_count is not None:
            # When both selectors are present, prefer recent generated images
            # once they exist; before that, fall back to explicit registered
            # paths.  Models commonly retain both fields across edit turns, so
            # deterministic canonicalization avoids a retry loop without
            # allowing an unregistered path onto the filesystem.
            if raw_paths is None and len(self._generated_paths) < recent_count:
                raise ValueError(
                    f"only {len(self._generated_paths)} recent generated image(s) are available for editing"
                )

    def execute(self, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]:
        self.validate_arguments(arguments)
        prompt = arguments["prompt"].strip()
        temporary = self.store.root / f".{re.sub(r'[^0-9A-Za-z_.-]+', '_', call_id or 'call')}.png"
        raw_paths = arguments.get("referenced_image_paths")
        recent_count = arguments.get("num_last_images_to_include")
        reference_paths: list[Path] = []
        if recent_count is not None and len(self._generated_paths) >= recent_count:
            reference_paths = self._generated_paths[-recent_count:]
            argument_normalization = (
                "preferred_recent_image_selector"
                if raw_paths is not None
                else "none"
            )
        elif raw_paths:
            reference_paths = [self._allowed_reference_paths[path.strip()] for path in raw_paths]
            argument_normalization = (
                "ignored_unavailable_recent_image_selector"
                if recent_count is not None
                else "none"
            )
        else:
            argument_normalization = (
                "ignored_unavailable_recent_image_selector"
                if raw_paths is not None and recent_count is not None
                else "none"
            )

        client = OpenAICompatibleClient(self.provider)
        try:
            with prompt_contract("tool.image_generation"):
                if reference_paths:
                    result = client.edit_image(
                        prompt,
                        reference_paths,
                        temporary,
                        model=self.model,
                        size=self.size or None,
                        timeout=self.timeout_seconds,
                    )
                    operation = "edit"
                else:
                    result = client.generate_image(
                        prompt,
                        temporary,
                        model=self.model,
                        size=self.size or None,
                        timeout=self.timeout_seconds,
                    )
                    operation = "generate"
            artifact = self.store.register(
                result.path,
                provider=result.provider,
                model=result.model,
                source_call_id=call_id,
            )
            self._generated_paths.append(Path(artifact.path))
        finally:
            temporary.unlink(missing_ok=True)
        raw_result = result.raw if isinstance(getattr(result, "raw", None), dict) else {}
        first_data = raw_result.get("data", [None])[0] if isinstance(raw_result.get("data"), list) and raw_result.get("data") else None
        provider_metadata = {
            "request_id": str(raw_result.get("id") or raw_result.get("request_id") or ""),
            "revised_prompt": str(
                (first_data.get("revised_prompt") if isinstance(first_data, dict) else "") or raw_result.get("revised_prompt") or ""
            ),
        }
        return {
            "ok": True,
            "asset": artifact.to_dict(),
            "operation": operation,
            "reference_image_count": len(reference_paths),
            "argument_normalization": argument_normalization,
            "provider_metadata": {key: value for key, value in provider_metadata.items() if value},
            "instruction": "Inspect the attached image. Use its asset_id in generated_images only if it satisfies your task.",
        }


def _model_tool_protocol(provider: Any, model: str) -> str:
    selected = str(model or "").strip()
    profile = dict((getattr(provider, "model_profiles", {}) or {}).get(selected) or {})
    return str(
        profile.get("api_protocol")
        or getattr(provider, "api_protocol", "chat_completions")
        or "chat_completions"
    ).strip().lower()


def _model_tool_client(
    client: OpenAICompatibleClient,
    provider: Any,
    model: str,
) -> tuple[OpenAICompatibleClient, bool]:
    """Bind the loop to the selected model's protocol, not the provider default."""

    protocol = _model_tool_protocol(provider, model)
    responses_protocol = protocol in {"responses", "responses_api"}
    client_matches = (
        isinstance(client, ResponsesAPIClient)
        if responses_protocol
        else isinstance(client, OpenAICompatibleClient)
        and not isinstance(client, (ResponsesAPIClient, AnthropicMessagesClient))
    )
    if client_matches:
        return client, responses_protocol
    profiles = {
        key: dict(value)
        for key, value in (getattr(provider, "model_profiles", {}) or {}).items()
    }
    profile = dict(profiles.get(str(model or "").strip()) or {})
    profile["api_protocol"] = protocol
    profiles[str(model or "").strip()] = profile
    model_provider = replace(
        provider,
        api_protocol=protocol,
        default_model=str(model or "").strip(),
        model_profiles=profiles,
    )
    active_client = OpenAICompatibleClient(model_provider)
    active_client._urlopen = client._urlopen
    return active_client, responses_protocol


class ModelToolLoop:
    """A Codex-style loop: the main model alone chooses whether tools run."""

    def __init__(
        self,
        client: OpenAICompatibleClient,
        tools: list[AgentTool],
        artifact_store: ImageArtifactStore,
        *,
        max_steps: int = 6,
        max_tool_calls: int = 4,
        session_id: str | None = None,
    ) -> None:
        if isinstance(client, AnthropicMessagesClient) or not isinstance(client, OpenAICompatibleClient):
            raise ValueError("model tool loop requires a registered Responses or Chat Completions client")
        self.client = client
        self.tools = {tool.name: tool for tool in tools}
        self.artifact_store = artifact_store
        self.max_steps = max(1, int(max_steps))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.session_id = str(session_id or uuid.uuid4())
        self._event_log = ToolEventLog(
            self.artifact_store.root / "tool_events.jsonl",
            session_id=self.session_id,
        )
        # One logical answer/repair transaction may invoke ``run_json`` again
        # after deterministic validation rejects a candidate.  Assets already
        # generated and shown in that same transaction remain eligible, but are
        # re-delivered below so the next model request actually sees them.
        self._session_artifacts: dict[str, ImageArtifact] = {}
        self._session_call_cache: dict[str, dict[str, Any]] = self._event_log.recover_call_cache()
        self._repeat_signature = ""
        self._repeat_count = 0

    @property
    def tool_event_log_path(self) -> Path:
        return self._event_log.path

    @staticmethod
    def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
        arguments = raw_arguments
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        # Snapshot the JSON value before policy/dispatch so a tool cannot
        # mutate the authoritative call arguments in-place.
        return json.loads(json.dumps(arguments, ensure_ascii=False))

    def _repeat_reminder(self, signature: str, *, tool_name: str, arguments: Any) -> str:
        if signature == self._repeat_signature:
            self._repeat_count += 1
        else:
            self._repeat_signature = signature
            self._repeat_count = 1
        if self._repeat_count != DEFAULT_REPEAT_REMINDER_THRESHOLD:
            return ""
        preview = _canonical_json(arguments)
        if len(preview) > 500:
            omitted = len(preview) - 500
            preview = preview[:500] + f"… (+{omitted} more chars)"
        return (
            "You are repeating the exact same tool call with identical arguments. "
            "Carefully analyze the previous result before calling again: if the task is not complete, "
            "try a different approach or different arguments instead of repeating the call. "
            f"tool={tool_name}; consecutive_calls={self._repeat_count}; arguments={preview}"
        )

    def _dispatch_tool_call(
        self,
        call: dict[str, Any],
        *,
        model: str,
        protocol: str,
        step: int,
        call_index: int,
        budget_exhausted: bool,
    ) -> tuple[dict[str, Any], str]:
        call_id = str(call.get("call_id") or "")
        tool_name = str(call.get("name") or "")
        raw_arguments = call.get("arguments")
        try:
            arguments: Any = self._parse_tool_arguments(raw_arguments)
            argument_error: Exception | None = None
        except Exception as exc:
            arguments = raw_arguments
            argument_error = exc
        fingerprint = _tool_call_fingerprint(tool_name, arguments)
        self._event_log.append(
            "tool/call",
            call_id=call_id,
            tool=tool_name,
            model=model,
            protocol=protocol,
            step=step,
            call_index=call_index,
            arguments=arguments,
            arguments_sha256=fingerprint,
        )
        cached = self._session_call_cache.get(call_id)
        cache_hit = False
        if cached is not None:
            cache_hit = cached.get("fingerprint") == fingerprint
            if cache_hit:
                result = json.loads(json.dumps(cached["result"], ensure_ascii=False))
            else:
                result = _tool_failure(
                    "TOOL_CALL_ID_REUSED",
                    "the same call_id was reused with a different tool name or arguments",
                    name="ToolCallIdentityError",
                )
        elif budget_exhausted:
            result = _tool_failure(
                "TOOL_CALL_LIMIT",
                f"main model exceeded image tool call limit ({self.max_tool_calls})",
                name="ToolCallLimitError",
            )
        elif argument_error is not None:
            result = _tool_failure(
                "INVALID_TOOL_ARGUMENTS",
                f"{type(argument_error).__name__}: {argument_error}",
                name="ToolArgumentsError",
            )
        else:
            tool = self.tools.get(tool_name)
            if tool is None:
                result = _tool_failure(
                    "UNKNOWN_TOOL",
                    f"unknown tool: {tool_name}",
                    name="UnknownToolError",
                )
            else:
                validator = getattr(tool, "validate_arguments", None)
                try:
                    if callable(validator):
                        validator(arguments)
                except ValueError as exc:
                    result = _tool_failure(
                        "INVALID_TOOL_ARGUMENTS",
                        f"{type(exc).__name__}: {exc}",
                        name="ToolArgumentsError",
                    )
                else:
                    self._event_log.append(
                        "tool/started",
                        call_id=call_id,
                        tool=tool_name,
                        model=model,
                        protocol=protocol,
                        step=step,
                        call_index=call_index,
                        arguments_sha256=fingerprint,
                    )
                    try:
                        result = tool.execute(arguments, call_id=call_id)
                    except Exception as exc:
                        self._event_log.append(
                            "tool/outcome_unknown",
                            call_id=call_id,
                            tool=tool_name,
                            model=model,
                            protocol=protocol,
                            step=step,
                            call_index=call_index,
                            arguments_sha256=fingerprint,
                            error={"name": type(exc).__name__, "message": str(exc)},
                        )
                        result = _tool_failure(
                            "TOOL_OUTCOME_UNKNOWN",
                            (
                                f"{type(exc).__name__}: {exc}; the external operation may have completed, "
                                "so this call_id will not be replayed automatically"
                            ),
                            name=type(exc).__name__,
                        )
            self._session_call_cache[call_id] = {
                "fingerprint": fingerprint,
                "result": json.loads(json.dumps(result, ensure_ascii=False)),
            }
        reminder = self._repeat_reminder(
            fingerprint,
            tool_name=tool_name,
            arguments=arguments,
        )
        self._event_log.append(
            "tool/result",
            call_id=call_id,
            tool=tool_name,
            model=model,
            protocol=protocol,
            step=step,
            call_index=call_index,
            arguments_sha256=fingerprint,
            cache_hit=cache_hit,
            result=result,
        )
        if reminder:
            self._event_log.append(
                "user/message",
                source="repeat-tool-reminder",
                step=step,
                content=reminder,
            )
        return result, reminder

    def run_json(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        thinking: str,
        timeout: int,
    ) -> ToolLoopResult:
        provider = getattr(self.client, "config", None)
        if provider is None or not tool_loop_supported(self.client, provider, model):
            raise ModelToolLoopUnavailableError(
                "所选服务商、模型和协议未登记可用的原生工具调用与图片回看闭环。"
            )
        active_client, responses_protocol = _model_tool_client(self.client, provider, model)
        input_items = (
            active_client.responses_input_items(messages)
            if responses_protocol
            else json.loads(json.dumps(messages, ensure_ascii=False))
        )
        tool_definitions = [tool.definition() for tool in self.tools.values()]
        raw_responses: list[dict[str, Any]] = []
        delivered_assets: set[str] = set()
        generated_assets: dict[str, ImageArtifact] = {}
        tool_call_count = 0

        if self._session_artifacts:
            prior_content: list[dict[str, Any]] = [
                {
                    "type": "input_text" if responses_protocol else "text",
                    "text": (
                        "These images were generated and inspected earlier in this same answer-repair transaction. "
                        "Re-inspect them now. You may reuse an asset_id in generated_images only if it still satisfies "
                        "the current corrected answer; otherwise call generate_image again or omit it."
                    ),
                }
            ]
            for artifact in self._session_artifacts.values():
                prior_content.append(
                    {
                        "type": "input_text" if responses_protocol else "text",
                        "text": json.dumps({"asset": artifact.to_dict()}, ensure_ascii=False),
                    }
                )
                prior_content.append(
                    (
                        {"type": "input_image", "image_url": self.artifact_store.data_url(artifact.asset_id)}
                        if responses_protocol
                        else {
                            "type": "image_url",
                            "image_url": {"url": self.artifact_store.data_url(artifact.asset_id)},
                        }
                    )
                )
                delivered_assets.add(artifact.asset_id)
                generated_assets[artifact.asset_id] = artifact
            input_items.append({"role": "user", "content": prior_content})

        for step in range(1, self.max_steps + 1):
            protocol = "responses" if responses_protocol else "chat_completions"
            provider_name = str(getattr(provider, "name", "") or "")
            before_measurement = measure_request_tokens(
                input_items,
                provider=provider_name,
                model=model,
                tools=tool_definitions,
            )
            quality_limit = model_stage_quality_limit(provider_name, model, "answer_generation")
            if before_measurement.estimated_input_tokens > quality_limit:
                candidate_items, compaction = compact_non_core_history(input_items)
                if compaction["compacted_result_count"]:
                    input_items = candidate_items
                    after_measurement = measure_request_tokens(
                        input_items,
                        provider=provider_name,
                        model=model,
                        tools=tool_definitions,
                    )
                    self._event_log.append(
                        "history/compacted",
                        provider=provider_name,
                        model=model,
                        protocol=protocol,
                        step=step,
                        quality_input_token_limit=quality_limit,
                        estimated_tokens_before=before_measurement.estimated_input_tokens,
                        estimated_tokens_after=after_measurement.estimated_input_tokens,
                        **compaction,
                    )
            self._event_log.append(
                "agent/request",
                provider=provider_name,
                model=model,
                protocol=protocol,
                step=step,
                thinking=thinking,
                max_tokens=max_tokens,
                input_sha256=hashlib.sha256(_canonical_json(input_items).encode("utf-8")).hexdigest(),
                tools_sha256=hashlib.sha256(_canonical_json(tool_definitions).encode("utf-8")).hexdigest(),
            )
            try:
                raw = active_client.create_tool_response(
                    input_items,
                    tools=tool_definitions,
                    model=model,
                    max_tokens=max_tokens,
                    thinking=thinking,
                    timeout=timeout,
                    json_object=True,
                )
            except Exception as exc:
                self._event_log.append(
                    "agent/request_error",
                    provider=str(getattr(provider, "name", "") or ""),
                    model=model,
                    protocol=protocol,
                    step=step,
                    error={"name": type(exc).__name__, "message": str(exc)},
                )
                raise
            raw_responses.append(raw)
            calls = _function_calls(raw) if responses_protocol else _chat_function_calls(raw)
            self._event_log.append(
                "agent/completion",
                provider=str(getattr(provider, "name", "") or ""),
                model=model,
                protocol=protocol,
                step=step,
                tool_call_count=len(calls),
                response_sha256=hashlib.sha256(_canonical_json(raw).encode("utf-8")).hexdigest(),
            )
            if not calls:
                content = (
                    active_client.responses_output_text(raw)
                    if responses_protocol
                    else _chat_output_text(raw)
                )
                if not content:
                    raise LLMError("main model returned neither a tool call nor final JSON")
                try:
                    value = parse_json_content(content)
                except LLMError:
                    if step >= self.max_steps:
                        raise
                    repair_text = (
                        "Your previous final answer was not one valid JSON object. Repair only its "
                        "JSON syntax and string escaping. Preserve the accepted image asset_ids and "
                        "answer content. Return exactly one JSON object: no preamble, Markdown, code "
                        "fence, or trailing explanation. Do not call an image tool unless the image "
                        "itself is actually wrong."
                    )
                    if responses_protocol:
                        output_items = raw.get("output")
                        if isinstance(output_items, list):
                            input_items.extend(output_items)
                        input_items.append(
                            {"role": "user", "content": [{"type": "input_text", "text": repair_text}]}
                        )
                    else:
                        input_items.append({"role": "assistant", "content": content})
                        input_items.append({"role": "user", "content": repair_text})
                    continue
                selected_asset_ids = _generated_image_refs(value)
                unknown = selected_asset_ids - delivered_assets
                if unknown:
                    raise LLMError("main model referenced image assets it had not inspected: " + ", ".join(sorted(unknown)))
                return ToolLoopResult(
                    value=value,
                    steps=step,
                    tool_calls=tool_call_count,
                    generated_artifacts=[item.to_dict() for item in generated_assets.values()],
                    raw_responses=raw_responses,
                    tool_event_log=str(self.tool_event_log_path.resolve()),
                )

            if responses_protocol:
                output_items = raw.get("output")
                if isinstance(output_items, list):
                    input_items.extend(output_items)
            else:
                # Preserve the complete gateway message (including any
                # provider-specific thought signature) when acknowledging its
                # native tool calls.
                input_items.append(json.loads(json.dumps(_chat_message(raw), ensure_ascii=False)))
            chat_visual_content: list[dict[str, Any]] = []
            repeat_reminders: list[str] = []
            for call_index, call in enumerate(calls, start=1):
                tool_call_count += 1
                call_id = call["call_id"]
                if not call_id:
                    raise LLMError(f"tool call {call_index} in step {step} is missing call_id")
                result, repeat_reminder = self._dispatch_tool_call(
                    call,
                    model=model,
                    protocol=protocol,
                    step=step,
                    call_index=call_index,
                    budget_exhausted=tool_call_count > self.max_tool_calls,
                )
                if repeat_reminder:
                    repeat_reminders.append(repeat_reminder)

                content: list[dict[str, Any]] = [
                    {"type": "input_text", "text": json.dumps(result, ensure_ascii=False)}
                ]
                asset = result.get("asset") if isinstance(result, dict) else None
                asset_id = str(asset.get("asset_id") or "") if isinstance(asset, dict) else ""
                if asset_id:
                    artifact = self.artifact_store.get(asset_id)
                    if artifact is None:
                        raise LLMError(f"tool returned unknown local asset: {asset_id}")
                    generated_assets[asset_id] = artifact
                    self._session_artifacts[asset_id] = artifact
                    content.append({"type": "input_image", "image_url": self.artifact_store.data_url(asset_id)})
                    delivered_assets.add(asset_id)
                if responses_protocol:
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": content,
                        }
                    )
                else:
                    input_items.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    if asset_id:
                        chat_visual_content.extend(
                            [
                                {
                                    "type": "text",
                                    "text": (
                                        "Inspect the generated pixels for this tool result before deciding whether "
                                        f"to accept asset_id {asset_id}:\n"
                                        + json.dumps(result, ensure_ascii=False)
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": self.artifact_store.data_url(asset_id)},
                                },
                            ]
                        )
            if not responses_protocol and chat_visual_content:
                # Chat Completions requires every tool result to immediately
                # follow the assistant tool_calls message.  Deliver pixels in
                # one subsequent multimodal user message only after all tool
                # acknowledgements have been appended.
                input_items.append({"role": "user", "content": chat_visual_content})
            if repeat_reminders:
                reminder_text = "\n\n".join(repeat_reminders)
                input_items.append(
                    {
                        "role": "user",
                        "content": ([{"type": "input_text", "text": reminder_text}] if responses_protocol else reminder_text),
                    }
                )
        raise LLMError(f"main model did not produce final JSON within {self.max_steps} agent steps")


def tool_loop_supported(client: OpenAICompatibleClient, provider: Any, model: str) -> bool:
    from .model_capability_registry import (
        get_native_tool_route,
        provider_has_capability_registry,
    )
    from .settings import provider_model_supports_vision

    selected = str(model or "").strip()
    if not provider_model_supports_vision(provider, selected):
        return False
    if isinstance(client, AnthropicMessagesClient) or not isinstance(client, OpenAICompatibleClient):
        return False
    profile = dict((getattr(provider, "model_profiles", {}) or {}).get(selected) or {})
    if profile.get("supports_tool_calls") is not True:
        return False
    protocol = _model_tool_protocol(provider, selected)
    if protocol not in {"responses", "responses_api", "chat_completions", "openai_compatible", ""}:
        return False
    provider_name = str(getattr(provider, "name", "") or "").strip()
    route = get_native_tool_route(provider_name, selected)
    if route is None:
        # Tests and external custom providers can still opt in explicitly, but
        # a built-in provider/model pair is denied unless it is in the closed
        # verified registry allowlist.
        return not provider_has_capability_registry(provider_name)
    return str(route.get("protocol") or "").strip().lower() == protocol
