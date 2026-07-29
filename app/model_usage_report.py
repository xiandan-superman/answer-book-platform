from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


MODEL_USAGE_REPORT_NAME = "模型调用汇总.md"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _questions(structured_exam: dict[str, Any]) -> list[dict[str, Any]]:
    items = structured_exam.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    questions: list[dict[str, Any]] = []
    for section in structured_exam.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        for question in section.get("questions", []) or []:
            if isinstance(question, dict):
                questions.append(question)
    return questions


def _stage_final_models(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    provider = str(data.get("provider") or "").strip()
    default_model = str(data.get("model") or "").strip()
    by_qid: dict[str, dict[str, Any]] = {}
    for feedback in data.get("model_token_feedback", []) or []:
        if not isinstance(feedback, dict):
            continue
        qid = str(feedback.get("question_id") or "").strip()
        if not qid:
            continue
        attempts = [item for item in feedback.get("attempts", []) or [] if isinstance(item, dict)]
        final = attempts[-1] if attempts else {}
        by_qid[qid] = {
            "provider": provider,
            "model": str(final.get("model") or default_model or "").strip(),
            "strategy": str(final.get("strategy") or "").strip(),
            "thinking": final.get("thinking"),
            "max_tokens": final.get("max_tokens"),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "ok": feedback.get("ok"),
            "error": str(final.get("error") or "").strip(),
        }
    return by_qid


def _fallback_stage_model(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(data.get("provider") or "").strip(),
        "model": str(data.get("model") or "").strip(),
        "strategy": "",
        "thinking": None,
        "max_tokens": None,
        "attempt_count": 0,
        "attempts": [],
        "ok": None,
        "error": "",
    }


def _token_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _format_token(value: Any) -> str:
    number = _token_int(value)
    return f"{number:,}" if number is not None else "未返回"


def _attempt_billable_tokens(attempt: dict[str, Any]) -> int | None:
    prompt = _token_int(attempt.get("prompt_tokens"))
    completion = _token_int(attempt.get("completion_tokens"))
    if prompt is None and completion is None:
        return None
    return (prompt or 0) + (completion or 0)


def _stage_token_total(info: dict[str, Any] | None) -> tuple[int | None, int]:
    if not info:
        return None, 1
    attempts = [item for item in info.get("attempts", []) if isinstance(item, dict)]
    if not attempts:
        return None, 1
    total = 0
    known = False
    missing_count = 0
    for attempt in attempts:
        attempt_total = _attempt_billable_tokens(attempt)
        if attempt_total is None:
            missing_count += 1
            continue
        known = True
        total += attempt_total
    return (total if known else None), missing_count


def _token_detail(info: dict[str, Any]) -> str:
    attempts = [item for item in info.get("attempts", []) if isinstance(item, dict)]
    final = attempts[-1] if attempts else {}
    parts = [
        f"输入 {_format_token(final.get('prompt_tokens'))}",
        f"输出 {_format_token(final.get('completion_tokens'))}",
        f"推理 {_format_token(final.get('reasoning_tokens'))}",
        f"本次合计 {_format_token(_attempt_billable_tokens(final))}",
    ]
    if len(attempts) > 1:
        stage_total, missing_count = _stage_token_total(info)
        parts.append(f"阶段合计 {_format_token(stage_total)}")
        if missing_count:
            parts.append(f"{missing_count}次未返回usage")
    return "tokens：" + "；".join(parts)


def _format_model(info: dict[str, Any] | None, fallback: dict[str, Any] | None = None, *, include_tokens: bool = False) -> str:
    info = info or fallback or {}
    provider = str(info.get("provider") or "").strip()
    model = str(info.get("model") or "").strip()
    if provider and model:
        text = f"{provider}/{model}"
    else:
        text = model or provider or "未记录"
    details: list[str] = []
    strategy = str(info.get("strategy") or "").strip()
    if strategy:
        details.append(strategy)
    if info.get("thinking") is not None:
        details.append(f"thinking={info.get('thinking')}")
    if info.get("max_tokens"):
        details.append(f"max_tokens={info.get('max_tokens')}")
    if int(info.get("attempt_count") or 0) > 1:
        details.append(f"{info.get('attempt_count')}次尝试")
    if info.get("error"):
        details.append("最终尝试有错误")
    text += ("<br>" + "；".join(details)) if details else ""
    if include_tokens:
        text += "<br>" + _token_detail(info)
    return text


def _question_token_total(stage_infos: list[tuple[str, dict[str, Any] | None]], *, figure_token_missing: bool) -> str:
    total = 0
    known = False
    missing: list[str] = []
    for label, info in stage_infos:
        stage_total, missing_count = _stage_token_total(info)
        if stage_total is None:
            missing.append(label)
        else:
            known = True
            total += stage_total
            if missing_count:
                missing.append(f"{label}{missing_count}次未返回")
    if figure_token_missing:
        missing.append("作图/图片")
    text = f"可统计 {total:,}" if known else "未返回"
    if missing:
        text += "<br>未返回 usage：" + "；".join(missing)
    return text


def _short_text(value: Any, limit: int = 46) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _escape_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _drawing_code_models(data: dict[str, Any]) -> dict[str, str]:
    provider = str(data.get("provider") or "").strip()
    default_model = str(data.get("model") or "").strip()
    result: dict[str, str] = {}
    for item in data.get("generated", []) or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        model = str(item.get("model") or default_model).strip()
        if qid:
            result[qid] = f"{provider}/{model}" if provider and model else (model or provider or "未记录")
    return result


def _direct_image_models(data: dict[str, Any]) -> dict[str, str]:
    provider = str(data.get("provider") or "").strip()
    default_model = str(data.get("image_model") or data.get("model") or "").strip()
    result: dict[str, str] = {}
    for item in data.get("generated", []) or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        model = str(item.get("model") or default_model).strip()
        if qid:
            result[qid] = f"{provider}/{model}" if provider and model else (model or provider or "未记录")
    return result


def _figure_models(stage_dir: Path) -> dict[str, str]:
    audit = _read_json(stage_dir / "figure_generation_audit.json")
    code_models = _drawing_code_models(_read_json(stage_dir / "drawing_code_generation.json"))
    image_models = _direct_image_models(_read_json(stage_dir / "direct_model_figures.json"))
    by_qid: dict[str, list[dict[str, Any]]] = {}
    for item in audit.get("items", []) or []:
        if isinstance(item, dict):
            qid = str(item.get("question_id") or "").strip()
            if qid:
                by_qid.setdefault(qid, []).append(item)
    result: dict[str, str] = {}
    for qid, items in by_qid.items():
        if any(item.get("generation_method") == "image_model" for item in items):
            text = f"生图兜底：{image_models.get(qid, '未记录')}"
        elif any(item.get("generation_method") == "model_code_renderer" and not item.get("needs_manual_review") for item in items):
            text = f"模型代码+程序渲染：{code_models.get(qid, '未记录')}"
        elif any(item.get("generation_method") == "model_code_renderer" for item in items):
            text = f"模型代码+程序渲染（需复核）：{code_models.get(qid, '未记录')}"
        else:
            text = f"作图未形成可确认最终输出；代码来源：{code_models.get(qid, '未记录')}"
        risk_notes: list[str] = []
        for item in items:
            for note in item.get("risk_notes", []) or []:
                note_text = str(note).strip()
                if note_text and note_text not in risk_notes:
                    risk_notes.append(note_text)
        if risk_notes:
            text += "<br>审计提示：" + "；".join(risk_notes[:2])
        text += "<br>tokens：未记录（当前作图代码/图片阶段未落盘usage）"
        result[qid] = text
    return result


def build_model_usage_report(stage_dir: Path, output_dir: Path, task_id: str = "") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    structured_exam = _read_json(stage_dir / "structured_exam.json")
    knowledge = _read_json(stage_dir / "knowledge_plans.json")
    evidence = _read_json(stage_dir / "evidence_selection.json")
    answers = _read_json(stage_dir / "answer_fragments.json")
    progress = _read_json(stage_dir / "answer_generation_progress.json")
    questions = _questions(structured_exam)

    knowledge_models = _stage_final_models(knowledge)
    evidence_models = _stage_final_models(evidence)
    answer_models = _stage_final_models(answers)
    figure_models = _figure_models(stage_dir)

    knowledge_fallback = _fallback_stage_model(knowledge)
    evidence_fallback = _fallback_stage_model(evidence)
    answer_fallback = _fallback_stage_model(answers)

    lines: list[str] = [
        "# 模型调用汇总",
        "",
        f"- 任务：`{task_id or structured_exam.get('task_id') or ''}`",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 答案生成状态：`{progress.get('status') or '未记录'}`，完成 `{progress.get('completed', '-')}/{progress.get('total', '-')}`",
        "- token 统计口径：总计按 `prompt_tokens + completion_tokens` 计算；`reasoning_tokens` 单独列出，不重复计入。失败请求如果平台未返回 usage，则标记为“未返回”。",
        "",
        "## 阶段默认配置",
        "",
        "| 阶段 | 默认服务商/模型 |",
        "|---|---|",
        f"| 知识点识别 | {_escape_cell(_format_model(knowledge_fallback))} |",
        f"| 教材证据确认 | {_escape_cell(_format_model(evidence_fallback))} |",
        f"| 答案生成 | {_escape_cell(_format_model(answer_fallback))} |",
        "",
        "## 每题最终使用模型",
        "",
        "| 题目 | 类型/分值 | 题干摘要 | 知识点识别 | 教材证据确认 | 答案生成 | 作图/图片 | 总计 token |",
        "|---|---:|---|---|---|---|---|---:|",
    ]

    for question in questions:
        qid = str(question.get("question_id") or question.get("id") or "").strip()
        number = str(question.get("number") or "").strip()
        qtype = str(question.get("confirmed_question_type") or question.get("question_type") or question.get("type") or "").strip()
        score = question.get("confirmed_score", question.get("score", ""))
        type_score = f"{qtype}<br>{score}分" if score != "" and score is not None else qtype
        figure_text = figure_models.get(qid, "不涉及" if not question.get("needs_figure") else "未记录<br>tokens：未记录")
        total_text = _question_token_total(
            [
                ("知识点识别", knowledge_models.get(qid)),
                ("教材证据确认", evidence_models.get(qid)),
                ("答案生成", answer_models.get(qid)),
            ],
            figure_token_missing=qid in figure_models or bool(question.get("needs_figure")),
        )
        lines.append(
            "| "
            + " | ".join(
                _escape_cell(cell)
                for cell in [
                    f"`{qid}`<br>{number}",
                    type_score,
                    _short_text(question.get("stem") or question.get("question") or question.get("text")),
                    _format_model(knowledge_models.get(qid), knowledge_fallback, include_tokens=True),
                    _format_model(evidence_models.get(qid), evidence_fallback, include_tokens=True),
                    _format_model(answer_models.get(qid), answer_fallback, include_tokens=True),
                    figure_text,
                    total_text,
                ]
            )
            + " |"
        )

    retries = [
        (qid, info)
        for qid, info in answer_models.items()
        if int(info.get("attempt_count") or 0) > 1 or str(info.get("strategy") or "") not in {"", "primary", "attempt_1"}
    ]
    if retries:
        lines.extend(["", "## 答案生成重试/兜底", ""])
        for qid, info in sorted(retries):
            lines.append(
                f"- `{qid}` 最终使用 `{_format_model(info).split('<br>', 1)[0]}`，"
                f"策略 `{info.get('strategy') or '未记录'}`，共 `{info.get('attempt_count')}` 次尝试。"
            )

    report_path = output_dir / MODEL_USAGE_REPORT_NAME
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path
