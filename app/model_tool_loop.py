from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
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

IMAGE_TOOL_NAME = "generate_image"
MAX_REFERENCE_IMAGES = 5
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

    def execute(self, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]: ...


@dataclass
class ToolLoopResult:
    value: dict[str, Any]
    steps: int
    tool_calls: int
    generated_artifacts: list[dict[str, Any]] = field(default_factory=list)
    raw_responses: list[dict[str, Any]] = field(default_factory=list)


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
        reference_images: Iterable[str | Path] = (),
    ) -> None:
        self.provider = provider
        self.model = str(model or getattr(provider, "image_model", "") or "")
        self.store = store
        self.size = str(size or getattr(provider, "image_size", "") or "")
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

    def execute(self, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]:
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        temporary = self.store.root / f".{re.sub(r'[^0-9A-Za-z_.-]+', '_', call_id or 'call')}.png"
        raw_paths = arguments.get("referenced_image_paths")
        recent_count = arguments.get("num_last_images_to_include")
        if raw_paths is not None and recent_count is not None:
            raise ValueError(
                "referenced_image_paths and num_last_images_to_include cannot be used together"
            )
        reference_paths: list[Path] = []
        if raw_paths is not None:
            if not isinstance(raw_paths, list):
                raise ValueError("referenced_image_paths must be an array")
            if len(raw_paths) > MAX_REFERENCE_IMAGES:
                raise ValueError("referenced_image_paths cannot contain more than 5 paths")
            for raw_path in raw_paths:
                requested = str(raw_path or "").strip()
                path = self._allowed_reference_paths.get(requested)
                if path is None:
                    raise ValueError("referenced_image_paths contains an unregistered task-local path")
                reference_paths.append(path)
        elif recent_count is not None:
            if isinstance(recent_count, bool):
                raise ValueError("num_last_images_to_include must be an integer from 1 to 5")
            try:
                recent_count = int(recent_count)
            except (TypeError, ValueError) as exc:
                raise ValueError("num_last_images_to_include must be an integer from 1 to 5") from exc
            if not 1 <= recent_count <= MAX_REFERENCE_IMAGES:
                raise ValueError("num_last_images_to_include must be an integer from 1 to 5")
            if len(self._generated_paths) < recent_count:
                raise ValueError(
                    f"only {len(self._generated_paths)} recent generated image(s) are available for editing"
                )
            reference_paths = self._generated_paths[-recent_count:]

        client = OpenAICompatibleClient(self.provider)
        if reference_paths:
            result = client.edit_image(
                prompt,
                reference_paths,
                temporary,
                model=self.model,
                size=self.size or None,
            )
            operation = "edit"
        else:
            result = client.generate_image(
                prompt,
                temporary,
                model=self.model,
                size=self.size or None,
            )
            operation = "generate"
        artifact = self.store.register(
            result.path,
            provider=result.provider,
            model=result.model,
            source_call_id=call_id,
        )
        self._generated_paths.append(Path(artifact.path))
        temporary.unlink(missing_ok=True)
        return {
            "ok": True,
            "asset": artifact.to_dict(),
            "operation": operation,
            "reference_image_count": len(reference_paths),
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
    ) -> None:
        if isinstance(client, AnthropicMessagesClient) or not isinstance(client, OpenAICompatibleClient):
            raise ValueError("model tool loop requires a registered Responses or Chat Completions client")
        self.client = client
        self.tools = {tool.name: tool for tool in tools}
        self.artifact_store = artifact_store
        self.max_steps = max(1, int(max_steps))
        self.max_tool_calls = max(1, int(max_tool_calls))
        # One logical answer/repair transaction may invoke ``run_json`` again
        # after deterministic validation rejects a candidate.  Assets already
        # generated and shown in that same transaction remain eligible, but are
        # re-delivered below so the next model request actually sees them.
        self._session_artifacts: dict[str, ImageArtifact] = {}

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
        call_cache: dict[str, dict[str, Any]] = {}
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
            raw = active_client.create_tool_response(
                input_items,
                tools=tool_definitions,
                model=model,
                max_tokens=max_tokens,
                thinking=thinking,
                timeout=timeout,
                json_object=True,
            )
            raw_responses.append(raw)
            calls = _function_calls(raw) if responses_protocol else _chat_function_calls(raw)
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
                unknown = _generated_image_refs(value) - delivered_assets
                if unknown:
                    raise LLMError("main model referenced image assets it had not inspected: " + ", ".join(sorted(unknown)))
                return ToolLoopResult(
                    value=value,
                    steps=step,
                    tool_calls=tool_call_count,
                    generated_artifacts=[item.to_dict() for item in generated_assets.values()],
                    raw_responses=raw_responses,
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
            for call_index, call in enumerate(calls, start=1):
                tool_call_count += 1
                if tool_call_count > self.max_tool_calls:
                    raise LLMError(f"main model exceeded image tool call limit ({self.max_tool_calls})")
                call_id = call["call_id"]
                if not call_id:
                    raise LLMError(f"tool call {call_index} in step {step} is missing call_id")
                result = call_cache.get(call_id)
                if result is None:
                    tool = self.tools.get(call["name"])
                    if tool is None:
                        result = {"ok": False, "error": f"unknown tool: {call['name']}"}
                    else:
                        try:
                            arguments = call["arguments"]
                            if isinstance(arguments, str):
                                arguments = json.loads(arguments or "{}")
                            if not isinstance(arguments, dict):
                                raise ValueError("tool arguments must be a JSON object")
                            result = tool.execute(arguments, call_id=call_id)
                        except Exception as exc:
                            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    call_cache[call_id] = result

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
