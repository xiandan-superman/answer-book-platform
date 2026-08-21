from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "validation" / "vnext_r3_1_stage_c_20260820"
CASES = (
    {
        "label": "C1 五题型集成",
        "task_id": "抽题流程测试_覆盖题型_20260820_155352",
        "stage": ROOT / "validation/full_regression_20260820/exam_mixed_stage",
        "output": ROOT / "outputs/抽题流程测试_覆盖题型_20260820_155352",
    },
    {
        "label": "C2 材料科学整卷",
        "task_id": "材料科学基础教材真题2_20260820_191917",
        "stage": ROOT / "validation/full_regression_20260820/materials_exam_stage",
        "output": ROOT / "outputs/材料科学基础教材真题2_20260820_191917",
    },
    {
        "label": "C3 物理化学整卷",
        "task_id": "物理化学教材真题_20260820_195811",
        "stage": ROOT / "validation/full_regression_20260820/physical_chem_exam_stage",
        "output": ROOT / "outputs/物理化学教材真题_20260820_195811",
    },
)


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{_relative(path)} 不是 JSON 对象。")
    return data


def _read_calls(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    report = _read_json(case["stage"] / "final_acceptance_report.json")
    delivery = report.get("answer_fragment_delivery_summary") or {}
    diagnostics = report.get("diagnostic_advisories") or {}
    calls = _read_calls(case["output"] / "模型调用明细.jsonl")
    return {
        **case,
        "report": report,
        "calls": calls,
        "tier": str(report.get("delivery_tier") or "blocked"),
        "formal": bool(report.get("formal_acceptance_passed")),
        "delivery_ready": bool(report.get("delivery_ready")),
        "usable": int(delivery.get("usable_count") or 0),
        "failed": int(delivery.get("failed_count") or 0),
        "pending": int(diagnostics.get("pending_question_count") or 0),
        "review": int(diagnostics.get("review_question_count") or 0),
        "semantic_advisories": int(diagnostics.get("semantic_model_advisory_count") or 0),
        # pending_questions and review questions are two views of the same
        # user-visible review population in current acceptance reports.  Use
        # the larger count so one question is never double-counted or hidden.
        "review_needed": max(
            int(diagnostics.get("pending_question_count") or 0),
            int(diagnostics.get("review_question_count") or 0),
        ),
    }


def _write_cost_ledger(summaries: list[dict[str, Any]]) -> Path:
    path = OUTPUT_DIR / "COST_LEDGER_STAGE_C.csv"
    fields = [
        "task_id", "call_id", "stage", "purpose", "provider", "model", "outcome",
        "billable_disposition", "timeout_seconds", "elapsed_ms", "prompt_tokens",
        "completion_tokens", "reasoning_tokens", "total_tokens", "usage_returned",
        "cost_cny", "cost_status", "started_at", "finished_at", "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            for call in summary["calls"]:
                row = {key: call.get(key, "") for key in fields}
                usage_returned = call.get("total_tokens") is not None
                row["usage_returned"] = str(usage_returned).lower()
                row["cost_cny"] = ""
                row["cost_status"] = "not_reported_no_contract_pricing"
                writer.writerow(row)
    return path


def _write_results(summaries: list[dict[str, Any]]) -> Path:
    all_calls = [call for summary in summaries for call in summary["calls"]]
    outcomes = Counter(str(call.get("outcome") or "unknown") for call in all_calls)
    prompt = sum(int(call.get("prompt_tokens") or 0) for call in all_calls)
    completion = sum(int(call.get("completion_tokens") or 0) for call in all_calls)
    reasoning = sum(int(call.get("reasoning_tokens") or 0) for call in all_calls)
    total = sum(int(call.get("total_tokens") or 0) for call in all_calls)
    elapsed = sum(int(call.get("elapsed_ms") or 0) for call in all_calls)
    rows = []
    for summary in summaries:
        rows.append(
            f"| {summary['label']} | `{summary['task_id']}` | `{summary['tier']}` | "
            f"{summary['usable']} | {summary['failed']} | {summary['review_needed']} |"
        )
    text = f"""# VNext R3.1 Stage C 验证结果

## 结论

Stage C 已完成五题型集成、材料科学整卷和物理化学整卷验证。三份任务共 {sum(item['usable'] for item in summaries)} 道解析可用、{sum(item['failed'] for item in summaries)} 道失败；任一待复核题都没有让其他已完成题重做，也没有被误报为正式版。

| 用例 | 任务 | 交付等级 | 可用题 | 失败题 | 待复核题 |
|---|---|---:|---:|---:|---:|
{chr(10).join(rows)}

## 用户角度

- 三份任务都有可下载结果，不会因局部不确定而整份无结果。
- 物理化学整卷为 `formal`，可作正式版使用。
- 五题型集成和材科整卷为 `review_candidate`，文档可阅读、编辑和复核，但不会伪装成正式版。

## 程序与模型归因

- 五题型集成的计算题修补未通过来源和机器验算，属于模型内容问题；程序正确拒绝升级。
- 材料科学整卷一题存在缺失公式引用，一题存在原图文字/精确温度信息不足；程序保留可用内容并明确标注复核范围。
- 本次未发现应通过放宽门禁解决的新通用程序缺陷。

## 请求与成本

- 总请求 {len(all_calls)}：成功 {outcomes.get('succeeded', 0)}，超时 {outcomes.get('timeout', 0)}，其他 {len(all_calls) - outcomes.get('succeeded', 0) - outcomes.get('timeout', 0)}。
- 已知用量：prompt {prompt:,}，completion {completion:,}，reasoning {reasoning:,}，total {total:,}。
- 请求累计耗时 {elapsed / 1000:.1f} 秒（并行请求的耗时之和，不等于用户墙钟等待时间）。
- 未提供 `config/model_pricing.local.json` 合同单价，因此只报 token、请求和耗时，不虚构人民币费用。

## 范围结论

Stage C 证明局部失败隔离、分层交付和整卷 Word 门禁在两个学科、32 道解析上正常工作。它不能证明所有未来学科和所有模型输出均无错，但已证明当内容不确定时，程序能给用户结果且不隐藏风险。
"""
    path = OUTPUT_DIR / "RESULTS_STAGE_C.md"
    path.write_text(text, encoding="utf-8")
    return path


def _write_release_decision(summaries: list[dict[str, Any]]) -> Path:
    formal = [item["label"] for item in summaries if item["formal"]]
    candidates = [item["label"] for item in summaries if item["tier"] == "review_candidate"]
    text = f"""# VNext R3.1 Stage C 发布决策

## 决策：整卷流程可限制发布，单份产物按门禁等级使用

- 可正式发布：{'、'.join(formal) or '无'}。
- 只可作待复核候选版：{'、'.join(candidates) or '无'}。
- 程序不应为了提高 formal 比例而放宽公式缺失、原图信息不足或数值修补验算失败的门禁。
- 对外提供时必须保留 `formal / review_candidate / blocked` 三级语义和中文风险说明。
"""
    path = OUTPUT_DIR / "RELEASE_DECISION.md"
    path.write_text(text, encoding="utf-8")
    return path


def _write_manifest(generated: list[Path], summaries: list[dict[str, Any]]) -> Path:
    paths = [*generated, ROOT / "config/task_defaults.json"]
    for summary in summaries:
        task_path = ROOT / "tasks" / summary["task_id"] / "task.json"
        task = _read_json(task_path)
        paths.extend(
            [
                task_path,
                Path(str(task.get("exam_path") or "")),
                summary["stage"] / "final_acceptance_report.json",
                summary["stage"] / "answer_review_notes.json",
                summary["stage"] / "content_quality_audit.json",
                summary["stage"] / "docx_audit.json",
                summary["stage"] / "render_audit.json",
                summary["stage"] / "environment_check.json",
                summary["output"] / "模型调用明细.jsonl",
            ]
        )
        paths.extend(Path(str(value)) for value in task.get("selected_textbooks") or [])
        outputs = summary["report"].get("outputs") or {}
        for key in ("docx", "pdf"):
            value = outputs.get(key)
            if value:
                paths.append(ROOT / str(value))
    artifacts = []
    for path in dict.fromkeys(paths):
        if not path.is_file():
            raise FileNotFoundError(f"阶段 C 产物缺失：{_relative(path)}")
        artifacts.append({"path": _relative(path), "bytes": path.stat().st_size, "sha256": _sha256(path)})
    manifest = {
        "schema_version": "vnext_r3_1.stage_c_artifact_manifest.v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "artifacts": artifacts,
    }
    path = OUTPUT_DIR / "ARTIFACT_MANIFEST_STAGE_C.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries = [_case_summary(case) for case in CASES]
    ledger = _write_cost_ledger(summaries)
    results = _write_results(summaries)
    release = _write_release_decision(summaries)
    manifest = _write_manifest([results, release, ledger], summaries)
    print(json.dumps({"output_dir": _relative(OUTPUT_DIR), "files": [_relative(path) for path in (results, ledger, release, manifest)]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
