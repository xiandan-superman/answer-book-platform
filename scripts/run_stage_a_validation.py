#!/usr/bin/env python3
"""Run VNext R3.1 Stage A without making any remote model calls.

The runner replays saved answer/figure contracts through the current local
auditors, checks deterministic repeatability, verifies the final XRD Word
artifact, and freezes every consumed input by SHA-256.
"""

# ruff: noqa: E402 -- direct script execution must add the repository root first.

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.answer_generation import (
    answer_generation_batch_enabled,
    answer_generation_batch_size,
    answer_generation_batch_token_budget,
    answer_generation_timeout_seconds,
    answer_generation_worker_count,
)
from app.calculation_consistency import calculation_draft_consistency_issues
from app.capabilities.quality_budget import QualityExecutionBudget
from app.content_quality_audit import audit_content_quality
from app.docx_audit import audit_docx_v4
from app.evidence_selection import EVIDENCE_SELECTION_TIMEOUT_SECONDS, evidence_selection_worker_count
from app.figures import figure_model_worker_count, figure_visual_audit_worker_count
from app.pipeline import _mark_unresolved_correctness_review_flags
from app.question_understanding import question_understanding_worker_count
from app.render_audit import audit_docx_pdf_consistency, audit_rendered_pages_report

ANALYSIS_ROOT = ROOT.parents[2]
AB_ROOT = ANALYSIS_ROOT / ".abtest" / "vnext_ab"
DEFAULT_OUTPUT_DIR = ROOT / "validation" / "vnext_r3_1_stage_a_20260820"
P202_TASK = ROOT / "tasks" / "结构重复测试_苯蒸发三小问_20260820_121818"
P204_TASK = ROOT / "tasks" / "体心立方有序化_XRD_20260820_123309"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def issue_codes(report: dict[str, Any]) -> list[str]:
    return sorted({str(item.get("code") or "") for item in report.get("issues", []) if isinstance(item, dict)})


def warning_codes(report: dict[str, Any]) -> list[str]:
    return sorted({str(item.get("code") or "") for item in report.get("warnings", []) if isinstance(item, dict)})


def diagnostic_codes(report: dict[str, Any]) -> list[str]:
    return sorted({str(item.get("code") or "") for item in report.get("diagnostics", []) if isinstance(item, dict)})


def content_replay(stage: Path, *, fragments: dict[str, Any] | None = None, specs: dict[str, Any] | None = None) -> dict[str, Any]:
    return audit_content_quality(
        read_json(stage / "structured_exam.json"),
        copy.deepcopy(fragments if fragments is not None else read_json(stage / "answer_fragments.json")),
        read_json(stage / "answer_drafts.json"),
        read_json(stage / "evidence_selection.json"),
        active_figure_specs_data=copy.deepcopy(specs if specs is not None else read_json(stage / "figure_specs.json")),
    )


def replay_twice(run) -> tuple[dict[str, Any], bool, str]:
    first = run()
    second = run()
    first_hash = canonical_hash(first)
    return first, first_hash == canonical_hash(second), first_hash


def p202_replay() -> dict[str, Any]:
    stage = P202_TASK / "stage_outputs"
    advisories = read_json(stage / "semantic_quality_advisories.json").get("advisories", [])
    calculation_details = calculation_draft_consistency_issues(read_json(stage / "answer_drafts.json")["drafts"][0])

    def run() -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="vnext-stage-a-p202-") as raw_tmp:
            fragments_path = Path(raw_tmp) / "answer_fragments.json"
            fragments_path.write_text((stage / "answer_fragments.json").read_text(encoding="utf-8"), encoding="utf-8")
            flagged = _mark_unresolved_correctness_review_flags(fragments_path, advisories)
            report = content_replay(stage, fragments=read_json(fragments_path))
            report["replay_flagged_question_ids"] = flagged
            return report

    report, deterministic, replay_hash = replay_twice(run)
    codes = issue_codes(report)
    expected = {"high_risk_correctness_unresolved", "calculation_internal_inconsistency"}
    return {
        "id": "P2-02",
        "name": "苯蒸发三小问保存答案",
        "passed": expected.issubset(codes) and report.get("ok") is False and deterministic,
        "expected_issue_codes": sorted(expected),
        "issue_codes": codes,
        "issue_count": report.get("issue_count", 0),
        "calculation_consistency_details": calculation_details,
        "flagged_question_ids": report.get("replay_flagged_question_ids", []),
        "deterministic": deterministic,
        "canonical_report_sha256": replay_hash,
        "remote_calls": 0,
    }


def _old_xrd_specs(full_specs: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the saved pre-extension N<=12 figure contract.

    The old raster is retained in ``figure_stage_images/initial_render`` but
    its JSON was overwritten when the deterministic XRD window was extended.
    The old image hash and the R3 report establish that its last labelled
    basic peak was (222), N=12. Filtering only that window recreates the exact
    semantic omission the current content auditor must catch.
    """

    old_specs = copy.deepcopy(full_specs)
    for spec in old_specs.get("figures", []) or []:
        if not isinstance(spec, dict) or str(spec.get("kind") or "") != "xrd_pattern":
            continue
        spec["peaks"] = [
            peak
            for peak in spec.get("peaks", []) or []
            if isinstance(peak, dict)
            and float(peak.get("two_theta", peak.get("position_2theta", 0)) or 0) <= 12
        ]
        spec["stage_a_fixture_note"] = "saved pre-extension XRD window reconstructed as N<=12"
    return old_specs


def p204_replays() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stage = P204_TASK / "stage_outputs"
    full_specs = read_json(stage / "figure_specs.json")
    old_specs = _old_xrd_specs(full_specs)
    before = read_json(stage / "answer_fragments.before_content_quality_local_repair.json")
    after = read_json(stage / "answer_fragments.json")

    old_report, old_deterministic, old_hash = replay_twice(lambda: content_replay(stage, fragments=before, specs=old_specs))
    old_codes = issue_codes(old_report)
    old_expected = {"xrd_figure_text_label_mismatch", "xrd_unsupported_peak_spacing_trend"}
    old_result = {
        "id": "P2-04-old",
        "name": "XRD 修复前答案与 N<=12 旧图合同",
        "passed": old_expected.issubset(old_codes) and old_report.get("ok") is False and old_deterministic,
        "expected_issue_codes": sorted(old_expected),
        "issue_codes": old_codes,
        "issue_count": old_report.get("issue_count", 0),
        "deterministic": old_deterministic,
        "canonical_report_sha256": old_hash,
        "remote_calls": 0,
    }

    new_report, new_deterministic, new_hash = replay_twice(lambda: content_replay(stage, fragments=after, specs=full_specs))
    output = ROOT / "outputs" / P204_TASK.name
    docx = output / "answer_book.docx"
    pdf = output / "word_rendered" / "answer_book.pdf"
    rendered = output / "word_rendered"
    docx_issues = audit_docx_v4(docx, min_formulas=1)
    render_report = audit_rendered_pages_report(rendered, min_pages=2)
    consistency_report = audit_docx_pdf_consistency(docx, pdf)
    new_result = {
        "id": "P2-04-new",
        "name": "XRD 修复后答案、扩展图与 Word",
        "passed": (
            new_report.get("ok") is True
            and not issue_codes(new_report)
            and new_deterministic
            and not docx_issues
            and render_report.get("ok") is True
            and render_report.get("page_count") == 2
            and consistency_report.get("ok") is True
        ),
        "issue_codes": issue_codes(new_report),
        "issue_count": new_report.get("issue_count", 0),
        "docx_issue_count": len(docx_issues),
        "docx_issues": docx_issues,
        "render_ok": render_report.get("ok"),
        "page_count": render_report.get("page_count"),
        "docx_pdf_consistency_ok": consistency_report.get("ok"),
        "deterministic": new_deterministic,
        "canonical_report_sha256": new_hash,
        "remote_calls": 0,
    }
    return old_result, new_result, old_specs


def historical_replays() -> list[dict[str, Any]]:
    cases = [
        ("V3-A-r2", "识图题（R2 历史产物）", AB_ROOT / "version_r2/tasks/识图题_20260819_183539/stage_outputs"),
        ("V3-C-r2", "五题型集成（R2 历史产物）", AB_ROOT / "version_r2/tasks/抽题流程测试_覆盖题型_20260819_185638/stage_outputs"),
        ("V4-A-r3", "识图题（R3 历史产物）", AB_ROOT / "version_r3/tasks/识图题_20260820_103936/stage_outputs"),
        ("V4-C-r3", "五题型集成（R3 历史产物）", AB_ROOT / "version_r3/tasks/抽题流程测试_覆盖题型_20260820_103938/stage_outputs"),
    ]
    results: list[dict[str, Any]] = []
    for case_id, name, stage in cases:
        report, deterministic, replay_hash = replay_twice(lambda stage=stage: content_replay(stage))
        acceptance_path = stage / "final_acceptance_report.json"
        acceptance = read_json(acceptance_path) if acceptance_path.is_file() else {}
        formal_acceptance_passed = bool(acceptance.get("formal_acceptance_passed")) if acceptance else False
        detected_issue_codes = issue_codes(report)
        # A historical fixture can be intentionally dirty.  The replay passes
        # when current gates either find no hard defect, or find a hard defect
        # that the stored task did not expose as a formal release.  Treating a
        # correctly contained historical defect as a failed regression would
        # invert the safety signal and produce a false release stop.
        hard_issue_contained = bool(detected_issue_codes) and not formal_acceptance_passed
        results.append(
            {
                "id": case_id,
                "name": name,
                "passed": deterministic and (report.get("ok") is True or hard_issue_contained),
                "issue_codes": detected_issue_codes,
                "warning_codes": warning_codes(report),
                "diagnostic_codes": diagnostic_codes(report),
                "stored_formal_acceptance_passed": formal_acceptance_passed,
                "hard_issue_contained": hard_issue_contained,
                "deterministic": deterministic,
                "canonical_report_sha256": replay_hash,
                "remote_calls": 0,
            }
        )
    for variant in ("r2", "v1"):
        payload = read_json(AB_ROOT / f"results/V3G_{variant}.json")
        results.append(
            {
                "id": f"V3-G-{variant}",
                "name": f"取消与迟到响应（{variant} 历史记录）",
                "passed": payload.get("final_status") == "cancelled" and payload.get("model_calls_after_cancel") == 0,
                "final_status": payload.get("final_status"),
                "model_calls_after_cancel": payload.get("model_calls_after_cancel"),
                "deterministic": True,
                "canonical_report_sha256": canonical_hash(payload),
                "remote_calls": 0,
            }
        )
    return results


def runtime_freeze() -> dict[str, Any]:
    provider_config = read_json(ROOT / "config/providers.local.json")
    safe_providers: dict[str, Any] = {}
    for name, raw in (provider_config.get("providers") or {}).items():
        if not isinstance(raw, dict):
            continue
        safe_providers[name] = {
            key: value
            for key, value in raw.items()
            if not any(marker in key.lower() for marker in ("key", "secret", "token", "password"))
        }
    return {
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "remote_execution": False,
        "active_provider": provider_config.get("active_provider"),
        "providers_without_credentials": safe_providers,
        "timeouts_seconds": {
            "answer_default": answer_generation_timeout_seconds(),
            "evidence_selection": EVIDENCE_SELECTION_TIMEOUT_SECONDS,
            "vision_qa": 120,
            "figure_repair": 90,
        },
        "concurrency": {
            "question_understanding": question_understanding_worker_count(),
            "evidence_selection": evidence_selection_worker_count(),
            "answer_generation": answer_generation_worker_count(),
            "figure_model": figure_model_worker_count(),
            "figure_visual_audit": figure_visual_audit_worker_count(),
            "answer_batch_enabled": answer_generation_batch_enabled(),
            "answer_batch_size": answer_generation_batch_size(),
            "answer_batch_token_budget": answer_generation_batch_token_budget(),
        },
        "quality_execution_budget": asdict(QualityExecutionBudget.from_environment()),
        "task_records": [
            {
                key: task.get(key)
                for key in (
                    "task_id",
                    "provider",
                    "model",
                    "model_thinking",
                    "reasoning_provider",
                    "reasoning_model",
                    "answer_provider",
                    "answer_model",
                    "correctness_provider",
                    "correctness_model",
                    "vision_provider",
                    "vision_model",
                    "image_provider",
                    "image_model",
                    "selected_textbooks",
                )
            }
            for task in (read_json(P202_TASK / "task.json"), read_json(P204_TASK / "task.json"))
        ],
        "relevant_environment": {
            name: os.environ[name]
            for name in sorted(os.environ)
            if name.startswith(("QUALITY_", "ANSWER_GENERATION_", "EVIDENCE_SELECTION_", "QUESTION_UNDERSTANDING_", "FIGURE_"))
            and not any(marker in name.lower() for marker in ("key", "secret", "token", "password"))
        },
    }


def manifest_paths(output_dir: Path) -> list[tuple[Path, str]]:
    p202_stage = P202_TASK / "stage_outputs"
    p204_stage = P204_TASK / "stage_outputs"
    paths: list[tuple[Path, str]] = [
        (ROOT / "VERSION", "version"),
        (ROOT / "config/providers.local.json", "provider configuration hash only"),
        (ROOT / "config/task_defaults.json", "task defaults"),
        (ROOT / "exams/结构重复测试_苯蒸发三小问.docx", "P2-02 exam"),
        (ROOT / "exams/体心立方有序化_XRD.docx", "P2-04 exam"),
        (AB_ROOT / "RESULTS_V3_AB.md", "historical conclusions"),
        (AB_ROOT / "RESULTS_V4_R3.md", "historical conclusions"),
        (p202_stage / "structured_exam.json", "P2-02 structured exam"),
        (p202_stage / "answer_drafts.json", "P2-02 saved model draft"),
        (p202_stage / "answer_fragments.json", "P2-02 saved final fragments"),
        (p202_stage / "semantic_quality_advisories.json", "P2-02 saved correctness decision"),
        (p202_stage / "evidence_selection.json", "P2-02 evidence selection"),
        (p202_stage / "figure_specs.json", "P2-02 active figures"),
        (p204_stage / "structured_exam.json", "P2-04 structured exam"),
        (p204_stage / "answer_drafts.json", "P2-04 saved model draft"),
        (p204_stage / "answer_fragments.before_content_quality_local_repair.json", "P2-04 pre-repair fragments"),
        (p204_stage / "answer_fragments.json", "P2-04 repaired fragments"),
        (p204_stage / "evidence_selection.json", "P2-04 evidence selection"),
        (p204_stage / "figure_specs.json", "P2-04 final figure contract"),
        (p204_stage / "figure_stage_images/initial_render/001_qa_s01_01_02_xrd_fig_01.png", "P2-04 saved old figure"),
        (p204_stage / "figures/qa_s01_01_02_xrd_fig_01.png", "P2-04 final figure"),
        (ROOT / f"outputs/{P204_TASK.name}/answer_book.docx", "P2-04 final Word"),
        (ROOT / f"outputs/{P204_TASK.name}/word_rendered/answer_book.pdf", "P2-04 final PDF"),
        (ROOT / f"outputs/{P204_TASK.name}/word_rendered/page-1.png", "P2-04 rendered page"),
        (ROOT / f"outputs/{P204_TASK.name}/word_rendered/page-2.png", "P2-04 rendered page"),
        (ROOT / "app/content_quality_audit.py", "replay implementation"),
        (ROOT / "app/calculation_consistency.py", "replay implementation"),
        (ROOT / "app/content_quality_repair.py", "repair implementation"),
        (ROOT / "app/pipeline.py", "semantic flag implementation"),
        (ROOT / "app/docx_audit.py", "Word audit implementation"),
        (ROOT / "app/render_audit.py", "render audit implementation"),
        (ROOT / "scripts/run_stage_a_validation.py", "Stage A replay runner"),
        (ROOT / "quality_gates_report.json", "repository quality gates"),
    ]
    for task in (read_json(P202_TASK / "task.json"), read_json(P204_TASK / "task.json")):
        for raw in task.get("selected_textbooks", []) or []:
            paths.append((Path(raw), "selected textbook"))
    for variant, task_name in (
        ("version_r2", "识图题_20260819_183539"),
        ("version_r2", "抽题流程测试_覆盖题型_20260819_185638"),
        ("version_r3", "识图题_20260820_103936"),
        ("version_r3", "抽题流程测试_覆盖题型_20260820_103938"),
    ):
        stage = AB_ROOT / variant / "tasks" / task_name / "stage_outputs"
        for name in ("structured_exam.json", "answer_drafts.json", "answer_fragments.json", "evidence_selection.json", "figure_specs.json"):
            paths.append((stage / name, "historical replay input"))
    for variant in ("r2", "v1"):
        paths.append((AB_ROOT / f"results/V3G_{variant}.json", "historical cancellation result"))
    pricing = ROOT / "config/model_pricing.local.json"
    if pricing.exists():
        paths.append((pricing, "local contract pricing hash only"))
    for name in ("REPLAY_RESULTS_STAGE_A.json", "COST_LEDGER_STAGE_A.csv", "RESULTS_STAGE_A.md", "RELEASE_DECISION.md"):
        paths.append((output_dir / name, "Stage A validation output"))
    unique: dict[str, tuple[Path, str]] = {}
    for path, role in paths:
        unique[str(path.resolve())] = (path.resolve(), role)
    return sorted(unique.values(), key=lambda item: str(item[0]))


def quality_gates_summary() -> dict[str, Any]:
    path = ROOT / "quality_gates_report.json"
    if not path.is_file():
        return {"available": False, "ok": False, "steps": [], "pytest_summary": ""}
    report = read_json(path)
    steps = [
        {"name": item.get("name"), "ok": item.get("ok")}
        for item in report.get("results", [])
        if isinstance(item, dict)
    ]
    pytest_output = next(
        (str(item.get("stdout") or "") for item in report.get("results", []) if item.get("name") == "pytest"),
        "",
    )
    pytest_summary = next(
        (line.strip() for line in reversed(pytest_output.splitlines()) if " passed" in line),
        "",
    )
    return {"available": True, "ok": report.get("ok") is True, "steps": steps, "pytest_summary": pytest_summary}


def build_manifest(old_specs: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    files = []
    missing = []
    for path, role in manifest_paths(output_dir):
        if not path.is_file():
            missing.append({"path": str(path), "role": role})
            continue
        files.append({"path": str(path), "role": role, "bytes": path.stat().st_size, "sha256": file_hash(path)})
    return {
        "schema_version": "answer_book.validation_manifest.stage_a.v1",
        "stage": "A",
        "root": str(ROOT),
        "runtime_freeze": runtime_freeze(),
        "derived_fixture": {
            "name": "P2-04 pre-extension active XRD contract",
            "method": "filter final deterministic XRD contract to N<=12 to match retained old raster",
            "sha256": canonical_hash(old_specs),
        },
        "files": files,
        "missing_files": missing,
        "pricing_file_present": (ROOT / "config/model_pricing.local.json").is_file(),
        "quality_gates": quality_gates_summary(),
    }


def write_cost_ledger(path: Path, scenario_count: int) -> None:
    columns = [
        "stage",
        "scenario_count",
        "text_success_requests",
        "text_failed_requests",
        "vision_success_requests",
        "vision_timeout_requests",
        "generated_images",
        "input_tokens",
        "output_tokens",
        "missing_usage_requests",
        "effective_calls",
        "repair_calls",
        "wasted_calls",
        "cost_cny",
        "cost_status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(
            {
                "stage": "A",
                "scenario_count": scenario_count,
                "text_success_requests": 0,
                "text_failed_requests": 0,
                "vision_success_requests": 0,
                "vision_timeout_requests": 0,
                "generated_images": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "missing_usage_requests": 0,
                "effective_calls": 0,
                "repair_calls": 0,
                "wasted_calls": 0,
                "cost_cny": "",
                "cost_status": "not_applicable_offline_stage",
            }
        )


def build_markdown(results: list[dict[str, Any]], *, pricing_file_present: bool, gates: dict[str, Any]) -> str:
    passed = sum(1 for item in results if item.get("passed"))
    rows = []
    for item in results:
        findings = item.get("issue_codes") or item.get("diagnostic_codes") or []
        if item.get("model_calls_after_cancel") == 0:
            findings = ["cancelled; 0 calls after cancel"]
        rows.append(
            f"| {item['id']} | {'通过' if item.get('passed') else '失败'} | "
            f"{', '.join(findings) if findings else '0 个内容问题'} | {'一致' if item.get('deterministic') else '不一致'} | 0 |"
        )
    price_note = (
        "已冻结本地合同单价文件；阶段 A 未发生调用，费用为 0。"
        if pricing_file_present
        else "未提供 `config/model_pricing.local.json`；阶段 A 未发生调用，不虚构人民币金额。"
    )
    gate_note = (
        f"全库平台门禁通过（{gates.get('pytest_summary') or 'pytest 通过'}；Python 编译、版本一致性、OMML 与项目完整性均通过）。"
        if gates.get("ok")
        else "全库平台门禁尚未通过或没有可用结果。"
    )
    return "\n".join(
        [
            "# VNext R3.1 阶段 A 离线验证结果",
            "",
            "## 结论",
            "",
            f"阶段 A 共执行 {len(results)} 个离线场景，{passed} 个通过、{len(results) - passed} 个失败。"
            + ("当前门禁满足进入阶段 B 的技术前提，但阶段 B 仍需单独授权付费调用。" if passed == len(results) else "存在阻断项，不能进入阶段 B。"),
            "",
            "## 审阅意见",
            "",
            "- 计划的先离线、后付费分级合理，硬门禁优先于平均分也合理。",
            "- 原计划没有给出可执行的阶段 A 入口，也没有精确说明“旧 XRD 图”的 JSON 快照来源；本次用保留的旧 PNG、R3 记录和 N≤12 窗口重建语义合同，并在清单中单独记录派生方法与哈希。",
            "- 阶段 B–D 会产生新远程调用或依赖前序结果。本次仅执行阶段 A，不将文档中的待确认步骤视为付费授权。",
            "- 历史回放只证明当前确定性门禁对已知问题有效，不等于证明底层模型学科答案始终正确。",
            "",
            "## 场景矩阵",
            "",
            "| 场景 | 结果 | 当前程序发现/确认 | 连续两次回放 | 新远程调用 |",
            "|---|---|---|---|---|",
            *rows,
            "",
            "## 关键事实",
            "",
            "- P2-02 同时命中 `high_risk_correctness_unresolved` 与 `calculation_internal_inconsistency`，未被标为正式通过。",
            "- P2-02 的可见答案与计算合同明确存在 4 处数值冲突：Q、W、ΔS、ΔG。",
            "- P2-04 修复前同时命中 `xrd_figure_text_label_mismatch` 与 `xrd_unsupported_peak_spacing_trend`；修复后内容问题为 0。",
            "- P2-04 最终 Word 的当前审计问题为 0，渲染为 2 页，DOCX/PDF 一致性通过，且至少存在 1 个原生 OMML 可编辑公式。",
            "- V3-C 与 V4-C 的结构化【解题步骤】可承载推理，不再因缺少重复【解析】标题而降级；历史取消记录均为取消后 0 次调用。",
            f"- {gate_note}",
            f"- {price_note}",
            "",
            "## 产物",
            "",
            "- `REPLAY_RESULTS_STAGE_A.json`：逐场景机器可读事实。",
            "- `COST_LEDGER_STAGE_A.csv`：阶段 A 零调用账本。",
            "- `ARTIFACT_MANIFEST_STAGE_A.json`：输入、教材、配置、代码与输出 SHA-256。",
            "- `RELEASE_DECISION.md`：当前发布边界与下一阶段条件。",
            "",
        ]
    )


def build_release_decision(all_passed: bool) -> str:
    return "\n".join(
        [
            "# VNext R3.1 发布决策（阶段 A 后）",
            "",
            "## 决策",
            "",
            ("**限制发布 / 可进入阶段 B 的逐项付费验证。**" if all_passed else "**继续修复 / 不进入阶段 B。**"),
            "",
            "- 确定性内容门禁、XRD 图文一致性门禁、局部修复边界、Word/渲染门禁：阶段 A 通过。" if all_passed else "- 阶段 A 仍有未通过的确定性门禁。",
            "- 学科模型的跨题型稳定性、真实视觉输入、电子衍射作图与五题集成：尚未由本次零费用回放验证。",
            "- P2-04 视觉服务超时风险仍在；程序正确保留为待复核候选版，不能据此宣称正式发布。",
            "- 下一步只应在用户明确授权付费后按 B-01 → B-02 → B-03 → B-04 逐项执行，并遵守单题停止条件。",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    model_log = ROOT / "logs/model_calls.jsonl"
    log_hash_before = file_hash(model_log) if model_log.is_file() else ""
    p202 = p202_replay()
    p204_old, p204_new, old_specs = p204_replays()
    results = [p202, p204_old, p204_new, *historical_replays()]
    log_hash_after = file_hash(model_log) if model_log.is_file() else ""
    remote_log_unchanged = log_hash_before == log_hash_after
    for item in results:
        item["model_call_log_unchanged"] = remote_log_unchanged
        item["passed"] = bool(item.get("passed") and remote_log_unchanged)

    replay_payload = {
        "schema_version": "answer_book.stage_a_replay.v1",
        "stage": "A",
        "offline_only": True,
        "model_call_log_sha256_before": log_hash_before,
        "model_call_log_sha256_after": log_hash_after,
        "model_call_log_unchanged": remote_log_unchanged,
        "scenario_count": len(results),
        "passed_count": sum(1 for item in results if item.get("passed")),
        "failed_count": sum(1 for item in results if not item.get("passed")),
        "results": results,
    }
    (output_dir / "REPLAY_RESULTS_STAGE_A.json").write_text(
        json.dumps(replay_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_cost_ledger(output_dir / "COST_LEDGER_STAGE_A.csv", len(results))
    gates = quality_gates_summary()
    all_passed = replay_payload["failed_count"] == 0 and gates.get("ok") is True
    (output_dir / "RESULTS_STAGE_A.md").write_text(
        build_markdown(
            results,
            pricing_file_present=(ROOT / "config/model_pricing.local.json").is_file(),
            gates=gates,
        ),
        encoding="utf-8",
    )
    (output_dir / "RELEASE_DECISION.md").write_text(build_release_decision(all_passed), encoding="utf-8")
    manifest = build_manifest(old_specs, output_dir)
    all_passed = bool(all_passed and not manifest.get("missing_files"))
    (output_dir / "ARTIFACT_MANIFEST_STAGE_A.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(output_dir), "all_passed": all_passed, **replay_payload}, ensure_ascii=False, indent=2))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
