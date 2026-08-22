from __future__ import annotations

import json
import traceback
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .environment import check_environment
from .capabilities.academic_expressions import audit_academic_expressions
from .evidence_selection import load_confirmed_candidates
from .exam_audit import audit_exam_structure
from .exam_extract import extract_exam_structure
from .exam_structure_review import auto_confirm_exam_structure
from .pipeline import build_and_audit_docx_with_repair, output_dir, stage_dir, write_json
from .pipeline_delivery import complete_pipeline_delivery
from .pipeline_telemetry import PipelineRunTelemetry
from .settings import get_provider
from .task_store import load_task, update_task, update_task_hybrid
from .textbook_index_cache import install_textbook_index_cache


def prepare_hybrid_input(task_id: str) -> dict:
    """Run every Word-sensitive extraction step locally before cloud upload."""

    record = load_task(task_id)
    sdir = stage_dir(task_id)
    sdir.mkdir(parents=True, exist_ok=True)
    update_task(task_id, status="running", current_stage="hybrid_preprocess", error="")
    update_task_hybrid(task_id, execution_mode="hybrid", hybrid_phase="local_preprocess", cloud_error="")
    try:
        environment = check_environment()
        write_json(sdir / "hybrid_local_environment.json", environment)
        formula = environment.get("formula_conversion", {})
        if not formula.get("preferred_chain_ready"):
            raise RuntimeError("本机公式转换链未就绪，不能安全地把题面交给云端。")
        exam_path = Path(record.exam_path).expanduser()
        if not exam_path.is_file():
            raise FileNotFoundError(f"Exam file not found: {exam_path}")
        structured_exam = extract_exam_structure(exam_path, sdir / "structured_exam.json")
        issues = audit_exam_structure(structured_exam, sdir / "exam_structure_audit.json")
        if issues:
            raise RuntimeError("Exam structure audit failed during hybrid preprocessing")
        structured_exam = auto_confirm_exam_structure(task_id, structured_exam, sdir / "structured_exam.json")
        if not record.selected_textbooks:
            raise RuntimeError("当前任务没有绑定已索引教材。")
        index = install_textbook_index_cache(
            record.selected_textbooks,
            sdir,
            record.textbook_display_names or {},
        )
        if not index.get("page_map_ok", True):
            raise RuntimeError("教材页码索引未通过本机检查。")
        report = {
            "schema_version": "answer_book.hybrid_preprocess.v1",
            "task_id": task_id,
            "status": "passed",
            "question_count": len(structured_exam.get("items", [])),
            "textbook_count": index.get("textbook_count", 0),
            "formula_chain_ready": True,
        }
        write_json(sdir / "hybrid_preprocess.json", report)
        return report
    except Exception as exc:
        write_json(
            sdir / "hybrid_preprocess_error.json",
            {
                "schema_version": "answer_book.hybrid_failure.v1",
                "phase": "local_preprocess",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        update_task_hybrid(task_id, hybrid_phase="local_preprocess_failed", cloud_error="")
        update_task(task_id, status="failed", current_stage="hybrid_preprocess", error=str(exc))
        raise


def complete_hybrid_local_delivery(task_id: str, *, render_with_word: bool, use_model: bool = True) -> dict:
    """Build, Word-render, and accept a cloud-computed task on the originating computer."""

    record = load_task(task_id)
    sdir = stage_dir(task_id)
    odir = output_dir(task_id)
    required = {
        "structured_exam": sdir / "structured_exam.json",
        "fragments": sdir / "answer_fragments.json",
        "candidates": sdir / "confirmed_evidence_candidates.csv",
        "selection": sdir / "evidence_selection.json",
        "content_quality": sdir / "content_quality_audit.json",
        "handoff": sdir / "hybrid_handoff.json",
        "cloud_status": sdir / "cloud_pipeline_status.json",
        "import_receipt": sdir / "hybrid_import_receipt.json",
    }
    missing = [path.name for path in required.values() if not path.is_file()]
    if missing:
        raise RuntimeError("云端结果诊断不完整，禁止生成 Word：" + ", ".join(missing))
    update_task(task_id, status="running", current_stage="local_delivery", error="")
    update_task_hybrid(task_id, hybrid_phase="local_delivery", cloud_status="completed", cloud_error="")
    telemetry = PipelineRunTelemetry(
        task_id=task_id,
        status_path=sdir / "pipeline_status.json",
        run_id=uuid4().hex,
        quality_governance={"mode": "hybrid_local_delivery", "human_review_required": False},
    )
    telemetry.start_heartbeat()
    mark = telemetry.mark
    try:
        local_environment = check_environment()
        write_json(sdir / "hybrid_local_environment.json", local_environment)
        write_json(sdir / "environment_check.json", local_environment)
        formula_ready = bool(local_environment.get("formula_conversion", {}).get("preferred_chain_ready"))
        mark(
            "hybrid_local_environment",
            "passed" if formula_ready else "failed",
            {
                "formula_chain_ready": formula_ready,
                "word_mac": local_environment.get("microsoft_word", {}).get("mac", {}),
                "word_windows": local_environment.get("microsoft_word", {}).get("windows", {}),
            },
        )
        if not formula_ready:
            raise RuntimeError("本机公式转换链未就绪，已禁止生成最终 Word。")
        structured_exam = json.loads(required["structured_exam"].read_text(encoding="utf-8"))
        fragments_data = json.loads(required["fragments"].read_text(encoding="utf-8"))
        selection_data = json.loads(required["selection"].read_text(encoding="utf-8"))
        content_quality = json.loads(required["content_quality"].read_text(encoding="utf-8"))
        candidates = load_confirmed_candidates(required["candidates"])
        thinking_mode = getattr(record, "model_thinking", "auto") or "auto"
        answer_provider_name = getattr(record, "answer_provider", "") or record.provider
        provider = replace(get_provider(answer_provider_name), thinking_mode=thinking_mode)
        model = str(
            getattr(record, "answer_model", "")
            or (record.model if answer_provider_name == record.provider else provider.default_model)
            or record.model
        ).strip()
        local_expression_audit = audit_academic_expressions(
            fragments_data,
            structured_exam=structured_exam,
            output_json=sdir / "academic_expression_audit.local_delivery.json",
            render_preflight=True,
        )
        mark(
            "academic_expressions_local_delivery",
            "passed" if local_expression_audit.get("ok") else "failed",
            {
                "expression_count": local_expression_audit.get("expression_count", 0),
                "issue_count": local_expression_audit.get("issue_count", 0),
                "render_preflight_failure_count": local_expression_audit.get("render_preflight_failure_count", 0),
            },
        )
        if not local_expression_audit.get("ok"):
            raise RuntimeError("本机 Word 公式对象预检失败，已禁止生成最终文档。")
        report = complete_pipeline_delivery(
            task_id=task_id,
            fragments_json=required["fragments"],
            stage_dir=sdir,
            output_dir=odir,
            structured_exam=structured_exam,
            candidates=candidates,
            selection_data=selection_data,
            provider=provider,
            model=model,
            use_model=use_model,
            render_with_word=render_with_word,
            content_quality=content_quality,
            mark=mark,
            write_json=write_json,
            build_docx_with_repair=build_and_audit_docx_with_repair,
        )
        update_task_hybrid(task_id, hybrid_phase="completed", cloud_status="completed", cloud_error="")
        return report
    except Exception as exc:
        write_json(
            sdir / "hybrid_local_delivery_error.json",
            {
                "schema_version": "answer_book.hybrid_failure.v1",
                "phase": "local_delivery",
                "cloud_job_id": record.cloud_job_id,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        update_task_hybrid(task_id, hybrid_phase="local_delivery_failed", cloud_status="completed")
        update_task(task_id, status="failed", current_stage="local_delivery", error=str(exc))
        mark("hybrid_local_delivery", "failed", {"error": str(exc)})
        raise
    finally:
        telemetry.stop()
