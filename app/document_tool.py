from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DOCUMENT_TOOL_RESULT_SCHEMA = "answer_book.document_tool_result.v1"
DOCUMENT_TOOL_EVENT_SCHEMA = "answer_book.document_tool_events.v1"


def _file_revision(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    content = path.read_bytes()
    return {
        "name": path.name,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


@dataclass
class DocumentToolFailure(Exception):
    """A typed, actionable document operation failure.

    The exception deliberately carries locations and hashes instead of document
    text.  Callers can persist it safely and still tell a deterministic renderer
    failure from a model-content problem.
    """

    code: str
    message: str
    suggestion: str
    responsibility: str = "deterministic_postprocess"
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "responsibility": self.responsibility,
            "retryable": self.retryable,
            "details": self.details,
        }
        if self.candidates:
            result["candidates"] = self.candidates
        return result


class DocumentToolSession:
    """Run document operations with durable call/result pairs and one envelope.

    This is the local equivalent of a Harness tool boundary: every started call
    receives a linked result, including failures, and every failure states the
    next safe action.  Domain-specific construction and audits remain callbacks
    owned by this project.
    """

    def __init__(self, event_path: Path | None, *, session_id: str) -> None:
        self.event_path = Path(event_path) if event_path is not None else None
        self.session_id = str(session_id)
        self._lock = threading.Lock()
        self._sequence = 0
        self._attempts: dict[str, int] = {}
        if self.event_path is not None:
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            self._recover_sequence()

    def _recover_sequence(self) -> None:
        if self.event_path is None or not self.event_path.is_file():
            return
        try:
            lines = self.event_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict) or str(event.get("session_id") or "") != self.session_id:
                continue
            try:
                self._sequence = max(self._sequence, int(event.get("sequence") or 0))
            except (TypeError, ValueError):
                continue

    def _append(self, event_type: str, **payload: Any) -> None:
        if self.event_path is None:
            return
        with self._lock:
            self._sequence += 1
            event = {
                "schema_version": DOCUMENT_TOOL_EVENT_SCHEMA,
                "session_id": self.session_id,
                "sequence": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                **payload,
            }
            with self.event_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def run(
        self,
        operation: str,
        action: Callable[[], dict[str, Any] | None],
        *,
        artifact_path: Path | None = None,
        input_revision: str = "",
    ) -> dict[str, Any]:
        operation_name = str(operation or "document_operation")
        attempt = self._attempts.get(operation_name, 0) + 1
        self._attempts[operation_name] = attempt
        call_id = uuid.uuid4().hex
        self._append(
            "tool/call",
            call_id=call_id,
            operation=operation_name,
            attempt=attempt,
            input_revision=input_revision,
        )
        started = time.perf_counter()
        data: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        try:
            data = action() or {}
            ok = True
        except DocumentToolFailure as exc:
            ok = False
            error = exc.to_dict()
        except Exception as exc:
            ok = False
            error = DocumentToolFailure(
                code="DOCUMENT_TOOL_INTERNAL_ERROR",
                message=str(exc) or exc.__class__.__name__,
                suggestion="保留当前候选件和工具结果，按错误位置修复文档执行层后重试。",
                details={"exception_type": exc.__class__.__name__},
            ).to_dict()
        envelope = {
            "schema_version": DOCUMENT_TOOL_RESULT_SCHEMA,
            "ok": ok,
            "data": data if ok else None,
            "error": error,
            "meta": {
                "session_id": self.session_id,
                "call_id": call_id,
                "operation": operation_name,
                "attempt": attempt,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "input_revision": input_revision,
                "artifact": _file_revision(artifact_path),
            },
        }
        self._append(
            "tool/result",
            call_id=call_id,
            operation=operation_name,
            attempt=attempt,
            result=envelope,
        )
        return envelope
