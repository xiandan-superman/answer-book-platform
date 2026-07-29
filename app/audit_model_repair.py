from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .answer_generation import attach_program_evidence_block, evidence_for_answer_generation, fallback_fragment, fragment_from_analysis_draft
from .drawing_code import question_drawing_mode
from .formula_audit import audit_text_segments_no_formula
from .llm_client import OpenAICompatibleClient
from .prompts import question_image_parts
from .question_types import question_has_type
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


def _qids_from_text(text: str, known_qids: set[str]) -> list[str]:
    found = []
    for qid in known_qids:
        if qid and qid in text:
            found.append(qid)
    if found:
        return found
    head = str(text).split(":", 1)[0].strip()
    return [head] if head in known_qids else []


def collect_audit_issue_targets(audit_report: dict[str, Any], known_qids: set[str]) -> dict[str, list[dict[str, Any]]]:
    targets: dict[str, list[dict[str, Any]]] = {}
    for severity in ("issues", "warnings"):
        for raw in audit_report.get(severity, []) if isinstance(audit_report, dict) else []:
            item = raw if isinstance(raw, dict) else {"message": str(raw)}
            qids: list[str] = []
            if isinstance(item, dict) and _qid(item):
                qids = [_qid(item)]
            elif isinstance(item, dict):
                text = str(item.get("message") or item)
                qids = _qids_from_text(text, known_qids)
                if "missing answer fragments:" in text:
                    missing_text = text.split("missing answer fragments:", 1)[1]
                    for token in re.split(r"[,，、\s]+", missing_text):
                        token = token.strip()
                        if token in known_qids and token not in qids:
                            qids.append(token)
            for qid in qids:
                targets.setdefault(qid, []).append({**item, "severity": "issue" if severity == "issues" else "warning"})
    return targets


def _segments_text(segments: Any) -> str:
    parts: list[str] = []
    for segment in segments or []:
        if isinstance(segment, dict) and segment.get("type") == "text":
            text = str(segment.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _repair_context(fragment: dict[str, Any] | None, issues: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(fragment, dict):
        return {}
    wanted_labels = {"解析", "答案", "选项分析", "解题步骤", "易错点及注意事项", "待复核公式"}
    blocks: list[dict[str, Any]] = []
    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict):
            continue
        label = str(block.get("label") or "").strip()
        if label == "教材依据" or label not in wanted_labels:
            continue
        blocks.append({"label": label, "text": _segments_text(block.get("segments"))})
    return {
        "question_id": _qid(fragment),
        "section": fragment.get("section", ""),
        "number": fragment.get("number", ""),
        "answer": fragment.get("answer", ""),
        "answer_summary": fragment.get("answer_summary", ""),
        "blocks_to_repair": blocks,
        "formulas": fragment.get("formulas", []),
        "drawing_code_specs": fragment.get("drawing_code_specs", []),
        "figure_specs": fragment.get("figure_specs", []),
        "repair_scope": [str(issue.get("code") or issue.get("message") or "") for issue in issues],
        "note": "已移除程序生成的教材依据块；不要补写教材页码或教材依据。",
    }


def _repair_prompt(
    *,
    audit_stage: str,
    question: dict[str, Any],
    evidence: list[dict[str, Any]],
    fragment: dict[str, Any] | None,
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    image_parts = question_image_parts(question)
    needs_answer_figure = question_has_type(question, "作图题")
    drawing_mode = question_drawing_mode(question)
    drawing_output_key = "drawing_code_specs" if drawing_mode == "code" else "figure_specs"
    visual_context = {
        "has_input_images": bool(image_parts),
        "input_image_count": len(image_parts),
        "needs_figure": needs_answer_figure,
        "drawing_generation_mode": drawing_mode,
        "required_drawing_output": drawing_output_key,
        "image_refs": list(question.get("image_refs") or []),
        "repair_instruction": "",
    }
    if image_parts:
        visual_context["repair_instruction"] = "本题原题图片已随同本消息附上；修复答案时必须结合图片信息。"
    elif needs_answer_figure:
        visual_context["repair_instruction"] = f"本题未抽取到原题图片，但题干要求作图/图示；请仅按题干文字生成 {drawing_output_key}，并在 uncertainties 中说明“原题未抽取到图片，按题干文字生成图示规格”。"

    user_payload = {
        "task": "repair_one_answer_draft_after_audit_failed",
        "audit_stage": audit_stage,
        "audit_issues": issues,
        "question": question,
        "visual_context": visual_context,
        "confirmed_evidence": evidence[:20],
        "current_answer_context": _repair_context(fragment, issues),
        "output_schema": {
            "schema_version": "answer_book.answer_draft.v1",
            "question_id": _qid(question),
            "answer": "答案。计算题可写最终答案摘要；若含公式，公式也必须进入 formulas。",
            "analysis": "解析思路。计算题不要堆完整计算过程。",
            "analysis_segments": "非计算题使用；公式必须用 {f1} 等占位符真正融入句子。",
            "answer_units": [{"number": "多小问题必填的原始编号", "question_type": "确认题型", "answer": "该小问结论", "analysis_segments": [], "steps": []}],
            "steps": "计算题必须逐步写。text 只写本步目标；不要在 text 中写 {f1}。每步用 relation_formula_indices / substitution_formula_indices / result_formula_indices 引用 formulas。",
            "formulas": [{"latex": "公式 LaTeX", "role": "relation|substitution|result|definition", "meaning": "用途"}],
            "drawing_code_specs": [{"figure_id": "可选", "caption": "中文图注", "code": "必须定义 draw(output_path: str) -> None 的 Python/Matplotlib 代码", "notes": "可选"}],
            "figure_specs": [],
            "mistake_notes": [],
            "uncertainties": [],
        },
        "hard_rules": [
            "只修复当前这一题，不要改变题号、题型和教材依据含义。",
            "必须针对 audit_issues 指出的失败原因修改，不要只复述原答案。",
            "题目有多个作答单元时，必须返回 answer_units，并为每个原始小问编号返回一个独立对象；答案、解析和步骤只能放入所属小问，不能混写在顶层字段。",
            "不得丢失原题要求、答案、代入过程、单位和图示需求。",
            "严格服从确认后的 question_type；如果确认题型不是作图题，不得因为题干出现画出、绘制、作图、示意图、图示、标出、衍射花样、晶胞等词自行补充 drawing_code_specs 或 figure_specs。",
            "如果 visual_context.has_input_images 为 true，必须结合随消息附带的原题图片进行修复。",
            "如果 visual_context.needs_figure 为 true，必须按 visual_context.required_drawing_output 补充作图输出：code 模式输出 drawing_code_specs，figure_specs 模式输出 figure_specs。",
            "code 模式的 drawing_code_specs 每项必须包含 code 字符串，代码必须定义 draw(output_path: str) -> None，使用 Matplotlib 保存 PNG，图中文字说明优先中文，XRD/BCC/FCC/CsCl/hkl/2θ/a.u./[110]/(110) 等惯用标识可保留英文或符号；黑白打印可读，不能靠颜色区分关键含义。",
            "如果 visual_context.needs_figure 为 true 且 visual_context.has_input_images 为 false，必须仅依据题干文字生成 visual_context.required_drawing_output，并在 uncertainties 中说明原题未抽取到图片、按题干文字生成图示规格。",
            "如果 audit_issues 包含 missing_required_figure，必须优先补充 visual_context.required_drawing_output；只有在题干与图片均不足以确定图形时，才可在 uncertainties 中说明无法可靠作图。",
            "不要输出教材依据、页码、课本-p、evidence_id 或引用格式；教材依据由程序统一合并。",
            "不要复制 current_answer_context 之外的程序字段；current_answer_context 中也不包含教材依据块。",
            "计算题必须保留循序渐进的解题步骤：本步目的 -> 关系式 -> 带入数值 -> 求得结果。",
            "计算题 step.text 只写本步要计算什么和依据什么，不能写 {f1}、公式正文、代入式或结果式；公式统一放入 formulas 并通过索引字段引用。",
            "不得在 第(2)问、第2小问、第3步 这类中文序号标签中间插入换行。",
            "非计算题如需公式，必须在 analysis_segments.text 中用 {f1} 这类占位符把公式自然嵌入解析句子，不要集中罗列公式。",
            "不得把公式、判据、等量关系、比例关系、反应式或中文公式化表达写成普通正文。",
            "Return exactly one valid JSON object.",
        ],
    }
    user_text = json.dumps(user_payload, ensure_ascii=False)
    user_content: str | list[dict[str, Any]]
    if image_parts:
        user_content = [{"type": "text", "text": user_text}, *image_parts]
    else:
        user_content = user_text
    return [
        {
            "role": "system",
            "content": "你是真题解析平台的单题审查修复器。只能返回 JSON，不要返回 Markdown。",
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]


def repair_fragments_with_model_for_audit(
    fragments_json: Path,
    structured_exam: dict[str, Any],
    candidates: list[EvidenceCandidate],
    *,
    selection_data: dict[str, Any] | None,
    provider: ProviderConfig,
    model: str,
    audit_stage: str,
    audit_report: dict[str, Any],
    client: Any | None = None,
    backup_path: Path | None = None,
    max_repairs: int = 5,
) -> dict[str, Any]:
    data = json.loads(fragments_json.read_text(encoding="utf-8")) if fragments_json.exists() else {"fragments": []}
    original = copy.deepcopy(data)
    fragments = [fragment for fragment in data.get("fragments", []) if isinstance(fragment, dict)]
    questions = {
        _qid(question): question
        for question in structured_exam.get("items", [])
        if isinstance(question, dict) and _qid(question)
    }
    targets = collect_audit_issue_targets(audit_report, set(questions))
    if not targets:
        return {"ok": False, "changed": False, "repaired_count": 0, "repaired_question_ids": [], "issues": ["未定位到可交给模型修复的题目。"]}

    repair_client = client or OpenAICompatibleClient(provider)
    selections = _selection_map(selection_data)
    fragments_by_qid = {_qid(fragment): fragment for fragment in fragments if _qid(fragment)}
    fallback_model = next((item for item in provider.model_options if item != model), None)
    repaired_qids: list[str] = []
    repair_issues: list[dict[str, Any]] = []

    for qid, issues in list(targets.items())[: max(1, max_repairs)]:
        question = questions.get(qid)
        if not question:
            repair_issues.append({"question_id": qid, "issues": ["缺少题目结构，无法模型修复。"]})
            continue
        evidence_selection = selections.get(qid)
        evidence = evidence_for_answer_generation(candidates, qid, evidence_selection)
        fragment = fragments_by_qid.get(qid)
        try:
            draft = repair_client.chat_json_object(
                _repair_prompt(audit_stage=audit_stage, question=question, evidence=evidence, fragment=fragment, issues=issues),
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
            meta.update(
                {
                    "provider": provider.name,
                    "model": model,
                    "recovered_by": f"{audit_stage}_model_repair",
                    "audit_repair_issues": issues[:10],
                    "llm_retry": getattr(repair_client, "last_json_retry_report", {}),
                }
            )
            repaired["_meta"] = meta
        except Exception as exc:
            repair_issues.append({"question_id": qid, "issues": [str(exc)]})
            continue

        if fragment is None:
            fragments.append(repaired)
        else:
            for index, current in enumerate(fragments):
                if _qid(current) == qid:
                    fragments[index] = repaired
                    break
        fragments_by_qid[qid] = repaired
        repaired_qids.append(qid)

    changed = bool(repaired_qids)
    report = {
        "ok": changed and not repair_issues,
        "changed": changed,
        "repaired_count": len(repaired_qids),
        "repaired_question_ids": repaired_qids,
        "issue_count": len(repair_issues),
        "issues": repair_issues[:30],
        "targets": targets,
    }
    if not changed:
        return report

    if backup_path:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if fragments_json.exists():
            shutil.copy2(fragments_json, backup_path)
        report["backup"] = str(backup_path)
    data["fragments"] = fragments
    data.setdefault("recovery_events", []).extend(
        {"question_id": qid, "strategy": f"{audit_stage}_model_repair", "issues": targets.get(qid, [])[:5]}
        for qid in repaired_qids
    )
    data["recovered_count"] = int(data.get("recovered_count", 0)) + len(repaired_qids)
    fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report["original_preserved"] = original != data
    return report


def fill_missing_fragments_locally(
    fragments_json: Path,
    structured_exam: dict[str, Any],
    candidates: list[EvidenceCandidate],
    reason: str,
) -> dict[str, Any]:
    data = json.loads(fragments_json.read_text(encoding="utf-8")) if fragments_json.exists() else {"fragments": []}
    fragments = [fragment for fragment in data.get("fragments", []) if isinstance(fragment, dict)]
    existing = {_qid(fragment) for fragment in fragments if _qid(fragment)}
    repaired: list[str] = []
    for question in structured_exam.get("items", []):
        qid = _qid(question)
        if not qid or qid in existing:
            continue
        evidence = evidence_for_answer_generation(candidates, qid, None)
        fragment = fallback_fragment(question, evidence, reason)
        attach_program_evidence_block(fragment, evidence)
        fragments.append(fragment)
        repaired.append(qid)
    if repaired:
        data["fragments"] = fragments
        data.setdefault("recovery_events", []).extend({"question_id": qid, "strategy": "coverage_local_placeholder"} for qid in repaired)
        data["recovered_count"] = int(data.get("recovered_count", 0)) + len(repaired)
        data["fallback_count"] = int(data.get("fallback_count", 0)) + len(repaired)
        fragments_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "changed": bool(repaired), "repaired_question_ids": repaired, "repaired_count": len(repaired)}
