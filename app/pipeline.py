from __future__ import annotations

import copy
import json
import re
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .analysis_profiles import analysis_uses_textbook_evidence
from .answer_coverage_audit import audit_answer_coverage
from .answer_generation import (
    attach_program_evidence_block,
    bind_top_evidence,
    fallback_fragment,
    generate_answer_fragments,
    has_bound_evidence,
    reconcile_confirmed_evidence_binding,
    semantic_generation_issues,
    write_demo_fragments,
)
from .audit_model_repair import fill_missing_fragments_locally, repair_fragments_with_model_for_audit
from .audit_review_gate import enforce_unattended_audit_report
from .capabilities.academic_expressions import audit_academic_expressions
from .capabilities.quality_budget import QualityExecutionBudget
from .capabilities.quality_governance import ActionCeiling, governance_for
from .capabilities.selective_review import review_selective_quality
from .capabilities.shadow_quality import build_shadow_quality_report
from .content_quality_audit import audit_content_quality
from .content_quality_repair import repair_content_quality_locally
from .docx_audit import audit_docx_v4
from .docx_model_repair import repair_fragments_with_model_for_docx
from .docx_v4 import build_docx_from_fragments
from .environment import check_environment
from .evidence_audit import audit_retrieval_candidates
from .evidence_selection import confirm_evidence_selection, filter_candidates_by_selection, load_confirmed_candidates
from .exam_audit import audit_exam_structure
from .exam_extract import extract_exam_structure
from .exam_structure_review import auto_confirm_exam_structure
from .expression_promotion import promote_inline_mathematical_expressions, promote_inline_reactions
from .figure_schema_planning import attach_figure_schema_plans, plan_figure_schemas
from .figures import audit_figures_with_vision, prepare_figures_for_fragments, repair_figures_with_model_for_visual_qa
from .fragment_repair import repair_answer_fragments_for_docx
from .image_orchestration import LEGACY_FIGURE_PIPELINE, MAIN_MODEL_TOOL_LOOP, normalize_image_orchestration
from .knowledge_planning import generate_knowledge_plans, load_knowledge_plans
from .model_diagnostics import pin_model_diagnostics_for_failure
from .model_usage_report import build_model_usage_report
from .paths import OUTPUTS_DIR, ensure_project_dirs
from .pipeline_checkpoints import (
    answer_checkpoint_reusable as _answer_checkpoint_reusable,
)
from .pipeline_checkpoints import (
    early_upstream_checkpoint_reusable as _early_upstream_checkpoint_reusable,
)
from .pipeline_checkpoints import (
    figure_schema_checkpoint_reusable,
)
from .pipeline_checkpoints import (
    migrate_legacy_answer_source_contract as _migrate_legacy_answer_source_contract,
)
from .pipeline_checkpoints import (
    normalize_answer_checkpoint as _normalize_answer_checkpoint,
)
from .pipeline_checkpoints import (
    reconcile_answer_generation_checkpoint as _reconcile_answer_generation_checkpoint,
)
from .pipeline_checkpoints import (
    restore_failed_content_repair_checkpoint as _restore_failed_content_repair_checkpoint,
)
from .pipeline_checkpoints import (
    reusable_answer_fragment_map as _reusable_answer_fragment_map,
)
from .pipeline_checkpoints import (
    rollback_repaired_questions as _rollback_repaired_questions,
)
from .pipeline_checkpoints import (
    upstream_checkpoint_contract as _upstream_checkpoint_contract,
)
from .pipeline_checkpoints import (
    upstream_checkpoint_contract_fingerprint as _upstream_checkpoint_contract_fingerprint,
)
from .pipeline_checkpoints import (
    upstream_checkpoint_reusable as _upstream_checkpoint_reusable,
)
from .pipeline_checkpoints import (
    write_upstream_checkpoint_contract as _write_upstream_checkpoint_contract,
)
from .pipeline_delivery import complete_pipeline_delivery
from .pipeline_telemetry import PipelineRunTelemetry
from .question_requirements import answer_figure_required
from .question_types import question_has_type
from .question_understanding import QUESTION_UNDERSTANDING_POLICY_VERSION, build_question_understandings
from .resource_ids import bounded_resource_path
from .retrieval import build_candidates, candidates_for_question
from .review_notes import build_answer_review_notes
from .runtime_monitor import model_call_context
from .settings import get_provider, provider_model_supports_vision, provider_supports_image_generation
from .task_control import TaskCancelled, checkpoint
from .task_store import load_task, task_dir, update_task
from .textbook_index_cache import install_textbook_index_cache, textbook_index_key
from .v4_schema import validate_v4_answer_fragment

CONTENT_QUALITY_MODEL_REPAIR_CODES = {
    "missing_required_figure",
    "calculation_missing_mistake_notes",
    "formula_absence_after_retry",
    "calculation_missing_steps",
    "calculation_missing_subquestion_steps",
    "calculation_invalid_subquestion_number",
    "missing_answer_unit_content",
    "missing_answer_unit_steps",
    "calculation_answer_missing_unit",
    "calculation_missing_substitution",
    "missing_analysis",
    "calculation_internal_inconsistency",
    "answer_analysis_comparative_contradiction",
    "composition_partition_missing_declared_component",
    "spatial_relation_improper_membership_inference",
    "xrd_figure_text_label_mismatch",
    "xrd_unsupported_peak_spacing_trend",
}


def _isolated_image_routes(
    mode: str,
    *,
    image_provider: object,
    image_model: str,
    code_provider: object,
    code_model: str,
) -> dict[str, object]:
    """Split dependencies once so neither image path can leak into the other."""

    normalized = normalize_image_orchestration(mode)
    main_mode = normalized == MAIN_MODEL_TOOL_LOOP
    routes: dict[str, object] = {
        "mode": normalized,
        "answer_image_provider": image_provider if main_mode else None,
        "answer_image_model": image_model if main_mode else "",
        "legacy_image_provider": None if main_mode else image_provider,
        "legacy_image_model": "" if main_mode else image_model,
        "legacy_code_provider": None if main_mode else code_provider,
        "legacy_code_model": "" if main_mode else code_model,
    }
    assert not (
        routes["answer_image_provider"] is not None
        and (routes["legacy_image_provider"] is not None or routes["legacy_code_provider"] is not None)
    ), "image orchestration routes must be mutually exclusive"
    return routes


def _pin_text_provider_model(provider: object, model: str):
    """Keep every recovery path on the model explicitly selected for a task role."""

    selected = str(model or "").strip()
    if not selected:
        return provider
    return replace(
        provider,
        default_model=selected,
        model_options=(selected,),
    )


def _pin_vision_provider_model(provider: object, model: str):
    """Pin both generic and vision candidate lists to the configured vision model."""

    selected = str(model or "").strip()
    if not selected:
        return provider
    return replace(
        provider,
        default_model=selected,
        model_options=(selected,),
        vision_model=selected,
        vision_model_options=(selected,),
    )


def _normalize_generated_expression_segments(payload: dict) -> list[str]:
    """Apply deterministic academic-expression typing after every model write.

    Generation and audit repair both pass through their own converters, but a
    later merge or fragment migration can reintroduce plain symbolic text.  A
    common post-write boundary keeps the validator and renderer aligned without
    spending another model call on typography-only defects.
    """

    changed: list[str] = []
    fragments = payload.get("fragments") if isinstance(payload.get("fragments"), list) else []
    for index, fragment in enumerate(fragments):
        if not isinstance(fragment, dict):
            continue
        before = copy.deepcopy(fragment)
        normalized = promote_inline_reactions(fragment)
        normalized = promote_inline_mathematical_expressions(normalized)
        fragments[index] = normalized
        if normalized != before:
            changed.append(str(normalized.get("question_id") or "").strip())
    return [qid for qid in changed if qid]
DOCX_MODEL_REPAIR_CODES = {"formula_like_normal_text", "raw_latex_marker"}
FIGURE_SCHEMA_POLICY_VERSION = "answer_book.figure_routing.v6"


@dataclass
class PipelineOptions:
    use_model: bool = True
    allow_demo_without_key: bool = False
    render_with_word: bool = False
    preserve_document_diagnostics: bool = False
    reuse_fragments: bool = False
    require_preferred_formula_chain: bool = True
    preprocessed_input: bool = False
    defer_local_delivery: bool = False


def stage_dir(task_id: str) -> Path:
    return task_dir(task_id) / "stage_outputs"


def output_dir(task_id: str) -> Path:
    return bounded_resource_path(OUTPUTS_DIR, task_id)


TRANSIENT_PROGRESS_FILES = (
    "question_understanding_progress.json",
    "knowledge_planning_progress.json",
    "evidence_selection_progress.json",
    "answer_generation_progress.json",
    "figure_progress.json",
)


def reset_transient_progress(stage_output_dir: Path) -> None:
    for progress_name in TRANSIENT_PROGRESS_FILES:
        (stage_output_dir / progress_name).unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark_unresolved_correctness_review_flags(
    fragments_json: Path,
    advisories: list[dict],
) -> list[str]:
    """Make a rejected high-risk science repair visible to hard audits.

    A reviewer suggestion is not ground truth, so it must not block creation of
    a clearly labelled candidate artifact.  But when the reviewer explicitly
    requests a repair and no valid patch was applied, the answer also must not
    retain a clean content-audit status.
    """

    if not fragments_json.exists():
        return []
    unresolved_ids = {
        str(item.get("question_id") or "").strip()
        for item in advisories
        if isinstance(item, dict)
        and str(item.get("decision") or "").strip().lower() == "repair"
        and str(item.get("question_id") or "").strip()
    }
    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    flagged: list[str] = []
    for fragment in data.get("fragments", []) or []:
        if not isinstance(fragment, dict):
            continue
        qid = str(fragment.get("question_id") or "").strip()
        flags = [
            flag
            for flag in fragment.get("_review_flags", []) or []
            if not isinstance(flag, dict)
            or str(flag.get("code") or "") != "high_risk_correctness_unresolved"
        ]
        if qid in unresolved_ids:
            flags.append(
                {
                    "code": "high_risk_correctness_unresolved",
                    "message": "高风险学科正确性复核提出具体修复，但候选修复未通过确定性校验，本题仅可作为待复核候选结果。",
                }
            )
            flagged.append(qid)
        if flags:
            fragment["_review_flags"] = flags
        else:
            fragment.pop("_review_flags", None)
    write_json(fragments_json, data)
    return flagged


def attach_source_images_to_fragments(structured_exam: dict, fragments_json: Path) -> dict:
    """Carry original question images into the final Word input exactly once."""

    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    questions = {
        str(question.get("question_id") or "").strip(): question
        for question in structured_exam.get("items", []) or []
        if isinstance(question, dict) and str(question.get("question_id") or "").strip()
    }
    attached: list[str] = []
    missing: list[dict[str, str]] = []
    reconciled_warning_question_ids: list[str] = []
    changed = False
    for fragment in data.get("fragments", []) or []:
        if not isinstance(fragment, dict):
            continue
        qid = str(fragment.get("question_id") or "").strip()
        source_paths = [Path(str(raw)) for raw in questions.get(qid, {}).get("image_refs", []) or [] if str(raw).strip()]
        if not source_paths:
            continue
        available_source_paths = [source_path for source_path in source_paths if source_path.exists()]
        if available_source_paths:
            # A warning emitted before source-image recovery must not keep a
            # successfully recovered final document in review-candidate tier.
            # Reconcile only the factual "image was not extracted" warning;
            # unrelated model or content warnings retain their authority.
            warnings = [str(item) for item in fragment.get("warnings", []) or [] if str(item).strip()]
            reconciled_warnings = [
                warning
                for warning in warnings
                if "原题未抽取到图片" not in warning and "未抽取到原题图片" not in warning
            ]
            if reconciled_warnings != warnings:
                if reconciled_warnings:
                    fragment["warnings"] = reconciled_warnings
                else:
                    fragment.pop("warnings", None)
                reconciled_warning_question_ids.append(qid)
                changed = True
        existing_paths = {
            str(segment.get("path") or "")
            for block in fragment.get("blocks", []) or []
            if isinstance(block, dict)
            for segment in block.get("segments", []) or []
            if isinstance(segment, dict) and segment.get("type") == "image_ref"
        }
        segments: list[dict] = []
        for index, source_path in enumerate(source_paths, start=1):
            if not source_path.exists():
                missing.append({"question_id": qid, "path": str(source_path)})
                continue
            if str(source_path) in existing_paths:
                continue
            segments.append({
                "type": "image_ref",
                "image_id": f"{qid}_source_{index:02d}",
                "path": str(source_path),
                "role": "source_question_image",
            })
        if not segments:
            continue
        blocks = list(fragment.get("blocks", []) or [])
        insert_at = 1 if blocks and str(blocks[0].get("label") or "") == "教材依据" else 0
        blocks.insert(insert_at, {"label": "原题图", "segments": segments})
        fragment["blocks"] = blocks
        meta = dict(fragment.get("_meta") or {})
        meta["source_images_attached"] = len(segments)
        fragment["_meta"] = meta
        attached.append(qid)
        changed = True
    delivery = {
        "attached_question_ids": attached,
        "attached_question_count": len(attached),
        "missing": missing,
        "reconciled_warning_question_ids": reconciled_warning_question_ids,
        "reconciled_warning_count": len(reconciled_warning_question_ids),
    }
    if changed or missing:
        data["source_image_delivery"] = delivery
        write_json(fragments_json, data)
    return data.get("source_image_delivery") or delivery


def _figure_schema_checkpoint_reusable(report: dict, *, upstream_contract_fingerprint: str = "") -> bool:
    return figure_schema_checkpoint_reusable(
        report,
        policy_version=FIGURE_SCHEMA_POLICY_VERSION,
        upstream_contract_fingerprint=upstream_contract_fingerprint,
    )


def _plan_figure_schemas_with_checkpoint(
    structured_exam: dict,
    output_json: Path,
    *,
    provider,
    model: str,
    upstream_contract_fingerprint: str,
) -> dict:
    """Write a route-bound schema report before it becomes reusable on retry."""

    report = plan_figure_schemas(structured_exam, output_json, provider=provider, model=model)
    if upstream_contract_fingerprint:
        report = {**report, "upstream_checkpoint_contract_fingerprint": upstream_contract_fingerprint}
        write_json(output_json, report)
    return report


def _provider_key_issue(label: str, provider) -> str:
    env_name = str(getattr(provider, "api_key_env", "") or "").strip()
    hint = f"（环境变量 {env_name}）" if env_name else "（providers.local.json 或 .env）"
    return f"{label} {getattr(provider, 'name', 'unknown')} 未配置 API Key {hint}"


def _validate_required_provider_keys(
    *,
    use_model: bool,
    allow_demo_without_key: bool,
    provider,
    reasoning_provider,
    answer_provider,
    correctness_provider,
    vision_provider,
    image_provider,
    image_model: str,
    require_vision_provider: bool = True,
    require_reasoning_provider: bool = True,
) -> list[str]:
    if not use_model or allow_demo_without_key:
        return []
    checks = [
        ("基础/作图规则模型", provider),
        ("答案生成模型", answer_provider),
        ("高风险正确性复核模型", correctness_provider),
    ]
    if require_reasoning_provider:
        checks.insert(1, ("知识点与教材依据模型", reasoning_provider))
    if require_vision_provider:
        checks.append(("读图模型", vision_provider))
    if str(image_model or "").strip():
        checks.append(("作图生图模型", image_provider))
    issues: list[str] = []
    seen: set[tuple[str, str]] = set()
    for label, cfg in checks:
        name = str(getattr(cfg, "name", "") or "")
        key = (label, name)
        if key in seen:
            continue
        seen.add(key)
        if not str(getattr(cfg, "api_key", "") or "").strip():
            issues.append(_provider_key_issue(label, cfg))
    return issues


class FigureProgressTracker:
    """Persist figure sub-stage events and a heartbeat while remote calls are pending."""

    def __init__(self, output_path: Path, heartbeat_seconds: int = 15):
        self.output_path = output_path
        self.heartbeat_seconds = heartbeat_seconds
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._state: dict = {"stage": "figures", "status": "running", "recent_events": []}

    def emit(self, event: str, detail: dict | None = None) -> None:
        detail = dict(detail or {})
        now = time.strftime("%H:%M:%S")
        terminal_status = str(detail.get("status") or "").strip().lower()
        status = (
            terminal_status
            if event == "stage_completed" and terminal_status in {"passed", "failed", "advisory"}
            else "running"
        )
        with self._lock:
            record = {"time": now, "event": event, **detail}
            events = list(self._state.get("recent_events") or [])
            events.append(record)
            self._state.update(detail)
            self._state.update(
                {
                    "stage": "figures",
                    "status": status,
                    "active_event": event,
                    "updated_at": now,
                    "elapsed_seconds": max(0, int(time.monotonic() - self.started_at)),
                    "recent_events": events[-20:],
                }
            )
            write_json(self.output_path, self._state)

    @contextmanager
    def operation(self, name: str, **detail):
        self.emit("operation_started", {"operation": name, **detail})
        stop = threading.Event()

        def heartbeat() -> None:
            while not stop.wait(self.heartbeat_seconds):
                self.emit("heartbeat", {"operation": name, **detail})

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield
        except Exception as exc:
            self.emit("operation_failed", {"operation": name, **detail, "error": str(exc)[:300]})
            raise
        else:
            self.emit("operation_completed", {"operation": name, **detail})
        finally:
            stop.set()
            thread.join(timeout=0.2)


def attach_figure_generation_audit(content_quality: dict, sdir: Path) -> dict:
    audit_path = sdir / "figure_generation_audit.json"
    if audit_path.exists():
        try:
            content_quality["figure_generation_audit"] = json.loads(audit_path.read_text(encoding="utf-8"))
        except Exception as exc:
            content_quality["figure_generation_audit"] = {"error": str(exc)[:300]}
    visual_qa_path = sdir / "figure_visual_qa.json"
    if visual_qa_path.exists():
        try:
            content_quality["figure_visual_qa"] = json.loads(visual_qa_path.read_text(encoding="utf-8"))
        except Exception as exc:
            content_quality["figure_visual_qa"] = {"error": str(exc)[:300]}
    if audit_path.exists() or visual_qa_path.exists():
        write_json(sdir / "content_quality_audit.json", content_quality)
    return content_quality


def figure_visual_qa_issue_count(report: dict) -> int:
    if not isinstance(report, dict) or not report.get("enabled"):
        return 0
    count = len(report.get("failed", []) if isinstance(report.get("failed"), list) else [])
    item_figure_ids: set[str] = set()
    for item in report.get("items", []):
        if not isinstance(item, dict):
            continue
        figure_id = str(item.get("figure_id") or "").strip()
        if figure_id:
            item_figure_ids.add(figure_id)
        qa = item.get("qa") if isinstance(item.get("qa"), dict) else {}
        if qa.get("ok") is not True:
            count += 1
    for item in report.get("skipped", []):
        figure_id = str(item.get("figure_id") or "").strip() if isinstance(item, dict) else ""
        if isinstance(item, dict) and str(item.get("reason") or "") == "figure image missing" and figure_id not in item_figure_ids:
            count += 1
    return count


def required_visual_understanding_failures(report: dict) -> list[dict[str, str]]:
    """Return questions whose required visual evidence was not obtained."""

    failures: list[dict[str, str]] = []
    if not isinstance(report, dict):
        return failures
    for item in report.get("items", []) if isinstance(report.get("items"), list) else []:
        if (
            not isinstance(item, dict)
            or not item.get("needs_vision_model")
            or item.get("vision_used")
            or item.get("direct_multimodal")
        ):
            continue
        failures.append(
            {
                "question_id": str(item.get("question_id") or "").strip(),
                "reason": "; ".join(str(value) for value in item.get("uncertainties", []) if str(value).strip())
                or "required visual understanding is unavailable",
            }
        )
    return failures


def figure_visual_qa_blocking_findings(report: dict) -> list[dict[str, str]]:
    """Return only machine-verifiable figure delivery failures.

    A vision model's opinion about scientific labels, completeness, or visual
    quality remains useful repair evidence, but it is not ground truth and may
    not hard-block unattended delivery. Missing or unreadable image artifacts
    are deterministic platform failures and remain blocking.
    """

    if not isinstance(report, dict) or not report.get("enabled"):
        return []
    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(*, question_id: str, figure_id: str, reason: str) -> None:
        key = (question_id, figure_id, reason)
        if key in seen:
            return
        seen.add(key)
        findings.append(
            {
                "question_id": question_id,
                "figure_id": figure_id,
                "reason": reason,
            }
        )

    for item in report.get("items", []) if isinstance(report.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id") or "").strip()
        figure_id = str(item.get("figure_id") or "").strip()
        qa = item.get("qa") if isinstance(item.get("qa"), dict) else {}
        if str(qa.get("error") or "").strip():
            add(
                question_id=question_id,
                figure_id=figure_id,
                reason=f"figure visual QA unavailable: {str(qa.get('error'))[:180]}",
            )
            continue
        raw_path = str(item.get("path") or item.get("image_path") or "").strip()
        if not raw_path:
            # Some legacy QA reports omit the path even though the image was
            # successfully inspected. Their QA payload is still advisory.
            continue
        image_path = Path(raw_path)
        if not image_path.is_file():
            add(question_id=question_id, figure_id=figure_id, reason="figure image missing")
            continue
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                image.verify()
            if width <= 0 or height <= 0:
                add(question_id=question_id, figure_id=figure_id, reason="figure image has invalid dimensions")
        except Exception as exc:
            add(
                question_id=question_id,
                figure_id=figure_id,
                reason=f"figure image unreadable: {type(exc).__name__}",
            )

    for item in report.get("skipped", []) if isinstance(report.get("skipped"), list) else []:
        if not isinstance(item, dict) or str(item.get("reason") or "") != "figure image missing":
            continue
        add(
            question_id=str(item.get("question_id") or "").strip(),
            figure_id=str(item.get("figure_id") or "").strip(),
            reason="figure image missing",
        )
    return findings


def _review_selective_quality_with_fallback(
    *,
    report_json: Path,
    primary_provider,
    primary_model: str,
    fallback_provider,
    fallback_model: str,
    max_provider_fallbacks: int = 0,
    **review_kwargs,
) -> dict:
    """Run one bounded reviewer fallback without confusing service errors with answer errors."""

    fallback_available = bool(
        max_provider_fallbacks > 0
        and fallback_provider is not None
        and getattr(fallback_provider, "api_key", "")
        and str(fallback_model or "").strip()
        and (
            str(getattr(fallback_provider, "name", "")) != str(getattr(primary_provider, "name", ""))
            or str(fallback_model or "") != str(primary_model or "")
        )
    )
    primary_report = review_selective_quality(
        report_json=report_json,
        provider=primary_provider,
        model=primary_model,
        **review_kwargs,
    )
    if str(primary_report.get("status") or "") != "degraded" or not fallback_available:
        return primary_report

    primary_diagnostic_path = report_json.with_name(f"{report_json.stem}.primary_degraded.json")
    write_json(primary_diagnostic_path, primary_report)
    fallback_report = review_selective_quality(
        report_json=report_json,
        provider=fallback_provider,
        model=fallback_model,
        **review_kwargs,
    )
    fallback_report = dict(fallback_report)
    primary_calls = int(primary_report.get("remote_model_calls_this_run", 0) or 0)
    fallback_calls = int(fallback_report.get("remote_model_calls_this_run", 0) or 0)
    fallback_report["remote_model_calls_this_run"] = primary_calls + fallback_calls
    fallback_report["remote_model_calls"] = int(primary_report.get("remote_model_calls", 0) or 0) + int(
        fallback_report.get("remote_model_calls", 0) or 0
    )
    fallback_report["fallback_routing"] = {
        "used": True,
        "primary_provider": str(getattr(primary_provider, "name", "")),
        "primary_model": str(primary_model or ""),
        "fallback_provider": str(getattr(fallback_provider, "name", "")),
        "fallback_model": str(fallback_model or ""),
        "primary_status": str(primary_report.get("status") or ""),
        "primary_diagnostic": str(primary_diagnostic_path),
        "primary_remote_model_calls_this_run": primary_calls,
        "fallback_remote_model_calls_this_run": fallback_calls,
    }
    write_json(report_json, fallback_report)
    return fallback_report


def _docx_issue_code(issue: str) -> str:
    text = str(issue or "")
    if (
        "Formula-like text must not be written" in text
        or "Formula-like text leaked" in text
        or "formula-like normal text" in text
    ):
        return "formula_like_normal_text"
    if "OMML formula count" in text:
        return "omml_formula_count_below_expected"
    if "raw radical" in text or "√" in text:
        return "raw_radical_normal_text"
    if "raw subscript" in text:
        return "raw_subscript_normal_text"
    if "raw latex" in text or "leftharpoons" in text or "\\" in text:
        return "raw_latex_marker"
    return "docx_audit_issue"


def _filter_audit_report_for_model_repair(report: dict, allowed_codes: set[str]) -> dict:
    filtered = dict(report)
    for key in ("issues", "warnings"):
        items = []
        for item in report.get(key, []) if isinstance(report, dict) else []:
            code = str(item.get("code") or "").strip() if isinstance(item, dict) else ""
            if code in allowed_codes:
                items.append(item)
        filtered[key] = items
    filtered["issue_count"] = len(filtered.get("issues", []))
    filtered["warning_count"] = len(filtered.get("warnings", []))
    filtered["ok"] = not filtered["issue_count"]
    return filtered


def _governed_content_model_repair_codes(candidate_codes: set[str]) -> set[str]:
    """Return only rules whose unattended contract justifies remote repair.

    A content heuristic may still be useful as a delivery warning, but it must
    not silently turn into a slow remote repair.  Remote repair is reserved for
    rules whose machine-verifiable contract can block delivery before or after
    a bounded repair attempt.
    """

    repair_ceilings = {ActionCeiling.BLOCK, ActionCeiling.REPAIR_THEN_BLOCK}
    return {
        code
        for code in candidate_codes
        if governance_for(f"content_quality.{code}").action_ceiling in repair_ceilings
    }


def _content_repair_touches_drawing_question(model_repair: dict, structured_exam: dict) -> bool:
    """Whether a changed content repair can invalidate an existing figure.

    Missing repair metadata is treated conservatively.  With normal repair
    reports, text-only question edits reuse the already generated and reviewed
    figures instead of paying the image/vision cost again.
    """

    repaired_ids = {
        str(value).strip()
        for value in model_repair.get("repaired_question_ids", [])
        if str(value).strip()
    }
    if not repaired_ids:
        return True
    for question in structured_exam.get("items", structured_exam.get("questions", [])):
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or question.get("id") or "").strip()
        if question_id in repaired_ids and answer_figure_required(question):
            return True
    return False


def _filter_docx_issues_for_model_repair(issues: list[str]) -> list[str]:
    return [issue for issue in issues if _docx_issue_code(issue) in DOCX_MODEL_REPAIR_CODES]


def build_and_audit_docx_with_repair(
    task_id: str,
    fragments_json: Path,
    docx_path: Path,
    sdir: Path,
    mark,
    *,
    structured_exam: dict | None = None,
    candidates: list | None = None,
    selection_data: dict | None = None,
    provider=None,
    model: str = "",
    use_model: bool = False,
) -> dict:
    attempts: list[dict] = []

    def attempt(label: str) -> list[str]:
        try:
            build_docx_from_fragments(fragments_json, docx_path)
            fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
            expected_formula_count = sum(len(x.get("formulas", [])) for x in fragments_data.get("fragments", []))
            issues = audit_docx_v4(docx_path, min_formulas=expected_formula_count)
            attempts.append({"attempt": label, "ok": not issues, "issues": issues[:30]})
            return issues
        except Exception as exc:
            attempts.append({"attempt": label, "ok": False, "error": str(exc), "traceback": traceback.format_exc()})
            return [str(exc)]

    issues = attempt("initial")
    if not issues:
        write_json(sdir / "docx_repair.json", {"needed": False, "attempts": attempts})
        return {"ok": True, "issues": [], "repair": {"needed": False, "attempts": attempts}}

    repair_payload = {"needed": True, "attempts": attempts, "strategy": "local_first_then_bounded_model"}
    mark(
        "docx_repair",
        "started",
        {"initial_error": attempts[-1].get("error"), "initial_issues": attempts[-1].get("issues", [])[:10]},
    )
    try:
        repair_report = repair_answer_fragments_for_docx(
            fragments_json,
            sdir / "answer_fragments.before_docx_repair.json",
        )
    except Exception as exc:
        repair_report = {
            "ok": False,
            "changed": False,
            "repaired_count": 0,
            "error": str(exc),
            "issues": [f"local_docx_repair_failed:{exc}"],
        }
    repair_payload["repair"] = repair_report
    repair_payload["local_repair"] = repair_report
    if repair_report.get("changed"):
        mark("docx_repair", "applied", repair_report)
        issues = attempt("after_local_repair")
        if not issues:
            repair_payload["attempts"] = attempts
            repair_payload["ok"] = True
            write_json(sdir / "docx_repair.json", repair_payload)
            return {"ok": True, "issues": [], "repair": repair_payload}
    else:
        mark("docx_repair", "skipped", repair_report)

    model_repair_issues = _filter_docx_issues_for_model_repair(issues)
    model_available = bool(
        model_repair_issues
        and use_model
        and provider is not None
        and getattr(provider, "api_key", "")
        and structured_exam is not None
    )
    if model_available:
        mark(
            "docx_model_repair",
            "started",
            {
                "issues": model_repair_issues[:10],
                "skipped_issues": [issue for issue in issues if issue not in model_repair_issues][:10],
                "reason": "本地确定性修复后仍有特定单题内容问题，执行一次有界模型回修。",
            },
        )
        model_repair_report = repair_fragments_with_model_for_docx(
            fragments_json,
            structured_exam,
            candidates or [],
            selection_data=selection_data or {},
            provider=provider,
            model=model,
            docx_issues=model_repair_issues,
            backup_path=sdir / "answer_fragments.before_docx_model_repair.json",
        )
        repair_payload["model_repair"] = model_repair_report
        mark("docx_model_repair", "applied" if model_repair_report.get("changed") else "skipped", model_repair_report)
        if model_repair_report.get("changed"):
            issues = attempt("after_model_repair")
            if issues:
                try:
                    final_local_repair = repair_answer_fragments_for_docx(
                        fragments_json,
                        sdir / "answer_fragments.before_docx_final_local_repair.json",
                    )
                except Exception as exc:
                    final_local_repair = {
                        "ok": False,
                        "changed": False,
                        "repaired_count": 0,
                        "error": str(exc),
                        "issues": [f"post_model_local_docx_repair_failed:{exc}"],
                    }
                repair_payload["post_model_local_repair"] = final_local_repair
                mark(
                    "docx_repair",
                    "applied" if final_local_repair.get("changed") else "skipped",
                    {**final_local_repair, "phase": "after_model_repair"},
                )
                if final_local_repair.get("ok") and final_local_repair.get("changed"):
                    issues = attempt("after_model_local_repair")
    else:
        reason = (
            "本地修复后的剩余问题不在模型回修白名单内。"
            if not model_repair_issues
            else "模型回修未启用或未配置可用服务。"
        )
        mark("docx_model_repair", "skipped", {"reason": reason, "issues": issues[:30]})
        repair_payload["model_repair"] = {
            "ok": False,
            "changed": False,
            "repaired_count": 0,
            "repaired_question_ids": [],
            "issues": [reason],
            "skipped_issues": issues[:30],
        }
    repair_payload["attempts"] = attempts
    repair_payload["ok"] = not issues
    write_json(sdir / "docx_repair.json", repair_payload)
    return {"ok": not issues, "issues": issues, "repair": repair_payload}


def _run_pipeline_impl(task_id: str, options: PipelineOptions | None = None, *, run_id: str = "") -> dict:
    options = options or PipelineOptions()
    quality_budget = QualityExecutionBudget.from_environment()
    ensure_project_dirs()
    record = load_task(task_id)
    textbook_evidence_enabled = analysis_uses_textbook_evidence(record.analysis_profile)
    sdir = stage_dir(task_id)
    odir = output_dir(task_id)
    sdir.mkdir(parents=True, exist_ok=True)
    odir.mkdir(parents=True, exist_ok=True)
    reset_transient_progress(sdir)
    telemetry = PipelineRunTelemetry(
        task_id=task_id,
        status_path=sdir / "pipeline_status.json",
        run_id=run_id,
        quality_governance={
            "mode": "unattended",
            "human_review_required": False,
            "budget": quality_budget.to_dict(),
        },
    )
    pipeline_started_at = telemetry.started_at
    schema_executor: ThreadPoolExecutor | None = None
    schema_future: Future | None = None
    figure_schema_plan: dict | None = None
    schema_checkpoint_reused = False
    mark = telemetry.mark

    try:
        # Admission paths clear stale controls before queuing an explicit new
        # run. The worker itself must never clear here: a user can cancel after
        # admission but before this first checkpoint, and that cancellation is
        # authoritative.
        checkpoint(task_id)
        update_task(task_id, status="running", current_stage="environment", error="")
        telemetry.start_heartbeat()
        env = check_environment()
        write_json(sdir / "environment_check.json", env)
        if options.require_preferred_formula_chain and not env.get("formula_conversion", {}).get("preferred_chain_ready"):
            mark("environment", "failed", {"formula_conversion": env.get("formula_conversion", {})})
            raise RuntimeError("Preferred formula conversion chain is not ready")
        mark(
            "environment",
            "passed",
            {
                "word_mac": env["microsoft_word"]["mac"],
                "word_windows": env["microsoft_word"]["windows"],
                "formula_conversion": env.get("formula_conversion", {}),
            },
        )

        exam_path = Path(record.exam_path).expanduser()
        textbooks_dir = Path(record.textbooks_dir).expanduser()
        if not options.preprocessed_input:
            if not exam_path.exists():
                raise FileNotFoundError(f"Exam file not found: {exam_path}")
            if textbook_evidence_enabled and not textbooks_dir.exists():
                raise FileNotFoundError(f"Textbooks dir not found: {textbooks_dir}")
        thinking_mode = getattr(record, "model_thinking", "auto") or "auto"
        provider = replace(get_provider(record.provider), thinking_mode=thinking_mode)
        reasoning_provider_name = getattr(record, "reasoning_provider", "") or record.provider
        reasoning_provider = replace(get_provider(reasoning_provider_name), thinking_mode=thinking_mode)
        reasoning_model = str(getattr(record, "reasoning_model", "") or (record.model if reasoning_provider_name == record.provider else reasoning_provider.default_model) or record.model).strip()
        answer_provider_name = getattr(record, "answer_provider", "") or record.provider
        answer_provider = replace(get_provider(answer_provider_name), thinking_mode=thinking_mode)
        answer_model = str(getattr(record, "answer_model", "") or (record.model if answer_provider_name == record.provider else answer_provider.default_model) or record.model).strip()
        correctness_provider_name = getattr(record, "correctness_provider", "") or answer_provider_name
        correctness_provider = replace(get_provider(correctness_provider_name), thinking_mode=thinking_mode)
        correctness_model = str(
            getattr(record, "correctness_model", "")
            or (answer_model if correctness_provider_name == answer_provider_name else correctness_provider.default_model)
            or answer_model
        ).strip()
        vision_provider = get_provider(getattr(record, "vision_provider", "") or record.provider)
        vision_model = str(getattr(record, "vision_model", "") or getattr(vision_provider, "vision_model", "") or record.model).strip()
        provider = _pin_text_provider_model(provider, record.model)
        reasoning_provider = _pin_text_provider_model(reasoning_provider, reasoning_model)
        answer_provider = _pin_text_provider_model(answer_provider, answer_model)
        correctness_provider = _pin_text_provider_model(correctness_provider, correctness_model)
        vision_provider = _pin_vision_provider_model(vision_provider, vision_model)
        direct_answer_multimodal = provider_model_supports_vision(answer_provider, answer_model)
        image_provider = get_provider(getattr(record, "image_provider", "") or record.provider)
        image_model = str(getattr(record, "image_model", "") or getattr(image_provider, "image_model", "") or "").strip()
        if not provider_supports_image_generation(image_provider):
            image_model = ""
        if image_model:
            image_provider = replace(
                image_provider,
                image_model=image_model,
                image_model_options=(image_model,),
            )
        image_orchestration = normalize_image_orchestration(
            getattr(record, "image_orchestration", LEGACY_FIGURE_PIPELINE)
        )
        if image_orchestration == MAIN_MODEL_TOOL_LOOP and not image_model:
            raise RuntimeError("主模型自主生图模式缺少可用的生图模型；任务未降级到传统绘图链路。")
        image_routes = _isolated_image_routes(
            image_orchestration,
            image_provider=image_provider,
            image_model=image_model,
            code_provider=answer_provider,
            code_model=answer_model,
        )
        answer_image_provider = image_routes["answer_image_provider"]
        answer_image_model = str(image_routes["answer_image_model"])
        legacy_image_provider = image_routes["legacy_image_provider"]
        legacy_image_model = str(image_routes["legacy_image_model"])
        legacy_code_provider = image_routes["legacy_code_provider"]
        legacy_code_model = str(image_routes["legacy_code_model"])
        key_issues = _validate_required_provider_keys(
            use_model=options.use_model,
            allow_demo_without_key=options.allow_demo_without_key,
            provider=provider,
            reasoning_provider=reasoning_provider,
            answer_provider=answer_provider,
            correctness_provider=correctness_provider,
            vision_provider=vision_provider,
            image_provider=image_provider,
            image_model=image_model,
            require_vision_provider=not direct_answer_multimodal,
            require_reasoning_provider=textbook_evidence_enabled,
        )
        if key_issues:
            mark("provider_config", "failed", {"issues": key_issues})
            raise RuntimeError("；".join(key_issues))

        rollback_checkpoint = (
            _restore_failed_content_repair_checkpoint(sdir, current_run_started_at=pipeline_started_at)
            if options.reuse_fragments
            else ""
        )
        # A new run owns a fresh error state. Recovery inspected the previous
        # failure above; keeping it after a successful rerun misleads task
        # diagnostics and the UI.
        (sdir / "pipeline_error.json").unlink(missing_ok=True)
        checkpoint_contract: dict = {}
        if not options.preprocessed_input:
            # An empty textbook selection is still a concrete input state.  It
            # cannot reach retrieval, but writing it explicitly keeps the
            # checkpoint contract total and avoids a special "never reuse"
            # case if admission rules change.
            textbook_cache_key = ""
            textbook_manifest: list[dict] = []
            if record.selected_textbooks:
                textbook_cache_key, textbook_manifest = textbook_index_key(
                    [Path(path).expanduser().resolve() for path in record.selected_textbooks],
                    record.textbook_display_names or {},
                )
            checkpoint_contract = _upstream_checkpoint_contract(
                exam_path,
                textbook_cache_key=textbook_cache_key,
                textbook_manifest=textbook_manifest,
                strategy={
                    "question_understanding_policy_version": QUESTION_UNDERSTANDING_POLICY_VERSION,
                    "analysis_profile": record.analysis_profile,
                    "use_model": bool(options.use_model),
                    "allow_demo_without_key": bool(options.allow_demo_without_key),
                    "thinking_mode": thinking_mode,
                    "primary": {"provider": provider.name, "model": record.model},
                    "reasoning": {"provider": reasoning_provider.name, "model": reasoning_model},
                    "answer": {"provider": answer_provider.name, "model": answer_model},
                    "vision": {"provider": vision_provider.name, "model": vision_model},
                    "direct_answer_multimodal": bool(direct_answer_multimodal),
                    "figure_schema_routing_policy_version": FIGURE_SCHEMA_POLICY_VERSION,
                },
            )
        reusable_early_upstream = _early_upstream_checkpoint_reusable(
            sdir,
            requested=options.reuse_fragments,
            contract=checkpoint_contract,
        )
        reusable_upstream = textbook_evidence_enabled and _upstream_checkpoint_reusable(
            sdir,
            requested=options.reuse_fragments,
            contract=checkpoint_contract,
        )
        checkpoint_contract_fingerprint = _upstream_checkpoint_contract_fingerprint(checkpoint_contract)
        checkpoint(task_id)
        update_task(task_id, current_stage="extract_exam")
        if options.preprocessed_input:
            structured_exam_path = sdir / "structured_exam.json"
            if not structured_exam_path.is_file():
                raise RuntimeError("Hybrid input is missing structured_exam.json")
            structured_exam = json.loads(structured_exam_path.read_text(encoding="utf-8"))
            exam_issues = audit_exam_structure(structured_exam, sdir / "exam_structure_audit.json")
            if exam_issues:
                mark("extract_exam", "failed", {"issues": exam_issues[:30], "preprocessed_input": True})
                raise RuntimeError("Preprocessed exam structure audit failed")
            mark(
                "extract_exam",
                "reused",
                {
                    "question_count": len(structured_exam.get("items", [])),
                    "preprocessed_input": True,
                },
            )
        elif reusable_early_upstream:
            structured_exam = json.loads((sdir / "structured_exam.json").read_text(encoding="utf-8"))
            # Reuse the expensive extraction result, but always rerun the
            # deterministic audit so policy fixes do not leave stale warnings
            # or stale passes attached to a resumed task forever.
            exam_issues = audit_exam_structure(structured_exam, sdir / "exam_structure_audit.json")
            if exam_issues:
                mark("extract_exam", "failed", {"issues": exam_issues[:30], "checkpoint": True})
                raise RuntimeError("Exam structure audit failed")
            mark(
                "extract_exam",
                "reused",
                {
                    "question_count": len(structured_exam.get("items", [])),
                    "checkpoint": True,
                    "content_repair_rollback": rollback_checkpoint,
                },
            )
        else:
            structured_exam = extract_exam_structure(exam_path, sdir / "structured_exam.json")
            exam_issues = audit_exam_structure(structured_exam, sdir / "exam_structure_audit.json")
            if exam_issues:
                mark("extract_exam", "failed", {"issues": exam_issues[:30]})
                raise RuntimeError("Exam structure audit failed")
            mark("extract_exam", "passed", {"question_count": len(structured_exam.get("items", []))})

        checkpoint(task_id)
        update_task(task_id, current_stage="exam_structure_review")
        structure_review_reused = reusable_early_upstream or options.preprocessed_input
        mark("exam_structure_review", "reused" if structure_review_reused else "started", {"question_count": len(structured_exam.get("items", []))})
        if not structure_review_reused:
            structured_exam = auto_confirm_exam_structure(task_id, structured_exam, sdir / "structured_exam.json")
        mark(
            "exam_structure_review",
            "passed",
            {
                "question_count": len(structured_exam.get("items", [])),
                "reviewed": True,
                "review_mode": "unattended",
                "human_review_required": False,
                "checkpoint_reused": structure_review_reused,
                "preprocessed_input": options.preprocessed_input,
            },
        )

        # A schema plan depends only on the confirmed structured question and
        # the model route used for planning. Both are part of the early
        # contract. Reuse a policy-current report before scheduling background
        # work so a retrieval retry cannot spend a second visual/model call.
        existing_early_schema_plan = None
        if reusable_early_upstream and (sdir / "figure_schema_plan.json").is_file():
            try:
                existing_early_schema_plan = json.loads((sdir / "figure_schema_plan.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                existing_early_schema_plan = None
        schema_checkpoint_reused = bool(
            isinstance(existing_early_schema_plan, dict)
            and _figure_schema_checkpoint_reusable(
                existing_early_schema_plan,
                upstream_contract_fingerprint=checkpoint_contract_fingerprint,
            )
        )
        if schema_checkpoint_reused:
            figure_schema_plan = existing_early_schema_plan
            mark(
                "figure_schema_planning",
                "reused",
                {
                    "checkpoint_reused": True,
                    "background": False,
                    "dependency": "复用与当前题面、路由和政策一致的图件结构计划。",
                },
            )
        elif not reusable_upstream:
            schema_has_images = any(
                isinstance(question, dict) and question.get("image_refs")
                for question in structured_exam.get("items", []) or []
            )
            schema_provider = provider
            schema_model = record.model
            if schema_has_images and options.use_model:
                if direct_answer_multimodal and answer_provider.api_key:
                    # The selected answer model already sees the original
                    # question image.  Figure-schema planning is part of the
                    # same visual reasoning path and must not invoke a second
                    # OCR/vision model first.
                    schema_provider = answer_provider
                    schema_model = answer_model
                elif vision_provider.api_key and provider_model_supports_vision(vision_provider, vision_model):
                    schema_provider = vision_provider
                    schema_model = vision_model
            schema_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="figure-schema-background")
            schema_future = schema_executor.submit(
                copy_context().run,
                _plan_figure_schemas_with_checkpoint,
                copy.deepcopy(structured_exam),
                sdir / "figure_schema_plan.json",
                provider=schema_provider if options.use_model and schema_provider.api_key else None,
                model=schema_model,
                upstream_contract_fingerprint=checkpoint_contract_fingerprint,
            )
            mark(
                "figure_schema_planning",
                "started",
                {
                    "background": True,
                    "dependency": "仅依赖已确认原始题面；将在图件生成前汇总",
                },
            )

        checkpoint(task_id)
        update_task(task_id, current_stage="question_understanding")
        mark("question_understanding", "reused" if reusable_early_upstream else "started", {"question_count": len(structured_exam.get("items", []))})
        understanding_checkpoint_current = False
        if reusable_early_upstream:
            try:
                understanding_report = json.loads((sdir / "question_understanding.json").read_text(encoding="utf-8"))
                understanding_checkpoint_current = (
                    understanding_report.get("policy_version") == QUESTION_UNDERSTANDING_POLICY_VERSION
                )
            except Exception:
                understanding_report = {"question_count": len(structured_exam.get("items", [])), "vision_required_count": 0, "vision_used_count": 0}
        if not reusable_early_upstream or not understanding_checkpoint_current:
            understanding_report = build_question_understandings(
                structured_exam,
                sdir / "question_understanding.json",
                provider=(
                    None
                    if direct_answer_multimodal
                    else vision_provider if options.use_model and vision_provider.api_key else None
                ),
                model="" if direct_answer_multimodal else vision_model,
                progress_json=sdir / "question_understanding_progress.json",
                direct_multimodal=(answer_provider.name, answer_model) if direct_answer_multimodal else None,
            )
        attached_understanding_count = sum(
            1
            for item in structured_exam.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("question_understanding"), dict)
        )
        write_json(sdir / "structured_exam.json", structured_exam)
        mark(
            "question_understanding",
            "passed",
            {
                "question_count": understanding_report.get("question_count", 0),
                "vision_required_count": understanding_report.get("vision_required_count", 0),
                "vision_used_count": understanding_report.get("vision_used_count", 0),
                "direct_multimodal_count": understanding_report.get("direct_multimodal_count", 0),
                "attached_understanding_count": attached_understanding_count,
                "checkpoint_reused": reusable_early_upstream and understanding_checkpoint_current,
                "downstream_reuse": [
                    "knowledge_planning",
                    "evidence_selection",
                    "answer_generation",
                ],
            },
        )

        visual_understanding_failures = required_visual_understanding_failures(understanding_report)
        if visual_understanding_failures:
            mark(
                "question_understanding",
                "review_candidate",
                {
                    "failure_count": len(visual_understanding_failures),
                    "failures": visual_understanding_failures[:30],
                    "reason": (
                        "独立视觉理解在有界模型切换后仍未获得。继续下游时，支持视觉的答案模型将直接读取原图；"
                        "否则该题保留为待复核占位，不再让一题失败中断整份任务。"
                    ),
                },
            )

        checkpoint(task_id)
        if not textbook_evidence_enabled:
            index_detail = {"ok": True, "analysis_profile": record.analysis_profile, "reason": "题目解析不处理教材。"}
            mark("textbook_index", "skipped", index_detail)
        else:
            update_task(task_id, current_stage="textbook_index")
            if options.preprocessed_input:
                required_index_files = (
                    sdir / "textbook_blocks.csv",
                    sdir / "textbook_page_map.csv",
                    sdir / "textbook_index_status.json",
                )
                missing_index_files = [path.name for path in required_index_files if not path.is_file()]
                if missing_index_files:
                    raise RuntimeError("Hybrid input is missing textbook index files: " + ", ".join(missing_index_files))
                index_detail = json.loads((sdir / "textbook_index_status.json").read_text(encoding="utf-8"))
                index_detail = {**index_detail, "preprocessed_input": True, "installed": True}
            else:
                if not record.selected_textbooks:
                    raise RuntimeError("当前任务没有绑定已索引教材。请先在教材管理页选择教材并建立索引，再创建解析任务。")
                index_detail = install_textbook_index_cache(
                    record.selected_textbooks,
                    sdir,
                    record.textbook_display_names or {},
                )
            mark("textbook_index", "passed" if index_detail.get("page_map_ok", True) else "failed", index_detail)
            if not index_detail.get("page_map_ok", True):
                issues = index_detail.get("page_map_issues") or []
                messages = [str(issue.get("message", issue)) for issue in issues if isinstance(issue, dict)]
                raise RuntimeError("教材页码读取失败：" + "；".join(messages[:5]))

        checkpoint(task_id)
        plans_json = sdir / "knowledge_plans.json"
        if not textbook_evidence_enabled:
            knowledge_plans = []
            mark("knowledge_planning", "skipped", {"analysis_profile": record.analysis_profile, "reason": "未启用教材知识点规划。"})
        elif reusable_early_upstream:
            update_task(task_id, current_stage="knowledge_planning")
            knowledge_plans = load_knowledge_plans(plans_json)
            plan_detail = {
                "ok": True,
                "reused": True,
                "question_count": len(knowledge_plans),
                "output_json": str(plans_json),
            }
            mark("knowledge_planning", "reused", plan_detail)
        elif options.use_model and reasoning_provider.api_key:
            update_task(task_id, current_stage="knowledge_planning")
            plan_result = generate_knowledge_plans(
                structured_exam,
                reasoning_provider,
                reasoning_model,
                plans_json,
                use_model=True,
                progress_json=sdir / "knowledge_planning_progress.json",
                visual_provider=answer_provider if direct_answer_multimodal else None,
                visual_model=answer_model if direct_answer_multimodal else "",
            )
        elif options.allow_demo_without_key:
            update_task(task_id, current_stage="knowledge_planning")
            plan_result = generate_knowledge_plans(
                structured_exam,
                reasoning_provider,
                reasoning_model,
                plans_json,
                use_model=False,
                progress_json=sdir / "knowledge_planning_progress.json",
            )
        else:
            raise RuntimeError(f"API key not configured for reasoning provider: {reasoning_provider.name}")
        if textbook_evidence_enabled and not reusable_early_upstream:
            plan_detail = asdict(plan_result)
            mark("knowledge_planning", "passed" if plan_result.ok else "failed", plan_detail)
            if not plan_result.ok:
                raise RuntimeError("Knowledge planning failed")
            knowledge_plans = load_knowledge_plans(plans_json)
            _write_upstream_checkpoint_contract(sdir, checkpoint_contract)

        checkpoint(task_id)
        if not textbook_evidence_enabled:
            candidates = []
            mark("retrieval", "skipped", {"analysis_profile": record.analysis_profile, "reason": "未启用教材检索。"})
        else:
            update_task(task_id, current_stage="retrieval")
            candidates = build_candidates(
                structured_exam,
                sdir / "textbook_blocks.csv",
                sdir / "textbook_page_map.csv",
                sdir / "retrieval_candidates.csv",
                knowledge_plans=knowledge_plans,
            )
            retrieval_issues = audit_retrieval_candidates(structured_exam, sdir / "retrieval_candidates.csv", sdir / "retrieval_audit.json")
            if retrieval_issues:
                mark("retrieval", "failed", {"issues": retrieval_issues[:30]})
                raise RuntimeError("Retrieval audit failed")
            mark("retrieval", "passed", {"candidate_count": len(candidates)})

        checkpoint(task_id)
        if not textbook_evidence_enabled:
            selection_data = {"analysis_profile": record.analysis_profile, "selections": []}
            evidence_selections = {}
            mark("evidence_selection", "skipped", {"analysis_profile": record.analysis_profile, "reason": "题目解析不绑定教材依据。"})
        elif reusable_upstream:
            update_task(task_id, current_stage="evidence_selection")
            selection_data_preview = json.loads((sdir / "evidence_selection.json").read_text(encoding="utf-8"))
            selections_by_qid = {
                str(selection.get("question_id") or ""): selection
                for selection in selection_data_preview.get("selections", [])
                if isinstance(selection, dict)
            }
            confirmed_candidates = load_confirmed_candidates(sdir / "confirmed_evidence_candidates.csv")
            if not confirmed_candidates:
                confirmed_candidates = filter_candidates_by_selection(candidates, selections_by_qid)
            selection_detail = {
                "ok": True,
                "reused": True,
                "selected_question_count": len(selections_by_qid),
                "selected_evidence_count": len(confirmed_candidates),
            }
        else:
            update_task(task_id, current_stage="evidence_selection")
            selection_result, confirmed_candidates = confirm_evidence_selection(
                structured_exam,
                knowledge_plans,
                candidates,
                reasoning_provider,
                reasoning_model,
                sdir / "evidence_selection.json",
                sdir / "textbook_blocks.csv",
                sdir / "textbook_page_map.csv",
                progress_json=sdir / "evidence_selection_progress.json",
                use_model=options.use_model,
                visual_provider=answer_provider if direct_answer_multimodal else None,
                visual_model=answer_model if direct_answer_multimodal else "",
            )
            selection_detail = asdict(selection_result)
        if textbook_evidence_enabled:
            selection_data_preview = {}
            try:
                selection_data_preview = json.loads((sdir / "evidence_selection.json").read_text(encoding="utf-8"))
            except Exception:
                selection_data_preview = {}
            unresolved_evidence = selection_data_preview.get("unresolved_evidence") if isinstance(selection_data_preview, dict) else []
            selection_detail["unresolved_evidence_count"] = len(unresolved_evidence or [])
            selection_detail["unresolved_evidence_policy"] = "auto_label_no_user_review"
            mark("evidence_selection", "reused" if reusable_upstream else ("passed" if selection_result.ok else "failed"), selection_detail)
            if not selection_detail.get("ok"):
                raise RuntimeError("Evidence selection failed")
            selection_data = json.loads((sdir / "evidence_selection.json").read_text(encoding="utf-8"))
            evidence_selections = {
                str(selection.get("question_id", "")).strip(): selection
                for selection in selection_data.get("selections", [])
                if str(selection.get("question_id", "")).strip()
            }
            candidates = confirmed_candidates

        checkpoint(task_id)
        # Answer generation needs the per-answer-unit drawing contract.  Schema
        # planning still runs in the background alongside understanding,
        # indexing and retrieval, but it becomes a dependency here rather than
        # being attached only after the answer model has already chosen a figure
        # representation.
        existing_schema_plan = figure_schema_plan
        if existing_schema_plan is None and reusable_early_upstream and (sdir / "figure_schema_plan.json").exists():
            try:
                existing_schema_plan = json.loads((sdir / "figure_schema_plan.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                existing_schema_plan = None
        schema_checkpoint_reused = bool(
            isinstance(existing_schema_plan, dict)
            and _figure_schema_checkpoint_reusable(
                existing_schema_plan,
                upstream_contract_fingerprint=checkpoint_contract_fingerprint,
            )
        )
        if schema_checkpoint_reused:
            figure_schema_plan = existing_schema_plan
        elif schema_future is not None:
            figure_schema_plan = schema_future.result()
        else:
            schema_has_images = any(
                isinstance(question, dict) and question.get("image_refs")
                for question in structured_exam.get("items", []) or []
            )
            schema_provider = provider
            schema_model = record.model
            if schema_has_images and options.use_model:
                if direct_answer_multimodal and answer_provider.api_key:
                    schema_provider = answer_provider
                    schema_model = answer_model
                elif vision_provider.api_key and provider_model_supports_vision(vision_provider, vision_model):
                    schema_provider = vision_provider
                    schema_model = vision_model
            figure_schema_plan = _plan_figure_schemas_with_checkpoint(
                structured_exam,
                sdir / "figure_schema_plan.json",
                provider=schema_provider if options.use_model and schema_provider.api_key else None,
                model=schema_model,
                upstream_contract_fingerprint=checkpoint_contract_fingerprint,
            )
        if schema_executor is not None:
            schema_executor.shutdown(wait=True)
            schema_executor = None
        structured_exam = attach_figure_schema_plans(structured_exam, figure_schema_plan)
        write_json(sdir / "structured_exam.json", structured_exam)

        checkpoint(task_id)
        update_task(task_id, current_stage="answer_generation")
        fragments_json = sdir / "answer_fragments.json"
        checkpoint_migrations = (
            _normalize_answer_checkpoint(sdir, structured_exam) if options.reuse_fragments else []
        )
        checkpoint_source_contract_migrated = (
            _migrate_legacy_answer_source_contract(sdir, structured_exam)
            if options.reuse_fragments
            else False
        )
        checkpoint_reconciliation = (
            _reconcile_answer_generation_checkpoint(
                sdir,
                structured_exam,
                output_json=sdir / "answer_checkpoint_reconciliation.json",
            )
            if options.reuse_fragments
            else {}
        )
        draft_optional_question_ids = {
            str(question_id).strip()
            for question_id in checkpoint_reconciliation.get("reusable_question_ids", [])
            if str(question_id).strip()
        }
        reusable_answers = _answer_checkpoint_reusable(sdir, structured_exam, requested=options.reuse_fragments)
        reusable_fragment_map = (
            _reusable_answer_fragment_map(sdir, structured_exam, requested=options.reuse_fragments)
            if not reusable_answers
            else {}
        )
        if reusable_answers:
            if not fragments_json.exists():
                raise RuntimeError("Cannot reuse fragments: answer_fragments.json not found")
            fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
            fragments = list(fragments_data.get("fragments", []))
            existing_ids = {str(fragment.get("question_id", "")).strip() for fragment in fragments}
            questions_by_id = {str(question.get("question_id", "")).strip(): question for question in structured_exam.get("items", [])}
            recovered_missing = []
            recovered_evidence = []
            migrated_evidence_reason = []
            for question in structured_exam.get("items", []):
                qid = str(question.get("question_id", "")).strip()
                if not qid or qid in existing_ids:
                    continue
                evidence = candidates_for_question(candidates, qid)
                fragment = fallback_fragment(
                    question,
                    evidence,
                    "previous model run did not produce valid structured JSON; auto-filled for review",
                )
                fragments.append(attach_program_evidence_block(fragment, evidence))
                existing_ids.add(qid)
                recovered_missing.append(qid)
            for fragment in fragments:
                qid = str(fragment.get("question_id", "")).strip()
                if not qid or qid not in questions_by_id or has_bound_evidence(fragment):
                    continue
                evidence = candidates_for_question(candidates, qid)
                if not evidence:
                    continue
                bind_top_evidence(
                    fragment,
                    evidence,
                    reason="历史结构化结果未提供不绑定候选证据的原因，程序按检索排序补充最相关教材证据。",
                )
                recovered_evidence.append(qid)
            for fragment in fragments:
                qid = str(fragment.get("question_id", "")).strip()
                meta = dict(fragment.get("_meta") or {})
                if not qid or not has_bound_evidence(fragment) or meta.get("evidence_binding"):
                    continue
                warnings = [str(x) for x in fragment.get("warnings", [])]
                if not any("程序自动绑定" in warning for warning in warnings):
                    continue
                evidence_ids = [str(x) for x in fragment.get("evidence_ids", []) if str(x).strip()]
                reason = "历史结果已由程序补充候选证据；原模型未提供不绑定候选证据的原因。"
                meta["evidence_binding"] = {
                    "strategy": "program_top_evidence",
                    "reason": reason,
                    "bound_evidence_ids": evidence_ids,
                }
                fragment["_meta"] = meta
                migrated_evidence_reason.append(qid)
            if recovered_missing:
                fragments_data["fragments"] = fragments
                fragments_data.setdefault("issues", []).append(
                    {
                        "question_id": ",".join(recovered_missing),
                        "issues": ["missing fragments auto-filled for review"],
                    }
                )
                fragments_data["recovered_count"] = int(fragments_data.get("recovered_count", 0)) + len(recovered_missing)
                fragments_data["fallback_count"] = int(fragments_data.get("fallback_count", 0)) + len(recovered_missing)
            if recovered_evidence:
                fragments_data["fragments"] = fragments
                fragments_data.setdefault("recovery_events", []).extend(
                    {"question_id": qid, "strategy": "program_evidence_binding", "reason": "历史结构化结果未提供不绑定候选证据的原因"} for qid in recovered_evidence
                )
                fragments_data["recovered_count"] = int(fragments_data.get("recovered_count", 0)) + len(recovered_evidence)
            if migrated_evidence_reason:
                fragments_data["fragments"] = fragments
                fragments_data.setdefault("recovery_events", []).extend(
                    {"question_id": qid, "strategy": "program_evidence_binding_metadata_migration"} for qid in migrated_evidence_reason
                )
            if recovered_missing or recovered_evidence or migrated_evidence_reason:
                write_json(fragments_json, fragments_data)
            fragment_issues = []
            for idx, fragment in enumerate(fragments_data.get("fragments", []), start=1):
                for issue in validate_v4_answer_fragment(fragment):
                    fragment_issues.append(f"fragment {idx}: {issue}")
            if fragment_issues:
                mark("answer_generation", "failed", {"reused": True, "issues": fragment_issues[:30]})
                raise RuntimeError("Reused answer fragments failed v4 validation")
            generation_detail = {
                "ok": True,
                "reused": True,
                "fragment_count": len(fragments_data.get("fragments", [])),
                "recovered_count": len(recovered_missing) + len(recovered_evidence),
                "fallback_count": len(recovered_missing),
                "evidence_bound_count": len(recovered_evidence),
                "evidence_binding_metadata_migrated_count": len(migrated_evidence_reason),
                "output": str(fragments_json),
                "issues": [],
                "checkpoint_expression_migrations": checkpoint_migrations,
            }
        elif options.use_model and answer_provider.api_key:
            generation = generate_answer_fragments(
                structured_exam,
                candidates,
                answer_provider,
                answer_model,
                fragments_json,
                progress_json=sdir / "answer_generation_progress.json",
                evidence_selections=evidence_selections,
                reusable_fragments=reusable_fragment_map,
                image_provider=answer_image_provider,
                image_model=answer_image_model,
                include_textbook_evidence=textbook_evidence_enabled,
            )
            generation_detail = asdict(generation)
        elif options.allow_demo_without_key:
            generation = write_demo_fragments(structured_exam, candidates, fragments_json)
            generation_detail = asdict(generation)
        else:
            raise RuntimeError(f"API key not configured for answer provider: {answer_provider.name}")
        fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
        fragments_data["analysis_profile"] = record.analysis_profile
        fragments_data["document_title"] = "题目解析" if not textbook_evidence_enabled else "真题答案解析"
        write_json(fragments_json, fragments_data)
        reconciled_evidence_bindings: list[str] = []
        for fragment in fragments_data.get("fragments", []) or []:
            if not isinstance(fragment, dict):
                continue
            qid = str(fragment.get("question_id") or "").strip()
            selection = evidence_selections.get(qid)
            if not selection:
                continue
            question_evidence = candidates_for_question(candidates, qid)
            if reconcile_confirmed_evidence_binding(fragment, question_evidence, selection):
                reconciled_evidence_bindings.append(qid)
        if reconciled_evidence_bindings:
            write_json(fragments_json, fragments_data)
        expression_normalized_question_ids = _normalize_generated_expression_segments(fragments_data)
        if expression_normalized_question_ids:
            write_json(fragments_json, fragments_data)
        generation_detail["evidence_selection_reconciled_count"] = len(reconciled_evidence_bindings)
        generation_detail["evidence_selection_reconciled_question_ids"] = reconciled_evidence_bindings
        generation_detail["expression_normalized_count"] = len(expression_normalized_question_ids)
        generation_detail["expression_normalized_question_ids"] = expression_normalized_question_ids
        generation_detail["checkpoint_reused"] = reusable_answers
        generation_detail["upstream_checkpoint_reused"] = reusable_upstream
        generation_detail["checkpoint_reconciliation"] = checkpoint_reconciliation
        generation_detail["checkpoint_source_contract_migrated"] = checkpoint_source_contract_migrated
        if not generation_detail["ok"] and expression_normalized_question_ids:
            questions_by_id = {
                str(question.get("question_id") or "").strip(): question
                for question in structured_exam.get("items", [])
                if isinstance(question, dict)
            }
            normalized_issues: list[dict] = []
            for fragment in fragments_data.get("fragments", []) or []:
                if not isinstance(fragment, dict):
                    continue
                qid = str(fragment.get("question_id") or "").strip()
                remaining = validate_v4_answer_fragment(fragment) + semantic_generation_issues(
                    questions_by_id.get(qid, {}), fragment
                )
                if remaining:
                    normalized_issues.append({"question_id": qid, "issues": remaining})
            expected_ids = set(questions_by_id)
            actual_ids = {
                str(fragment.get("question_id") or "").strip()
                for fragment in fragments_data.get("fragments", []) or []
                if isinstance(fragment, dict)
            }
            if actual_ids != expected_ids:
                normalized_issues.append(
                    {"question_id": "", "issues": [f"question_id_coverage_mismatch:{sorted(expected_ids - actual_ids)}"]}
                )
            generation_detail["post_expression_normalization_issues"] = normalized_issues[:30]
            if not normalized_issues:
                generation_detail.update({"ok": True, "issue_count": 0, "repaired_by_expression_normalization": True})
        if (
            not generation_detail["ok"]
            and quality_budget.max_answer_generation_repair_rounds > 0
            and options.use_model
            and answer_provider.api_key
        ):
            failed_payload = json.loads(fragments_json.read_text(encoding="utf-8"))
            generation_audit_issues: list[dict] = []
            for row in failed_payload.get("issues", []) or []:
                if not isinstance(row, dict):
                    continue
                qid = str(row.get("question_id") or "").strip()
                for raw_issue in row.get("issues", []) or []:
                    generation_audit_issues.append(
                        {
                            "question_id": qid,
                            "code": "answer_generation_validation",
                            "message": str(raw_issue),
                        }
                    )
            mark(
                "answer_generation_model_repair",
                "started",
                {"question_count": len({item.get('question_id') for item in generation_audit_issues}), "issues": generation_audit_issues[:30]},
            )
            generation_repair = repair_fragments_with_model_for_audit(
                fragments_json,
                structured_exam,
                candidates,
                selection_data=selection_data,
                provider=correctness_provider,
                model=correctness_model,
                audit_stage="answer_generation",
                audit_report={"issues": generation_audit_issues, "warnings": []},
                image_provider=answer_image_provider,
                image_model=answer_image_model,
                backup_path=sdir / "answer_fragments.before_answer_generation_model_repair.json",
                max_repairs=quality_budget.max_content_repair_questions,
            )
            repaired_payload = json.loads(fragments_json.read_text(encoding="utf-8"))
            normalized_after_model_repair = _normalize_generated_expression_segments(repaired_payload)
            if normalized_after_model_repair:
                write_json(fragments_json, repaired_payload)
            generation_repair["expression_normalized_question_ids"] = normalized_after_model_repair
            questions_by_id = {
                str(question.get("question_id") or "").strip(): question
                for question in structured_exam.get("items", [])
                if isinstance(question, dict)
            }
            remaining_generation_issues: list[dict] = []
            for fragment in repaired_payload.get("fragments", []) or []:
                if not isinstance(fragment, dict):
                    continue
                qid = str(fragment.get("question_id") or "").strip()
                # A successful repair produces a new durable fragment.  Never
                # validate it through the pre-repair stored draft: that stale
                # snapshot can reintroduce the exact contract/formulas just
                # repaired and falsely fail migration to a newer schema.
                audit_fragment = dict(fragment)
                remaining = validate_v4_answer_fragment(fragment) + semantic_generation_issues(
                    questions_by_id.get(qid, {}), audit_fragment
                )
                if remaining:
                    remaining_generation_issues.append({"question_id": qid, "issues": remaining})
            expected_ids = set(questions_by_id)
            actual_ids = {
                str(fragment.get("question_id") or "").strip()
                for fragment in repaired_payload.get("fragments", []) or []
                if isinstance(fragment, dict)
            }
            if actual_ids != expected_ids:
                remaining_generation_issues.append(
                    {"question_id": "", "issues": [f"question_id_coverage_mismatch:{sorted(expected_ids - actual_ids)}"]}
                )
            repair_ok = bool(generation_repair.get("changed")) and not remaining_generation_issues
            generation_repair_rounds = [generation_repair]
            if remaining_generation_issues:
                second_round_issues = [
                    {
                        "question_id": str(row.get("question_id") or ""),
                        "code": "answer_generation_validation",
                        "message": str(issue),
                    }
                    for row in remaining_generation_issues
                    if isinstance(row, dict) and str(row.get("question_id") or "")
                    for issue in row.get("issues", []) or []
                ]
                if (
                    second_round_issues
                    and quality_budget.max_answer_generation_repair_rounds >= 2
                ):
                    mark(
                        "answer_generation_model_repair_round_2",
                        "started",
                        {
                            "question_count": len({item["question_id"] for item in second_round_issues}),
                            "issues": second_round_issues[:30],
                        },
                    )
                    second_repair = repair_fragments_with_model_for_audit(
                        fragments_json,
                        structured_exam,
                        candidates,
                        selection_data=selection_data,
                        provider=correctness_provider,
                        model=correctness_model,
                        audit_stage="answer_generation_round_2",
                        audit_report={"issues": second_round_issues, "warnings": []},
                        image_provider=answer_image_provider,
                        image_model=answer_image_model,
                        backup_path=sdir / "answer_fragments.before_answer_generation_model_repair_round_2.json",
                        max_repairs=min(
                            quality_budget.max_content_repair_questions,
                            len({item["question_id"] for item in second_round_issues}),
                        ),
                    )
                    generation_repair_rounds.append(second_repair)
                    repaired_payload = json.loads(fragments_json.read_text(encoding="utf-8"))
                    normalized_after_second_repair = _normalize_generated_expression_segments(repaired_payload)
                    if normalized_after_second_repair:
                        write_json(fragments_json, repaired_payload)
                    second_repair["expression_normalized_question_ids"] = normalized_after_second_repair
                    remaining_generation_issues = []
                    for fragment in repaired_payload.get("fragments", []) or []:
                        if not isinstance(fragment, dict):
                            continue
                        qid = str(fragment.get("question_id") or "").strip()
                        audit_fragment = dict(fragment)
                        remaining = validate_v4_answer_fragment(fragment) + semantic_generation_issues(
                            questions_by_id.get(qid, {}), audit_fragment
                        )
                        if remaining:
                            remaining_generation_issues.append({"question_id": qid, "issues": remaining})
                    actual_ids = {
                        str(fragment.get("question_id") or "").strip()
                        for fragment in repaired_payload.get("fragments", []) or []
                        if isinstance(fragment, dict)
                    }
                    if actual_ids != expected_ids:
                        remaining_generation_issues.append(
                            {"question_id": "", "issues": [f"question_id_coverage_mismatch:{sorted(expected_ids - actual_ids)}"]}
                        )
                    repair_ok = bool(second_repair.get("changed")) and not remaining_generation_issues
                    mark(
                        "answer_generation_model_repair_round_2",
                        "passed" if repair_ok else "failed",
                        {**second_repair, "remaining_issues": remaining_generation_issues[:30]},
                    )
            mark(
                "answer_generation_model_repair",
                "passed" if repair_ok else "failed",
                {
                    **generation_repair,
                    "round_count": len(generation_repair_rounds),
                    "rounds": generation_repair_rounds,
                    "remaining_issues": remaining_generation_issues[:30],
                },
            )
            if repair_ok:
                generation_detail.update(
                    {
                        "ok": True,
                        "repaired_after_generation": True,
                        "generation_model_repair": generation_repair,
                        "issue_count": 0,
                    }
                )
        # Publish one stable progress contract for generated, reused, repaired,
        # and demo answers alike. Telemetry must not infer completion from the
        # implementation path that happened to produce the fragments.
        generation_detail["source_image_delivery"] = attach_source_images_to_fragments(structured_exam, fragments_json)
        fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
        generation_detail["total"] = len(structured_exam.get("items", []))
        generation_detail["completed"] = len(fragments_data.get("fragments", []))
        mark("answer_generation", "passed" if generation_detail["ok"] else "failed", generation_detail)
        if not generation_detail["ok"]:
            raise RuntimeError("Answer generation failed v4 validation")

        checkpoint(task_id)
        update_task(task_id, current_stage="answer_coverage")
        fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
        review_notes = build_answer_review_notes(fragments_data, sdir / "answer_review_notes.json")
        coverage = audit_answer_coverage(
            structured_exam,
            fragments_data,
            sdir / "answer_coverage_audit.json",
            require_evidence=textbook_evidence_enabled,
        )
        if not coverage["ok"] and options.use_model and answer_provider.api_key:
            mark("answer_coverage_model_repair", "started", {"issues": coverage["issues"][:30], "warnings": coverage["warnings"][:30]})
            model_repair = repair_fragments_with_model_for_audit(
                fragments_json,
                structured_exam,
                candidates,
                selection_data=selection_data,
                provider=answer_provider,
                model=answer_model,
                audit_stage="answer_coverage",
                audit_report=coverage,
                image_provider=answer_image_provider,
                image_model=answer_image_model,
                backup_path=sdir / "answer_fragments.before_answer_coverage_model_repair.json",
            )
            mark("answer_coverage_model_repair", "applied" if model_repair.get("changed") else "skipped", model_repair)
            fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
            review_notes = build_answer_review_notes(fragments_data, sdir / "answer_review_notes.json")
            coverage = audit_answer_coverage(
                structured_exam,
                fragments_data,
                sdir / "answer_coverage_audit.json",
                require_evidence=textbook_evidence_enabled,
            )
        if not coverage["ok"]:
            mark("answer_coverage_local_repair", "started", {"issues": coverage["issues"][:30]})
            local_repair = fill_missing_fragments_locally(
                fragments_json,
                structured_exam,
                candidates,
                "覆盖检查失败后，本地生成仅用于诊断的占位解析；若仍不完整则自动阻断交付。",
            )
            mark("answer_coverage_local_repair", "applied" if local_repair.get("changed") else "skipped", local_repair)
            fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
            review_notes = build_answer_review_notes(fragments_data, sdir / "answer_review_notes.json")
            coverage = audit_answer_coverage(
                structured_exam,
                fragments_data,
                sdir / "answer_coverage_audit.json",
                require_evidence=textbook_evidence_enabled,
            )
        if not coverage["ok"]:
            mark(
                "answer_coverage_unattended_gate",
                "failed",
                {
                    "human_review_required": False,
                    "issues": coverage.get("issues", [])[:30],
                    "reason": "模型修复与本地修复后仍不完整，无人值守模式不允许人工放行。",
                },
            )
        mark(
            "answer_coverage",
            "passed" if coverage["ok"] else "failed",
            {
                "question_count": coverage["question_count"],
                "fragment_count": coverage["fragment_count"],
                "covered_count": coverage["covered_count"],
                "issue_count": coverage["issue_count"],
                "warning_count": coverage["warning_count"],
                "review_note_count": review_notes["note_count"],
                "issues": coverage["issues"][:30],
                "warnings": coverage["warnings"][:30],
            },
        )
        if not coverage["ok"]:
            raise RuntimeError("Answer coverage audit failed")

        # Correctness must be checked before figures are rendered.  Otherwise a
        # flawed high-risk answer can spend minutes in visual QA only to be
        # rejected by a later content gate.  This is selective (not all-item),
        # one-batch, evidence-backed, fingerprint-cached, and bounded by the
        # existing unattended quality budget.
        def requires_conclusion_reason_review(question: dict) -> bool:
            text = str(question.get("stem") or "")
            return bool(
                re.search(r"(?:是否|能否|可否|一定|必须|必然)", text)
                and re.search(r"(?:为什么|为何|原因|理由|说明依据)", text)
            )

        high_risk_questions = [
            question
            for question in structured_exam.get("items", [])
            if isinstance(question, dict)
            and (
                question_has_type(question, "计算题")
                or question_has_type(question, "作图题")
                or requires_conclusion_reason_review(question)
            )
        ][: quality_budget.max_selective_review_candidates]
        if high_risk_questions:
            high_risk_candidates = [
                {
                    "question_id": str(question.get("question_id") or "").strip(),
                    "code": "high_risk_correctness",
                    "reason": (
                        "结论—因果题需核对是否结论与理由同时成立，避免只核对结论而放过明显错因。"
                        if requires_conclusion_reason_review(question)
                        and not (question_has_type(question, "计算题") or question_has_type(question, "作图题"))
                        else "计算/作图综合题需在出图前核对结论、数值、作答单元完整性与教材依据。"
                    ),
                    "priority": 95,
                }
                for question in high_risk_questions
            ]
            high_risk_evidence = {
                str(question.get("question_id") or "").strip(): candidates_for_question(
                    candidates, str(question.get("question_id") or "").strip(), limit=10
                )
                for question in high_risk_questions
            }
            fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
            prefigure_review = _review_selective_quality_with_fallback(
                academic_report={"review_candidates": high_risk_candidates},
                content_quality_report={"warnings": []},
                structured_exam=structured_exam,
                fragments_data=fragments_data,
                report_json=sdir / "prefigure_correctness_review.json",
                primary_provider=correctness_provider,
                primary_model=correctness_model,
                fallback_provider=answer_provider,
                fallback_model=answer_model,
                max_provider_fallbacks=quality_budget.max_selective_review_provider_fallbacks,
                max_candidates=quality_budget.max_selective_review_candidates,
                max_batches=quality_budget.max_selective_review_batches,
                max_attempts_per_batch=quality_budget.max_selective_review_attempts_per_batch,
                enabled=options.use_model,
                evidence_context=high_risk_evidence,
            )
            selected_review_count = int(prefigure_review.get("selection", {}).get("selected_count", 0) or 0)
            review_decisions = [
                decision
                for decision in prefigure_review.get("decisions", []) or []
                if isinstance(decision, dict)
            ]
            review_execution_ok = (
                str(prefigure_review.get("status") or "") in {"completed", "not_needed"}
                and len(review_decisions) == selected_review_count
            )
            unresolved_decisions = [
                decision
                for decision in review_decisions
                if str(decision.get("decision") or "") == "repair"
            ]
            if not review_execution_ok:
                unresolved_decisions = [
                    {
                        "candidate_id": str(candidate.get("candidate_id") or ""),
                        "decision": "warn",
                        "confidence": 0.0,
                        "reason": "correctness_reviewer_unavailable_or_incomplete",
                        "suggested_fix": "",
                    }
                    for candidate in prefigure_review.get("candidates", []) or []
                    if isinstance(candidate, dict)
                ] or [{"candidate_id": "", "decision": "warn", "confidence": 0.0, "reason": "correctness_reviewer_unavailable_or_incomplete", "suggested_fix": ""}]
            mark(
                "prefigure_correctness_review",
                (
                    "degraded"
                    if not review_execution_ok
                    else ("repair_required" if unresolved_decisions else "passed")
                ),
                {
                    "candidate_count": prefigure_review.get("selection", {}).get("selected_count", 0),
                    "remote_model_calls_this_run": prefigure_review.get("remote_model_calls_this_run", 0),
                    "cache_hit": prefigure_review.get("cache", {}).get("hit", False),
                    "unresolved_count": len(unresolved_decisions),
                    "decisions": prefigure_review.get("decisions", [])[:20],
                },
            )
            repairable_decisions = [
                decision
                for decision in unresolved_decisions
                if str(decision.get("decision") or "") == "repair"
            ]
            if (
                repairable_decisions
                and quality_budget.max_prefigure_correctness_repair_rounds > 0
                and options.use_model
                and correctness_provider.api_key
            ):
                by_candidate = {
                    str(candidate.get("candidate_id") or ""): candidate
                    for candidate in prefigure_review.get("candidates", []) or []
                    if isinstance(candidate, dict)
                }
                repair_issues = []
                for decision in repairable_decisions:
                    candidate = by_candidate.get(str(decision.get("candidate_id") or ""), {})
                    repair_issues.append(
                        {
                            "question_id": str(candidate.get("question_id") or ""),
                            "code": "high_risk_correctness",
                            "message": str(decision.get("reason") or candidate.get("reason") or "高风险题正确性未通过。"),
                            "suggested_fix": str(decision.get("suggested_fix") or ""),
                            "atomic_defects": decision.get("defects") if isinstance(decision.get("defects"), list) else [],
                            "validated_numeric_patch": decision.get("proposed_calculation_contract") or {},
                        }
                    )
                mark("prefigure_correctness_repair", "started", {"issues": repair_issues[:20]})
                correctness_repair = repair_fragments_with_model_for_audit(
                    fragments_json,
                    structured_exam,
                    candidates,
                    selection_data=selection_data,
                    provider=correctness_provider,
                    model=correctness_model,
                    audit_stage="prefigure_correctness",
                    audit_report={"issues": repair_issues, "warnings": []},
                    image_provider=answer_image_provider,
                    image_model=answer_image_model,
                    backup_path=sdir / "answer_fragments.before_prefigure_correctness_repair.json",
                    max_repairs=quality_budget.max_content_repair_questions,
                )
                mark(
                    "prefigure_correctness_repair",
                    # A rejected model patch means the validator protected the
                    # current answer. It is a bounded semantic advisory, not a
                    # pipeline failure; deterministic contracts remain hard gates.
                    "applied" if correctness_repair.get("changed") else "advisory",
                    correctness_repair,
                )
                if (
                    correctness_repair.get("changed")
                    and quality_budget.max_prefigure_correctness_repair_rounds >= 2
                ):
                    fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
                    prefigure_review = _review_selective_quality_with_fallback(
                        academic_report={"review_candidates": high_risk_candidates},
                        content_quality_report={"warnings": []},
                        structured_exam=structured_exam,
                        fragments_data=fragments_data,
                        report_json=sdir / "prefigure_correctness_review.after_repair.json",
                        primary_provider=correctness_provider,
                        primary_model=correctness_model,
                        fallback_provider=answer_provider,
                        fallback_model=answer_model,
                        max_provider_fallbacks=quality_budget.max_selective_review_provider_fallbacks,
                        max_candidates=quality_budget.max_selective_review_candidates,
                        max_batches=quality_budget.max_selective_review_batches,
                        max_attempts_per_batch=quality_budget.max_selective_review_attempts_per_batch,
                        enabled=options.use_model,
                        evidence_context=high_risk_evidence,
                    )
                    selected_review_count = int(prefigure_review.get("selection", {}).get("selected_count", 0) or 0)
                    review_decisions = [
                        decision
                        for decision in prefigure_review.get("decisions", []) or []
                        if isinstance(decision, dict)
                    ]
                    review_execution_ok = (
                        str(prefigure_review.get("status") or "") in {"completed", "not_needed"}
                        and len(review_decisions) == selected_review_count
                    )
                    unresolved_decisions = [
                        decision
                        for decision in review_decisions
                        if str(decision.get("decision") or "") == "repair"
                    ]
                    if not review_execution_ok:
                        unresolved_decisions = [
                            {
                                "candidate_id": str(candidate.get("candidate_id") or ""),
                                "decision": "warn",
                                "confidence": 0.0,
                                "reason": "correctness_reviewer_unavailable_or_incomplete",
                                "suggested_fix": "",
                            }
                            for candidate in prefigure_review.get("candidates", []) or []
                            if isinstance(candidate, dict)
                        ] or [{"candidate_id": "", "decision": "warn", "confidence": 0.0, "reason": "correctness_reviewer_unavailable_or_incomplete", "suggested_fix": ""}]
                    mark(
                        "prefigure_correctness_recheck",
                        (
                            "degraded"
                            if not review_execution_ok
                            else ("repair_required" if unresolved_decisions else "passed")
                        ),
                        {
                            "unresolved_count": len(unresolved_decisions),
                            "decisions": prefigure_review.get("decisions", [])[:20],
                        },
                    )
                    remaining_repair_ids = {
                        str(candidate.get("question_id") or "")
                        for decision in unresolved_decisions
                        for candidate in prefigure_review.get("candidates", []) or []
                        if isinstance(candidate, dict)
                        and str(candidate.get("candidate_id") or "") == str(decision.get("candidate_id") or "")
                    }
                    repaired_ids = {
                        str(value or "")
                        for value in correctness_repair.get("repaired_question_ids", []) or []
                        if str(value or "")
                    }
                    rollback_ids = repaired_ids & remaining_repair_ids
                    if rollback_ids and quality_budget.max_prefigure_correctness_repair_rounds < 2:
                        restored = _rollback_repaired_questions(
                            fragments_json,
                            sdir / "answer_fragments.before_prefigure_correctness_repair.json",
                            rollback_ids,
                        )
                        mark(
                            "prefigure_correctness_repair_rollback",
                            "applied" if restored else "failed",
                            {"restored_question_ids": restored},
                        )
                elif correctness_repair.get("changed"):
                    # The default profile spends one correctness review and one
                    # bounded repair. A second semantic reviewer call is not
                    # disguised as a "recheck"; deterministic validators have
                    # already accepted the patch, while semantic uncertainty is
                    # retained as an advisory.
                    review_decisions = [
                        {
                            **decision,
                            "decision": "warn",
                            "reason": "已应用一次有界修复；按默认质量预算不再调用模型复检。",
                            "suggested_fix": "",
                        }
                        for decision in review_decisions
                    ]
                    unresolved_decisions = []
                    mark(
                        "prefigure_correctness_recheck",
                        "shadow_only",
                        {
                            "reason": "default_single_review_single_repair_budget",
                            "remote_model_calls_this_run": 0,
                        },
                    )
            # A second remote repair is opt-in.  Repeated semantic reviewers can
            # oscillate between two individually plausible answers, adding cost
            # without adding evidence.  The default budget therefore permits one
            # repair transaction; deterministic ledgers arbitrate its arithmetic.
            residual_repairs = [
                decision
                for decision in unresolved_decisions
                if str(decision.get("decision") or "") == "repair"
            ]
            if (
                quality_budget.max_prefigure_correctness_repair_rounds >= 2
                and residual_repairs
                and options.use_model
                and correctness_provider.api_key
            ):
                residual_candidates = {
                    str(candidate.get("candidate_id") or ""): candidate
                    for candidate in prefigure_review.get("candidates", []) or []
                    if isinstance(candidate, dict)
                }
                residual_issues = []
                for decision in residual_repairs:
                    candidate = residual_candidates.get(str(decision.get("candidate_id") or ""), {})
                    residual_issues.append(
                        {
                            "question_id": str(candidate.get("question_id") or ""),
                            "code": "high_risk_correctness",
                            "message": str(decision.get("reason") or "高风险题残余正确性缺陷。"),
                            "suggested_fix": str(decision.get("suggested_fix") or ""),
                            "atomic_defects": decision.get("defects") if isinstance(decision.get("defects"), list) else [],
                            "validated_numeric_patch": decision.get("proposed_calculation_contract") or {},
                        }
                    )
                mark("prefigure_correctness_residual_repair", "started", {"issues": residual_issues[:20]})
                residual_result = repair_fragments_with_model_for_audit(
                    fragments_json,
                    structured_exam,
                    candidates,
                    selection_data=selection_data,
                    provider=correctness_provider,
                    model=correctness_model,
                    audit_stage="prefigure_correctness_residual",
                    audit_report={"issues": residual_issues, "warnings": []},
                    image_provider=answer_image_provider,
                    image_model=answer_image_model,
                    backup_path=sdir / "answer_fragments.before_prefigure_correctness_residual_repair.json",
                    max_repairs=quality_budget.max_content_repair_questions,
                )
                mark(
                    "prefigure_correctness_residual_repair",
                    "applied" if residual_result.get("changed") else "advisory",
                    residual_result,
                )
                if residual_result.get("changed"):
                    fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
                    prefigure_review = _review_selective_quality_with_fallback(
                        academic_report={"review_candidates": high_risk_candidates},
                        content_quality_report={"warnings": []},
                        structured_exam=structured_exam,
                        fragments_data=fragments_data,
                        report_json=sdir / "prefigure_correctness_review.after_residual_repair.json",
                        primary_provider=correctness_provider,
                        primary_model=correctness_model,
                        fallback_provider=answer_provider,
                        fallback_model=answer_model,
                        max_provider_fallbacks=quality_budget.max_selective_review_provider_fallbacks,
                        max_candidates=quality_budget.max_selective_review_candidates,
                        max_batches=quality_budget.max_selective_review_batches,
                        max_attempts_per_batch=quality_budget.max_selective_review_attempts_per_batch,
                        enabled=options.use_model,
                        evidence_context=high_risk_evidence,
                    )
                    selected_review_count = int(prefigure_review.get("selection", {}).get("selected_count", 0) or 0)
                    review_decisions = [
                        decision
                        for decision in prefigure_review.get("decisions", []) or []
                        if isinstance(decision, dict)
                    ]
                    review_execution_ok = (
                        str(prefigure_review.get("status") or "") in {"completed", "not_needed"}
                        and len(review_decisions) == selected_review_count
                    )
                    unresolved_decisions = [
                        decision
                        for decision in review_decisions
                        if str(decision.get("decision") or "") == "repair"
                    ]
                    if not review_execution_ok:
                        unresolved_decisions = [
                            {
                                "candidate_id": str(candidate.get("candidate_id") or ""),
                                "decision": "warn",
                                "confidence": 0.0,
                                "reason": "correctness_reviewer_unavailable_or_incomplete",
                                "suggested_fix": "",
                            }
                            for candidate in prefigure_review.get("candidates", []) or []
                            if isinstance(candidate, dict)
                        ] or [{"candidate_id": "", "decision": "warn", "confidence": 0.0, "reason": "correctness_reviewer_unavailable_or_incomplete", "suggested_fix": ""}]
                    mark(
                        "prefigure_correctness_final_recheck",
                        (
                            "degraded"
                            if not review_execution_ok
                            else ("repair_required" if unresolved_decisions else "passed")
                        ),
                        {
                            "unresolved_count": len(unresolved_decisions),
                            "decisions": prefigure_review.get("decisions", [])[:20],
                        },
                    )
                    remaining_repair_ids = {
                        str(candidate.get("question_id") or "")
                        for decision in unresolved_decisions
                        for candidate in prefigure_review.get("candidates", []) or []
                        if isinstance(candidate, dict)
                        and str(candidate.get("candidate_id") or "") == str(decision.get("candidate_id") or "")
                    }
                    all_repaired_ids = {
                        str(value or "")
                        for value in [
                            *(correctness_repair.get("repaired_question_ids", []) or []),
                            *(residual_result.get("repaired_question_ids", []) or []),
                        ]
                        if str(value or "")
                    }
                    rollback_ids = all_repaired_ids & remaining_repair_ids
                    if rollback_ids:
                        restored = _rollback_repaired_questions(
                            fragments_json,
                            sdir / "answer_fragments.before_prefigure_correctness_repair.json",
                            rollback_ids,
                        )
                        mark(
                            "prefigure_correctness_residual_repair_rollback",
                            "applied" if restored else "failed",
                            {"restored_question_ids": restored},
                        )
            # The review above is a model judgment, not machine-verifiable
            # ground truth. It may improve an answer once, but unresolved A/B
            # choices or disciplinary conclusions cannot block an unattended
            # delivery indefinitely. Preserve them as explicit semantic
            # advisories while deterministic structure/render contracts remain
            # hard gates later in the pipeline.
            semantic_governance = governance_for("selective_quality.high_risk_correctness")
            candidate_by_id = {
                str(candidate.get("candidate_id") or ""): candidate
                for candidate in prefigure_review.get("candidates", []) or []
                if isinstance(candidate, dict)
            }
            semantic_advisories = []
            for decision in review_decisions if review_execution_ok else []:
                if str(decision.get("decision") or "") == "pass":
                    continue
                candidate = candidate_by_id.get(str(decision.get("candidate_id") or ""), {})
                semantic_advisories.append(
                    {
                        "question_id": str(candidate.get("question_id") or ""),
                        "decision": str(decision.get("decision") or "warn"),
                        "confidence": decision.get("confidence", 0.0),
                        "reason": str(decision.get("reason") or candidate.get("reason") or "模型语义复核仍存在分歧。"),
                        "suggested_fix": str(decision.get("suggested_fix") or ""),
                        "candidate_id": str(decision.get("candidate_id") or ""),
                    }
                )
            semantic_report = {
                "schema_version": "answer_book.semantic_quality_advisories.v1",
                "ok": True,
                "delivery_blocked": False,
                "review_attempted": True,
                "repair_round_limit": quality_budget.max_prefigure_correctness_repair_rounds,
                "evidence_class": semantic_governance.evidence_class.value,
                "action_ceiling": semantic_governance.action_ceiling.value,
                "advisory_count": len(semantic_advisories),
                "advisories": semantic_advisories,
                "review_service_status": str(prefigure_review.get("status") or "unknown"),
                "review_service_advisory_count": 0 if review_execution_ok else 1,
                "review_service_advisory": (
                    "学科正确性复核服务不可用或返回不完整；本次未将调用故障计为答案错误。"
                    if not review_execution_ok
                    else ""
                ),
                "fallback_routing": prefigure_review.get("fallback_routing", {}),
                "responsibility_boundary": (
                    "模型的学科结论、选项或专业判断只做一次有界复核与修复；"
                    "程序结构、作答覆盖、公式对象、作图产物、Word 与渲染合同仍为硬门禁。"
                ),
            }
            semantic_report["unresolved_correctness_question_ids"] = (
                _mark_unresolved_correctness_review_flags(fragments_json, semantic_advisories)
            )
            write_json(sdir / "semantic_quality_advisories.json", semantic_report)
            mark(
                "prefigure_semantic_quality_boundary",
                "passed" if not semantic_advisories and review_execution_ok else "advisory",
                semantic_report,
            )

        checkpoint(task_id)
        update_task(task_id, current_stage="figure_schema_planning")
        if figure_schema_plan is None:
            raise RuntimeError("Figure schema planning did not produce a report")
        structured_exam = attach_figure_schema_plans(structured_exam, figure_schema_plan)
        write_json(sdir / "structured_exam.json", structured_exam)
        mark(
            "figure_schema_planning",
            "passed",
            {
                "planned_count": figure_schema_plan.get("planned_count", 0),
                "schema_found_count": sum(1 for item in figure_schema_plan.get("items", []) if (item.get("schema_resolution") or {}).get("status") == "schema_found"),
                "schema_proposed_count": sum(1 for item in figure_schema_plan.get("items", []) if (item.get("schema_resolution") or {}).get("status") == "schema_proposed"),
                "checkpoint_reused": schema_checkpoint_reused,
                "background": schema_future is not None,
            },
        )

        checkpoint(task_id)
        update_task(task_id, current_stage="figures")
        figure_specs = sdir / "figure_specs.json"
        figure_progress = FigureProgressTracker(sdir / "figure_progress.json")
        figure_progress.emit("stage_started", {"question_count": len(structured_exam.get("items", []))})
        with figure_progress.operation("prepare_figures"):
            generated_figures = prepare_figures_for_fragments(
                structured_exam,
                fragments_json,
                figure_specs,
                sdir / "figures",
                provider=legacy_image_provider,
                model=legacy_image_model or record.model,
                code_provider=legacy_code_provider,
                code_model=legacy_code_model,
                progress_callback=figure_progress.emit,
            )
        with figure_progress.operation("visual_qa", figure_count=len(generated_figures), model=vision_model):
            figure_qa = audit_figures_with_vision(
                structured_exam,
                figure_specs,
                sdir / "figures",
                sdir / "figure_visual_qa.json",
                provider=vision_provider,
                model=vision_model,
                reuse_unchanged=quality_budget.reuse_existing_visual_qa,
                progress_callback=figure_progress.emit,
            )
        if figure_visual_qa_issue_count(figure_qa) and options.use_model and vision_provider.api_key:
            with figure_progress.operation("visual_qa_repair", model=vision_model):
                figure_repair = repair_figures_with_model_for_visual_qa(
                    structured_exam,
                    fragments_json,
                    figure_specs,
                    sdir / "figures",
                    sdir / "figure_visual_qa.json",
                    sdir / "figure_visual_qa_repair.json",
                    qa_report=figure_qa,
                    provider=vision_provider,
                    model=vision_model,
                    vision_provider=vision_provider,
                    vision_model=vision_model,
                    image_provider=answer_image_provider,
                    image_model=answer_image_model,
                    max_rounds=quality_budget.max_figure_repair_rounds,
                    max_candidates_per_target=quality_budget.max_figure_repair_candidates_per_target,
                    progress_callback=figure_progress.emit,
                )
            figure_qa = figure_repair.get("latest_visual_qa") if isinstance(figure_repair.get("latest_visual_qa"), dict) else figure_qa
            # Count only artifacts referenced by the current semantic specs.
            # Historical candidates may intentionally remain for diagnostics,
            # but they are not current task outputs and must not inflate runtime
            # telemetry or user-visible progress.
            current_spec_data = json.loads(figure_specs.read_text(encoding="utf-8")) if figure_specs.exists() else {}
            generated_figures = [
                sdir / "figures" / f"{str(spec.get('figure_id') or '').strip()}.png"
                for spec in current_spec_data.get("figures", []) or []
                if isinstance(spec, dict)
                and str(spec.get("figure_id") or "").strip()
                and (sdir / "figures" / f"{str(spec.get('figure_id') or '').strip()}.png").exists()
            ]
            mark(
                "figure_visual_qa_model_repair",
                "applied" if figure_repair.get("changed") else "skipped",
                {
                    "changed": figure_repair.get("changed"),
                    "rounds": figure_repair.get("rounds", [])[:5],
                    "visual_qa_issue_count": figure_visual_qa_issue_count(figure_qa),
                },
            )
        fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
        figure_qa_issue_count = figure_visual_qa_issue_count(figure_qa)
        figure_qa_blocking_findings = figure_visual_qa_blocking_findings(figure_qa)
        figure_stage_status = (
            "failed"
            if figure_qa_blocking_findings
            else ("advisory" if figure_qa_issue_count else "passed")
        )
        mark(
            "figures",
            figure_stage_status,
            {
                "generated_count": len(generated_figures),
                "visual_qa_enabled": figure_qa.get("enabled"),
                "visual_qa_count": len(figure_qa.get("items", [])),
                "visual_qa_issue_count": figure_qa_issue_count,
                "blocking_artifact_count": len(figure_qa_blocking_findings),
            },
        )
        figure_progress.emit(
            "stage_completed",
            {
                "generated_count": len(generated_figures),
                "visual_qa_issue_count": figure_qa_issue_count,
                "blocking_artifact_count": len(figure_qa_blocking_findings),
                "status": figure_stage_status,
            },
        )
        build_shadow_quality_report(sdir)

        checkpoint(task_id)
        update_task(task_id, current_stage="content_quality")
        mark("content_quality", "started", {"message": "开始进行内容质量审查。"})
        drafts_path = sdir / "answer_drafts.json"
        selection_path = sdir / "evidence_selection.json"
        drafts_data = json.loads(drafts_path.read_text(encoding="utf-8")) if drafts_path.exists() else {"drafts": []}
        selection_data = (
            json.loads(selection_path.read_text(encoding="utf-8"))
            if selection_path.exists()
            else {"analysis_profile": record.analysis_profile, "selections": []}
        )
        content_quality = audit_content_quality(
            structured_exam,
            fragments_data,
            drafts_data,
            selection_data,
            sdir / "content_quality_audit.json",
            draft_optional_question_ids=draft_optional_question_ids,
            active_figure_specs_data=(
                json.loads(figure_specs.read_text(encoding="utf-8"))
                if figure_specs.exists()
                else {"figures": []}
            ),
        )
        governed_model_repair_codes = _governed_content_model_repair_codes(CONTENT_QUALITY_MODEL_REPAIR_CODES)
        model_repair_quality = _filter_audit_report_for_model_repair(content_quality, governed_model_repair_codes)
        model_repair_has_targets = bool(model_repair_quality.get("issues") or model_repair_quality.get("warnings"))
        if model_repair_has_targets and options.use_model and answer_provider.api_key:
            semantic_correctness_codes = {
                "answer_analysis_comparative_contradiction",
                "composition_partition_missing_declared_component",
                "calculation_internal_inconsistency",
                "spatial_relation_improper_membership_inference",
                "xrd_figure_text_label_mismatch",
                "xrd_unsupported_peak_spacing_trend",
            }
            model_repair_codes = {
                str(item.get("code") or "")
                for item in [*model_repair_quality.get("issues", []), *model_repair_quality.get("warnings", [])]
                if isinstance(item, dict)
            }
            content_repair_provider = correctness_provider if model_repair_codes & semantic_correctness_codes else answer_provider
            content_repair_model = correctness_model if model_repair_codes & semantic_correctness_codes else answer_model
            skipped_quality_issues = [
                item
                for item in list(content_quality.get("issues", [])) + list(content_quality.get("warnings", []))
                if str(item.get("code") or "").strip() not in governed_model_repair_codes
            ]
            mark(
                "content_quality_model_repair",
                "started",
                {
                    "issues": model_repair_quality["issues"][:30],
                    "warnings": model_repair_quality["warnings"][:30],
                    "skipped_issues": skipped_quality_issues[:30],
                },
            )
            model_repair = repair_fragments_with_model_for_audit(
                fragments_json,
                structured_exam,
                candidates,
                selection_data=selection_data,
                provider=content_repair_provider,
                model=content_repair_model,
                audit_stage="content_quality",
                audit_report=model_repair_quality,
                image_provider=answer_image_provider,
                image_model=answer_image_model,
                backup_path=sdir / "answer_fragments.before_content_quality_model_repair.json",
                max_repairs=quality_budget.max_content_repair_questions,
            )
            mark("content_quality_model_repair", "applied" if model_repair.get("changed") else "skipped", model_repair)
            if model_repair.get("changed") and _content_repair_touches_drawing_question(model_repair, structured_exam):
                repaired_figures = prepare_figures_for_fragments(
                    structured_exam,
                    fragments_json,
                    figure_specs,
                    sdir / "figures",
                    provider=legacy_image_provider,
                    model=legacy_image_model or record.model,
                    code_provider=legacy_code_provider,
                    code_model=legacy_code_model,
                )
                repaired_figure_qa = audit_figures_with_vision(
                    structured_exam,
                    figure_specs,
                    sdir / "figures",
                    sdir / "figure_visual_qa.json",
                    provider=vision_provider,
                    model=vision_model,
                    reuse_unchanged=quality_budget.reuse_existing_visual_qa,
                )
                if figure_visual_qa_issue_count(repaired_figure_qa) and options.use_model and vision_provider.api_key:
                    figure_repair = repair_figures_with_model_for_visual_qa(
                        structured_exam,
                        fragments_json,
                        figure_specs,
                        sdir / "figures",
                        sdir / "figure_visual_qa.json",
                        sdir / "figure_visual_qa_repair.after_content_quality.json",
                        qa_report=repaired_figure_qa,
                        provider=vision_provider,
                        model=vision_model,
                        vision_provider=vision_provider,
                        vision_model=vision_model,
                        image_provider=answer_image_provider,
                        image_model=answer_image_model,
                        max_rounds=quality_budget.max_figure_repair_rounds,
                        max_candidates_per_target=quality_budget.max_figure_repair_candidates_per_target,
                        progress_callback=figure_progress.emit,
                    )
                    repaired_figure_qa = figure_repair.get("latest_visual_qa") if isinstance(figure_repair.get("latest_visual_qa"), dict) else repaired_figure_qa
                    repaired_figures = sorted((sdir / "figures").glob("*.png"))
                    mark(
                        "figure_visual_qa_model_repair_after_content_quality",
                        "applied" if figure_repair.get("changed") else "skipped",
                        {
                            "changed": figure_repair.get("changed"),
                            "rounds": figure_repair.get("rounds", [])[:5],
                            "visual_qa_issue_count": figure_visual_qa_issue_count(repaired_figure_qa),
                        },
                    )
                mark(
                    "figures_after_content_quality_model_repair",
                    (
                        "failed"
                        if figure_visual_qa_blocking_findings(repaired_figure_qa)
                        else ("advisory" if figure_visual_qa_issue_count(repaired_figure_qa) else "passed")
                    ),
                    {
                        "generated_count": len(repaired_figures),
                        "visual_qa_enabled": repaired_figure_qa.get("enabled"),
                        "visual_qa_count": len(repaired_figure_qa.get("items", [])),
                        "visual_qa_issue_count": figure_visual_qa_issue_count(repaired_figure_qa),
                        "blocking_artifact_count": len(figure_visual_qa_blocking_findings(repaired_figure_qa)),
                        "paths": [str(path) for path in repaired_figures[:20]],
                    },
                )
                # The content repair may invalidate an earlier figure, but the
                # refreshed audit above is now authoritative for delivery.
                figure_qa = repaired_figure_qa
                figure_qa_issue_count = figure_visual_qa_issue_count(figure_qa)
            elif model_repair.get("changed"):
                mark(
                    "figures_after_content_quality_model_repair",
                    "reused",
                    {
                        "reason": "repaired_questions_do_not_include_drawing_question",
                        "repaired_question_ids": model_repair.get("repaired_question_ids", []),
                        "visual_qa_reused": True,
                    },
                )
            fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
            review_notes = build_answer_review_notes(fragments_data, sdir / "answer_review_notes.json")
            content_quality = audit_content_quality(
                structured_exam,
                fragments_data,
                drafts_data,
                selection_data,
                sdir / "content_quality_audit.json",
                draft_optional_question_ids=draft_optional_question_ids,
                active_figure_specs_data=(
                    json.loads(figure_specs.read_text(encoding="utf-8"))
                    if figure_specs.exists()
                    else {"figures": []}
                ),
            )
        elif not content_quality["ok"]:
            mark(
                "content_quality_model_repair",
                "skipped",
                {
                    "reason": "质量审查问题不在模型回修白名单内，跳过模型回修，进入程序自修。",
                    "issues": content_quality["issues"][:30],
                    "warnings": content_quality["warnings"][:30],
                },
            )
        if not content_quality["ok"]:
            mark("content_quality_local_repair", "started", {"issues": content_quality["issues"][:30]})
            local_repair = repair_content_quality_locally(
                fragments_json,
                content_quality,
                sdir / "answer_fragments.before_content_quality_local_repair.json",
            )
            if not local_repair.get("changed"):
                local_repair = repair_answer_fragments_for_docx(
                    fragments_json,
                    sdir / "answer_fragments.before_content_quality_local_repair.json",
                )
            mark("content_quality_local_repair", "applied" if local_repair.get("changed") else "skipped", local_repair)
            fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
            content_quality = audit_content_quality(
                structured_exam,
                fragments_data,
                drafts_data,
                selection_data,
                sdir / "content_quality_audit.json",
                draft_optional_question_ids=draft_optional_question_ids,
                active_figure_specs_data=(
                    json.loads(figure_specs.read_text(encoding="utf-8"))
                    if figure_specs.exists()
                    else {"figures": []}
                ),
            )
        if not content_quality["ok"]:
            content_quality = enforce_unattended_audit_report(
                content_quality,
                source="content_quality",
                output_json=sdir / "content_quality_audit.json",
            )
        if not content_quality["ok"]:
            mark(
                "content_quality_unattended_gate",
                "review_candidate",
                {
                    "human_review_required": False,
                    "blocked_count": content_quality.get("blocked_count", 0),
                    "issues": content_quality.get("issues", [])[:30],
                    "delivery_policy": "保留可阅读答案并生成待复核候选版；不宣称正式验收通过。",
                },
            )
        figure_qa_blocking_findings = figure_visual_qa_blocking_findings(figure_qa)
        visual_governance = governance_for("figure_visual_qa.review_failed")
        visual_advisory_report = {
            "schema_version": "answer_book.figure_visual_quality_advisories.v1",
            "ok": not figure_qa_blocking_findings,
            "delivery_blocked": bool(figure_qa_blocking_findings),
            "repair_round_limit": quality_budget.max_figure_repair_rounds,
            "evidence_class": visual_governance.evidence_class.value,
            "action_ceiling": visual_governance.action_ceiling.value,
            "advisory_count": figure_qa_issue_count if not figure_qa_blocking_findings else 0,
            "blocking_artifact_count": len(figure_qa_blocking_findings),
            "blocking_artifacts": figure_qa_blocking_findings,
            "responsibility_boundary": (
                "视觉模型对学科标签、科学结论与图面完整性的判断只触发一次有界修复；"
                "图片缺失、损坏等机器可验证的交付故障仍为硬门禁。"
            ),
        }
        write_json(sdir / "figure_visual_quality_advisories.json", visual_advisory_report)
        if figure_qa_blocking_findings:
            mark(
                "figure_quality_unattended_gate",
                "failed",
                {
                    "human_review_required": False,
                    "visual_qa_issue_count": figure_qa_issue_count,
                    "blocking_artifact_count": len(figure_qa_blocking_findings),
                    "blocking_artifacts": figure_qa_blocking_findings,
                    "reason": "作图产物在有界修复后仍存在机器可验证的缺失或损坏。",
                },
            )
            raise RuntimeError("Figure artifact validation failed after bounded repairs")
        mark(
            "figure_quality_unattended_gate",
            "advisory" if figure_qa_issue_count else "passed",
            visual_advisory_report,
        )
        content_quality = attach_figure_generation_audit(content_quality, sdir)
        academic_expression_report = audit_academic_expressions(
            fragments_data,
            structured_exam=structured_exam,
            output_json=sdir / "academic_expression_audit.json",
            render_preflight=not options.defer_local_delivery,
        )
        mark(
            "academic_expressions",
            "passed" if academic_expression_report["ok"] else "failed",
            {
                "mode": academic_expression_report["mode"],
                "remote_model_calls": academic_expression_report["remote_model_calls"],
                "expression_count": academic_expression_report["expression_count"],
                "issue_count": academic_expression_report["issue_count"],
                "warning_count": academic_expression_report["warning_count"],
            },
        )
        if not academic_expression_report["ok"]:
            raise RuntimeError("Academic expression render contract failed")
        selective_review = _review_selective_quality_with_fallback(
            academic_report=academic_expression_report,
            content_quality_report=content_quality,
            structured_exam=structured_exam,
            fragments_data=fragments_data,
            report_json=sdir / "selective_quality_review.json",
            primary_provider=correctness_provider,
            primary_model=correctness_model,
            fallback_provider=answer_provider,
            fallback_model=answer_model,
            max_provider_fallbacks=quality_budget.max_selective_review_provider_fallbacks,
            max_candidates=quality_budget.max_selective_review_candidates,
            max_batches=quality_budget.max_selective_review_batches,
            max_attempts_per_batch=quality_budget.max_selective_review_attempts_per_batch,
            enabled=options.use_model and quality_budget.post_content_selective_review_enabled,
            shadow_only=not quality_budget.post_content_selective_review_enabled,
        )
        mark(
            "selective_quality_review",
            selective_review["status"],
            {
                "candidate_count": selective_review["selection"]["selected_count"],
                "batch_count": selective_review["batch_count"],
                "remote_model_calls_this_run": selective_review["remote_model_calls_this_run"],
                "cache_hit": selective_review["cache"]["hit"],
                "warning_count": len(selective_review["warnings"]),
            },
        )
        build_shadow_quality_report(sdir)
        mark(
            "content_quality",
            "passed" if content_quality["ok"] else "review_candidate",
            {
                "question_count": content_quality["question_count"],
                "checked_count": content_quality["checked_count"],
                "issue_count": content_quality["issue_count"],
                "warning_count": content_quality["warning_count"],
                "review_required": not content_quality["ok"],
                "governance_mode": "unattended",
                "issues": content_quality["issues"][:30],
                "warnings": content_quality["warnings"][:30],
            },
        )

        # Audit repairs replace a complete answer fragment transactionally.
        # Reassert immutable source-question images after every possible repair
        # and immediately before document construction, so a valid content
        # repair cannot silently remove required source material from Word.
        final_source_image_delivery = attach_source_images_to_fragments(structured_exam, fragments_json)
        mark(
            "source_image_delivery_finalizer",
            "passed" if not final_source_image_delivery.get("missing") else "failed",
            final_source_image_delivery,
        )
        if final_source_image_delivery.get("missing"):
            raise RuntimeError("Required source question image is missing before document delivery")

        if options.defer_local_delivery:
            handoff = {
                "schema_version": "answer_book.hybrid_handoff.v1",
                "task_id": task_id,
                "status": "awaiting_local_delivery",
                "cloud_pipeline_complete": True,
                "local_delivery_required": True,
                "content_quality_review_required": not content_quality.get("ok", False),
                "required_local_inputs": [
                    "structured_exam.json",
                    "answer_fragments.json",
                    "confirmed_evidence_candidates.csv",
                    "evidence_selection.json",
                    "content_quality_audit.json",
                ],
            }
            write_json(sdir / "hybrid_handoff.json", handoff)
            mark("cloud_handoff", "passed", handoff)
            update_task(
                task_id,
                status="awaiting_local_delivery",
                current_stage="awaiting_local_delivery",
                error="",
            )
            return handoff

        return complete_pipeline_delivery(
            task_id=task_id,
            fragments_json=fragments_json,
            stage_dir=sdir,
            output_dir=odir,
            structured_exam=structured_exam,
            candidates=candidates,
            selection_data=selection_data,
            provider=answer_provider,
            model=answer_model,
            use_model=options.use_model,
            render_with_word=options.render_with_word,
            content_quality=content_quality,
            preserve_document_diagnostics=options.preserve_document_diagnostics,
            mark=mark,
            write_json=write_json,
            build_docx_with_repair=build_and_audit_docx_with_repair,
        )
    except TaskCancelled as exc:
        write_json(
            sdir / "pipeline_error.json",
            {"error": str(exc), "cancelled": True, "run_started_at": pipeline_started_at},
        )
        mark("pipeline", "cancelled", {"error": str(exc)})
        update_task(task_id, status="cancelled", current_stage="cancelled", error=str(exc))
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        try:
            pinned_model_traces = pin_model_diagnostics_for_failure(task_id)
        except Exception:
            pinned_model_traces = 0
        write_json(
            sdir / "pipeline_error.json",
            {
                "error": str(exc),
                "traceback": tb,
                "run_started_at": pipeline_started_at,
                "diagnostic_context": {"pinned_model_traces": pinned_model_traces},
            },
        )
        try:
            build_model_usage_report(sdir, odir, task_id)
        except Exception:
            pass
        update_task(task_id, status="failed", error=str(exc))
        mark("pipeline", "failed", {"error": str(exc)})
        raise
    finally:
        telemetry.stop()
        if options.defer_local_delivery:
            pipeline_status = sdir / "pipeline_status.json"
            cloud_pipeline_status = sdir / "cloud_pipeline_status.json"
            if pipeline_status.is_file():
                cloud_pipeline_status.write_bytes(pipeline_status.read_bytes())
        if schema_executor is not None:
            schema_executor.shutdown(wait=False, cancel_futures=True)


def run_pipeline(task_id: str, options: PipelineOptions | None = None) -> dict:
    """Run every entry path inside the same per-task model-call ledger scope.

    GUI workers already establish this context, while the documented CLI path
    historically called the pipeline directly and therefore produced an empty
    per-task ledger.  Keeping the boundary here makes GUI, CLI, recovery, and
    future callers consistent; nested contexts merge safely.
    """

    run_id = uuid4().hex
    with model_call_context(task_id=task_id, run_id=run_id, operation="解析任务"):
        return _run_pipeline_impl(task_id, options, run_id=run_id)
