from __future__ import annotations

import json
import random
import re
from typing import Any

from .llm_client import LLMError, OpenAICompatibleClient
from .practice_inputs import parse_practice_sources
from .settings import DEFAULT_MODEL_MAX_TOKENS, get_provider, resolve_provider_model


SCHEMA_VERSION = "answer_book.practice_set.v1"
ALLOWED_DIFFICULTIES = {"基础", "进阶", "挑战"}
ALLOWED_TYPES = {"单选题", "多选题", "判断题", "填空题", "简答题", "计算题", "作图题", "综合题"}


def _clean(value: Any, limit: int = 8000) -> str:
    text = re.sub(r"\r\n?", "\n", str(value or "")).strip()
    return text[:limit]


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item, 500) for item in value[:limit] if _clean(item, 500)]


def _normalize_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(value[:8]):
        if isinstance(raw, dict):
            label = _clean(raw.get("label"), 4) or chr(65 + index)
            text = _clean(raw.get("text"), 1200)
        else:
            label = chr(65 + index)
            text = _clean(raw, 1200)
        if text:
            rows.append({"label": label, "text": text})
    return rows


def _normalize_formulas(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    for index, raw in enumerate(value[:16], start=1):
        if not isinstance(raw, dict):
            continue
        latex = _clean(raw.get("latex"), 3000)
        if latex:
            rows.append(
                {
                    "formula_id": _clean(raw.get("formula_id"), 50) or f"f{index}",
                    "latex": latex,
                    "location": _clean(raw.get("location"), 20) or "stem",
                    "display": bool(raw.get("display", True)),
                    "caption": _clean(raw.get("caption"), 300),
                }
            )
    return rows


def _normalize_tables(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    tables = []
    for index, raw in enumerate(value[:6], start=1):
        if not isinstance(raw, dict):
            continue
        headers = _string_list(raw.get("headers"), limit=10)
        rows = []
        for row in (raw.get("rows") or [])[:30]:
            if isinstance(row, list):
                rows.append([_clean(cell, 500) for cell in row[:10]])
        if headers or rows:
            tables.append(
                {
                    "table_id": _clean(raw.get("table_id"), 50) or f"t{index}",
                    "location": _clean(raw.get("location"), 20) or "stem",
                    "title": _clean(raw.get("title"), 300),
                    "headers": headers,
                    "rows": rows,
                }
            )
    return tables


def _normalize_figures(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    figures = []
    for index, raw in enumerate(value[:6], start=1):
        if not isinstance(raw, dict):
            continue
        series = []
        for raw_series in (raw.get("series") or [])[:8]:
            if not isinstance(raw_series, dict):
                continue
            points = []
            for point in (raw_series.get("points") or [])[:80]:
                if isinstance(point, list) and len(point) >= 2:
                    try:
                        points.append([float(point[0]), float(point[1])])
                    except (TypeError, ValueError):
                        continue
            series.append({"name": _clean(raw_series.get("name"), 100), "points": points})
        figures.append(
            {
                "figure_id": _clean(raw.get("figure_id"), 50) or f"g{index}",
                "location": _clean(raw.get("location"), 20) or "stem",
                "figure_type": _clean(raw.get("figure_type"), 30) or "diagram",
                "title": _clean(raw.get("title"), 300),
                "description": _clean(raw.get("description"), 1500),
                "x_label": _clean(raw.get("x_label"), 100),
                "y_label": _clean(raw.get("y_label"), 100),
                "series": series,
            }
        )
    return figures


def _type_plan(selected: list[str], count: int) -> list[str]:
    rng = random.SystemRandom()
    pool = selected or sorted(ALLOWED_TYPES)
    plan: list[str] = []
    while len(plan) < count:
        cycle = list(pool)
        rng.shuffle(cycle)
        plan.extend(cycle)
    return plan[:count]


def _parse_practice_json(content: str) -> dict[str, Any]:
    """Parse practice output without changing the platform-wide strict parser."""
    cleaned = str(content or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        extracted = cleaned[start : end + 1]
        if extracted != cleaned:
            candidates.append(extracted)
    last_error: Exception | None = None
    for candidate in candidates:
        for strict in (True, False):
            try:
                value = json.loads(candidate, strict=strict)
                if not isinstance(value, dict):
                    raise ValueError("模型 JSON 输出必须是对象。")
                return value
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
    preview = cleaned.replace("\n", "\\n")[:220]
    raise LLMError(f"专项练习 JSON 解析失败：{last_error}；内容预览：{preview}")


def normalize_practice_set(
    raw: dict[str, Any],
    *,
    requested_count: int,
    subject: str,
    planned_types: list[str] | None = None,
    planned_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("模型输出必须是 JSON 对象。")
    source = raw.get("source_analysis") if isinstance(raw.get("source_analysis"), dict) else {}
    blueprint = raw.get("blueprint") if isinstance(raw.get("blueprint"), dict) else {}
    exercises_raw = raw.get("exercises")
    if not isinstance(exercises_raw, list) or not exercises_raw:
        raise ValueError("模型没有生成 exercises 列表。")

    exercises: list[dict[str, Any]] = []
    for index, item in enumerate(exercises_raw[:requested_count], start=1):
        if not isinstance(item, dict):
            continue
        stem = _clean(item.get("stem"), 6000)
        answer = _clean(item.get("answer"), 4000)
        if not stem or not answer:
            continue
        difficulty = _clean(item.get("difficulty"), 12)
        question_type = _clean(item.get("question_type"), 20)
        if planned_types and index <= len(planned_types):
            question_type = planned_types[index - 1]
        exercises.append(
            {
                "exercise_id": f"practice_{index:02d}",
                "number": index,
                "question_type": question_type if question_type in ALLOWED_TYPES else "综合题",
                "source_question_id": (
                    _clean(planned_source_ids[index - 1], 80)
                    if planned_source_ids and index <= len(planned_source_ids)
                    else _clean(item.get("source_question_id"), 80)
                ),
                "difficulty": difficulty if difficulty in ALLOWED_DIFFICULTIES else "进阶",
                "target_skill": _clean(item.get("target_skill"), 500),
                "variation_type": _clean(item.get("variation_type"), 100),
                "stem": stem,
                "options": _normalize_options(item.get("options")),
                "answer": answer,
                "solution_steps": _string_list(item.get("solution_steps"), limit=10),
                "knowledge_points": _string_list(item.get("knowledge_points"), limit=10),
                "verification_note": _clean(item.get("verification_note"), 1000),
                "formulas": _normalize_formulas(item.get("formulas")),
                "tables": _normalize_tables(item.get("tables")),
                "figures": _normalize_figures(item.get("figures")),
            }
        )

    if not exercises:
        raise ValueError("生成结果缺少可用题干或答案。")

    warnings: list[str] = []
    if len(exercises) != requested_count:
        warnings.append(f"请求生成 {requested_count} 题，实际得到 {len(exercises)} 题。")
    for item in exercises:
        if item["question_type"] in {"单选题", "多选题"} and len(item["options"]) < 2:
            warnings.append(f"第 {item['number']} 题为选择题，但有效选项少于 2 个。")
        if not item["solution_steps"]:
            warnings.append(f"第 {item['number']} 题缺少分步解析。")

    return {
        "schema_version": SCHEMA_VERSION,
        "source_analysis": {
            "subject": _clean(source.get("subject"), 100) or _clean(subject, 100) or "未指定",
            "question_type": _clean(source.get("question_type"), 100),
            "knowledge_points": _string_list(source.get("knowledge_points")),
            "skills": _string_list(source.get("skills")),
            "difficulty": _clean(source.get("difficulty"), 100),
            "solution_strategy": _string_list(source.get("solution_strategy")),
            "common_errors": _string_list(source.get("common_errors")),
            "uncertainties": _string_list(source.get("uncertainties")),
        },
        "blueprint": {
            "training_goal": _clean(blueprint.get("training_goal"), 1000),
            "progression": _string_list(blueprint.get("progression"), limit=requested_count),
            "design_notes": _string_list(blueprint.get("design_notes")),
        },
        "exercises": exercises,
        "quality": {
            "status": "warning" if warnings else "passed",
            "warnings": warnings,
            "generated_count": len(exercises),
        },
    }


def _exercise_contract() -> dict[str, Any]:
    return {
        "source_question_id": "对应的原题 ID；单题可为空",
        "question_type": "单选题/多选题/判断题/填空题/简答题/计算题/作图题/综合题",
        "difficulty": "基础/进阶/挑战",
        "target_skill": "本题训练的具体能力",
        "variation_type": "变式类型",
        "stem": "完整独立题干，Markdown 文本；公式使用 LaTeX",
        "options": [{"label": "A", "text": "选项内容"}],
        "answer": "明确答案",
        "solution_steps": ["分步解析"],
        "knowledge_points": ["知识点"],
        "verification_note": "内容自检说明",
        "formulas": [
            {
                "formula_id": "f1",
                "latex": "不含美元符号的 LaTeX",
                "location": "stem/solution",
                "display": True,
                "caption": "可选说明",
            }
        ],
        "tables": [
            {
                "table_id": "t1",
                "location": "stem/solution",
                "title": "表题",
                "headers": ["列名"],
                "rows": [["单元格"]],
            }
        ],
        "figures": [
            {
                "figure_id": "g1",
                "location": "stem/solution",
                "figure_type": "line/bar/scatter/diagram",
                "title": "图题",
                "description": "完整图示说明",
                "x_label": "横轴",
                "y_label": "纵轴",
                "series": [{"name": "系列", "points": [[0, 0], [1, 1]]}],
            }
        ],
    }


def _model_runtime(payload: dict[str, Any], has_images: bool):
    provider_name = _clean(payload.get("vision_provider"), 100) if has_images else ""
    provider = get_provider(provider_name or _clean(payload.get("provider"), 100) or "openai")
    requested_model = (
        _clean(payload.get("vision_model"), 200)
        if has_images
        else _clean(payload.get("model"), 200)
    )
    if has_images and not requested_model:
        requested_model = _clean(provider.vision_model, 200)
    model = resolve_provider_model(provider, requested_model or None)
    if has_images and not provider.supports_vision:
        raise ValueError(f"当前服务商 {provider.name} 未配置视觉模型，请改用支持读图的模型。")
    return provider, model


def _user_content(text: str, images: list[str]) -> Any:
    if not images:
        return text
    return [
        {"type": "text", "text": text},
        *[{"type": "image_url", "image_url": {"url": image}} for image in images],
    ]


def _call_practice_json(
    client: OpenAICompatibleClient,
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float,
    thinking: str | None,
) -> dict[str, Any]:
    result = client.chat_json(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max(DEFAULT_MODEL_MAX_TOKENS, 10000),
        thinking=thinking,
    )
    try:
        raw = _parse_practice_json(result.content)
    except LLMError as first_error:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": result.content},
            {
                "role": "user",
                "content": (
                    "只修复上一个回答的 JSON 语法，不改变题目内容。"
                    "所有字符串中的真实换行、制表符和反斜杠必须正确转义。"
                    "只输出一个合法 JSON 对象，不要 Markdown 代码围栏。"
                ),
            },
        ]
        repaired = client.chat_json(
            repair_messages,
            model=model,
            temperature=0,
            max_tokens=max(DEFAULT_MODEL_MAX_TOKENS, 10000),
            thinking="disabled",
        )
        try:
            raw = _parse_practice_json(repaired.content)
        except LLMError as repair_error:
            raise LLMError(f"{first_error}；自动修复后仍失败：{repair_error}") from repair_error
    return raw


def _normalize_plan(
    raw: dict[str, Any],
    *,
    count: int,
    planned_types: list[str],
    difficulty: str,
    selected_types: list[str],
    source_files: list[str],
    source_scope: dict[str, Any] | None = None,
    selected_source_questions: list[dict[str, str]] | None = None,
    planned_source_ids: list[str] | None = None,
    generation_strategy: str = "single",
) -> dict[str, Any]:
    source = raw.get("source_analysis") if isinstance(raw.get("source_analysis"), dict) else {}
    blueprint = raw.get("blueprint") if isinstance(raw.get("blueprint"), dict) else {}
    raw_plan = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []
    exercise_plan = []
    default_difficulties = ["基础", "进阶", "挑战"]
    selected_ids = {
        _clean(item.get("source_question_id"), 80)
        for item in (selected_source_questions or [])
        if _clean(item.get("source_question_id"), 80)
    }
    for index in range(count):
        row = raw_plan[index] if index < len(raw_plan) and isinstance(raw_plan[index], dict) else {}
        source_question_id = _clean(row.get("source_question_id"), 80)
        if planned_source_ids and index < len(planned_source_ids):
            source_question_id = _clean(planned_source_ids[index], 80)
        elif selected_ids and source_question_id not in selected_ids:
            source_question_id = _clean(
                (selected_source_questions or [{}])[index % len(selected_source_questions or [{}])].get("source_question_id"),
                80,
            )
        exercise_plan.append(
            {
                "number": index + 1,
                "question_type": planned_types[index],
                "difficulty": _clean(row.get("difficulty"), 20)
                if _clean(row.get("difficulty"), 20) in ALLOWED_DIFFICULTIES
                else default_difficulties[min(index * 3 // max(count, 1), 2)],
                "target_skill": _clean(row.get("target_skill"), 500),
                "variation_type": _clean(row.get("variation_type"), 200),
                "design_intent": _clean(row.get("design_intent"), 800),
                "source_question_id": source_question_id,
            }
        )
    return {
        "schema_version": "answer_book.practice_plan.v1",
        "source_analysis": {
            "subject": _clean(source.get("subject"), 100) or "未指定",
            "question_type": _clean(source.get("question_type"), 100),
            "knowledge_points": _string_list(source.get("knowledge_points")),
            "skills": _string_list(source.get("skills")),
            "difficulty": _clean(source.get("difficulty"), 100),
            "solution_strategy": _string_list(source.get("solution_strategy")),
            "common_errors": _string_list(source.get("common_errors")),
            "uncertainties": _string_list(source.get("uncertainties")),
        },
        "blueprint": {
            "training_goal": _clean(blueprint.get("training_goal"), 1000),
            "progression": _string_list(blueprint.get("progression"), limit=count),
            "design_notes": _string_list(blueprint.get("design_notes")),
            "exercise_plan": exercise_plan,
            "question_type_mode": "selected" if selected_types else "random",
            "question_type_plan": planned_types,
            "difficulty_mode": difficulty,
            "generation_strategy": generation_strategy,
        },
        "source_files": source_files,
        "source_scope": source_scope or {"mode": "single", "questions": []},
        "selected_source_questions": selected_source_questions or [],
    }


def _normalize_source_scope(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    questions = []
    for index, item in enumerate(value.get("questions") or [], start=1):
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title"), 300)
        excerpt = _clean(item.get("stem_excerpt"), 1200)
        if not title and not excerpt:
            continue
        questions.append(
            {
                "source_question_id": _clean(item.get("source_question_id"), 80) or f"source_{index:02d}",
                "number": _clean(item.get("number"), 50) or str(index),
                "title": title or excerpt[:80],
                "stem_excerpt": excerpt,
                "question_type": _clean(item.get("question_type"), 100),
                "knowledge_points": _string_list(item.get("knowledge_points"), limit=8),
            }
        )
    mode = _clean(value.get("mode"), 30)
    if mode not in {"single", "question_set"}:
        mode = "question_set" if len(questions) > 1 else "single"
    if mode == "question_set" and len(questions) < 2:
        mode = "single"
    return {
        "mode": mode,
        "title": _clean(value.get("title"), 300),
        "questions": questions,
    }


def _source_type(value: Any) -> str:
    text = _clean(value, 100)
    for allowed in ALLOWED_TYPES:
        if allowed in text:
            return allowed
    if "证明" in text:
        return "综合题"
    return "综合题"


def _strategy_plan(
    payload: dict[str, Any],
    *,
    selected_source_questions: list[dict[str, Any]],
    selected_types: list[str],
) -> tuple[str, int, list[str], list[str]]:
    if not selected_source_questions:
        count = max(1, min(8, int(payload.get("count") or 5)))
        return "single", count, _type_plan(selected_types, count), []

    strategy = _clean(payload.get("generation_strategy"), 40)
    if strategy not in {"targeted_set", "parallel_exam", "per_question"}:
        raise ValueError("请选择整套专项补强、平行试卷或逐题变式。")

    if strategy == "targeted_set":
        count = max(1, min(20, int(payload.get("strategy_count") or 10)))
        return strategy, count, _type_plan(selected_types, count), []

    if strategy == "parallel_exam":
        questions = selected_source_questions[:30]
        types = [_source_type(item.get("question_type")) for item in questions]
        source_ids = [_clean(item.get("source_question_id"), 80) for item in questions]
        return strategy, len(questions), types, source_ids

    variants = max(1, min(3, int(payload.get("variants_per_question") or 1)))
    rows = [
        (item, copy_index)
        for item in selected_source_questions
        for copy_index in range(variants)
    ][:30]
    if selected_types:
        types = _type_plan(selected_types, len(rows))
    else:
        types = [_source_type(item.get("question_type")) for item, _ in rows]
    source_ids = [_clean(item.get("source_question_id"), 80) for item, _ in rows]
    return strategy, len(rows), types, source_ids


def plan_practice_set(payload: dict[str, Any]) -> dict[str, Any]:
    sources = parse_practice_sources(payload)
    difficulty = _clean(payload.get("difficulty"), 100) or "基础到进阶"
    focus = _clean(payload.get("focus"), 1000) or "围绕原题核心考点形成由浅入深的专项练习"
    selected_types = [
        _clean(item, 20)
        for item in (payload.get("question_types") or [])
        if _clean(item, 20) in ALLOWED_TYPES
    ]
    selected_source_questions = [
        {
            "source_question_id": _clean(item.get("source_question_id"), 80),
            "number": _clean(item.get("number"), 50),
            "title": _clean(item.get("title"), 300),
            "stem_excerpt": _clean(item.get("stem_excerpt"), 1200),
            "question_type": _clean(item.get("question_type"), 100),
            "knowledge_points": _string_list(item.get("knowledge_points"), limit=8),
        }
        for item in (payload.get("selected_source_questions") or [])
        if isinstance(item, dict) and _clean(item.get("source_question_id"), 80)
    ]
    generation_strategy, count, planned_types, planned_source_ids = _strategy_plan(
        payload,
        selected_source_questions=selected_source_questions,
        selected_types=selected_types,
    )
    prior_source_scope = _normalize_source_scope(payload.get("source_scope"))
    provider, model = _model_runtime(payload, bool(sources["images"]))
    contract = {
        "source_scope": {
            "mode": "single/question_set",
            "title": "试卷或题目标题",
            "questions": [
                {
                    "source_question_id": "source_01",
                    "number": "原题编号",
                    "title": "便于选择的短标题",
                    "stem_excerpt": "足以区分题目的题干摘要",
                    "question_type": "原题题型",
                    "knowledge_points": ["知识点"],
                }
            ],
        },
        "source_analysis": {
            "subject": "自动识别学科",
            "question_type": "原题题型",
            "knowledge_points": ["知识点"],
            "skills": ["能力点"],
            "difficulty": "研究生层级难度判断",
            "solution_strategy": ["正确解题路径"],
            "common_errors": ["常见错误"],
            "uncertainties": [],
        },
        "blueprint": {
            "training_goal": "明确训练目标",
            "progression": ["逐题梯度"],
            "design_notes": ["设计原则"],
            "exercise_plan": [
                {
                    "number": 1,
                    "question_type": "题型",
                    "difficulty": "基础/进阶/挑战",
                    "target_skill": "能力",
                    "variation_type": "变式类型",
                    "design_intent": "本题为何这样设计",
                    "source_question_id": "该练习对应的原题 ID",
                }
            ],
        },
    }
    task = f"""# 任务

只解析原题并设计研究生专项练习蓝图，本阶段不要生成具体题目。

## 原题材料

{sources["text"] or "请读取附带的题目图片或 PDF 页面。"}

## 专项要求

{focus}

## 参数

- 题量：{count}
- 难度梯度：{difficulty}
- 生成策略：{generation_strategy}
- 程序指定的逐题题型：{"、".join(planned_types)}
- 程序指定的逐题原题来源：{"、".join(planned_source_ids) if any(planned_source_ids) else "由蓝图按整套试卷考点分布合理分配"}
- 用户已选择的原题：{json.dumps(selected_source_questions, ensure_ascii=False) if selected_source_questions else "尚未选择"}

## 约束

- 全部内容保持研究生层级
- 首先判断材料是一道独立题，还是包含多道独立题的试卷/题目集
- 如果是题目集且用户尚未选择原题：完整列出可辨认的原题，source_scope.mode 返回 question_set，exercise_plan 必须为空，不得把整套试卷混成一个训练目标
- 如果用户已经选择原题：只围绕选中的原题设计蓝图，exercise_plan 每项用 source_question_id 标明来源
- targeted_set：按选中原题的考点重要性生成指定总题量，不要求每道原题平均分配
- parallel_exam：每道选中原题对应一道平行题，保持题型结构
- per_question：每道选中原题按指定数量生成变式
- 如果确实只有一道题：source_scope.mode 返回 single，并直接设计蓝图
- 只有单题或用户已经选择原题时，exercise_plan 才必须恰好 {count} 项，并严格使用上面的逐题题型
- 每题说明目标能力、变式方式和设计意图
- 只输出合法 JSON，不输出具体题干

## 输出结构

{json.dumps(contract, ensure_ascii=False, indent=2)}
"""
    messages = [
        {
            "role": "system",
            "content": (
                "你是研究生教育教研专家。先准确识别原题，再制定可审查的训练蓝图。"
                "不要在本阶段生成练习题正文。只输出一个合法 JSON 对象。"
            ),
        },
        {"role": "user", "content": _user_content(task, sources["images"])},
    ]
    raw = _call_practice_json(
        OpenAICompatibleClient(provider),
        messages,
        model=model,
        temperature=0.2,
        thinking=_clean(payload.get("thinking"), 20) or None,
    )
    source_scope = _normalize_source_scope(raw.get("source_scope"))
    if prior_source_scope.get("mode") == "question_set" and selected_source_questions:
        source_scope = prior_source_scope
    if source_scope["mode"] == "question_set" and not selected_source_questions:
        return {
            "schema_version": "answer_book.practice_source_selection.v1",
            "requires_source_selection": True,
            "source_scope": source_scope,
            "source_analysis": raw.get("source_analysis") if isinstance(raw.get("source_analysis"), dict) else {},
            "source_files": sources["file_names"],
            "generation": {"provider": provider.name, "model": model, "stage": "source_detection"},
        }
    plan = _normalize_plan(
        raw,
        count=count,
        planned_types=planned_types,
        difficulty=difficulty,
        selected_types=selected_types,
        source_files=sources["file_names"],
        source_scope=source_scope,
        selected_source_questions=selected_source_questions,
        planned_source_ids=planned_source_ids,
        generation_strategy=generation_strategy,
    )
    plan["generation"] = {"provider": provider.name, "model": model, "stage": "planning"}
    return plan


def generate_practice_from_plan(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    blueprint = plan.get("blueprint") if isinstance(plan.get("blueprint"), dict) else {}
    exercise_plan = blueprint.get("exercise_plan") if isinstance(blueprint.get("exercise_plan"), list) else []
    if not exercise_plan:
        raise ValueError("缺少已确认的训练蓝图。")
    count = max(1, min(30, len(exercise_plan)))
    planned_types = [
        _clean(item.get("question_type"), 20) if isinstance(item, dict) else "综合题"
        for item in exercise_plan[:count]
    ]
    planned_types = [item if item in ALLOWED_TYPES else "综合题" for item in planned_types]
    planned_source_ids = [
        _clean(item.get("source_question_id"), 80) if isinstance(item, dict) else ""
        for item in exercise_plan[:count]
    ]
    sources = parse_practice_sources(payload)
    provider, model = _model_runtime(payload, bool(sources["images"]))
    contract = {"exercises": [_exercise_contract()]}
    all_exercises: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(provider)
    batch_size = 5
    for batch_start in range(0, count, batch_size):
        batch_plan = exercise_plan[batch_start : batch_start + batch_size]
        batch_count = len(batch_plan)
        task = f"""# 任务

根据已经确认的研究生专项训练蓝图，生成完整练习题。

## 原题材料

{sources["text"] or "请读取附带的题目图片或 PDF 页面。"}

## 已确认的原题分析

{json.dumps(plan.get("source_analysis") or {}, ensure_ascii=False, indent=2)}

## 本次选中的原题

{json.dumps(plan.get("selected_source_questions") or plan.get("source_scope") or {}, ensure_ascii=False, indent=2)}

## 已确认的训练蓝图

{json.dumps({**blueprint, "exercise_plan": batch_plan}, ensure_ascii=False, indent=2)}

## 内容要求

- 这是总任务第 {batch_start + 1} 至 {batch_start + batch_count} 题
- 必须生成恰好 {batch_count} 道题，逐题严格遵循本批 exercise_plan
- 文字、术语和推导深度保持研究生层级
- 题干与答案可使用 Markdown；公式统一使用 LaTeX 并放入 formulas
- 表格放入 tables；折线图、柱状图、散点图和示意图规格放入 figures
- 每题独立、条件充分、答案明确，不得只是替换数字
- 只输出合法 JSON

## 输出结构

{json.dumps(contract, ensure_ascii=False, indent=2)}
"""
        messages = [
            {
                "role": "system",
                "content": (
                    "你是严谨的研究生教研专家。严格执行已确认蓝图生成练习正文。"
                    "不得改变题型方案，不得降低到中学或普通本科入门层级。只输出合法 JSON。"
                ),
            },
            {"role": "user", "content": _user_content(task, sources["images"])},
        ]
        raw_batch = _call_practice_json(
            client,
            messages,
            model=model,
            temperature=0.35,
            thinking=_clean(payload.get("thinking"), 20) or None,
        )
        batch_exercises = raw_batch.get("exercises") if isinstance(raw_batch.get("exercises"), list) else []
        all_exercises.extend(item for item in batch_exercises if isinstance(item, dict))
    raw = {"exercises": all_exercises}
    result = normalize_practice_set(
        raw,
        requested_count=count,
        subject="",
        planned_types=planned_types,
        planned_source_ids=planned_source_ids,
    )
    result["source_analysis"] = plan.get("source_analysis") or result["source_analysis"]
    result["blueprint"] = {**result["blueprint"], **blueprint}
    result["source_scope"] = plan.get("source_scope") or {"mode": "single", "questions": []}
    result["selected_source_questions"] = plan.get("selected_source_questions") or []
    result["generation_strategy"] = blueprint.get("generation_strategy") or "single"
    source_lookup = {
        _clean(item.get("source_question_id"), 80): item
        for item in (plan.get("selected_source_questions") or [])
        if isinstance(item, dict)
    }
    grouped: dict[str, list[str]] = {}
    for exercise in result["exercises"]:
        source_id = _clean(exercise.get("source_question_id"), 80)
        grouped.setdefault(source_id, []).append(exercise["exercise_id"])
    result["exercise_groups"] = [
        {
            "source_question_id": source_id,
            "source_question": source_lookup.get(source_id) or {},
            "exercise_ids": exercise_ids,
        }
        for source_id, exercise_ids in grouped.items()
    ]
    result["generation"] = {"provider": provider.name, "model": model, "stage": "generation"}
    return result


def regenerate_practice_exercise(payload: dict[str, Any]) -> dict[str, Any]:
    practice = payload.get("practice") if isinstance(payload.get("practice"), dict) else {}
    exercises = practice.get("exercises") if isinstance(practice.get("exercises"), list) else []
    index = int(payload.get("exercise_index") or 0)
    if index < 0 or index >= len(exercises):
        raise ValueError("需要重新生成的题目序号无效。")
    current = exercises[index] if isinstance(exercises[index], dict) else {}
    provider, model = _model_runtime(payload, False)
    target_type = _clean(current.get("question_type"), 20)
    task = f"""# 任务

只重新生成专项练习中的第 {index + 1} 题，其他题目不会传给你修改。

## 原题分析

{json.dumps(practice.get("source_analysis") or {}, ensure_ascii=False, indent=2)}

## 训练蓝图

{json.dumps(practice.get("blueprint") or {}, ensure_ascii=False, indent=2)}

## 当前题目

{json.dumps(current, ensure_ascii=False, indent=2)}

## 用户补充要求

{_clean(payload.get("instruction"), 1000) or "保持训练目标，换一种有效变式。"}

## 约束

- 保持题型为 {target_type or "综合题"}
- 保持研究生层级
- 只输出包含 exercises 数组的合法 JSON，数组中恰好一题

## 输出结构

{json.dumps({"exercises": [_exercise_contract()]}, ensure_ascii=False, indent=2)}
"""
    messages = [
        {"role": "system", "content": "你是研究生教研专家，只重写指定的一道练习题。只输出合法 JSON。"},
        {"role": "user", "content": task},
    ]
    raw = _call_practice_json(
        OpenAICompatibleClient(provider),
        messages,
        model=model,
        temperature=0.45,
        thinking=_clean(payload.get("thinking"), 20) or None,
    )
    normalized = normalize_practice_set(
        {
            "source_analysis": practice.get("source_analysis") or {},
            "blueprint": practice.get("blueprint") or {},
            "exercises": raw.get("exercises") or [],
        },
        requested_count=1,
        subject="",
        planned_types=[target_type] if target_type in ALLOWED_TYPES else ["综合题"],
        planned_source_ids=[_clean(current.get("source_question_id"), 80)],
    )
    exercise = normalized["exercises"][0]
    exercise["exercise_id"] = _clean(current.get("exercise_id"), 100) or f"practice_{index + 1:02d}"
    exercise["number"] = index + 1
    return {"exercise": exercise, "generation": {"provider": provider.name, "model": model}}


def generate_practice_set(payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry point for callers that still expect one request."""
    plan = plan_practice_set(payload)
    return generate_practice_from_plan({**payload, "plan": plan})
