from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel

from ..llm_client import IncompleteOutputError, LLMError, StructuredOutputError
from ..prompt_registry import current_prompt_contract_id, prompt_contract

ModelT = TypeVar("ModelT", bound=BaseModel)


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
    """Validate model output with Instructor without bypassing platform transport.

    Instructor owns schema injection and validation re-asks.  Every actual HTTP
    request still goes through ``client.chat_json``, so the platform's call
    budget, concurrency limit, cancellation and usage ledger remain authoritative.
    """

    try:
        import instructor
        from openai.types.chat import ChatCompletion
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise StructuredOutputError("结构化输出运行时缺少 instructor/openai 依赖") from exc

    attempt_count = 0
    last_content = ""

    def create(**kwargs: Any) -> Any:
        nonlocal attempt_count, last_content
        attempt_count += 1
        if callable(attempt_callback):
            attempt_callback("started", {"attempt": attempt_count, "strategy": "instructor_schema_validation"})
        with prompt_contract(current_prompt_contract_id() or "structured.validation"):
            result = client.chat_json(
                kwargs.get("messages") or [],
                model=str(kwargs.get("model") or model),
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                thinking=thinking,
                task_stage=task_stage,
                required_evidence_refs=required_evidence_refs,
                delivered_evidence_refs=delivered_evidence_refs,
                item_ids=item_ids,
                enforce_context_budget=enforce_context_budget,
            )
        last_content = result.content
        raw = getattr(result, "raw", {}) or {}
        choices = raw.get("choices")
        first_choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        finish_reason = str(first_choice.get("finish_reason") or "stop")
        if finish_reason == "length":
            raise IncompleteOutputError("Model structured output reached max_tokens")
        return ChatCompletion.model_validate(
            {
                "id": str(raw.get("id") or "answer-book-structured"),
                "created": int(raw.get("created") or 0),
                "model": str(getattr(result, "model", "") or model),
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish_reason,
                        "message": {"role": "assistant", "content": result.content},
                    }
                ],
            }
        )

    patched_create = instructor.patch(
        create=create,
        mode=instructor.Mode.JSON,
        provider=instructor.Provider.OPENAI,
    )
    try:
        validated = patched_create(
            response_model=response_model,
            messages=deepcopy(messages),
            model=model,
            max_retries=max(0, int(max_validation_retries)),
            timeout=timeout,
        )
        client.last_json_retry_report = {
            "ok": True,
            "attempts": [{"attempt": index + 1, "strategy": "instructor_schema_validation"} for index in range(attempt_count)],
            "recommendations": [],
            "quality_preserving": True,
        }
        if callable(attempt_callback):
            attempt_callback("succeeded", {"attempt": attempt_count, "strategy": "instructor_schema_validation"})
        return validated
    except (IncompleteOutputError, LLMError):
        raise
    except Exception as exc:
        client.last_json_retry_report = {
            "ok": False,
            "attempts": [{"attempt": index + 1, "strategy": "instructor_schema_validation"} for index in range(attempt_count)],
            "recommendations": ["inspect_pydantic_validation_errors"],
            "quality_preserving": True,
        }
        if callable(attempt_callback):
            attempt_callback("failed", {"attempt": attempt_count, "strategy": "instructor_schema_validation", "error": str(exc)})
        raise StructuredOutputError(
            f"同路由 JSON 修复后仍失败：模型输出未通过 {response_model.__name__} 校验：{exc}",
            content=last_content,
        ) from exc
