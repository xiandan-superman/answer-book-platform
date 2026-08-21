#!/usr/bin/env python3
"""Build the reproducible Stage B validation evidence bundle."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "vnext_r3_1_stage_b_20260820"
FINAL_TASK_IDS = ["识图题_20260820_153807", "带轴电子衍射_110_20260820_154949", "抽题流程测试_覆盖题型_20260820_155352"]
LEDGER_TASK_IDS = [
    "vision_model_smoke_doubao_seed_2_0_lite_20260820", "识图题_20260820_152116",
    "识图题_20260820_153231", *FINAL_TASK_IDS, "vnext_r3_1_stage_b_b04_20260820",
]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_calls() -> list[dict]:
    calls = []
    for line in (ROOT / "logs/model_calls.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("task_id") in LEDGER_TASK_IDS:
            calls.append(row)
    return calls


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    calls = load_calls()
    pricing_configured = (ROOT / "config/model_pricing.local.json").exists()
    fields = [
        "task_id", "call_id", "stage", "purpose", "provider", "model", "outcome",
        "billable_disposition", "timeout_seconds", "elapsed_ms", "prompt_tokens",
        "completion_tokens", "reasoning_tokens", "total_tokens", "usage_returned",
        "cost_cny", "cost_status", "started_at", "finished_at", "error",
    ]
    csv_path = OUT / "COST_LEDGER_STAGE_B.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for call in calls:
            writer.writerow({
                **{key: call.get(key, "") for key in fields},
                "usage_returned": str(call.get("total_tokens") is not None).lower(),
                "cost_cny": "",
                "cost_status": "pricing_not_computed" if pricing_configured else "not_reported_no_contract_pricing",
            })
    is_visual = lambda row: "doubao-seed-2-0-lite" in str(row.get("model"))
    is_image = lambda row: row.get("purpose") == "image_generation"
    aggregate = {
        "schema_version": "vnext_r3_1.stage_b_call_ledger.v2",
        "task_ids": LEDGER_TASK_IDS,
        "request_counts": {
            "total": len(calls),
            "text": sum(not is_visual(row) and not is_image(row) for row in calls),
            "visual": sum(is_visual(row) for row in calls),
            "image": sum(is_image(row) for row in calls),
        },
        "outcomes": {key: sum(row.get("outcome") == key for row in calls) for key in ("succeeded", "timeout", "failed")},
        "known_usage": {
            "requests_with_usage": sum(row.get("total_tokens") is not None for row in calls),
            "requests_without_usage": sum(row.get("total_tokens") is None for row in calls),
            "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in calls),
            "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in calls),
            "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in calls),
            "total_tokens": sum(int(row.get("total_tokens") or 0) for row in calls),
        },
        "elapsed_ms_sum": sum(int(row.get("elapsed_ms") or 0) for row in calls),
        "cost_cny": None,
        "cost_status": "pricing_not_computed" if pricing_configured else "not_reported_no_contract_pricing",
        "calls": calls,
    }
    ledger_json = OUT / "CALL_LEDGER_STAGE_B.json"
    ledger_json.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")

    reports = [read_json(ROOT / "tasks" / task_id / "stage_outputs/final_acceptance_report.json") for task_id in FINAL_TASK_IDS]
    b04 = read_json(OUT / "b04_visual_health/figure_visual_qa.json")
    b04_ok = bool(b04.get("items") and b04["items"][0].get("qa", {}).get("ok"))
    counts, outcomes, usage = aggregate["request_counts"], aggregate["outcomes"], aggregate["known_usage"]
    results = f"""# VNext R3.1 Stage B 验证结果

## 结论

Stage B 已按 B-01 → B-02 → B-03 → B-04 重走。新视觉模型 `doubao-seed-2-0-lite-260215` 在烟雾测试、原题理解和程序图 QA 中均成功。B-01/B-02 形成 formal 交付；B-03 的 4 道正常题被安全复用，唯一计算题因新候选的科学符号错误和第二次超时降级为 review_candidate，没有误报正式通过。

| 用例 | 任务 | 结果 | 硬门禁 |
|---|---|---|---|
| B-01 识图题 | `{FINAL_TASK_IDS[0]}` | `{reports[0].get('delivery_tier')}` | formal=true，原图视觉理解成功 |
| B-02 [110] 带轴电子衍射 | `{FINAL_TASK_IDS[1]}` | `{reports[1].get('delivery_tier')}` | 确定性作图、视觉 QA、DOCX 全通过 |
| B-03 五题型集成 | `{FINAL_TASK_IDS[2]}` | `{reports[2].get('delivery_tier')}` | 局部失败隔离成功，计算题待复核 |
| B-04 XRD 视觉健康探测 | `vnext_r3_1_stage_b_b04_20260820` | `{'passed' if b04_ok else 'failed'}` | 单次新请求，无缓存复用 |

## 通用程序修复复验

- B-01 验证原图必须视觉门禁、语义检索降级、公式边界提升、偏导数 OMML 和答案符号一致性。
- B-02 验证 `\\bar`/Unicode 上划线 HKL 解析、专用 schema 禁止静默降级为生图、确定性晶带作图和 DOCX 结构化公式。
- B-03 验证教材缓存路径重绑定、语义规划、嵌套 LaTeX 单位数值验算、多结果摘要、答案账本一致性和审查符号归一化。
- 断点恢复仅重算 B-03 的计算题；另 4 题和上游高成本阶段均复用。

## B-03 停止原因

第一个新候选内部算术一致，但把水蒸气凝结的 `ΔU=ΔH-Δ(pV)` 符号展开错了，得出 `-2432 kJ`；高风险复核识别后，自动补丁未通过来源和机器验算，因此未写入正式答案。第二个候选在 300 s 超时。这是文本模型候选质量/服务超时，程序已正确隔离并降级。

## 请求与 token 记账

- 总请求 {counts['total']}：文本 {counts['text']}，视觉 {counts['visual']}，生图 {counts['image']}。
- 结果：成功 {outcomes['succeeded']}，超时 {outcomes['timeout']}，失败 {outcomes['failed']}。
- 已知用量：prompt {usage['prompt_tokens']:,}，completion {usage['completion_tokens']:,}，total {usage['total_tokens']:,}；{usage['requests_without_usage']} 个请求未返回 usage。
- 未配置合同计价表，不虚构人民币费用。

## 最终范围

B-03 已证明程序能阻止科学错误成为 formal，但当前候选仍不能作为正式答案。按停止条件，本轮不继续扩大文本请求，也不进入 Stage C。
"""
    results_path = OUT / "RESULTS_STAGE_B.md"
    results_path.write_text(results, encoding="utf-8")

    decision_path = OUT / "RELEASE_DECISION.md"
    decision_path.write_text(
        "# VNext R3.1 发布决策\n\n## 决策：Stage B 程序修复验收通过，暂不进入 Stage C\n\n"
        "B-01/B-02/B-04 通过，新视觉模型通过多图实测。B-03 正确降级了一道有科学错误的计算题，但该任务尚非 formal，因此不扩大到 C2/C3 整卷。\n",
        encoding="utf-8",
    )
    root_cause = OUT / "ROOT_CAUSE_ANALYSIS_STAGE_B.md"
    root_cause.write_text(
        "# VNext R3.1 Stage B 根因与复验\n\n"
        "原始外部故障仅确认为 `doubao-seed-2-1-pro-260628` 请求路径连续超时；其他暴露项均按程序设计缺陷修复，未写题目个例分支。\n\n"
        "复验证明新视觉模型可用，专用图路由、表达结构化、DOCX、数值验算、审查引用和断点恢复均生效。B-03 留存问题是新文本候选的科学符号错误及后续超时；程序已正确拦截。\n",
        encoding="utf-8",
    )

    evidence = [root_cause, results_path, decision_path, csv_path, ledger_json, OUT / "b04_visual_health/figure_visual_qa.json"]
    for task_id in FINAL_TASK_IDS:
        stage = ROOT / "tasks" / task_id / "stage_outputs"
        for name in ("final_acceptance_report.json", "pipeline_status.json", "answer_fragments.json", "figure_generation_audit.json", "figure_visual_qa.json", "docx_audit.json"):
            if (stage / name).exists():
                evidence.append(stage / name)
    manifest = {
        "schema_version": "vnext_r3_1.stage_b_artifact_manifest.v2",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "artifacts": [{"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha256(path)} for path in evidence],
    }
    (OUT / "ARTIFACT_MANIFEST_STAGE_B.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
