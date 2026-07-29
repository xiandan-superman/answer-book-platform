from __future__ import annotations

import copy
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .answer_generation import attach_program_evidence_block, evidence_for_answer_generation, fragment_from_analysis_draft
from .formula_audit import audit_text_segments_no_formula, formula_like_matches
from .llm_client import OpenAICompatibleClient
from .retrieval import EvidenceCandidate
from .settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig
from .v4_schema import validate_v4_answer_fragment


def _qid(value: dict[str, Any]) -> str:
    return str(value.get("question_id") or "").strip()


def _selection_map(selection_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not selection_data:
        return {}
    if isinstance(selection_data.get("selections"), list):
        return {
            _qid(selection): selection
            for selection in selection_data.get("selections", [])
            if isinstance(selection, dict) and _qid(selection)
        }
    return {
        str(key).strip(): value
        for key, value in selection_data.items()
        if str(key).strip() and isinstance(value, dict)
    }


def _collect_docx_formula_findings(fragments: list[dict[str, Any]], docx_issues: list[str]) -> list[dict[str, Any]]:
    issue_text = "\n".join(str(issue) for issue in docx_issues)
    findings: list[dict[str, Any]] = []
    for fragment in fragments:
        qid = _qid(fragment)
        for block_index, block in enumerate(fragment.get("blocks", [])):
            label = str(block.get("label") or "")
            if label == "教材依据":
                continue
            for segment_index, segment in enumerate(block.get("segments", [])):
                if not isinstance(segment, dict) or segment.get("type") != "text":
                    continue
                text = str(segment.get("text") or "")
                matches = formula_like_matches(text, include_chinese_paraphrase=True)
                if not matches:
                    continue
                directly_reported = any(match in issue_text or text[:80] in issue_text for match in matches)
                findings.append(
                    {
                        "question_id": qid,
                        "block_label": label,
                        "block_index": block_index,
                        "segment_index": segment_index,
                        "text": text,
                        "matches": matches[:5],
                        "directly_reported": directly_reported,
                    }
                )
    findings.sort(key=lambda item: (not item.get("directly_reported"), str(item.get("question_id"))))
    return findings


def _compact_fragment_for_prompt(fragment: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(fragment)
    for key in ("_review_candidate_fragment", "_meta", "_draft", "evidence_ids"):
        value.pop(key, None)
    blocks = []
    for block in value.get("blocks", []) or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("label") or "").strip() == "教材依据":
            continue
        blocks.append(block)
    value["blocks"] = blocks
    return value


def _repair_prompt(
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    fragment: dict[str, Any],
    findings: list[dict[str, Any]],
    docx_issues: list[str],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "你是真题解析平台的单题结构化修复器。只能返回 JSON，不要返回 Markdown。",
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "repair_one_answer_draft_after_docx_audit_failed",
                    "failure_stage": "docx",
                    "docx_issues": docx_issues[:10],
                    "question": question,
                    "confirmed_evidence": evidence[:20],
                    "current_fragment": _compact_fragment_for_prompt(fragment),
                    "offending_segments": findings[:10],
                    "output_schema": {
                        "schema_version": "answer_book.answer_draft.v1",
                        "question_id": _qid(question),
                        "answer": "答案。计算题可写最终答案摘要；若含公式，公式也必须进入 formulas。",
                        "analysis": "解析思路。计算题不要堆完整计算过程。",
                        "analysis_segments": "非计算题使用；公式必须用 {f1} 等占位符真正融入句子。",
                        "answer_units": [{"number": "多小问题必填的原始编号", "question_type": "确认题型", "answer": "该小问结论", "analysis_segments": [], "steps": []}],
                        "steps": "计算题必须逐步写。text 只写本步目标；不要在 text 中写 {f1}。每步用 relation_formula_indices / substitution_formula_indices / result_formula_indices 引用 formulas。",
                        "formulas": [{"latex": "公式 LaTeX", "role": "relation|substitution|result|definition", "meaning": "用途"}],
                        "figure_specs": [],
                        "mistake_notes": [],
                        "uncertainties": [],
                    },
                    "hard_rules": [
                        "只修复当前这一题，不要改变题号、题型和教材依据含义。",
                        "题目有多个作答单元时，必须返回 answer_units，并为每个原始小问编号返回一个独立对象；答案、解析和步骤只能放入所属小问，不能混写在顶层字段。",
                        "current_fragment 已移除程序生成的教材依据块；不要输出教材依据、页码、课本-p、evidence_id 或引用格式。",
                        "不得把公式、判据、等量关系、比例关系、反应式或中文公式化表达写成普通正文。",
                        "出现“等于、正比于、乘积、差值、为零、大于零、小于零”等公式语义时，必须改成 formulas 中的公式，并在步骤或 analysis_segments 中引用。",
                        "计算题必须保留循序渐进的解题步骤：先说明本步目的，再给关系式、带入数值和求得结果，不能只罗列公式。",
                        "计算题 step.text 只写本步要计算什么和依据什么，不能写 {f1}、公式正文、代入式或结果式；公式统一放入 formulas 并通过索引字段引用。",
                        "计算题不得丢失代入数据、单位和最终结果。",
                        "不得在 第(2)问、第2小问、第3步 这类中文序号标签中间插入换行。",
                        "非计算题如需公式，必须在 analysis_segments.text 中用 {f1} 这类占位符把公式自然嵌入解析句子，不要集中罗列公式。",
                        "Return exactly one valid JSON object.",
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def repair_fragments_with_model_for_docx(
    fragments_json: Path,
    structured_exam: dict[str, Any],
    candidates: list[EvidenceCandidate],
    *,
    selection_data: dict[str, Any] | None,
    provider: ProviderConfig,
    model: str,
    docx_issues: list[str],
    client: Any | None = None,
    backup_path: Path | None = None,
    max_repairs: int = 3,
) -> dict[str, Any]:
    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    original = copy.deepcopy(data)
    fragments = [fragment for fragment in data.get("fragments", []) if isinstance(fragment, dict)]
    findings = _collect_docx_formula_findings(fragments, docx_issues)
    if not findings:
        return {"ok": False, "changed": False, "repaired_count": 0, "repaired_question_ids": [], "issues": ["未定位到可交给模型修复的公式化正文片段。"]}

    questions = {
        _qid(question): question
        for question in structured_exam.get("items", [])
        if isinstance(question, dict) and _qid(question)
    }
    selections = _selection_map(selection_data)
    grouped_findings: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        qid = str(finding.get("question_id") or "").strip()
        if qid and qid not in grouped_findings:
            grouped_findings[qid] = []
        if qid:
            grouped_findings[qid].append(finding)

    repair_client = client or OpenAICompatibleClient(provider)
    repaired_qids: list[str] = []
    repair_issues: list[dict[str, Any]] = []
    fragments_by_qid = {_qid(fragment): fragment for fragment in fragments if _qid(fragment)}
    fallback_model = next((item for item in provider.model_options if item != model), None)

    for qid, q_findings in list(grouped_findings.items())[: max(1, max_repairs)]:
        question = questions.get(qid)
        fragment = fragments_by_qid.get(qid)
        if not question or not fragment:
            repair_issues.append({"question_id": qid, "issues": ["缺少题目结构或原 fragment，无法模型修复。"]})
            continue
        evidence_selection = selections.get(qid)
        evidence = evidence_for_answer_generation(candidates, qid, evidence_selection)
        messages = _repair_prompt(question, evidence, fragment, q_findings, docx_issues)
        try:
            draft = repair_client.chat_json_object(
                messages,
                model=model,
                max_tokens=max(int(provider.max_tokens or DEFAULT_MODEL_MAX_TOKENS), DEFAULT_MODEL_MAX_TOKENS),
                fallback_model=fallback_model,
            )
            repaired = fragment_from_analysis_draft(draft, question, evidence, evidence_selection)
            attach_program_evidence_block(repaired, evidence, evidence_selection)
            syntax_issues = validate_v4_answer_fragment(repaired)
            formula_leaks = audit_text_segments_no_formula(repaired.get("blocks", []), ignored_block_labels={"教材依据"}, include_chinese_paraphrase=True)
            if syntax_issues or formula_leaks:
                repair_issues.append({"question_id": qid, "issues": syntax_issues + formula_leaks[:10]})
                continue
            meta = dict(repaired.get("_meta") or {})
            retry_report = getattr(repair_client, "last_json_retry_report", {})
            meta.update(
                {
                    "provider": provider.name,
                    "model": model,
                    "recovered_by": "docx_model_repair",
                    "docx_repair_findings": q_findings[:10],
                    "llm_retry": retry_report,
                }
            )
            repaired["_meta"] = meta
        except Exception as exc:
            repair_issues.append({"question_id": qid, "issues": [str(exc)]})
            continue

        for index, current in enumerate(fragments):
            if _qid(current) == qid:
                fragments[index] = repaired
                repaired_qids.append(qid)
                break

    changed = bool(repaired_qids)
    report = {
        "ok": changed and not repair_issues,
        "changed": changed,
        "repaired_count": len(repaired_qids),
        "repaired_question_ids": repaired_qids,
        "issue_count": len(repair_issues),
        "issues": repair_issues[:30],
        "findings": findings[:30],
    }
    if not changed:
        return report

    if backup_path:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fragments_json, backup_path)
        report["backup"] = str(backup_path)
    data["fragments"] = fragments
    data.setdefault("recovery_events", []).extend(
        {"question_id": qid, "strategy": "docx_model_repair", "issues": docx_issues[:5]}
        for qid in repaired_qids
    )
    data["recovered_count"] = int(data.get("recovered_count", 0)) + len(repaired_qids)
    fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report["original_preserved"] = original != data
    return report
