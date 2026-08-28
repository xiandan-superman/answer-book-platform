from __future__ import annotations

from typing import Any

from .practice_jobs import load_practice_job
from .practice_store import load_practice_record
from .redaction import redact_credentials, redact_diagnostic_value


def _sanitized(value: Any) -> Any:
    return redact_diagnostic_value(value)


def build_practice_diagnostics(task_id: str) -> dict[str, Any]:
    """Expose saved practice failure evidence through the unified task API.

    Job records retain the original exception type, traceback and structured
    failure context.  The public diagnostics response deliberately omits the
    request payload and full result checkpoint, and applies credential
    redaction again at read time.
    """
    normalized_id = str(task_id or "").strip()
    if normalized_id.startswith("generation_"):
        record = load_practice_job(normalized_id)
        diagnostic_context = record.get("diagnostic_context")
        failure_context = record.get("failure_context")
        return {
            "schema_version": "answer_book.practice_diagnostics.v1",
            "task_id": normalized_id,
            "task_kind": str(record.get("task_kind") or "practice"),
            "record_type": "practice_job",
            "status": str(record.get("status") or ""),
            "operation": str(record.get("operation") or ""),
            "stage": str(record.get("current_stage") or ""),
            "checkpoint_stage": str(record.get("checkpoint_stage") or ""),
            "support_id": str(record.get("support_id") or ""),
            "error": redact_credentials(str(record.get("error") or "")),
            "progress_message": redact_credentials(str(record.get("progress_message") or "")),
            "counts": {
                "generated": max(0, int(record.get("generated_count") or 0)),
                "failed": max(0, int(record.get("failed_count") or 0)),
                "total": max(0, int(record.get("total_count") or 0)),
            },
            "model_usage": _sanitized(record.get("model_usage") or {}),
            "batch_errors": _sanitized(record.get("batch_errors") or []),
            "batch_diagnostics": _sanitized(record.get("batch_diagnostics") or []),
            "failure_context": _sanitized(failure_context if isinstance(failure_context, dict) else {}),
            "diagnostic_context": _sanitized(diagnostic_context if isinstance(diagnostic_context, dict) else {}),
            "recovery": {
                "postprocess_recoverable": str(record.get("checkpoint_stage") or "") in {
                    "model_generation_complete",
                    "result_ready_for_history_save",
                },
                "completed_question_checkpoint_count": len(record.get("partial_exercises") or []),
                "full_result_checkpoint_available": bool(
                    isinstance(record.get("postprocess_checkpoint"), dict)
                    and isinstance(record.get("postprocess_checkpoint", {}).get("result"), dict)
                ),
            },
        }
    if normalized_id.startswith("practice_"):
        record = load_practice_record(normalized_id)
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        return {
            "schema_version": "answer_book.practice_diagnostics.v1",
            "task_id": normalized_id,
            "task_kind": str(record.get("task_kind") or "practice"),
            "record_type": "practice_history",
            "status": str(record.get("status") or ""),
            "operation": "generate_from_plan",
            "stage": "completed",
            "quality": _sanitized(data.get("quality") or record.get("quality") or {}),
            "generation": _sanitized(data.get("generation") or record.get("generation") or {}),
            "completion_issues": _sanitized(data.get("completion_issues") or record.get("completion_issues") or {}),
            "semantic_review": _sanitized(data.get("semantic_review") or {}),
            "format_repair": _sanitized(data.get("format_repair") or {}),
            "blueprint_audit_repair": _sanitized(data.get("blueprint_audit_repair") or {}),
        }
    raise FileNotFoundError("专项生题任务不存在。")
