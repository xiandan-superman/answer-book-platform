from __future__ import annotations

import json
import os
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..paths import CACHE_DIR, TASKS_DIR
from .quality_governance import governance_for, unattended_status
from .shadow_quality import evaluate_shadow_quality

METRICS_SCHEMA_VERSION = "answer_book.quality_metrics.v2"
METRICS_CACHE_VERSION = 2
DEFAULT_METRICS_CACHE = CACHE_DIR / "quality_metrics_cache.json"
_METRICS_LOCK = threading.RLock()

SHADOW_INPUT_FILENAMES = (
    "academic_expression_audit.json",
    "selective_quality_review.json",
    "content_quality_audit.json",
    "docx_audit.json",
    "figure_size_audit.json",
    "render_audit.json",
    "figure_generation_audit.json",
    "figure_visual_qa.json",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _fingerprint(paths: Iterable[Path]) -> str:
    rows: list[str] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(sorted(rows))


def _task_source_paths(task_path: Path) -> list[Path]:
    stage = task_path / "stage_outputs"
    names = ("quality_shadow_report.json", *SHADOW_INPUT_FILENAMES)
    paths = [
        task_path / "task.json",
        task_path / "review_decision_request.json",
        task_path / "review_decision_response.json",
        stage / "user_allowed_audit_issues.json",
    ]
    paths.extend(stage / name for name in names)
    return [path for path in paths if path.exists()]


def _human_review_counts(task_path: Path) -> tuple[Counter[str], Counter[str], Counter[str]]:
    reviewed: Counter[str] = Counter()
    allowed: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    decisions: list[tuple[str, str, str, list[Any]]] = []
    data = _read_json(task_path / "stage_outputs" / "user_allowed_audit_issues.json") or {}
    for entry in data.get("entries", []) if isinstance(data.get("entries"), list) else []:
        if not isinstance(entry, dict):
            continue
        decisions.append(
            (
                str(entry.get("request_id") or ""),
                str(entry.get("stage") or "audit").strip() or "audit",
                str(entry.get("decision") or ""),
                entry.get("items", []) if isinstance(entry.get("items"), list) else [],
            )
        )
    request = _read_json(task_path / "review_decision_request.json") or {}
    response = _read_json(task_path / "review_decision_response.json") or {}
    if request.get("request_id") and request.get("request_id") == response.get("request_id"):
        decisions.append(
            (
                str(request["request_id"]),
                str(request.get("stage") or "audit").strip() or "audit",
                str(response.get("decision") or ""),
                request.get("items", []) if isinstance(request.get("items"), list) else [],
            )
        )
    seen: set[tuple[str, str, str, str]] = set()
    for request_id, stage, decision, items in decisions:
        if "_auto_" in request_id or decision not in {"allow", "reject"}:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_code = str(item.get("code") or "audit_issue").strip() or "audit_issue"
            code = raw_code if raw_code.startswith(f"{stage}.") else f"{stage}.{raw_code}"
            identity = (request_id, code, str(item.get("question_id") or ""), str(item.get("message") or ""))
            if identity in seen:
                continue
            seen.add(identity)
            reviewed[code] += 1
            if decision == "allow":
                allowed[code] += 1
            else:
                rejected[code] += 1
    return reviewed, allowed, rejected


def _load_task_sample(task_path: Path) -> dict[str, Any] | None:
    task = _read_json(task_path / "task.json")
    if task is None:
        return None
    stage = task_path / "stage_outputs"
    shadow_path = stage / "quality_shadow_report.json"
    report = _read_json(shadow_path)
    input_paths = [stage / name for name in SHADOW_INPUT_FILENAMES if (stage / name).exists()]
    shadow_is_stale = bool(
        shadow_path.exists()
        and input_paths
        and any(path.stat().st_mtime_ns > shadow_path.stat().st_mtime_ns for path in input_paths)
    )
    if report is None or shadow_is_stale:
        report = evaluate_shadow_quality(stage)
    raw_findings = report.get("findings")
    findings: list[Any] = raw_findings if isinstance(raw_findings, list) else []
    compact_findings = [
        {
            "code": str(finding.get("code") or "unknown.audit_issue"),
            "source": str(finding.get("source") or "unknown"),
            "action": str(finding.get("action") or "ignore"),
            "confidence": float(finding.get("confidence", 0.0) or 0.0),
            "subject_id": str(finding.get("subject_id") or ""),
        }
        for finding in findings
        if isinstance(finding, dict)
    ]
    reviewed, allowed, rejected = _human_review_counts(task_path)
    return {
        "task_id": str(task.get("task_id") or task_path.name),
        "task_status": str(task.get("status") or "unknown"),
        "updated_at": str(task.get("updated_at") or ""),
        "finding_count": len(compact_findings),
        "would_block_count": int(report.get("would_block_count", 0) or 0),
        "would_warn_count": int(report.get("would_warn_count", 0) or 0),
        "findings": compact_findings,
        "human_reviewed_counts": dict(reviewed),
        "human_allowed_counts": dict(allowed),
        "human_rejected_counts": dict(rejected),
    }


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_cache(cache_path: Path) -> dict[str, Any]:
    cache = _read_json(cache_path) or {}
    if cache.get("cache_version") != METRICS_CACHE_VERSION or not isinstance(cache.get("tasks"), dict):
        return {"cache_version": METRICS_CACHE_VERSION, "tasks": {}}
    return cache


def _aggregate(samples: list[dict[str, Any]], *, cache_stats: dict[str, int]) -> dict[str, Any]:
    task_count = len(samples)
    task_status_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    rule_occurrences: Counter[str] = Counter()
    rule_subjects: dict[str, set[str]] = defaultdict(set)
    rule_tasks: dict[str, set[str]] = defaultdict(set)
    rule_confidence_sum: defaultdict[str, float] = defaultdict(float)
    rule_human_reviewed: Counter[str] = Counter()
    rule_human_allowed: Counter[str] = Counter()
    rule_human_rejected: Counter[str] = Counter()
    rule_actions: dict[str, Counter[str]] = defaultdict(Counter)
    rule_observations: dict[str, set[str]] = defaultdict(set)
    tasks_with_findings = 0
    tasks_would_block = 0
    total_findings = 0

    for sample in samples:
        task_id = str(sample.get("task_id") or "")
        task_status_counts[str(sample.get("task_status") or "unknown")] += 1
        raw_findings = sample.get("findings")
        findings: list[Any] = raw_findings if isinstance(raw_findings, list) else []
        if findings:
            tasks_with_findings += 1
        if int(sample.get("would_block_count", 0) or 0):
            tasks_would_block += 1
        total_findings += len(findings)
        rule_human_reviewed.update(sample.get("human_reviewed_counts") or {})
        rule_human_allowed.update(sample.get("human_allowed_counts") or {})
        rule_human_rejected.update(sample.get("human_rejected_counts") or {})
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            code = str(finding.get("code") or "unknown.audit_issue")
            source = str(finding.get("source") or "unknown")
            action = str(finding.get("action") or "ignore")
            subject_id = str(finding.get("subject_id") or "")
            rule_occurrences[code] += 1
            rule_tasks[code].add(task_id)
            if subject_id:
                rule_subjects[code].add(f"{task_id}:{subject_id}")
            rule_observations[code].add(f"{task_id}:{subject_id}" if subject_id else task_id)
            rule_confidence_sum[code] += float(finding.get("confidence", 0.0) or 0.0)
            source_counts[source] += 1
            action_counts[action] += 1
            rule_actions[code][action] += 1

    rules: list[dict[str, Any]] = []
    for code, occurrence_count in rule_occurrences.most_common():
        affected_task_count = len(rule_tasks[code])
        human_review_count = rule_human_reviewed[code]
        human_allowed_count = rule_human_allowed[code]
        human_rejected_count = rule_human_rejected[code]
        governance = governance_for(code)
        status, status_reasons = unattended_status(governance)
        rules.append(
            {
                "code": code,
                "occurrence_count": occurrence_count,
                "affected_task_count": affected_task_count,
                "affected_task_rate": round(affected_task_count / task_count, 4) if task_count else 0.0,
                "affected_subject_count": len(rule_subjects[code]),
                "duplicate_count": max(0, occurrence_count - len(rule_observations[code])),
                "average_confidence": round(rule_confidence_sum[code] / occurrence_count, 4),
                "action_counts": dict(sorted(rule_actions[code].items())),
                "human_review_count": human_review_count,
                "human_allowed_count": human_allowed_count,
                "human_rejected_count": human_rejected_count,
                "human_allowed_rate": round(human_allowed_count / human_review_count, 4) if human_review_count else None,
                "human_review_is_optional_telemetry": True,
                **governance.to_dict(),
                "unattended_status": status,
                "status_reasons": status_reasons,
                "promotion_status": "not_applicable_unattended",
                "promotion_reasons": ["rule_actions_are_capped_by_machine_verifiability"],
            }
        )

    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "observation_only": True,
        "automatic_promotion_enabled": False,
        "governance_mode": "unattended",
        "human_review_required": False,
        "task_count": task_count,
        "tasks_with_findings": tasks_with_findings,
        "tasks_would_block": tasks_would_block,
        "task_would_block_rate": round(tasks_would_block / task_count, 4) if task_count else 0.0,
        "finding_count": total_findings,
        "task_status_counts": dict(sorted(task_status_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "rule_count": len(rules),
        "rules": rules,
        "cache": cache_stats,
    }


def build_quality_metrics_report(
    tasks_dir: Path = TASKS_DIR,
    *,
    cache_path: Path | None = DEFAULT_METRICS_CACHE,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Aggregate per-task shadow reports without modifying any task artifacts."""

    with _METRICS_LOCK:
        cache = _load_cache(cache_path) if use_cache and cache_path is not None else {"cache_version": METRICS_CACHE_VERSION, "tasks": {}}
        raw_cached_tasks = cache.get("tasks")
        cached_tasks: dict[str, Any] = raw_cached_tasks if isinstance(raw_cached_tasks, dict) else {}
        next_cache_tasks: dict[str, Any] = {}
        samples: list[dict[str, Any]] = []
        reused_count = 0
        parsed_count = 0
        skipped_count = 0
        task_paths = sorted((path for path in tasks_dir.iterdir() if path.is_dir()), key=lambda path: path.name) if tasks_dir.exists() else []
        for task_path in task_paths:
            source_paths = _task_source_paths(task_path)
            fingerprint = _fingerprint(source_paths)
            raw_cached = cached_tasks.get(task_path.name)
            cached: dict[str, Any] | None = raw_cached if isinstance(raw_cached, dict) else None
            if use_cache and cached and cached.get("fingerprint") == fingerprint and isinstance(cached.get("sample"), dict):
                sample = cached["sample"]
                reused_count += 1
            else:
                sample = _load_task_sample(task_path)
                parsed_count += 1
            if not isinstance(sample, dict):
                skipped_count += 1
                continue
            samples.append(sample)
            next_cache_tasks[task_path.name] = {"fingerprint": fingerprint, "sample": sample}
        report = _aggregate(
            samples,
            cache_stats={
                "enabled": bool(use_cache and cache_path is not None),
                "reused_task_count": reused_count,
                "parsed_task_count": parsed_count,
                "skipped_task_count": skipped_count,
                "pruned_task_count": len(set(cached_tasks) - set(next_cache_tasks)),
            },
        )
        if use_cache and cache_path is not None:
            _atomic_write_json(cache_path, {"cache_version": METRICS_CACHE_VERSION, "tasks": next_cache_tasks})
        return report
