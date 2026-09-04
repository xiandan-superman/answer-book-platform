from __future__ import annotations

import json

from app.document_tool import DocumentToolFailure, DocumentToolSession


def test_document_tool_records_linked_call_and_actionable_failure(tmp_path) -> None:
    events = tmp_path / "document_tool_events.jsonl"
    session = DocumentToolSession(events, session_id="task-1")

    def fail() -> dict:
        raise DocumentToolFailure(
            code="FORMULA_RENDER_FAILED",
            message="formula_ref:f1 无法转换。",
            suggestion="保留原公式并修复文档执行层。",
            details={"location": "formula_ref:f1", "formula_sha256": "a" * 64},
        )

    result = session.run("build_validate_docx", fail, input_revision="b" * 64)
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]

    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "FORMULA_RENDER_FAILED"
    assert result["error"]["suggestion"]
    assert [row["type"] for row in rows] == ["tool/call", "tool/result"]
    assert rows[0]["call_id"] == rows[1]["call_id"] == result["meta"]["call_id"]
    assert "formula_ref:f1" in rows[1]["result"]["error"]["details"]["location"]


def test_document_tool_success_includes_output_revision_and_recovers_sequence(tmp_path) -> None:
    artifact = tmp_path / "answer.docx"
    events = tmp_path / "document_tool_events.jsonl"

    def write_artifact() -> dict:
        artifact.write_bytes(b"docx-bytes")
        return {"validated": True}

    first = DocumentToolSession(events, session_id="task-1").run(
        "build_docx",
        write_artifact,
        artifact_path=artifact,
    )
    second = DocumentToolSession(events, session_id="task-1").run(
        "validate_docx",
        lambda: {"validated": True},
        artifact_path=artifact,
    )
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]

    assert first["ok"] is True
    assert first["meta"]["artifact"]["sha256"]
    assert second["ok"] is True
    assert [row["sequence"] for row in rows] == [1, 2, 3, 4]
