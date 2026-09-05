from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..llm_client import (
    IncompleteOutputError,
    LLMError,
    StructuredOutputError,
    parse_json_content_with_result,
)
from ..prompt_registry import current_prompt_contract_id, prompt_contract

ModelT = TypeVar("ModelT", bound=BaseModel)


def _schema_instruction(response_model: type[BaseModel], schema: dict[str, Any]) -> dict[str, str]:
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return {
        "role": "system",
        "content": (
            "Return exactly one JSON object that validates against the following JSON Schema. "
            "Do not use a markdown fence or add commentary outside the JSON object.\n"
            f"Schema name: {response_model.__name__}\nJSON Schema:\n{encoded}"
        ),
    }


def _validation_feedback(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = [
            {
                "path": [str(part) for part in error.get("loc", ())],
                "type": str(error.get("type") or "validation_error"),
                "message": str(error.get("msg") or "invalid value"),
            }
            for error in exc.errors(include_url=False, include_input=False)
        ]
    else:
        errors = [{"path": [], "type": "invalid_json", "message": str(exc)}]
    return json.dumps(errors, ensure_ascii=False, separators=(",", ":"))


def _output_reached_limit(result: Any) -> bool:
    raw = getattr(result, "raw", {}) or {}
    choices = raw.get("choices") if isinstance(raw, dict) else None
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    if str(first.get("finish_reason") or "") == "length":
        return True
    incomplete = raw.get("incomplete_details") if isinstance(raw, dict) else None
    return bool(
        str(raw.get("status") or "") == "incomplete"
        and isinstance(incomplete, dict)
        and str(incomplete.get("reason") or "") in {"max_output_tokens", "max_tokens"}
    )


def structured_completion(
    client: Any,
    messages: list[dict[str, Any]],
    *,
    response_model: type[ModelT],
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int = 120,
    thinking: str | None = None,
    max_validation_retries: int = 1,
    task_stage: str = "general",
    required_evidence_refs: Any = (),
    delivered_evidence_refs: Any = (),
    item_ids: Any = (),
    enforce_context_budget: bool = False,
    attempt_callback: Any | None = None,
) -> ModelT:
    """Validate one request-scoped schema without a mutable global registry.

    The selected provider adapter receives the exact Pydantic JSON Schema. It
    uses a native schema parameter when supported and keeps the same schema in
    the model-visible request for JSON-only gateways. Local Pydantic validation
    remains authoritative, with a bounded correction on the same route.
    """

    schema = response_model.model_json_schema(mode="validation")
    request_messages = [_schema_instruction(response_model, schema), *deepcopy(messages)]
    attempt_limit = 1 + max(0, int(max_validation_retries))
    attempt_reports: list[dict[str, Any]] = []
    last_content = ""
    last_error: Exception | None = None

    for attempt in range(1, attempt_limit + 1):
        report: dict[str, Any] = {
            "attempt": attempt,
            "strategy": "request_scoped_schema_validation",
        }
        if callable(attempt_callback):
            attempt_callback("started", dict(report))
        try:
            with prompt_contract(current_prompt_contract_id() or "structured.validation"):
                result = client.chat_json(
                    request_messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    thinking=thinking,
                    task_stage=task_stage,
                    required_evidence_refs=required_evidence_refs,
                    delivered_evidence_refs=delivered_evidence_refs,
                    item_ids=item_ids,
                    enforce_context_budget=enforce_context_budget,
                    output_schema=schema,
                    output_schema_name=response_model.__name__,
                )
        except (IncompleteOutputError, LLMError):
            raise

        last_content = result.content
        if _output_reached_limit(result):
            raise IncompleteOutputError("Model structured output reached max_tokens")
        try:
            validated = response_model.model_validate(parse_json_content_with_result(result))
        except Exception as exc:
            last_error = exc
            report["error"] = str(exc)
            attempt_reports.append(report)
            if attempt >= attempt_limit:
                break
            request_messages.extend(
                [
                    {"role": "assistant", "content": last_content},
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON failed schema validation. Correct only the structure and values "
                            "needed to satisfy the same schema, while preserving the requested content. Return "
                            "only the complete corrected JSON object.\nValidation errors:\n"
                            f"{_validation_feedback(exc)}"
                        ),
                    },
                ]
            )
            continue

        attempt_reports.append(report)
        client.last_json_retry_report = {
            "ok": True,
            "attempts": attempt_reports,
            "recommendations": [],
            "quality_preserving": True,
        }
        if callable(attempt_callback):
            attempt_callback("succeeded", dict(report))
        return validated

    client.last_json_retry_report = {
        "ok": False,
        "attempts": attempt_reports,
        "recommendations": ["inspect_pydantic_validation_errors"],
        "quality_preserving": True,
    }
    if callable(attempt_callback) and attempt_reports:
        attempt_callback("failed", dict(attempt_reports[-1]))
    raise StructuredOutputError(
        f"同路由 JSON 修复后仍失败：模型输出未通过 {response_model.__name__} 校验：{last_error}",
        content=last_content,
    ) from last_error
