from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

from app import model_diagnostics, practice_export_jobs, practice_jobs, support_reporting
from scripts import inspect_support_report, support_receiver


def _write_bundle(
    path: Path,
    report_id: str,
    fingerprint: str,
    *,
    payload: str = "ok",
    diagnostic_coverage: dict | None = None,
    schema_version: int = 1,
) -> dict:
    manifest = {
        "schema_version": schema_version,
        "report_id": report_id,
        "fingerprint": fingerprint,
        "created_at": "2026-08-21T12:00:00+00:00",
        "scope": "question",
        "application": {"version": "8.23"},
        "context": {"page": "result", "question_id": "q1"},
    }
    if diagnostic_coverage is not None:
        manifest["diagnostic_coverage"] = diagnostic_coverage
        manifest["counts"] = diagnostic_coverage.get("counts") or {}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("related_content.json", json.dumps({"answer": payload}, ensure_ascii=False))
    return manifest


def test_model_diagnostic_keeps_relevant_content_and_redacts_credentials() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp) / "model_diagnostics"
        image = base64.b64encode(b"small-image").decode("ascii")
        call = {"task_id": "task-1", "call_id": "1", "stage": "answer_generation", "active_item": "q1"}
        request = {
            "api_key": "ark-12345678-1234-1234-1234-123456789abc-secret",
            "messages": [{"role": "user", "content": "请回答这道题"}],
            "image_url": f"data:image/png;base64,{image}",
        }
        response = {"choices": [{"message": {"content": "模型原始答案"}}]}
        with patch.object(model_diagnostics, "MODEL_DIAGNOSTICS_DIR", root):
            target = model_diagnostics.record_model_diagnostic(call, request, response_payload=response)
            assert target is not None
            traces = model_diagnostics.relevant_model_diagnostics("task-1", "q1")
            attachments = model_diagnostics.diagnostic_attachments("task-1", traces)
        text = json.dumps(traces, ensure_ascii=False)
        assert "请回答这道题" in text
        assert "模型原始答案" in text
        assert "ark-12345678-1234-1234-1234-123456789abc-secret" not in text
        assert '"api_key": "***"' in text
        assert len(attachments) == 1
        assert attachments[0].read_bytes() == b"small-image"


def test_successful_model_traces_are_pinned_when_outer_task_fails() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp) / "model_diagnostics"
        with patch.object(model_diagnostics, "MODEL_DIAGNOSTICS_DIR", root):
            for call_id in ("1", "2"):
                model_diagnostics.record_model_diagnostic(
                    {"task_id": "generation_failed", "call_id": call_id},
                    {"messages": [{"role": "user", "content": f"prompt-{call_id}"}]},
                    response_payload={"content": f"response-{call_id}"},
                )
            assert model_diagnostics.pin_model_diagnostics_for_failure("generation_failed") == 2
            paths = list((root / "generation_failed" / "traces").glob("*.json.gz"))
            traces = model_diagnostics.relevant_model_diagnostics("generation_failed")

        assert all("-failed-outer-" in path.name for path in paths)
        assert [trace["outcome"] for trace in traces] == ["ok", "ok"]
        assert "response-2" in json.dumps(traces, ensure_ascii=False)


def test_long_task_lifecycle_is_not_time_windowed_and_bundle_is_bounded() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        task_root = root / "tasks" / "task-long"
        task_root.mkdir(parents=True)
        event_path = task_root / "events.jsonl"
        with event_path.open("w", encoding="utf-8") as handle:
            for index in range(5000):
                handle.write(json.dumps({
                    "time": f"2026-08-{1 + index // 300:02d} 10:00:00",
                    "event": "stage_changed",
                    "payload": {"current_stage": f"stage_{index}", "status": "running"},
                }) + "\n")
        pending = root / "support_reports" / "pending"
        with (
            patch.object(support_reporting, "SUPPORT_ROOT", pending.parent),
            patch.object(support_reporting, "PENDING_DIR", pending),
            patch.object(support_reporting, "RECEIPTS_PATH", pending.parent / "receipts.jsonl"),
            patch.object(support_reporting, "task_dir", lambda _task_id: task_root),
            patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
            patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
            patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
            patch("app.support_reporting.relevant_model_diagnostics", return_value=[]),
            patch("app.support_reporting.diagnostic_attachments", return_value=[]),
        ):
            path, _manifest = support_reporting._build_report({"scope": "task", "task_id": "task-long", "events": []})
        assert path.stat().st_size < support_reporting.MAX_COMPRESSED_BYTES
        with zipfile.ZipFile(path) as zf:
            lifecycle = json.loads(zf.read("task_lifecycle.json"))
        assert lifecycle["truncated"] is True
        assert lifecycle["first_items"][0]["payload"]["current_stage"] == "stage_0"
        assert lifecycle["last_items"][-1]["payload"]["current_stage"] == "stage_4999"


def test_offline_queue_replaces_same_fingerprint_instead_of_adding_files() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        pending = root / "pending"
        patches = (
            patch.object(support_reporting, "SUPPORT_ROOT", root),
            patch.object(support_reporting, "PENDING_DIR", pending),
            patch.object(support_reporting, "RECEIPTS_PATH", root / "receipts.jsonl"),
            patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
            patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
            patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
            patch("app.support_reporting.relevant_model_diagnostics", return_value=[]),
            patch("app.support_reporting.diagnostic_attachments", return_value=[]),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            support_reporting._build_report({"scope": "page", "page": "home", "events": []})
            support_reporting._build_report({"scope": "page", "page": "home", "events": []})
            files = list(pending.glob("*.zip"))
        assert len(files) == 1


def test_offline_queue_preserves_richer_duplicate_bundle() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        pending = root / "pending"
        with (
            patch.object(support_reporting, "SUPPORT_ROOT", root),
            patch.object(support_reporting, "PENDING_DIR", pending),
            patch.object(support_reporting, "RECEIPTS_PATH", root / "receipts.jsonl"),
            patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
            patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
            patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
            patch("app.support_reporting.diagnostic_attachments", return_value=[]),
        ):
            trace = {"outcome": "ok", "call": {"task_id": ""}, "response": {"content": "保留这份证据"}}
            with patch("app.support_reporting.relevant_model_diagnostics", return_value=[trace]):
                first_path, first_manifest = support_reporting._build_report({"scope": "page", "page": "home", "task_id": "task-rich", "events": []})
            with patch("app.support_reporting.relevant_model_diagnostics", return_value=[]):
                second_path, second_manifest = support_reporting._build_report({"scope": "page", "page": "home", "task_id": "task-rich", "events": []})

        assert second_path == first_path
        assert second_manifest["report_id"] == first_manifest["report_id"]
        assert len(list(pending.glob("*.zip"))) == 1
        with zipfile.ZipFile(second_path) as zf:
            assert "保留这份证据" in zf.read("model_diagnostics.json").decode("utf-8")


def test_failed_generation_report_contains_request_model_result_and_batch_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        jobs = root / "practice_jobs"
        with patch.object(practice_jobs, "PRACTICE_JOB_DIR", jobs):
            first = practice_jobs.create_practice_job("analyze", {
                "source_mode": "exam",
                "practice_batch_id": "batch-feedback",
                "question_text": "原题：分析恒温条件下的平衡组成。",
                "provider": "lingsuan_openai",
                "model": "gpt-5.6-terra",
            })
            practice_jobs.update_practice_job(
                first["job_id"],
                status="failed",
                current_stage="failed",
                error="模型没有返回完整范围",
                failure_context={
                    "failure_type": "blueprint_audit",
                    "blueprint_audit": {"findings": [{"code": "scope_missing", "item_number": "2"}]},
                },
                diagnostic_context={"exception_type": "ValueError", "traceback": "local gate traceback"},
                result={"partial_scope": ["化学平衡"]},
                started_at="2026-08-22T10:01:00+08:00",
                completed_at="2026-08-22T10:03:00+08:00",
            )
            pending = root / "support_reports" / "pending"
            with (
                patch.object(support_reporting, "SUPPORT_ROOT", pending.parent),
                patch.object(support_reporting, "PENDING_DIR", pending),
                patch.object(support_reporting, "RECEIPTS_PATH", pending.parent / "receipts.jsonl"),
                patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
                patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
                patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
                patch("app.support_reporting.relevant_model_diagnostics", return_value=[]),
                patch("app.support_reporting.diagnostic_attachments", return_value=[]),
            ):
                path, manifest = support_reporting._build_report({
                    "scope": "task",
                    "page": "tasks",
                    "task_id": first["job_id"],
                    "task_kind": "practice",
                    "report_group_id": "group-1",
                    "events": [],
                })
        assert manifest["context"]["report_group_id"] == "group-1"
        with zipfile.ZipFile(path) as zf:
            content = json.loads(zf.read("related_content.json"))
            failure_context = json.loads(zf.read("failure_context.json"))
            failure_diagnostic = json.loads(zf.read("task_failure_diagnostic.json"))
            coverage = json.loads(zf.read("diagnostic_coverage.json"))
            lifecycle = json.loads(zf.read("task_lifecycle.json"))
        content_text = json.dumps(content, ensure_ascii=False)
        lifecycle_text = json.dumps(lifecycle, ensure_ascii=False)
        assert "分析恒温条件下的平衡组成" in content_text
        assert "gpt-5.6-terra" in content_text
        assert "模型没有返回完整范围" in content_text
        assert "partial_scope" in content_text
        assert failure_context["blueprint_audit"]["findings"][0]["item_number"] == "2"
        assert failure_diagnostic["exception_type"] == "ValueError"
        assert coverage["available"]["failure_context"] is True
        assert coverage["available"]["failure_diagnostic"] is True
        assert coverage["primary_content_kind"] == "practice_job"
        assert "practice_job_created" in lifecycle_text
        assert "practice_job_finished" in lifecycle_text
        assert "模型没有返回完整范围" in lifecycle_text


def test_support_report_keeps_user_note_for_successful_task_quality_feedback() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        pending = root / "support_reports" / "pending"
        with (
            patch.object(support_reporting, "SUPPORT_ROOT", pending.parent),
            patch.object(support_reporting, "PENDING_DIR", pending),
            patch.object(support_reporting, "RECEIPTS_PATH", pending.parent / "receipts.jsonl"),
            patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
            patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
            patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
            patch("app.support_reporting.relevant_model_diagnostics", return_value=[]),
            patch("app.support_reporting.diagnostic_attachments", return_value=[]),
        ):
            path, manifest = support_reporting._build_report({
                "scope": "task",
                "page": "tasks",
                "task_status": "completed",
                "feedback_kind": "completed_task_quality",
                "feedback_note": "Word 中第 3 题图片缺失，题目难度也偏低。",
                "events": [],
            })

        with zipfile.ZipFile(path) as zf:
            user_feedback = json.loads(zf.read("user_feedback.json"))
            coverage = json.loads(zf.read("diagnostic_coverage.json"))
        assert user_feedback["kind"] == "completed_task_quality"
        assert "第 3 题图片缺失" in user_feedback["note"]
        assert coverage["available"]["user_feedback"] is True
        assert manifest["context"]["feedback_note"] == user_feedback["note"]


def test_practice_feedback_includes_related_word_export_diagnostics() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        export_root = root / "practice_exports"
        job_root = export_root / "jobs"
        job_root.mkdir(parents=True)
        job_record = {
            "job_id": "practice_word_12345678",
            "status": "failed",
            "current_operation": "Word 生成失败",
            "error": "第 3 题图片未嵌入",
            "diagnostic_context": {"exception_type": "ValueError", "traceback": "export traceback"},
            "payload": {"history_id": "practice_feedback_12345678", "exercises": [{"stem": "不应重复打包整题"}]},
        }
        (job_root / "practice_word_12345678.json").write_text(
            json.dumps(job_record, ensure_ascii=False), encoding="utf-8"
        )
        practice_record = {
            "history_id": "practice_feedback_12345678",
            "data": {"quality": {"status": "passed"}, "generation": {"status": "completed"}, "exercises": []},
        }
        with (
            patch.object(practice_export_jobs, "EXPORT_CACHE_DIR", export_root),
            patch("app.practice_store.load_practice_record", return_value=practice_record),
        ):
            content = support_reporting._practice_content("practice_feedback_12345678", None)

        assert content["word_export_jobs"][0]["error"] == "第 3 题图片未嵌入"
        assert content["word_export_jobs"][0]["diagnostic_context"]["exception_type"] == "ValueError"
        assert "payload" not in content["word_export_jobs"][0]
        assert "不应重复打包整题" not in json.dumps(content, ensure_ascii=False)


def test_generation_task_remains_primary_when_page_also_has_history_context() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        jobs = root / "practice_jobs"
        with patch.object(practice_jobs, "PRACTICE_JOB_DIR", jobs):
            task = practice_jobs.create_practice_job("plan", {
                "source_mode": "knowledge",
                "question_text": "当前失败任务的材料",
            })
            practice_jobs.update_practice_job(
                task["job_id"], status="failed", current_stage="failed", error="当前蓝图门禁失败"
            )
            pending = root / "support_reports" / "pending"
            with (
                patch.object(support_reporting, "SUPPORT_ROOT", pending.parent),
                patch.object(support_reporting, "PENDING_DIR", pending),
                patch.object(support_reporting, "RECEIPTS_PATH", pending.parent / "receipts.jsonl"),
                patch.object(support_reporting, "RUNTIME_LOG", root / "runtime.jsonl"),
                patch.object(support_reporting, "ERROR_TRACE_LOG", root / "errors.jsonl"),
                patch.object(support_reporting, "MODEL_CALL_LEDGER", root / "models.jsonl"),
                patch("app.support_reporting._practice_content", return_value={
                    "history_id": "old-history", "generation": {"model": "old-model"}
                }),
                patch("app.support_reporting.relevant_model_diagnostics", return_value=[]),
                patch("app.support_reporting.diagnostic_attachments", return_value=[]),
            ):
                path, manifest = support_reporting._build_report({
                    "scope": "task",
                    "page": "tasks",
                    "task_id": task["job_id"],
                    "history_id": "old-history",
                    "events": [],
                })

        with zipfile.ZipFile(path) as zf:
            primary = json.loads(zf.read("related_content.json"))
            history = json.loads(zf.read("related_history_content.json"))
            coverage = json.loads(zf.read("diagnostic_coverage.json"))
        assert primary["job_id"] == task["job_id"]
        assert "当前失败任务的材料" in json.dumps(primary, ensure_ascii=False)
        assert history["generation"]["model"] == "old-model"
        assert coverage["related_history_included"] is True
        assert manifest["diagnostic_coverage"]["primary_content_kind"] == "practice_job"


def test_question_scoped_exam_feedback_keeps_task_level_pipeline_error() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        task_root = Path(raw_tmp) / "task-exam"
        stage_root = task_root / "stage_outputs"
        stage_root.mkdir(parents=True)
        (stage_root / "pipeline_error.json").write_text(
            json.dumps({"error": "最终验收失败", "traceback": "local traceback"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (stage_root / "docx_audit.json").write_text(
            json.dumps({"ok": False, "issues": ["第 2 页公式溢出"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        with patch.object(support_reporting, "task_dir", lambda _task_id: task_root):
            content = support_reporting._exam_content("task-exam", "question-7")

        assert content["pipeline_error.json"]["error"] == "最终验收失败"
        assert content["pipeline_error.json"]["traceback"] == "local traceback"
        assert content["docx_audit.json"]["issues"] == ["第 2 页公式溢出"]


def test_word_format_task_feedback_includes_audits_and_failure_diagnostic() -> None:
    payload = {
        "status": "completed_with_issues",
        "mode": "auto",
        "task": {"task_kind": "format", "description": "讲义.docx"},
        "report": {"summary": {"issue_count": 3}},
        "final_report": {"summary": {"issue_count": 1}},
    }
    record = {
        "error": "仍有一项格式问题",
        "diagnostic_context": {"exception_type": "LayoutError", "traceback": "format traceback"},
    }
    with (
        patch("app.word_format_tasks.word_format_task_payload", return_value=payload),
        patch("app.word_format_tasks._load_record", return_value=record),
    ):
        content = support_reporting._format_task_content("word_format_20260822_010101_abcdef12")

    assert content["status"] == "completed_with_issues"
    assert content["initial_audit"]["summary"]["issue_count"] == 3
    assert content["final_audit"]["summary"]["issue_count"] == 1
    assert content["diagnostic_context"]["exception_type"] == "LayoutError"


def test_support_logs_never_fall_back_to_unrelated_task_errors() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        runtime = root / "runtime.jsonl"
        backend = root / "backend.jsonl"
        unrelated = {"level": "error", "task_id": "other-task", "message": "无关任务故障"}
        runtime.write_text(json.dumps(unrelated, ensure_ascii=False) + "\n", encoding="utf-8")
        backend.write_text(json.dumps(unrelated, ensure_ascii=False) + "\n", encoding="utf-8")
        with (
            patch.object(support_reporting, "RUNTIME_LOG", runtime),
            patch.object(support_reporting, "ERROR_TRACE_LOG", backend),
        ):
            assert support_reporting._related_runtime("current-task", set(), set()) == []
            assert support_reporting._related_error_traces("current-task", set(), set()) == []


def test_support_fingerprint_keeps_different_failed_tasks_separate() -> None:
    first = support_reporting._fingerprint(
        {"scope": "task", "page": "tasks", "task_id": "generation_one"}, [], [], []
    )
    repeated = support_reporting._fingerprint(
        {"scope": "task", "page": "tasks", "task_id": "generation_one"}, [], [], []
    )
    second = support_reporting._fingerprint(
        {"scope": "task", "page": "tasks", "task_id": "generation_two"}, [], [], []
    )
    assert first == repeated
    assert first != second
    first_run = support_reporting._fingerprint(
        {"scope": "task", "page": "tasks", "task_id": "same", "task_run_started_at": "2026-08-22T01:00:00+08:00"}, [], [], []
    )
    second_run = support_reporting._fingerprint(
        {"scope": "task", "page": "tasks", "task_id": "same", "task_run_started_at": "2026-08-22T02:00:00+08:00"}, [], [], []
    )
    assert first_run != second_run


def test_manual_followup_and_automatic_failure_share_one_issue_fingerprint() -> None:
    base = {
        "scope": "task",
        "page": "tasks",
        "task_id": "generation_same",
        "task_run_started_at": "2026-08-22T13:00:00+08:00",
    }
    lifecycle = [{"event": "task_updated", "payload": {"status": "failed", "error": "timeout"}}]

    automatic = support_reporting._fingerprint(
        {**base, "submission_mode": "automatic_failure"}, [], lifecycle, []
    )
    manual = support_reporting._fingerprint(
        {**base, "feedback_kind": "failed", "feedback_note": "用户补充说明"}, [], lifecycle, []
    )

    assert automatic == manual


def test_automatic_failure_report_is_nonblocking_deduplicated_and_bounded() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        state_path = root / "automatic_failures.json"
        receipts = root / "receipts.jsonl"
        submitted: list[dict] = []
        completed = threading.Event()

        def fake_submit(context: dict) -> dict:
            submitted.append(context)
            completed.set()
            return {"ok": True, "status": "submitted", "report_id": "AB-AUTO-1"}

        context = {
            "task_id": "generation_auto",
            "task_kind": "practice",
            "task_run_started_at": "2026-08-22T13:00:00+08:00",
            "task_stage": "failed",
            "error": "model timeout",
        }
        support_reporting._AUTO_FAILURE_ACTIVE.clear()
        with (
            patch.object(support_reporting, "SUPPORT_ROOT", root),
            patch.object(support_reporting, "AUTO_FAILURE_STATE_PATH", state_path),
            patch.object(support_reporting, "RECEIPTS_PATH", receipts),
            patch.object(support_reporting, "_config", return_value={"receiver_url": "http://127.0.0.1", "receiver_token": "test"}),
            patch.object(support_reporting, "submit_support_report", side_effect=fake_submit),
        ):
            first = support_reporting.queue_automatic_failure_report(context)
            second = support_reporting.queue_automatic_failure_report(context)
            assert first["scheduled"] is True
            assert second["scheduled"] is False
            assert completed.wait(2)
            deadline = time.time() + 2
            while time.time() < deadline and not state_path.exists():
                time.sleep(0.01)
            third = support_reporting.queue_automatic_failure_report(context)

        assert third["scheduled"] is False
        assert len(submitted) == 1
        assert submitted[0]["submission_mode"] == "automatic_failure"
        assert submitted[0]["events"] == []
        assert len(json.loads(state_path.read_text(encoding="utf-8"))) == 1


def test_local_support_receipts_are_compacted() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        receipts = root / "receipts.jsonl"
        with (
            patch.object(support_reporting, "SUPPORT_ROOT", root),
            patch.object(support_reporting, "RECEIPTS_PATH", receipts),
            patch.object(support_reporting, "RECEIPT_LINE_LIMIT", 5),
        ):
            for index in range(8):
                support_reporting._append_receipt({"report_id": f"AB-{index}"})
        lines = receipts.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5
        assert json.loads(lines[0])["report_id"] == "AB-3"


def test_automatic_failure_markers_expire_and_keep_only_recent_entries() -> None:
    now = time.time()
    state = {
        "expired": {"created_epoch": now - 3 * 86400},
        "older": {"created_epoch": now - 30},
        "newer": {"created_epoch": now - 10},
        "newest": {"created_epoch": now},
    }
    with (
        patch.object(support_reporting, "AUTO_FAILURE_RETENTION_DAYS", 1),
        patch.object(support_reporting, "AUTO_FAILURE_STATE_LIMIT", 2),
    ):
        trimmed = support_reporting._trim_automatic_failure_state(state)

    assert list(trimmed) == ["newest", "newer"]


def test_receiver_groups_duplicate_issue_and_keeps_one_latest_bundle() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root, quota_bytes=64 * 1024 * 1024)
        first = root / "tmp" / "first.part"
        second = root / "tmp" / "second.part"
        first_manifest = _write_bundle(first, "AB-1", "fp-1", payload="first")
        second_manifest = _write_bundle(second, "AB-2", "fp-1", payload="second")
        first_sha = support_receiver.hashlib.sha256(first.read_bytes()).hexdigest()
        second_sha = support_receiver.hashlib.sha256(second.read_bytes()).hexdigest()
        first_result = inbox.store(first, first_manifest, first_sha, "device-a")
        second_result = inbox.store(second, second_manifest, second_sha, "device-a")
        row = inbox.issue("fp-1")
        assert row is not None
        assert first_result["duplicate"] is False
        assert second_result["duplicate"] is True
        assert row["occurrence_count"] == 2
        assert len(list(inbox.inbox.glob("*.zip"))) == 1
        with zipfile.ZipFile(Path(row["bundle_path"])) as zf:
            assert "second" in zf.read("related_content.json").decode("utf-8")


def test_receiver_preserves_richer_duplicate_bundle() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root, quota_bytes=64 * 1024 * 1024)
        rich = root / "tmp" / "rich.part"
        sparse = root / "tmp" / "sparse.part"
        rich_coverage = {
            "available": {"failure_context": True, "model_diagnostics": True},
            "counts": {"model_traces": 4, "lifecycle_events": 6},
            "missing_expected_evidence": [],
        }
        sparse_coverage = {
            "available": {"failure_context": False, "model_diagnostics": False},
            "counts": {"model_traces": 0, "lifecycle_events": 1},
            "missing_expected_evidence": ["model_diagnostics"],
        }
        rich_manifest = _write_bundle(rich, "AB-RICH", "fp-rich", payload="完整门禁证据", diagnostic_coverage=rich_coverage)
        sparse_manifest = _write_bundle(sparse, "AB-SPARSE", "fp-rich", payload="证据缺失", diagnostic_coverage=sparse_coverage)
        inbox.store(rich, rich_manifest, support_receiver.hashlib.sha256(rich.read_bytes()).hexdigest(), "device-a")
        result = inbox.store(sparse, sparse_manifest, support_receiver.hashlib.sha256(sparse.read_bytes()).hexdigest(), "device-a")
        row = inbox.issue("fp-rich")

        assert result["preserved_richer_bundle"] is True
        assert result["bundle_replaced"] is False
        assert row is not None
        assert row["occurrence_count"] == 2
        with zipfile.ZipFile(Path(row["bundle_path"])) as zf:
            assert "完整门禁证据" in zf.read("related_content.json").decode("utf-8")


def test_receiver_issue_list_identifies_exact_task_model_stage_and_feedback_batch() -> None:
    manifest = {
        "schema_version": 1,
        "report_id": "AB-TASK-1",
        "fingerprint": "fp-task-1",
        "scope": "task",
        "application": {"version": "0.9.4"},
        "context": {
            "page": "tasks",
            "task_id": "generation_20260822003146_ce4b0ba5",
            "task_kind": "practice",
            "task_title": "材料热处理与组织转变",
            "task_model_label": "GPT-5.6 Terra",
            "task_stage": "failed",
            "operation": "generate_from_plan",
            "practice_batch_id": "practice-preview-1",
            "report_group_id": "feedback-group-123456",
        },
    }
    assert support_receiver.issue_summary(manifest) == "按题出题 · GPT-5.6 Terra · 材料热处理与组织转变"
    metadata = support_receiver.issue_task_metadata(manifest)
    assert "generation_20260822003146_ce4b0ba5" in metadata
    assert "阶段：题目生成（失败）" in metadata
    assert "任务批次：practice-preview-1" in metadata
    assert "反馈批次：feedback-gro" in metadata

    historical_row = {
        "summary": "task · tasks",
        "manifest_json": json.dumps(manifest, ensure_ascii=False),
    }
    assert support_receiver.issue_display_summary(historical_row) == (
        "按题出题 · GPT-5.6 Terra · 材料热处理与组织转变"
    )


def test_receiver_formats_iso_time_as_readable_local_time() -> None:
    china = support_receiver.timezone(support_receiver.timedelta(hours=8))
    assert support_receiver.display_local_time("2026-08-21T16:56:27+00:00", china) == "2026年8月22日 00:56"
    assert support_receiver.display_local_time("not-a-date", china) == "not-a-date"


def test_codex_triage_extracts_report_by_public_id_without_browser() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root, quota_bytes=64 * 1024 * 1024)
        bundle = root / "tmp" / "codex.part"
        manifest = _write_bundle(bundle, "AB-CODEX-1", "fp-codex", payload="需要核对的模型答案")
        digest = support_receiver.hashlib.sha256(bundle.read_bytes()).hexdigest()
        inbox.store(bundle, manifest, digest, "device-a")

        result = inspect_support_report.inspect_report("AB-CODEX-1", root=root, destination=root / "triage")
        assert result["report"]["report_id"] == "AB-CODEX-1"
        assert result["report"]["bundle_available"] is True
        extracted = [Path(path) for path in result["diagnostic_files"]]
        related = next(path for path in extracted if path.name == "related_content.json")
        assert "需要核对的模型答案" in related.read_text(encoding="utf-8")
        assert "浏览器" not in result["instruction"]


def test_codex_triage_fetches_cloud_report_into_local_diagnostic_files() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        destination = Path(raw_tmp) / "triage"
        remote_case = "/tmp/answer-book-support-triage/fp-cloud"
        remote_result = {
            "ok": True,
            "report": {"report_id": "AB-CLOUD-1234", "fingerprint": "fp-cloud"},
            "case_directory": remote_case,
            "diagnostic_files": [f"{remote_case}/manifest.json"],
        }

        def fake_run(arguments, **_kwargs):
            if arguments[0] == "ssh":
                return inspect_support_report.subprocess.CompletedProcess(
                    arguments, 0, stdout=json.dumps(remote_result), stderr=""
                )
            assert arguments[0] == "scp"
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
            return inspect_support_report.subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        config = {
            "ssh_host": "root@support.example",
            "ssh_key": "/tmp/test-key",
            "remote_root": "/var/lib/answer-book-support",
            "remote_script": "/opt/answer-book-support/inspect_support_report.py",
        }
        with patch.object(inspect_support_report, "_cloud_config", return_value=config), patch.object(
            inspect_support_report.subprocess, "run", side_effect=fake_run
        ):
            result = inspect_support_report.inspect_cloud_report("AB-CLOUD-1234", destination=destination)

        assert result["source"] == "cloud"
        assert result["case_directory"] == str(destination.resolve())
        assert result["diagnostic_files"] == [str(destination.resolve() / "manifest.json")]


def test_receiver_exposes_copyable_codex_triage_command() -> None:
    prompt = support_receiver.codex_triage_prompt("AB-20260822-AFEB35CF")
    assert "python3 scripts/inspect_support_report.py AB-20260822-AFEB35CF" in prompt
    assert "不要使用浏览器页面" in prompt


def test_receiver_cleanup_removes_resolved_raw_bundle_but_keeps_summary() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root, quota_bytes=64 * 1024 * 1024)
        source = root / "tmp" / "source.part"
        manifest = _write_bundle(source, "AB-1", "fp-clean")
        digest = support_receiver.hashlib.sha256(source.read_bytes()).hexdigest()
        inbox.store(source, manifest, digest, "device-a")
        old = "2026-01-01T00:00:00+00:00"
        with inbox.connect() as connection:
            connection.execute("UPDATE issues SET status='resolved', resolved_at=? WHERE fingerprint='fp-clean'", (old,))
        inbox.cleanup()
        row = inbox.issue("fp-clean")
        assert row is not None
        assert row["bundle_path"] == ""
        assert row["occurrence_count"] == 1


def test_receiver_rejects_zip_path_traversal() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        path = Path(raw_tmp) / "bad.zip"
        manifest = {"schema_version": 1, "report_id": "AB-1", "fingerprint": "fp-1"}
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("../escape.txt", "bad")
        try:
            support_receiver.validate_bundle(path, "AB-1", "fp-1")
        except ValueError as exc:
            assert str(exc) == "unsafe_zip_path"
        else:
            raise AssertionError("unsafe ZIP path was accepted")


def test_receiver_accepts_current_v2_support_bundle() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        path = Path(raw_tmp) / "v2.zip"
        _write_bundle(path, "AB-V2", "fp-v2", schema_version=2)

        manifest = support_receiver.validate_bundle(path, "AB-V2", "fp-v2")

        assert manifest["schema_version"] == 2


def test_terminal_task_workers_schedule_automatic_failure_reports() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    for name in ("task_runner.py", "practice_jobs.py", "practice_export_jobs.py", "word_format_tasks.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "queue_automatic_failure_report" in source


def test_client_stream_upload_reaches_receiver_and_returns_canonical_id() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        inbox = support_receiver.Inbox(root / "receiver", quota_bytes=64 * 1024 * 1024)
        token = "test-upload-token"
        server = support_receiver.ThreadingHTTPServer(("127.0.0.1", 0), support_receiver.UploadHandler)
        server.inbox = inbox
        server.token = token
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bundle = root / "report.zip"
        manifest = _write_bundle(bundle, "AB-STREAM-1", "fp-stream")
        try:
            with patch("app.support_reporting._config", return_value={
                "receiver_url": f"http://127.0.0.1:{server.server_port}",
                "receiver_token": token,
            }):
                result = support_reporting._upload(bundle, manifest)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        assert result["submitted"] is True
        assert result["report_id"] == "AB-STREAM-1"
        assert inbox.issue("fp-stream") is not None


def test_receiver_caps_threads_before_request_handlers_are_created() -> None:
    class CountingHandler(BaseHTTPRequestHandler):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            with self.lock:
                type(self).active += 1
                type(self).maximum = max(type(self).maximum, type(self).active)
            try:
                time.sleep(0.04)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            finally:
                with self.lock:
                    type(self).active -= 1

    server = support_receiver.BoundedThreadingHTTPServer(("127.0.0.1", 0), CountingHandler, max_workers=2)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()

    def request() -> None:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request("GET", "/")
            assert connection.getresponse().status == 200
        finally:
            connection.close()

    workers = [threading.Thread(target=request) for _ in range(10)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            assert not worker.is_alive()
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
    assert CountingHandler.maximum == 2


def test_frontend_support_is_contextual_and_api_requests_have_correlation_ids() -> None:
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    html_text = (root / "web" / "index.html").read_text(encoding="utf-8")
    api_js = (root / "web" / "platform-api.js").read_text(encoding="utf-8")
    assert "反馈此题" in app_js
    assert 'id="supportFeedbackBtn"' in html_text
    assert '"X-Request-ID": correlationId' in api_js
    assert "window.SupportTelemetry?.snapshot()" in app_js
    assert "反馈此任务" in app_js
    assert "反馈质量" in app_js
    assert "任务显示成功也可以反馈" in app_js
    assert 'feedback_kind: feedbackKind' in app_js
    assert 'feedback_note: feedbackNote' in app_js
    assert "submitFailedTaskFeedback" not in app_js
    assert 'id="taskFeedbackFailedBtn"' not in html_text
    assert "失败诊断已自动处理" in html_text
    assert "断网时仅在本机限量保存并重试" in html_text
    assert "个失败任务的诊断已自动处理" in app_js
    assert "report_group_id" in app_js
    assert "failedTaskFeedbackReported" in app_js
    assert "rememberFailedTaskFeedback" in app_js
    assert "dismissFailedTaskFeedback" in app_js
    assert 'reported ? "再次反馈"' in app_js
    assert "之前反馈不影响再次提交" in app_js
    assert "support-task-reported" not in app_js
    assert "lockReportedFailure" not in app_js
    assert 'id="taskDismissFailedFeedbackBtn"' in html_text
    assert '<i class="fas fa-paper-plane"></i><span>一键反馈' not in app_js


def test_upload_implementation_streams_instead_of_reading_whole_bundle() -> None:
    source = Path(support_reporting.__file__).read_text(encoding="utf-8")
    start = source.index("def _upload(")
    end = source.index("\ndef _append_receipt", start)
    upload_source = source[start:end]
    assert "read_bytes()" not in upload_source
    assert "64 * 1024" in upload_source
