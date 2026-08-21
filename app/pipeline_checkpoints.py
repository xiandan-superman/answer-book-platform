from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .answer_generation import ANSWER_SOURCE_CONTRACT_VERSION, answer_source_contract, semantic_generation_issues
from .evidence_selection import SCHEMA_VERSION as EVIDENCE_SELECTION_SCHEMA_VERSION
from .exam_extract import EXAM_GROUPING_POLICY_VERSION
from .expression_promotion import promote_inline_mathematical_expressions, promote_inline_reactions
from .ocr_corrections import apply_declared_ocr_corrections
from .retrieval import RETRIEVAL_CONTEXT_POLICY_VERSION
from .v4_schema import validate_v4_answer_fragment

_NON_REUSABLE_REVIEW_FLAGS = {
    "answer_generation_failed",
    "answer_generation_review_candidate",
    "unresolved_formula_reference_removed",
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def rollback_repaired_questions(
    fragments_json: Path,
    backup_path: Path,
    question_ids: set[str],
) -> list[str]:
    """Restore only failed repair targets, preserving unrelated work."""

    if not question_ids or not fragments_json.exists() or not backup_path.exists():
        return []
    try:
        current = json.loads(fragments_json.read_text(encoding="utf-8"))
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(current, dict) or not isinstance(backup, dict):
        return []
    original_by_id = {
        str(item.get("question_id") or ""): item
        for item in _json_list(backup.get("fragments"))
        if isinstance(item, dict)
    }
    restored: list[str] = []
    rows = []
    for item in _json_list(current.get("fragments")):
        if not isinstance(item, dict):
            rows.append(item)
            continue
        qid = str(item.get("question_id") or "")
        if qid in question_ids and qid in original_by_id:
            rows.append(copy.deepcopy(original_by_id[qid]))
            restored.append(qid)
        else:
            rows.append(item)
    current["fragments"] = rows
    if restored:
        recovery_events = _json_list(current.get("recovery_events"))
        recovery_events.extend(
            {
                "question_id": qid,
                "strategy": "failed_correctness_repair_rollback",
            }
            for qid in restored
        )
        current["recovery_events"] = recovery_events
        _write_json(fragments_json, current)
    return restored


def restore_failed_content_repair_checkpoint(
    stage_dir: Path,
    *,
    current_run_started_at: str = "",
) -> str:
    """Roll back a failed repair transaction before checkpoint reuse."""

    error_path = stage_dir / "pipeline_error.json"
    if not error_path.exists():
        return ""
    try:
        error = json.loads(error_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(error, dict):
        return ""
    error_text = str(error.get("error") or "")
    if "High-risk answer correctness audit failed" in error_text:
        destination = stage_dir / "answer_fragments.json"
        backup = stage_dir / "answer_fragments.before_prefigure_correctness_repair.json"
        if destination.exists() and backup.exists():
            try:
                current = json.loads(destination.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                current = {}
            if not isinstance(current, dict):
                current = {}
            repaired_ids = {
                str(item.get("question_id") or "")
                for item in _json_list(current.get("fragments"))
                if isinstance(item, dict)
                and str(_json_dict(item.get("_meta")).get("recovered_by") or "").startswith(
                    "prefigure_correctness"
                )
            }
            restored = rollback_repaired_questions(destination, backup, repaired_ids)
            if restored:
                error_path.unlink(missing_ok=True)
                return "prefigure_correctness:" + ",".join(restored)
        return ""
    if "Content quality audit failed" not in error_text:
        return ""
    if current_run_started_at and str(error.get("run_started_at") or "") != current_run_started_at:
        return ""
    for name in (
        "answer_fragments.before_content_quality_model_repair.json",
        "answer_fragments.before_content_quality_local_repair.json",
    ):
        backup = stage_dir / name
        if not backup.exists():
            continue
        destination = stage_dir / "answer_fragments.json"
        shutil.copy2(backup, destination)
        error_path.unlink(missing_ok=True)
        return str(backup)
    return ""


def upstream_checkpoint_reusable(stage_dir: Path, *, requested: bool) -> bool:
    """Accept an upstream checkpoint only under current structural policies."""

    required = (
        "structured_exam.json",
        "knowledge_plans.json",
        "evidence_selection.json",
    )
    if not requested or not all((stage_dir / filename).exists() for filename in required):
        return False
    try:
        structured_exam = json.loads((stage_dir / "structured_exam.json").read_text(encoding="utf-8"))
        retrieval_summary = json.loads(
            (stage_dir / "retrieval_candidates.summary.json").read_text(encoding="utf-8")
        )
        evidence_selection = json.loads((stage_dir / "evidence_selection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not all(isinstance(payload, dict) for payload in (structured_exam, retrieval_summary, evidence_selection)):
        return False
    return (
        structured_exam.get("grouping_policy_version") == EXAM_GROUPING_POLICY_VERSION
        and retrieval_summary.get("retrieval_context_policy_version")
        == RETRIEVAL_CONTEXT_POLICY_VERSION
        and evidence_selection.get("schema_version") == EVIDENCE_SELECTION_SCHEMA_VERSION
    )


def answer_checkpoint_reusable(
    stage_dir: Path,
    structured_exam: dict[str, Any],
    *,
    requested: bool,
) -> bool:
    """Reject incomplete/failure placeholders without invalidating upstream work."""

    path = stage_dir / "answer_fragments.json"
    if not requested or not path.exists():
        return False
    try:
        fragments_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(fragments_data, dict):
        return False
    if not isinstance(structured_exam, dict):
        return False
    declared_source_contract = _json_dict(fragments_data.get("source_contract"))
    if declared_source_contract:
        current_source_contract = answer_source_contract(structured_exam)
        if (
            str(declared_source_contract.get("version") or "") != ANSWER_SOURCE_CONTRACT_VERSION
            or str(declared_source_contract.get("fingerprint") or "")
            != str(current_source_contract.get("fingerprint") or "")
        ):
            return False
    question_id_rows = [
        str(item.get("question_id") or "").strip()
        for item in _json_list(structured_exam.get("items"))
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    ]
    question_ids = set(question_id_rows)
    fragment_rows = _json_list(fragments_data.get("fragments"))
    fragment_id_rows = [
        str(item.get("question_id") or "").strip()
        for item in fragment_rows
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    ]
    fragment_ids = set(fragment_id_rows)
    if (
        not question_ids
        or len(question_id_rows) != len(question_ids)
        or len(fragment_rows) != len(question_id_rows)
        or len(fragment_id_rows) != len(fragment_rows)
        or len(fragment_id_rows) != len(fragment_ids)
        or fragment_ids != question_ids
    ):
        return False
    questions_by_id = {
        str(item.get("question_id") or "").strip(): item
        for item in _json_list(structured_exam.get("items"))
        if isinstance(item, dict)
    }
    drafts_by_id: dict[str, dict[str, Any]] = {}
    try:
        drafts_data = json.loads((stage_dir / "answer_drafts.json").read_text(encoding="utf-8"))
        if not isinstance(drafts_data, dict):
            drafts_data = {}
        drafts_by_id = {
            str(item.get("question_id") or "").strip(): item
            for item in _json_list(drafts_data.get("drafts"))
            if isinstance(item, dict)
        }
    except (OSError, ValueError, TypeError):
        drafts_by_id = {}
    for item in _json_list(fragments_data.get("fragments")):
        if not isinstance(item, dict):
            return False
        if str(item.get("answer") or "").strip() in {"", "待复核", "待人工复核"}:
            return False
        if str(_json_dict(item.get("_meta")).get("recovered_by") or "") == "failure_placeholder":
            return False
        if (
            _json_list(_json_dict(item.get("_meta")).get("review_candidate_issues"))
            and (_json_list(item.get("warnings")) or _json_list(item.get("_review_flags")))
        ):
            return False
        if any(
            isinstance(flag, dict)
            and str(flag.get("code") or "") in _NON_REUSABLE_REVIEW_FLAGS
            for flag in item.get("_review_flags", []) or []
        ):
            return False
        if validate_v4_answer_fragment(item):
            return False
        qid = str(item.get("question_id") or "").strip()
        audit_fragment = dict(item)
        # A repaired durable fragment is authoritative. Reattaching the
        # pre-repair stored draft can resurrect stale formula indices or the
        # exact arithmetic defect that the repair removed. Use legacy drafts
        # only when the fragment lacks durable unit/contract calculation state.
        if (
            qid in drafts_by_id
            and not isinstance(item.get("_draft"), dict)
            and not item.get("answer_units")
            and not item.get("calculation_contract")
        ):
            audit_fragment["_draft"] = drafts_by_id[qid]
        if semantic_generation_issues(questions_by_id.get(qid, {}), audit_fragment):
            return False
    return True


def reusable_answer_fragment_map(
    stage_dir: Path,
    structured_exam: dict[str, Any],
    *,
    requested: bool,
) -> dict[str, dict[str, Any]]:
    """Return individually valid fragments from an incomplete checkpoint.

    A contradiction in one answer must not force unrelated, already valid
    answers to call the model again. Invalid, placeholder, or explicitly
    review-candidate fragments are excluded and will be regenerated.
    """

    if not isinstance(structured_exam, dict):
        return {}
    path = stage_dir / "answer_fragments.json"
    if not requested or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    declared_source_contract = _json_dict(payload.get("source_contract"))
    current_source_contract = answer_source_contract(structured_exam)
    declared_question_fingerprints = _json_dict(declared_source_contract.get("question_fingerprints"))
    current_question_fingerprints = _json_dict(current_source_contract.get("question_fingerprints"))
    enforce_source_contract = bool(declared_source_contract)
    if enforce_source_contract and str(declared_source_contract.get("version") or "") != ANSWER_SOURCE_CONTRACT_VERSION:
        return {}
    questions_by_id = {
        str(item.get("question_id") or "").strip(): item
        for item in _json_list(structured_exam.get("items"))
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    }
    rows = [item for item in _json_list(payload.get("fragments")) if isinstance(item, dict)]
    id_counts: dict[str, int] = {}
    for item in rows:
        qid = str(item.get("question_id") or "").strip()
        if qid:
            id_counts[qid] = id_counts.get(qid, 0) + 1
    reusable: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        if not qid or id_counts.get(qid) != 1 or qid not in questions_by_id:
            continue
        if enforce_source_contract and (
            not str(declared_question_fingerprints.get(qid) or "")
            or str(declared_question_fingerprints.get(qid) or "")
            != str(current_question_fingerprints.get(qid) or "")
        ):
            continue
        if str(item.get("answer") or "").strip() in {"", "待复核", "待人工复核"}:
            continue
        meta = _json_dict(item.get("_meta"))
        if str(meta.get("recovered_by") or "") in {"failure_placeholder", "review_candidate_preserved"}:
            continue
        if (
            _json_list(meta.get("review_candidate_issues"))
            and (_json_list(item.get("warnings")) or _json_list(item.get("_review_flags")))
        ):
            continue
        if any(
            isinstance(flag, dict)
            and str(flag.get("code") or "") in _NON_REUSABLE_REVIEW_FLAGS
            for flag in item.get("_review_flags", []) or []
        ):
            continue
        if validate_v4_answer_fragment(item):
            continue
        if semantic_generation_issues(questions_by_id[qid], item):
            continue
        reusable[qid] = copy.deepcopy(item)
    return reusable


def reconcile_answer_generation_checkpoint(
    stage_dir: Path,
    structured_exam: dict[str, Any] | None = None,
    *,
    output_json: Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic reuse/redrive plan from paused answer artifacts.

    Progress JSON is observability state, not the source of truth.  Durable,
    individually validated fragments win even when a pause captured an older
    counter; missing, duplicate, foreign, or invalid fragments are redriven.
    The function never mutates the saved fragments or starts model work.
    """

    structured_path = stage_dir / "structured_exam.json"
    fragments_path = stage_dir / "answer_fragments.json"
    progress_path = stage_dir / "answer_generation_progress.json"
    parse_issues: list[str] = []
    if structured_exam is None:
        try:
            structured_exam = json.loads(structured_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            structured_exam = {}
            parse_issues.append(f"structured_exam unreadable: {exc}")
    if not isinstance(structured_exam, dict):
        structured_exam = {}
        parse_issues.append("structured_exam unreadable: root must be an object")
    if fragments_path.exists():
        try:
            fragments_payload = json.loads(fragments_path.read_text(encoding="utf-8"))
            if not isinstance(fragments_payload, dict):
                raise TypeError("root must be an object")
        except (OSError, ValueError, TypeError) as exc:
            fragments_payload = {}
            parse_issues.append(f"answer_fragments unreadable: {exc}")
    else:
        fragments_payload = {}
    try:
        progress_payload = json.loads(progress_path.read_text(encoding="utf-8"))
        if not isinstance(progress_payload, dict):
            raise TypeError("root must be an object")
    except (OSError, ValueError, TypeError) as exc:
        progress_payload = {}
        if progress_path.exists():
            parse_issues.append(f"answer_generation_progress unreadable: {exc}")

    expected_ids = [
        str(item.get("question_id") or "").strip()
        for item in _json_list((structured_exam or {}).get("items"))
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    ]
    expected_set = set(expected_ids)
    fragment_rows = [item for item in _json_list(fragments_payload.get("fragments")) if isinstance(item, dict)]
    fragment_ids = [str(item.get("question_id") or "").strip() for item in fragment_rows]
    counts: dict[str, int] = {}
    for qid in fragment_ids:
        if qid:
            counts[qid] = counts.get(qid, 0) + 1
    duplicate_ids = sorted(qid for qid, count in counts.items() if count > 1)
    foreign_ids = sorted(qid for qid in counts if qid not in expected_set)
    missing_ids = [qid for qid in expected_ids if qid not in counts]

    declared_source_contract = _json_dict(fragments_payload.get("source_contract"))
    current_source_contract = answer_source_contract(structured_exam)
    declared_question_fingerprints = _json_dict(declared_source_contract.get("question_fingerprints"))
    current_question_fingerprints = _json_dict(current_source_contract.get("question_fingerprints"))
    if not declared_source_contract:
        source_contract_status = "legacy_missing"
        source_mismatch_ids: list[str] = []
    elif str(declared_source_contract.get("version") or "") != ANSWER_SOURCE_CONTRACT_VERSION:
        source_contract_status = "unsupported_version"
        source_mismatch_ids = list(expected_ids)
    else:
        source_mismatch_ids = [
            qid
            for qid in expected_ids
            if not str(declared_question_fingerprints.get(qid) or "")
            or str(declared_question_fingerprints.get(qid) or "")
            != str(current_question_fingerprints.get(qid) or "")
        ]
        source_contract_status = "mismatched" if source_mismatch_ids else "matched"

    reusable_map = reusable_answer_fragment_map(stage_dir, structured_exam or {}, requested=True)
    for qid in duplicate_ids:
        reusable_map.pop(qid, None)
    reusable_ids = [qid for qid in expected_ids if qid in reusable_map]
    redrive_ids = [qid for qid in expected_ids if qid not in reusable_map]
    invalid_ids = [qid for qid in redrive_ids if qid not in missing_ids and qid not in duplicate_ids]

    succeeded_event_ids = {
        str(event.get("question_id") or "").strip()
        for event in _json_list(progress_payload.get("recent_events"))
        if isinstance(event, dict)
        and str(event.get("status") or "") == "succeeded"
        and str(event.get("question_id") or "").strip()
    }
    progress_total = int(progress_payload.get("total") or 0) if str(progress_payload.get("total") or "").isdigit() else 0
    progress_completed = (
        int(progress_payload.get("completed") or 0)
        if str(progress_payload.get("completed") or "").isdigit()
        else 0
    )
    inconsistencies = list(parse_issues)
    if progress_total and progress_total != len(expected_ids):
        inconsistencies.append(
            f"progress total {progress_total} differs from structured exam count {len(expected_ids)}"
        )
    if progress_payload and progress_completed != len(fragment_rows):
        inconsistencies.append(
            f"progress completed {progress_completed} differs from durable fragment count {len(fragment_rows)}"
        )
    if duplicate_ids:
        inconsistencies.append("duplicate fragment IDs: " + ",".join(duplicate_ids))
    if foreign_ids:
        inconsistencies.append("foreign fragment IDs: " + ",".join(foreign_ids))
    if source_contract_status == "legacy_missing" and fragments_path.exists():
        inconsistencies.append("answer checkpoint lacks source fingerprint; legacy semantic validation applied")
    elif source_mismatch_ids:
        inconsistencies.append("answer checkpoint source mismatch: " + ",".join(source_mismatch_ids))
    event_without_fragment = sorted(qid for qid in succeeded_event_ids if qid not in counts)
    if event_without_fragment:
        inconsistencies.append("recent succeeded events without durable fragments: " + ",".join(event_without_fragment))

    def modified_at(path: Path) -> str:
        if not path.exists():
            return ""
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

    structured_checkpoint_valid = not any(
        issue.startswith("structured_exam unreadable") for issue in parse_issues
    )
    answer_checkpoint_parse_valid = not any(
        issue.startswith("answer_fragments unreadable") for issue in parse_issues
    )
    safe_to_resume = bool(expected_ids) and structured_checkpoint_valid
    checkpoint_reuse_safe = safe_to_resume and answer_checkpoint_parse_valid
    if not safe_to_resume:
        resume_strategy = "rerun_upstream_before_answer_generation"
    elif not answer_checkpoint_parse_valid:
        resume_strategy = "discard_malformed_answer_checkpoint_and_regenerate"
    else:
        resume_strategy = "reuse_valid_regenerate_missing_or_invalid"
    report = {
        "schema_version": "answer_book.answer_checkpoint_reconciliation.v1",
        "ok": checkpoint_reuse_safe and not inconsistencies and not redrive_ids,
        "safe_to_resume": safe_to_resume,
        "checkpoint_reuse_safe": checkpoint_reuse_safe,
        "resume_strategy": resume_strategy,
        "expected_count": len(expected_ids),
        "fragment_count": len(fragment_rows),
        "unique_fragment_count": len(counts),
        "reusable_fragment_count": len(reusable_ids),
        "redrive_count": len(redrive_ids),
        "reusable_question_ids": reusable_ids,
        "redrive_question_ids": redrive_ids,
        "missing_question_ids": missing_ids,
        "invalid_question_ids": invalid_ids,
        "duplicate_question_ids": duplicate_ids,
        "foreign_question_ids": foreign_ids,
        "source_contract": {
            "status": source_contract_status,
            "version": str(declared_source_contract.get("version") or ""),
            "current_version": ANSWER_SOURCE_CONTRACT_VERSION,
            "mismatched_question_ids": source_mismatch_ids,
        },
        "progress": {
            "status": str(progress_payload.get("status") or ""),
            "total": progress_total,
            "completed": progress_completed,
            "current_question_id": str(progress_payload.get("current_question_id") or ""),
            "recent_succeeded_question_ids": sorted(succeeded_event_ids),
        },
        "artifact_modified_at": {
            "structured_exam": modified_at(structured_path),
            "answer_fragments": modified_at(fragments_path),
            "answer_generation_progress": modified_at(progress_path),
        },
        "inconsistencies": inconsistencies,
    }
    if output_json is not None:
        _write_json(output_json, report)
    return report


def migrate_legacy_answer_source_contract(
    stage_dir: Path,
    structured_exam: dict[str, Any],
) -> bool:
    """Fingerprint a legacy checkpoint only after full current validation."""

    path = stage_dir / "answer_fragments.json"
    if not path.exists() or not isinstance(structured_exam, dict):
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(payload, dict) or _json_dict(payload.get("source_contract")):
        return False
    if not answer_checkpoint_reusable(stage_dir, structured_exam, requested=True):
        return False
    payload["source_contract"] = answer_source_contract(structured_exam)
    recovery_events = _json_list(payload.get("recovery_events"))
    recovery_events.append({"strategy": "legacy_answer_source_contract_migration"})
    payload["recovery_events"] = recovery_events
    _write_json(path, payload)
    return True


def normalize_answer_checkpoint(
    stage_dir: Path,
    structured_exam: dict[str, Any],
) -> list[str]:
    """Migrate deterministic presentation-only issues in a saved checkpoint.

    A model candidate is never accepted merely because it exists.  We first
    promote formula/reaction prose into its already declared structured form,
    then remove a stale generation-review flag only when the current complete
    syntax and semantic validators both pass.  Scientific correctness remains
    the responsibility of the later selective correctness gate.
    """

    path = stage_dir / "answer_fragments.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    if not isinstance(structured_exam, dict):
        return []
    questions_by_id = {
        str(item.get("question_id") or "").strip(): item
        for item in _json_list(structured_exam.get("items"))
        if isinstance(item, dict)
    }
    migrated: list[str] = []
    current_issues: list[dict[str, Any]] = []
    for fragment in _json_list(payload.get("fragments")):
        if not isinstance(fragment, dict):
            continue
        qid = str(fragment.get("question_id") or "").strip()
        before = json.dumps(fragment, ensure_ascii=False, sort_keys=True)
        promote_inline_reactions(fragment)
        promote_inline_mathematical_expressions(fragment)
        apply_declared_ocr_corrections(fragment)
        issues = validate_v4_answer_fragment(fragment) + semantic_generation_issues(
            questions_by_id.get(qid, {}), fragment
        )
        if not issues:
            fragment["_review_flags"] = [
                flag
                for flag in fragment.get("_review_flags", []) or []
                if not isinstance(flag, dict)
                or str(flag.get("code") or "") != "answer_generation_review_candidate"
            ]
            if not fragment["_review_flags"]:
                fragment.pop("_review_flags", None)
            fragment.pop("_review_candidate_issues", None)
            fragment["warnings"] = [
                warning
                for warning in fragment.get("warnings", []) or []
                if "模型生成内容存在审查问题" not in str(warning)
            ]
            if not fragment["warnings"]:
                fragment.pop("warnings", None)
            meta = dict(_json_dict(fragment.get("_meta")))
            if str(meta.get("recovered_by") or "") == "review_candidate_preserved":
                meta.pop("recovered_by", None)
            if meta:
                fragment["_meta"] = meta
        else:
            current_issues.append({"question_id": qid, "issues": list(dict.fromkeys(issues))})
        if json.dumps(fragment, ensure_ascii=False, sort_keys=True) != before:
            migrated.append(qid)
    payload["issues"] = current_issues
    if migrated:
        recovery_events = _json_list(payload.get("recovery_events"))
        recovery_events.extend(
            {"question_id": qid, "strategy": "deterministic_expression_checkpoint_migration"}
            for qid in migrated
        )
        payload["recovery_events"] = recovery_events
        _write_json(path, payload)
    return migrated


def figure_schema_checkpoint_reusable(report: dict[str, Any], *, policy_version: str) -> bool:
    return isinstance(report, dict) and str(report.get("routing_policy_version") or "") == policy_version
