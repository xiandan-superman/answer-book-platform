from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

_INTERNAL_MARKERS = (
    "traceback",
    "invalid format specifier",
    "keyerror",
    "typeerror",
    "attributeerror",
    "filenotfounderror",
    "generate_from_contract",
    "generate_from_plan",
)


def _looks_user_safe(message: str) -> bool:
    lowered = message.lower()
    return bool(re.search(r"[\u4e00-\u9fff]", message)) and not any(marker in lowered for marker in _INTERNAL_MARKERS)


def public_error_payload(exc: Exception, *, status: int, path: str) -> dict[str, Any]:
    """Convert internal exceptions into stable, actionable user-facing errors."""
    message = str(exc or "").strip()
    lowered = message.lower()
    support_id = uuid4().hex[:10]
    if status == 400 and _looks_user_safe(message):
        code = "invalid_request"
        user_message = message
        suggested_action = "检查当前页面填写或选择的内容后重试。"
    elif "timeout" in lowered or "超时" in message or "524" in lowered:
        code = "provider_timeout"
        user_message = "模型服务响应超时，本次操作没有完整完成。"
        suggested_action = "返回任务中心，从已保存检查点重试。"
    elif "json" in lowered:
        code = "invalid_model_output"
        user_message = "模型返回格式未通过校验，本次结果没有写入正式产物。"
        suggested_action = "从当前模型步骤重试；已确认的范围和蓝图会继续保留。"
    elif "not found" in lowered or isinstance(exc, FileNotFoundError):
        code = "resource_not_found"
        user_message = "需要的任务或文件不存在，可能已经被移动或清理。"
        suggested_action = "刷新任务列表；如果问题仍存在，请在运行监控中查看技术日志。"
    else:
        code = "internal_error"
        user_message = "程序处理这次请求时遇到内部错误，已记录诊断编号。"
        suggested_action = "请重试一次；若仍失败，在运行监控中按诊断编号查找技术详情。"
    return {
        "ok": False,
        "error": user_message,
        "error_code": code,
        "suggested_action": suggested_action,
        "support_id": support_id,
        "path": path,
    }
