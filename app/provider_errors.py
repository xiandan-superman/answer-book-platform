from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderErrorInfo:
    kind: str
    title: str
    message: str
    suggested_action: str
    status_code: int | None = None
    retryable: bool = False
    requires_configuration: bool = False


def _contains(text: str, *markers: str) -> bool:
    return any(marker in text for marker in markers)


def _status_from_text(text: str) -> int | None:
    patterns = (
        r"(?:provider\s+)?http\s+(\d{3})",
        r"[\"']?status(?:_code)?[\"']?\s*[:=]\s*[\"']?(\d{3})",
        r"[\"']?code[\"']?\s*:\s*(\d{3})",
        r"\b(4\d{2}|5\d{2})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def classify_provider_error(
    error: Any,
    *,
    status_code: int | None = None,
    transport_phase: str = "",
    retry_after_seconds: float | None = None,
) -> ProviderErrorInfo:
    """Translate provider and transport failures into provider-neutral user copy.

    The original provider response must remain in diagnostics only. Callers should
    display this result as the primary error presentation.
    """
    raw = str(error or "").strip()
    lowered = raw.lower()
    status = status_code if isinstance(status_code, int) else _status_from_text(lowered)
    phase = str(transport_phase or "").strip().lower()

    def result(
        kind: str,
        title: str,
        message: str,
        action: str,
        *,
        retryable: bool = False,
        requires_configuration: bool = False,
    ) -> ProviderErrorInfo:
        return ProviderErrorInfo(
            kind=kind,
            title=title,
            message=message,
            suggested_action=action,
            status_code=status,
            retryable=retryable,
            requires_configuration=requires_configuration,
        )

    missing_key = re.fullmatch(
        r"api key is not configured for provider:\s*[a-z0-9_.-]+",
        raw,
        flags=re.IGNORECASE,
    )
    if missing_key:
        return result(
            "provider_missing_api_key",
            "模型服务尚未配置",
            "当前平台没有可用的 API Key，因此模型请求尚未发出。",
            "请前往 API 配置填写并验证对应平台的 Key，然后重试当前任务。",
            requires_configuration=True,
        )

    if (
        status == 401
        or _contains(
            lowered,
            "invalid api key",
            "invalid_api_key",
            "please pass a valid api key",
            "incorrect api key",
            "authentication failed",
            "authentication_error",
            "unauthorized",
            "apikeynotfound",
            "api key 无效",
        )
    ):
        return result(
            "provider_authentication",
            "API Key 无效",
            "模型服务未通过身份验证，API Key 可能无效、已过期或已被停用。",
            "请在 API 配置中重新填写并测试该平台的 Key，验证成功后再重试。",
            requires_configuration=True,
        )

    quota_markers = (
        "insufficient_quota",
        "quota exceeded",
        "quota_exceeded",
        "exceeded your current quota",
        "billing",
        "insufficient balance",
        "insufficient credit",
        "credit balance",
        "account balance",
        "余额不足",
        "额度不足",
        "配额已用尽",
        "欠费",
    )
    if status == 402 or _contains(lowered, *quota_markers) or (
        "resource_exhausted" in lowered and _contains(lowered, "quota", "credit", "billing")
    ):
        return result(
            "provider_quota_exhausted",
            "模型服务额度不足",
            "当前账号的调用额度、余额或免费配额可能已经用完。",
            "请在服务商控制台检查用量与账单，补充额度或更换可用平台后再继续。",
            requires_configuration=True,
        )

    concurrency_markers = (
        "concurrency limit",
        "concurrent request",
        "too many concurrent",
        "max concurrency",
        "concurrent_limit",
        "并发限制",
        "并发数",
        "并发已满",
    )
    if _contains(lowered, *concurrency_markers):
        action = "请等待其他模型任务完成后重试；也可以减少同时运行的任务数量。"
        if isinstance(retry_after_seconds, (int, float)) and retry_after_seconds > 0:
            action = f"请等待约 {max(1, round(retry_after_seconds))} 秒后重试，并减少同时运行的任务数量。"
        return result(
            "provider_concurrency_limit",
            "模型并发已达上限",
            "当前同时运行的模型请求过多，服务商暂时无法接收新请求。",
            action,
            retryable=True,
        )

    rate_markers = (
        "rate limit",
        "rate_limit",
        "too many requests",
        "request limit",
        "requests per minute",
        "tokens per minute",
        "resource_exhausted",
        "请求过于频繁",
        "频率限制",
        "限流",
    )
    if status == 429 or _contains(lowered, *rate_markers):
        action = "请稍等片刻后重试；连续出现时，请降低任务并发或检查平台限流规则。"
        if isinstance(retry_after_seconds, (int, float)) and retry_after_seconds > 0:
            action = f"请等待约 {max(1, round(retry_after_seconds))} 秒后重试，并适当降低任务并发。"
        return result(
            "provider_rate_limit",
            "模型请求过于频繁",
            "当前调用频率超过了服务商允许的速率，服务暂时拒绝了本次请求。",
            action,
            retryable=True,
        )

    if status == 403 or _contains(lowered, "permission denied", "forbidden", "access denied", "permission_denied"):
        return result(
            "provider_permission",
            "模型访问权限不足",
            "当前账号或 API Key 没有所选模型、接入点或所在区域的调用权限。",
            "请在服务商控制台开通对应权限，或在 API 配置中改用已获授权的模型。",
            requires_configuration=True,
        )

    if (
        status == 404
        or _contains(
            lowered,
            "modelnotopen",
            "invalidendpointormodel",
            "model not found",
            "endpoint not found",
            "model_not_found",
            "does not exist",
            "模型不存在",
            "模型未开通",
        )
    ):
        return result(
            "provider_target_not_found",
            "模型或接入点不可用",
            "服务商未找到当前模型或 Endpoint（接入点），也可能是该模型尚未开通。",
            "请核对模型名称、Endpoint 和可用区域，并在连接测试通过后再重试。",
            requires_configuration=True,
        )

    length_markers = (
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "too many tokens",
        "input is too long",
        "request too large",
        "payload too large",
        "prompt is too long",
        "上下文长度",
        "输入过长",
    )
    if status == 413 or _contains(lowered, *length_markers):
        return result(
            "provider_input_too_long",
            "提交给模型的内容过长",
            "本次输入或预计输出超过了所选模型可处理的长度。",
            "请缩小资料或题目范围、减少单次生成数量，或改用上下文更长的模型。",
        )

    safety_markers = (
        "content_filter",
        "content filtered",
        "safety policy",
        "safety_ratings",
        "responsibleai",
        "blocked_reason",
        "prohibited content",
        "内容审核",
        "安全策略",
        "内容被拦截",
    )
    if _contains(lowered, *safety_markers):
        return result(
            "provider_content_blocked",
            "内容未通过模型安全检查",
            "服务商的安全策略阻止了本次请求或响应。",
            "请检查输入中可能触发限制的内容，调整表述或资料范围后再试。",
        )

    invalid_markers = (
        "invalid argument",
        "invalid_argument",
        "invalid parameter",
        "invalid_parameter",
        "unsupported parameter",
        "unknown parameter",
        "bad request",
        "参数无效",
        "参数不支持",
    )
    if status in {400, 422} or _contains(lowered, *invalid_markers):
        return result(
            "provider_invalid_request",
            "模型请求参数不兼容",
            "当前模型不接受本次请求中的部分参数或内容格式。",
            "请重新测试所选模型；若仍失败，请切换模型或检查该模型的参数配置。",
            requires_configuration=True,
        )

    timeout_markers = (
        "timeout",
        "timed out",
        "deadline exceeded",
        "wall-clock deadline",
        "stream exceeded",
        "超时",
    )
    if phase in {"connect", "first_byte", "read_idle", "hard_timeout"} or status in {408, 504, 522, 524} or _contains(lowered, *timeout_markers):
        if phase == "connect":
            message = "平台未能在规定时间内连接到模型服务。"
        elif phase == "first_byte":
            message = "已连接模型服务，但迟迟没有收到首个响应。"
        elif phase == "read_idle":
            message = "模型开始响应后长时间没有继续返回内容。"
        else:
            message = "模型服务在规定时间内没有返回完整结果。"
        return result(
            "provider_timeout",
            "模型服务响应超时",
            message,
            "请检查网络后从当前步骤重试；若反复出现，可减少单次生成量或稍后再试。",
            retryable=True,
        )

    network_markers = (
        "network",
        "connection reset",
        "connection refused",
        "connection aborted",
        "remote end closed",
        "empty reply",
        "temporary failure in name resolution",
        "name or service not known",
        "dns",
        "ssl",
        "tls",
        "certificate verify failed",
        "eof occurred",
        "网络",
        "连接中断",
    )
    if status in {502, 520, 521, 523} or _contains(lowered, *network_markers):
        return result(
            "provider_network",
            "模型服务网络连接异常",
            "平台与模型服务之间的网络连接未建立或在传输过程中中断。",
            "请确认本机网络正常后重试；若持续发生，请稍后再试或切换服务商。",
            retryable=True,
        )

    overload_markers = (
        "overloaded",
        "server overloaded",
        "high demand",
        "capacity",
        "service unavailable",
        "temporarily unavailable",
        "模型繁忙",
        "服务繁忙",
    )
    if status == 503 or _contains(lowered, *overload_markers):
        return result(
            "provider_overloaded",
            "模型服务当前繁忙",
            "服务商当前负载较高，暂时无法处理本次请求。",
            "请稍后从当前步骤重试；无需重新提交已经完成的内容。",
            retryable=True,
        )

    if status == 409:
        return result(
            "provider_conflict",
            "模型请求状态冲突",
            "服务商暂时无法在当前状态下处理这次请求。",
            "请稍后重试当前步骤；若持续出现，请重新测试所选模型。",
            retryable=True,
        )

    if status is not None and 500 <= status <= 599:
        return result(
            "provider_internal_error",
            "模型服务内部异常",
            "服务商在处理请求时发生内部错误，本次任务未能完成。",
            "请稍后从当前步骤重试；若持续出现，可切换模型或服务商。",
            retryable=True,
        )

    return result(
        "provider_error",
        "模型服务调用失败",
        "模型服务未能完成本次请求，具体原因暂时无法确定。",
        "请先检查 API 配置和模型服务状态，再重试当前步骤；若仍失败，请检查网络或切换服务商。",
        retryable=True,
    )
