from __future__ import annotations

import pytest

from app.llm_client import LLMError
from app.provider_errors import classify_provider_error
from app.server import _provider_test_error_payload


@pytest.mark.parametrize(
    ("raw", "status", "kind", "title", "retryable", "requires_configuration"),
    [
        ("Please pass a valid API key", 400, "provider_authentication", "API Key 无效", False, True),
        ("insufficient_quota: credit balance is empty", 429, "provider_quota_exhausted", "模型服务额度不足", False, True),
        ("too many concurrent requests", 429, "provider_concurrency_limit", "模型并发已达上限", True, False),
        ("rate limit exceeded", 429, "provider_rate_limit", "模型请求过于频繁", True, False),
        ("Provider HTTP 403: error code: 1010", 403, "provider_gateway_client_blocked", "模型网关拒绝了当前客户端", False, False),
        ("context_length_exceeded", 400, "provider_input_too_long", "提交给模型的内容过长", False, False),
        ("blocked by content_filter", 400, "provider_content_blocked", "内容未通过模型安全检查", False, False),
        ("service unavailable: overloaded", 503, "provider_overloaded", "模型服务当前繁忙", True, False),
        ("connection reset by peer", None, "provider_network", "模型服务网络连接异常", True, False),
        ("internal server error", 500, "provider_internal_error", "模型服务内部异常", True, False),
    ],
)
def test_provider_errors_are_translated_to_actionable_copy(
    raw: str,
    status: int | None,
    kind: str,
    title: str,
    retryable: bool,
    requires_configuration: bool,
) -> None:
    info = classify_provider_error(raw, status_code=status)

    assert info.kind == kind
    assert info.title == title
    assert info.message
    assert info.suggested_action
    assert info.retryable is retryable
    assert info.requires_configuration is requires_configuration
    assert raw not in info.message
    assert raw not in info.suggested_action


def test_concurrency_retry_after_is_explained_without_raw_provider_response() -> None:
    info = classify_provider_error(
        '{"error":{"code":"concurrent_limit","request_id":"secret-request-id"}}',
        status_code=429,
        retry_after_seconds=12,
    )

    assert info.kind == "provider_concurrency_limit"
    assert "12 秒" in info.suggested_action
    assert "secret-request-id" not in f"{info.title}{info.message}{info.suggested_action}"


def test_provider_test_payload_never_returns_raw_provider_body() -> None:
    raw = 'Provider HTTP 429: {"error":{"code":"concurrent_limit","request_id":"req-secret"}}'
    payload = _provider_test_error_payload(
        "google_ai",
        "gemini-test",
        LLMError(raw, status_code=429, retry_after_seconds=8),
    )

    assert payload["error_code"] == "provider_concurrency_limit"
    assert payload["error_title"] == "模型并发已达上限"
    assert payload["retryable"] is True
    assert "8 秒" in str(payload["suggested_action"])
    assert "req-secret" not in str(payload)
    assert "Provider HTTP" not in str(payload)
